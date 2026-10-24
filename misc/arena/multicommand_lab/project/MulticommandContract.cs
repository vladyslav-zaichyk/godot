using Godot;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Reflection.Metadata;
using System.Reflection.PortableExecutable;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Json;
using System.Threading.Tasks;

// Small numerical consumer, deliberately not an FPS acceptance harness.
public partial class MulticommandContract : Node
{
    const int Prefix = 16;
    const uint Poison = 0xcdcdcdcd;
    static readonly string[] Inputs = { "MulticommandLab.csproj", "project.godot", "Main.tscn",
        "MulticommandContract.cs", "ContractErrorLogger.cs", "Producer.glslinc", "Consumer.gdshader" };
    readonly Dictionary<string, string> _args = new();
    readonly List<(Rid rid, uint bytes)> _owned = new();
    readonly List<ArrayMesh> _meshes = new();
    readonly List<ShaderMaterial> _materials = new();
    readonly List<Rid> _multimeshes = new(), _instances = new();
    readonly List<Command> _records = new();
    readonly List<object> _cases = new(), _phases = new(), _rebinds = new();
    readonly Dictionary<string, (string ids, string commands, string raster)> _loop = new();
    readonly ContractErrorLogger _errors = new();
    RenderingDevice _rd = null!;
    Shader? _shader;
    BufferRD? _idWrapper;
    SubViewport? _view;
    ViewportTexture? _texture;
    World3D? _world;
    Rid _producer, _pipeline, _set, _ids, _commands, _metadata;
    int _archetypes, _lods, _segments, _units, _columns, _rows, _width, _height;
    int _checks, _dispatches, _bufferReadbacks, _pixelSamples, _ownershipProbes;
    int _idWords, _commandBytes, _stride, _currentSegments;
    string _layout = "", _control = "", _failure = "";
    long _peakRequestedBytes;
    record Shape(uint IndexCount, uint FirstIndex, uint VertexOffset);
    record Command(int Owner, int IndexInOwner, int SpanCount, int SpanBaseBytes, int OffsetBytes,
        int IdBase, int FirstInstance, int Shape, int SegmentStart, int SegmentCount, Shape Geometry);
    readonly List<Shape> _shapes = new();

    public override async void _Ready()
    {
        OS.AddLogger(_errors);
        foreach (string argument in OS.GetCmdlineUserArgs()) {
            string[] pair = argument.TrimStart('-').Split('=', 2);
            _args[pair[0]] = pair.Length == 2 ? pair[1] : "true";
        }
        try {
            Check(BitConverter.IsLittleEndian, "Little-endian command words required.");
            Check(Engine.GetVersionInfo()["hash"].AsString() == Required("expected-engine-hash"), "Native source identity differs.");
            Check(SdkVersion == Required("expected-sdk-version"), "Compiled SDK differs.");
            Check(RenderingServer.GetCurrentRenderingDriverName() == "vulkan" &&
                RenderingServer.GetCurrentRenderingMethod() == "forward_plus", "Vulkan/Forward+ required.");
            _archetypes = Number("archetypes", 32, 1, 32); _lods = Number("lods", 4, 1, 4);
            _segments = Number("segments", 16, 1, 1024); _units = Number("units", 4, 1, 256);
            _layout = Required("layout"); Check(_layout is "separate" or "packed", "Unknown layout.");
            Check((long)_archetypes * _lods * _segments * _units <= 1_000_000, "Population bound exceeded.");
            Check(_layout != "packed" || _lods * _segments <= 1024, "Packed per-owner command bound exceeded.");
            _rd = RenderingServer.GetRenderingDevice() ?? throw new Exception("Global RD unavailable.");
            Engine.MaxFps = 120;
            BuildScene();
            await OnRender(() => {
                using var source = new RDShaderSource { SourceCompute = Godot.FileAccess.GetFileAsString("res://Producer.glslinc") };
                using var spirv = _rd.ShaderCompileSpirVFromSource(source);
                Check(string.IsNullOrWhiteSpace(spirv.CompileErrorCompute), spirv.CompileErrorCompute);
                _producer = Own(_rd.ShaderCreateFromSpirV(spirv)); _pipeline = Own(_rd.ComputePipelineCreate(_producer));
            });
            foreach (int segments in new[] { 1, _segments }.Distinct()) {
                _currentSegments = segments;
                foreach (int stride in new[] { 20, 32 }) {
                    _stride = stride;
                    foreach (string control in new[] { "C0", "C1", "C2" }) {
                        _control = control;
                        await CreateCase();
                        for (uint phase = 0; phase < 4; phase++) {
                            await OnRender(() => Produce(phase));
                            await Numeric(phase);
                        }
                        await RebindChecks();
                        await CleanupCase();
                        Check(_owned.Count == 2, "Case retained RD resources.");
                    }
                }
            }
        } catch (Exception error) {
            _failure = error.ToString(); GD.PushError(_failure);
        } finally {
            try {
                await CleanupCase();
                if (_rd != null) await OnRender(() => { FreeOwned(_pipeline); _pipeline = default; FreeOwned(_producer); _producer = default; });
                _texture?.Dispose(); _texture = null; _view?.Free(); _view = null;
                _world?.Dispose(); _world = null;
                foreach (ArrayMesh mesh in _meshes) mesh.Dispose(); _meshes.Clear();
                _shader?.Dispose(); _shader = null;
                await DrainFrames();
                Check(_owned.Count == 0 && _multimeshes.Count == 0 && _materials.Count == 0, "Final ownership ledger not empty.");
            } catch (Exception error) { _failure += "\nCleanup: " + error; GD.PushError(error.ToString()); }
            try { WriteResult(); }
            catch (Exception error) {
                GD.PushError(error.ToString());
                try { File.WriteAllBytes(Path.GetFullPath(Required("record")), JsonSerializer.SerializeToUtf8Bytes(new {
                    contract = "multicommand-numeric-contract-v1", passed = false, failure = error.ToString() })); }
                finally { OS.RemoveLogger(_errors); _errors.Dispose(); GetTree().Quit(2); }
            }
        }
    }

    string Required(string key) => _args.TryGetValue(key, out string? value) && value.Length > 0 ? value : throw new Exception("Missing --" + key);
    int Number(string key, int fallback, int min, int max) {
        int result = _args.TryGetValue(key, out string? value) ? int.Parse(value) : fallback;
        Check(result >= min && result <= max, "Invalid --" + key); return result;
    }
    static string SdkVersion => typeof(MulticommandContract).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>().SingleOrDefault(a => a.Key == "GodotSdkVersion")?.Value ?? "";
    static string BytesHash(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    static string Hash(string path) => BytesHash(File.ReadAllBytes(path));
    void Check(bool condition, string message) { _checks++; if (!condition) throw new Exception(message); }
    async Task OnRender(Action action) {
        var done = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        RenderingServer.CallOnRenderThread(Callable.From(() => { try { action(); done.SetResult(); } catch (Exception error) { done.SetException(error); } }));
        await done.Task;
    }
    async Task Frames(int count) { for (int i = 0; i < count; i++) await ToSignal(RenderingServer.Singleton, RenderingServer.SignalName.FramePostDraw); }
    async Task DrainFrames() { if (_rd == null) return; await Frames(checked((int)_rd.GetFrameDelay() + 4)); await OnRender(() => { }); }
    Rid Own(Rid rid, uint bytes = 0) {
        Check(rid.IsValid, "RD allocation failed."); _owned.Add((rid, bytes));
        _peakRequestedBytes = Math.Max(_peakRequestedBytes, _owned.Sum(item => (long)item.bytes)); return rid;
    }
    void FreeOwned(Rid rid) {
        if (!rid.IsValid) return;
        int index = _owned.FindIndex(item => item.rid == rid); Check(index >= 0, "Attempted to free an unowned RID.");
        _rd.FreeRid(rid); _owned.RemoveAt(index);
    }
    BufferRD Wrap(Rid rid) {
        var result = new BufferRD();
        try { Check(result.AttachStorageBuffer(rid) == Error.Ok && result.GetBufferRdRid() == rid, "BufferRD borrowed identity differs."); return result; }
        catch { result.Dispose(); throw; }
    }
    static byte[] Words(IEnumerable<uint> words) => MemoryMarshal.AsBytes(words.ToArray().AsSpan()).ToArray();

    void BuildScene()
    {
        int population = checked(_archetypes * _lods * _segments * _units);
        _columns = 1; while (_columns * _columns < population * 2) _columns *= 2;
        _rows = (population + _columns - 1) / _columns;
        // Fixed aspect is exactly columns/rows; at least four pixels per cell.
        _width = _columns * 4; _height = _rows * 4;
        Check(_width <= 8192 && _height <= 8192, "Numeric viewport bound exceeded.");
        _shader = GD.Load<Shader>("res://Consumer.gdshader") ?? throw new Exception("Missing consumer shader.");
        var packedVertices = new List<Vector3>(); var packedIndices = new List<int>();
        for (int shape = 0; shape < _archetypes * _lods; shape++) {
            int sides = 3 + (shape / _lods + shape % _lods) % 6;
            // Inradius stays above the half-pixel diagonal of the 4px probe grid.
            float radius = 0.45f + 0.002f * (shape % 8);
            var vertices = new List<Vector3> { Vector3.Zero }; var indices = new List<int>();
            for (int side = 0; side < sides; side++) {
                float angle = 2 * Mathf.Pi * side / sides;
                vertices.Add(new Vector3(Mathf.Cos(angle) * radius, Mathf.Sin(angle) * radius, 0));
                indices.AddRange(new[] { 0, 1 + side, 1 + (side + 1) % sides });
            }
            if (_layout == "packed") {
                Check(indices.All(index => index >= 0 && index < vertices.Count), "Packed shape index range invalid.");
                _shapes.Add(new Shape((uint)indices.Count, (uint)packedIndices.Count, (uint)packedVertices.Count));
                packedVertices.AddRange(vertices); packedIndices.AddRange(indices);
            } else {
                _shapes.Add(new Shape((uint)indices.Count, 0, 0)); _meshes.Add(Mesh(vertices, indices));
            }
        }
        if (_layout == "packed") {
            foreach (Shape shape in _shapes) {
                Check((ulong)shape.FirstIndex + shape.IndexCount <= (ulong)packedIndices.Count, "Packed index span exceeds surface.");
                for (uint i = 0; i < shape.IndexCount; i++)
                    Check((ulong)shape.VertexOffset + (uint)packedIndices[(int)(shape.FirstIndex + i)] < (ulong)packedVertices.Count,
                        "Packed base vertex exceeds surface.");
            }
            _meshes.Add(Mesh(packedVertices, packedIndices));
        }
        _world = new World3D();
        _view = new SubViewport { Size = new Vector2I(_width, _height), World3D = _world,
            TransparentBg = false, RenderTargetUpdateMode = SubViewport.UpdateMode.Always };
        AddChild(_view);
        _view.AddChild(new Camera3D { Projection = Camera3D.ProjectionType.Orthogonal, Size = _rows,
            KeepAspect = Camera3D.KeepAspectEnum.Height, Near = 0.1f, Far = 10,
            Position = new Vector3(0, 0, 2), Current = true });
        _texture = _view.GetTexture();
    }
    static ArrayMesh Mesh(List<Vector3> vertices, List<int> indices) {
        var mesh = new ArrayMesh();
        try {
            using var arrays = new Godot.Collections.Array(); arrays.Resize((int)Godot.Mesh.ArrayType.Max);
            arrays[(int)Godot.Mesh.ArrayType.Vertex] = vertices.ToArray(); arrays[(int)Godot.Mesh.ArrayType.Index] = indices.ToArray();
            mesh.AddSurfaceFromArrays(Godot.Mesh.PrimitiveType.Triangles, arrays); return mesh;
        } catch { mesh.Dispose(); throw; }
    }

    async Task CreateCase()
    {
        _records.Clear(); _idWords = 0; _commandBytes = Prefix;
        bool compact = _control == "C0", packed = _layout == "packed" && !compact;
        int ownerCount = packed ? _archetypes : _archetypes * _lods;
        for (int owner = 0; owner < ownerCount; owner++) {
            int shapeCount = packed ? _lods : 1;
            int spanCount = compact ? 1 : shapeCount * _currentSegments;
            int shapeCapacity = compact ? 1 + _currentSegments * _units : 1 + _currentSegments * (_units + 1);
            int capacity = shapeCount * shapeCapacity;
            int idBase = _idWords, spanBase = _commandBytes;
            _idWords += (capacity + 3) & ~3;
            for (int local = 0; local < spanCount; local++) {
                int localShape = compact ? 0 : local / _currentSegments;
                int shape = packed ? owner * _lods + localShape : owner;
                int segment = compact ? 0 : local % _currentSegments;
                int first = compact ? 1 : localShape * shapeCapacity + 1 + segment * (_units + 1);
                _records.Add(new Command(owner, local, spanCount, spanBase, spanBase + local * _stride,
                    idBase, first, shape, segment, compact ? _currentSegments : 1, _shapes[shape]));
            }
            _commandBytes += (spanCount - 1) * _stride + 20;
            // Preserve next owner alignment even for packed 20-byte records.
            _commandBytes = (_commandBytes + 3) & ~3;
            _materials.Add(new ShaderMaterial { Shader = _shader });
        }
        // BufferRD's ownership probe requires 16-byte storage alignment. Span
        // ends remain exact; the whole-buffer oracle also verifies this tail.
        _commandBytes = (_commandBytes + 15) & ~15;
        await OnRender(() => {
            _ids = Own(_rd.StorageBufferCreate((uint)_idWords * 4, Words(Enumerable.Repeat(uint.MaxValue, _idWords))), (uint)_idWords * 4);
            // A frame may render between this callback and the first producer callback.
            // Publish legal empty commands before exposing any native owner; poison only guards/padding.
            var initialCommands = Enumerable.Repeat(Poison, _commandBytes / 4).ToArray();
            foreach (Command c in _records) {
                int word = c.OffsetBytes / 4;
                uint[] empty = { c.Geometry.IndexCount, 0, c.Geometry.FirstIndex, c.Geometry.VertexOffset, (uint)c.FirstInstance };
                Array.Copy(empty, 0, initialCommands, word, 5);
            }
            _commands = Own(_rd.StorageBufferCreate((uint)_commandBytes, Words(initialCommands), (RenderingDevice.StorageBufferUsage)1), (uint)_commandBytes);
            var metadata = new List<uint>();
            foreach (Command c in _records) metadata.AddRange(new uint[] {
                (uint)c.OffsetBytes / 4, (uint)c.IdBase, (uint)c.FirstInstance, (uint)c.Shape,
                (uint)c.SegmentStart, (uint)c.SegmentCount, c.Geometry.FirstIndex, c.Geometry.IndexCount,
                c.Geometry.VertexOffset, (uint)_units, (uint)c.SpanBaseBytes / 4, (uint)c.SpanCount,
                (uint)c.IndexInOwner, (uint)_stride / 4, 0, 0 });
            byte[] meta = Words(metadata); _metadata = Own(_rd.StorageBufferCreate((uint)meta.Length, meta), (uint)meta.Length);
            _idWrapper = Wrap(_ids);
            using Variant ids = Variant.From(_idWrapper!);
            for (int owner = 0; owner < ownerCount; owner++) {
                Command c = _records.First(item => item.Owner == owner);
                int capacity = packed ? _lods * (1 + _currentSegments * (_units + 1)) : compact ? 1 + _currentSegments * _units : 1 + _currentSegments * (_units + 1);
                ShaderMaterial material = _materials[owner];
                material.SetShaderParameter("visible_ids", ids); material.SetShaderParameter("id_base", c.IdBase);
                material.SetShaderParameter("columns", _columns); material.SetShaderParameter("rows", _rows);
                Rid mm = RenderingServer.MultimeshCreate(); _multimeshes.Add(mm);
                RenderingServer.MultimeshSetMesh(mm, _meshes[_layout == "packed" ? 0 : owner].GetRid());
                RenderingServer.MultimeshSetCustomAabb(mm, new Aabb(new Vector3(-_columns, -_rows, -0.1f), new Vector3(_columns * 2, _rows * 2, 0.2f)));
                Error attached = compact ? RenderingServer.MultimeshAttachSharedDrawBuffer(mm, capacity, _commands, c.SpanBaseBytes) :
                    RenderingServer.MultimeshAttachSharedDrawCommandSpan(mm, capacity, _commands, c.SpanBaseBytes,
                        c.SpanCount, _stride, _control == "C1" ? RenderingServer.MultimeshIndirectSubmissionMode.Loop : RenderingServer.MultimeshIndirectSubmissionMode.MultiDraw);
                Check(attached == Error.Ok, "Attach failed: " + _control + " / " + attached);
                Check(RenderingServer.MultimeshIsShaderDriven(mm) && !RenderingServer.MultimeshGetBufferRdRid(mm).IsValid &&
                    !RenderingServer.MultimeshUsesShaderMotionVectors(mm), "Unexpected native transform bank/history mode.");
                Check(RenderingServer.MultimeshGetSharedDrawCommandCount(mm) == c.SpanCount &&
                    RenderingServer.MultimeshGetSharedDrawCommandStride(mm) == (compact ? 0 : _stride), "Span metadata differs.");
                Rid instance = RenderingServer.InstanceCreate(); _instances.Add(instance);
                RenderingServer.InstanceSetBase(instance, mm); RenderingServer.InstanceSetTransform(instance, Transform3D.Identity);
                RenderingServer.InstanceSetScenario(instance, _world!.Scenario);
                RenderingServer.InstanceGeometrySetMaterialOverride(instance, material.GetRid());
                RenderingServer.InstanceGeometrySetCastShadowsSetting(instance, RenderingServer.ShadowCastingSetting.Off);
                RenderingServer.InstanceSetVisible(instance, true);
            }
            var uniforms = new Godot.Collections.Array<RDUniform>(); using var lifetime = (Godot.Collections.Array)uniforms;
            var items = new List<RDUniform>();
            try {
                Rid[] buffers = { _ids, _commands, _metadata };
                for (int i = 0; i < buffers.Length; i++) {
                    var uniform = new RDUniform { UniformType = RenderingDevice.UniformType.StorageBuffer, Binding = i };
                    uniform.AddId(buffers[i]); uniforms.Add(uniform); items.Add(uniform);
                }
                _set = Own(_rd.UniformSetCreate(uniforms, _producer, 0));
            } finally { foreach (RDUniform item in items) item.Dispose(); }
            _cases.Add(new { control = _control, layout = _layout, segments = _currentSegments, stride = _stride,
                native_owner_count = ownerCount, allocated_command_slots = _records.Count,
                expected_rd_calls_per_color_pass = compact || _control == "C2" ? ownerCount : _records.Count,
                command_prefix_bytes = Prefix, requested_rd_bytes = _owned.Sum(item => (long)item.bytes),
                logical_rd_buffer_bytes = _rd.GetMemoryUsage(RenderingDevice.MemoryType.Buffers),
                comparison = _layout == "packed" && compact ? "Separate strongest existing one-command-per-shape control; owner count differs from packed C1/C2." : "C1/C2 same-owner controls; C0 producer differs." });
        });
    }

    void Produce(uint phase) {
        long list = _rd.ComputeListBegin(); _rd.ComputeListBindComputePipeline(list, _pipeline); _rd.ComputeListBindUniformSet(list, _set, 0);
        _rd.ComputeListSetPushConstant(list, Words(new uint[] { (uint)_records.Count, phase, (uint)_currentSegments, (uint)_units }), 16);
        _rd.ComputeListDispatch(list, (uint)(_records.Count + 63) / 64, 1, 1); _rd.ComputeListEnd(); _dispatches++;
    }
    bool Active(int segment, uint phase) => phase != 3 && (phase != 1 || segment % 2 == 0 || segment == _currentSegments - 1);
    async Task Numeric(uint phase)
    {
        await Frames(4);
        byte[] ids = Array.Empty<byte>(), commands = Array.Empty<byte>();
        await OnRender(() => { ids = _rd.BufferGetData(_ids); commands = _rd.BufferGetData(_commands); _bufferReadbacks += 2; });
        uint[] idWords = MemoryMarshal.Cast<byte, uint>(ids).ToArray(), commandWords = MemoryMarshal.Cast<byte, uint>(commands).ToArray();
        var expectedCommands = Enumerable.Repeat(Poison, _commandBytes / 4).ToArray();
        var admitted = new HashSet<uint>(); int nonempty = 0;
        foreach (Command c in _records) {
            Check(idWords[c.IdBase + c.FirstInstance - 1] == uint.MaxValue, "Producer overwrote a reserved identity guard.");
            int word = phase == 2 ? (c.SpanBaseBytes + (c.SpanCount - 1 - c.IndexInOwner) * _stride) / 4 : c.OffsetBytes / 4;
            int count = 0;
            for (int segment = c.SegmentStart; segment < c.SegmentStart + c.SegmentCount; segment++) {
                if (!Active(segment, phase)) continue;
                for (int unit = 0; unit < _units; unit++) {
                    uint id = checked((uint)((c.Shape * _currentSegments + segment) * _units + unit));
                    Check(idWords[c.IdBase + c.FirstInstance + count] == id, "ID coverage differs at command " + c.IndexInOwner);
                    Check(admitted.Add(id), "Identity emitted more than once."); count++;
                }
            }
            if (count != 0) nonempty++;
            uint[] words = { c.Geometry.IndexCount, (uint)count, c.Geometry.FirstIndex, c.Geometry.VertexOffset, (uint)c.FirstInstance };
            Array.Copy(words, 0, expectedCommands, word, 5);
            int capacity = RenderingServer.MultimeshGetInstanceCount(_multimeshes[c.Owner]);
            Check((ulong)c.FirstInstance + (uint)count <= (ulong)capacity, "Producer exceeded capacity.");
        }
        Check(commandWords.SequenceEqual(expectedCommands), "Whole command span/prefix/padding differs, including last command.");
        int expectedPopulation = _archetypes * _lods * Enumerable.Range(0, _currentSegments).Count(s => Active(s, phase)) * _units;
        Check(admitted.Count == expectedPopulation, "Missing admitted identities.");
        // Raster probes are numeric samples only; no screenshot artifact is written.
        using Image image = _texture!.GetImage();
        Check(!image.IsEmpty() && image.GetWidth() == _width && image.GetHeight() == _height, "Numeric viewport dimensions differ.");
        var probes = new List<uint> { 0, (uint)(_units * (_currentSegments - 1)), (uint)(_archetypes * _lods * _currentSegments * _units - 1) };
        foreach (uint id in probes.Distinct()) {
            int segment = (int)(id / (uint)_units % (uint)_currentSegments);
            int x = (int)(id % (uint)_columns) * 4 + 2;
            int y = _height - ((int)(id / (uint)_columns) * 4 + 2);
            Color color = image.GetPixel(x, y); uint bits = Active(segment, phase) ? 1 + id % 6 : 0;
            float[] actual = { color.R, color.G, color.B };
            for (int channel = 0; channel < 3; channel++) {
                bool on = (bits & (1u << channel)) != 0;
                Check(on ? actual[channel] > 0.85f : actual[channel] < 0.08f, "Raster identity probe differs.");
            }
            _pixelSamples++;
        }
        string key = _currentSegments + ":" + _stride + ":" + phase;
        var hashes = (BytesHash(ids), BytesHash(commands), BytesHash(image.GetData()));
        if (_control == "C1") _loop.Add(key, hashes);
        if (_control == "C2") Check(_loop.TryGetValue(key, out var reference) && reference == hashes, "C1/C2 producer data or numeric raster differs.");
        _phases.Add(new { control = _control, segments = _currentSegments, stride = _stride, phase,
            admitted_identities = admitted.Count, nonempty_command_slots = nonempty, final_command_checked = true,
            ids_sha256 = hashes.Item1, command_sha256 = hashes.Item2, numeric_raster_sha256 = hashes.Item3 });
    }

    async Task RebindChecks()
    {
        await OnRender(() => {
            Rid mm = _multimeshes[0]; Command c = _records[0];
            int capacity = RenderingServer.MultimeshGetInstanceCount(mm);
            int stride = RenderingServer.MultimeshGetSharedDrawCommandStride(mm);
            var mode = RenderingServer.MultimeshGetSharedDrawSubmissionMode(mm);
            bool Preserved() => RenderingServer.MultimeshGetCommandBufferRdRid(mm) == _commands &&
                RenderingServer.MultimeshGetCommandByteOffset(mm) == c.SpanBaseBytes &&
                RenderingServer.MultimeshGetSharedDrawCommandCount(mm) == c.SpanCount &&
                RenderingServer.MultimeshGetSharedDrawCommandStride(mm) == stride &&
                RenderingServer.MultimeshGetSharedDrawSubmissionMode(mm) == mode &&
                RenderingServer.MultimeshGetInstanceCount(mm) == capacity && RenderingServer.MultimeshIsShaderDriven(mm) &&
                !RenderingServer.MultimeshGetBufferRdRid(mm).IsValid && !RenderingServer.MultimeshUsesShaderMotionVectors(mm);
            var rejected = new List<string>();
            void Reject(string name, long size, long offset, long count, long step, RenderingServer.MultimeshIndirectSubmissionMode submission) {
                Error error = RenderingServer.MultimeshAttachSharedDrawCommandSpan(mm, size, _commands, offset, count, step, submission);
                Check(error == Error.InvalidParameter, "Invalid input accepted: " + name + " / " + error);
                Check(Preserved(), "Failed rebind mutated attachment: " + name); rejected.Add(name);
            }
            Reject("negative_capacity", -1, c.SpanBaseBytes, 1, 20, mode);
            Reject("capacity_above_int32", (long)int.MaxValue + 1, c.SpanBaseBytes, 1, 20, mode);
            Reject("negative_offset", capacity, -4, 1, 20, mode);
            Reject("offset_above_int32", capacity, (long)int.MaxValue + 1, 1, 20, mode);
            Reject("unaligned_offset", capacity, c.SpanBaseBytes + 1, 1, 20, mode);
            Reject("zero_count", capacity, c.SpanBaseBytes, 0, 20, mode);
            Reject("negative_count", capacity, c.SpanBaseBytes, -1, 20, mode);
            Reject("count_above_bound", capacity, c.SpanBaseBytes, 1025, 20, mode);
            Reject("short_stride", capacity, c.SpanBaseBytes, 1, 16, mode);
            Reject("unaligned_stride", capacity, c.SpanBaseBytes, 1, 21, mode);
            Reject("stride_above_int32", capacity, c.SpanBaseBytes, 1, (long)int.MaxValue + 1, mode);
            Reject("last_command_out_of_buffer", capacity, _commandBytes - 20, 2, 20, mode);
            Reject("offset_plus_command_out_of_buffer", capacity, _commandBytes - 16, 1, 20, mode);
            Reject("invalid_mode", capacity, c.SpanBaseBytes, 1, 20, (RenderingServer.MultimeshIndirectSubmissionMode)99);
            Check(RenderingServer.MultimeshAttachSharedDrawCommandSpan(mm, capacity, _commands, c.SpanBaseBytes, 1, 32,
                RenderingServer.MultimeshIndirectSubmissionMode.MultiDraw) == Error.Ok, "Count-one rebind failed.");
            Check(RenderingServer.MultimeshAttachSharedDrawBuffer(mm, capacity, _commands, c.SpanBaseBytes) == Error.Ok,
                "Old API rebind failed.");
            Check(RenderingServer.MultimeshGetSharedDrawCommandCount(mm) == 1 && RenderingServer.MultimeshGetSharedDrawCommandStride(mm) == 0,
                "Old API did not reset span metadata.");
            Check(RenderingServer.MultimeshGetSharedDrawSubmissionMode(mm) == RenderingServer.MultimeshIndirectSubmissionMode.Loop &&
                !RenderingServer.MultimeshUsesShaderMotionVectors(mm) && !RenderingServer.MultimeshGetBufferRdRid(mm).IsValid,
                "Old API did not restore single-command mode/history/transform contract.");
            _rebinds.Add(new { control = _control, segments = _currentSegments, stride = _stride, rejected,
                transactional = true, count_one_multidraw = true, old_api_resets_count_stride = true,
                old_api_resets_mode = true, history_false_and_no_transform_bank_preserved = true });
        });
    }

    async Task CleanupCase()
    {
        if (_rd == null) return;
        await OnRender(() => {
            foreach (Rid instance in _instances) RenderingServer.InstanceSetVisible(instance, false);
            foreach (ShaderMaterial material in _materials) material.SetShaderParameter("visible_ids", default(Variant));
        });
        await DrainFrames();
        await OnRender(() => {
            foreach (Rid instance in _instances) RenderingServer.FreeRid(instance); _instances.Clear();
            foreach (Rid mm in _multimeshes) {
                RenderingServer.MultimeshDetachSharedBuffers(mm);
                Check(!RenderingServer.MultimeshIsUsingSharedBuffers(mm) && RenderingServer.MultimeshGetSharedDrawCommandCount(mm) == 0,
                    "Detach retained shared span metadata.");
                RenderingServer.FreeRid(mm);
            }
            _multimeshes.Clear(); FreeOwned(_set); _set = default;
            foreach (Rid rid in new[] { _ids, _commands, _metadata }.Where(rid => rid.IsValid)) {
                using BufferRD borrowed = Wrap(rid); _ownershipProbes++;
            }
        });
        _idWrapper?.Dispose(); _idWrapper = null;
        foreach (ShaderMaterial material in _materials) material.Dispose(); _materials.Clear();
        await DrainFrames();
        await OnRender(() => {
            FreeOwned(_metadata); _metadata = default; FreeOwned(_commands); _commands = default;
            FreeOwned(_ids); _ids = default;
        });
    }

    string RuntimeAssemblyHash(Assembly assembly, string fallback) {
        string path = string.IsNullOrEmpty(assembly.Location) ? fallback : assembly.Location;
        using var stream = File.OpenRead(path); using var pe = new PEReader(stream);
        MetadataReader metadata = pe.GetMetadataReader();
        Check(metadata.GetGuid(metadata.GetModuleDefinition().Mvid) == assembly.ManifestModule.ModuleVersionId, "Loaded managed module differs from file.");
        return Hash(path);
    }
    void WriteResult()
    {
        string fixture = ProjectSettings.GlobalizePath("res://.godot/mono/temp/bin/Debug/MulticommandLab.dll");
        string api = Path.Combine(Path.GetDirectoryName(OS.GetExecutablePath())!, "GodotSharp/Api/Debug/GodotSharp.dll");
        string fixtureHash = RuntimeAssemblyHash(typeof(MulticommandContract).Assembly, fixture);
        string apiHash = RuntimeAssemblyHash(typeof(Node).Assembly, api);
        bool passed = _failure.Length == 0 && _errors.Errors.Length == 0 && _cases.Count >= 6 && _owned.Count == 0;
        var result = new {
            contract = "multicommand-numeric-contract-v1", passed, failure = _failure,
            engine_hash = Engine.GetVersionInfo()["hash"].AsString(), sdk_version = SdkVersion,
            driver = RenderingServer.GetCurrentRenderingDriverName(), renderer = RenderingServer.GetCurrentRenderingMethod(),
            gpu_name = RenderingServer.GetVideoAdapterName(), layout = _layout, archetypes = _archetypes, lods = _lods,
            segments = _segments, units = _units, checks = _checks,
            source_sha256 = Inputs.ToDictionary(name => name, name => Hash(ProjectSettings.GlobalizePath("res://" + name))),
            managed_fixture_assembly_sha256 = fixtureHash, managed_api_assembly_sha256 = apiHash,
            cases = _cases, phases = _phases, rebinds = _rebinds, native_diagnostics = _errors.Errors,
            compute_dispatches = _dispatches, gpu_buffer_readbacks = _bufferReadbacks, numeric_pixel_samples = _pixelSamples,
            caller_ownership_probes = _ownershipProbes, peak_owned_requested_buffer_bytes = _peakRequestedBytes,
            final_owned_rd_resource_count = _owned.Count, final_owned_requested_buffer_bytes = _owned.Sum(item => (long)item.bytes),
            image_files_written = 0, performance_window_count = 0, performance_qualified = false,
            not_qualified = new[] { "No FPS, full-frame or physical VRAM result", "No sustained lifetime plateau; bounded case retirement only",
                "No game AI, animation, combat or networking", "No motion vectors, E8, TAA/FSR2, shadows or auxiliary-pass proof",
                "No heterogeneous material/pipeline batching", "Expected RD call counts are source predictions, not GPU instrumentation",
                "Up to three distinct raster identity positions per phase plus whole-raster C1/C2 equivalence; full producer ID/command coverage is read back numerically",
                "Readbacks serialize this correctness subset and exclude performance conclusions" }
        };
        string path = Path.GetFullPath(Required("record")); Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllBytes(path, JsonSerializer.SerializeToUtf8Bytes(result, new JsonSerializerOptions { WriteIndented = true }));
        GD.Print("Multi-command numeric contract result " + path);
        OS.RemoveLogger(_errors); _errors.Dispose(); GetTree().Quit(passed ? 0 : 2);
    }
}

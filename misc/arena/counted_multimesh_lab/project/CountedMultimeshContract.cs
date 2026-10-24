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

// E9 procedural native-owner approach, with E10b GPU counters. No performance verdict.
public partial class CountedMultimeshContract : Node
{
    const uint Poison = 0xcdcdcdcd;
    const int Owners = 2, Capacity = 16, Prefix = 32, Columns = 5, Rows = 2, CellPixels = 16;
    static readonly string[] Inputs = { "CountedMultimeshLab.csproj", "project.godot", "Main.tscn",
        "CountedMultimeshContract.cs", "ContractErrorLogger.cs", "Consumer.gdshader", "Producer.glslinc" };
    static readonly RenderingDevice.StorageBufferUsage Indirect = (RenderingDevice.StorageBufferUsage)1;
    static readonly RenderingServer.MultimeshIndirectSubmissionMode CountedMode = (RenderingServer.MultimeshIndirectSubmissionMode)2;
    readonly Dictionary<string, string> _args = new();
    readonly List<(Rid rid, uint bytes)> _owned = new();
    readonly List<Rid> _multimeshes = new(), _instances = new();
    readonly List<ShaderMaterial> _materials = new();
    readonly List<object> _cases = new(), _phases = new(), _guards = new(), _ownership = new();
    readonly Dictionary<string, (string commands, string ids, string counter, string raster)> _fixed = new();
    readonly ContractErrorLogger _errors = new();
    RenderingDevice _rd = null!;
    Shader? _consumer;
    ArrayMesh? _mesh;
    BufferRD? _idWrapper;
    SubViewport? _view;
    ViewportTexture? _texture;
    World3D? _world;
    Rid _producer, _pipeline, _set, _ids, _commands, _counter;
    bool _supported, _shared, _counted;
    uint _limit;
    int _maximum, _stride, _spanBytes, _commandBytes;
    int _checks, _dispatches, _bufferReads, _pixels, _retired, _unavailable;
    long _peakBytes;
    string _failure = "", _lastRaster = "";

    public override async void _Ready()
    {
        OS.AddLogger(_errors);
        foreach (string argument in OS.GetCmdlineUserArgs()) {
            string[] pair = argument.TrimStart('-').Split('=', 2);
            _args[pair[0]] = pair.Length == 2 ? pair[1] : "true";
        }
        try {
            Check(BitConverter.IsLittleEndian, "Little-endian indexed commands required.");
            Check(Engine.GetVersionInfo()["hash"].AsString() == Required("expected-engine-hash"), "Native source differs.");
            Check(SdkVersion == Required("expected-sdk-version"), "Compiled SDK differs.");
            Check(RenderingServer.GetCurrentRenderingDriverName() == "vulkan" && RenderingServer.GetCurrentRenderingMethod() == "forward_plus", "Vulkan/Forward+ required.");
            _rd = RenderingServer.GetRenderingDevice() ?? throw new Exception("Global RD unavailable.");
            Engine.MaxFps = 120;
            BuildScene();
            await OnRender(() => {
                _supported = _rd.HasFeature(RenderingDevice.Features.DrawIndirectCount);
                _limit = checked((uint)_rd.LimitGet(RenderingDevice.Limit.MaxDrawIndirectCount));
                Check(_supported == (_limit > 0), "Enabled count capability/limit disagree.");
                using var source = new RDShaderSource { SourceCompute = Godot.FileAccess.GetFileAsString("res://Producer.glslinc") };
                using var spirv = _rd.ShaderCompileSpirVFromSource(source);
                Check(string.IsNullOrWhiteSpace(spirv.CompileErrorCompute), spirv.CompileErrorCompute);
                _producer = Own(_rd.ShaderCreateFromSpirV(spirv)); _pipeline = Own(_rd.ComputePipelineCreate(_producer));
            });
            if (_supported) {
                foreach (bool shared in new[] { false, true }) foreach (int stride in new[] { 20, 32 })
                foreach (int maximum in new[] { 1, (int)Math.Min(4u, _limit) }.Distinct()) foreach (bool counted in new[] { false, true }) {
                    _shared = shared; _stride = stride; _maximum = maximum; _counted = counted;
                    await CreateCase();
                    for (uint phase = 0; phase < (counted ? 7u : 5u); phase++) {
                        await OnRender(() => Produce(phase));
                        await Numeric(phase);
                    }
                    if (counted) { await RebindChecks(); await BorrowerChecks(); }
                    await CleanupCase();
                    Check(_owned.Count == 2, "Case retained RD resources.");
                }
            } else {
                _shared = false; _stride = 20; _maximum = 1; _counted = true;
                await CreateCase();
                await CleanupCase();
            }
        } catch (Exception error) { _failure = error.ToString(); GD.PushError(_failure); }
        finally {
            try {
                await CleanupCase();
                if (_rd != null) await OnRender(() => { FreeOwned(_pipeline); _pipeline = default; FreeOwned(_producer); _producer = default; });
                _texture?.Dispose(); _texture = null; _view?.Free(); _view = null;
                _world?.Dispose(); _world = null; _mesh?.Dispose(); _mesh = null; _consumer?.Dispose(); _consumer = null;
                await DrainFrames();
                Check(_owned.Count == 0 && _multimeshes.Count == 0 && _materials.Count == 0, "Final caller ledger not empty.");
            } catch (Exception error) { _failure += "\nCleanup: " + error; GD.PushError(error.ToString()); }
            try { WriteResult(); }
            catch (Exception error) {
                GD.PushError(error.ToString());
                try { File.WriteAllBytes(Path.GetFullPath(Required("record")), JsonSerializer.SerializeToUtf8Bytes(new {
                    contract = "counted-multimesh-numeric-contract-v1", passed = false, failure = error.ToString() })); }
                finally { OS.RemoveLogger(_errors); _errors.Dispose(); GetTree().Quit(2); }
            }
        }
    }

    string Required(string key) => _args.TryGetValue(key, out string? value) && value.Length > 0 ? value : throw new Exception("Missing --" + key);
    static string SdkVersion => typeof(CountedMultimeshContract).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>().SingleOrDefault(a => a.Key == "GodotSdkVersion")?.Value ?? "";
    static string BytesHash(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    static string Hash(string path) => BytesHash(File.ReadAllBytes(path));
    static byte[] Words(IEnumerable<uint> words) => MemoryMarshal.AsBytes(words.ToArray().AsSpan()).ToArray();
    void Check(bool condition, string message) { _checks++; if (!condition) throw new Exception(message); }
    async Task OnRender(Action action) {
        var done = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        RenderingServer.CallOnRenderThread(Callable.From(() => { try { action(); done.SetResult(); } catch (Exception error) { done.SetException(error); } }));
        await done.Task;
    }
    async Task Frames(int count) { for (int i = 0; i < count; i++) await ToSignal(RenderingServer.Singleton, RenderingServer.SignalName.FramePostDraw); }
    // Queue pacing only. Subsequent readbacks are explicit synchronization; this is not a fence API or a sustained lifetime test.
    async Task DrainFrames() { if (_rd == null) return; await Frames(checked((int)_rd.GetFrameDelay() + 4)); await OnRender(() => { }); }
    Rid Own(Rid rid, uint bytes = 0) {
        Check(rid.IsValid, "RD allocation failed."); _owned.Add((rid, bytes));
        _peakBytes = Math.Max(_peakBytes, _owned.Sum(item => (long)item.bytes)); return rid;
    }
    void FreeOwned(Rid rid) {
        if (!rid.IsValid) return;
        int index = _owned.FindIndex(item => item.rid == rid); Check(index >= 0, "Attempted to free unowned RID.");
        _rd.FreeRid(rid); _owned.RemoveAt(index);
    }
    BufferRD Wrap(Rid rid) {
        var wrapper = new BufferRD();
        try { Check(wrapper.AttachStorageBuffer(rid) == Error.Ok && wrapper.GetBufferRdRid() == rid, "Borrowed BufferRD identity differs."); return wrapper; }
        catch { wrapper.Dispose(); throw; }
    }
    int CommandOffset(int owner) => Prefix + owner * _spanBytes;
    static int CounterOffset(int owner) => 4 + owner * 8;

    void BuildScene()
    {
        _consumer = GD.Load<Shader>("res://Consumer.gdshader") ?? throw new Exception("Missing consumer shader.");
        // Two independently selectable quads: their side-probe coverage differs.
        _mesh = new ArrayMesh();
        using (var arrays = new Godot.Collections.Array()) {
            arrays.Resize((int)Godot.Mesh.ArrayType.Max);
            arrays[(int)Godot.Mesh.ArrayType.Vertex] = new[] {
                new Vector3(-.40f,-.40f,0), new Vector3(.40f,-.40f,0), new Vector3(.40f,.40f,0), new Vector3(-.40f,.40f,0),
                new Vector3(-.18f,-.40f,0), new Vector3(.18f,-.40f,0), new Vector3(.18f,.40f,0), new Vector3(-.18f,.40f,0) };
            arrays[(int)Godot.Mesh.ArrayType.Index] = new[] { 0,1,2,0,2,3, 0,1,2,0,2,3 };
            _mesh.AddSurfaceFromArrays(Godot.Mesh.PrimitiveType.Triangles, arrays);
        }
        _world = new World3D();
        _view = new SubViewport { Size = new Vector2I(Columns * CellPixels, Rows * CellPixels), World3D = _world,
            TransparentBg = false, RenderTargetUpdateMode = SubViewport.UpdateMode.Always };
        AddChild(_view);
        _view.AddChild(new Camera3D { Projection = Camera3D.ProjectionType.Orthogonal, Size = Rows,
            KeepAspect = Camera3D.KeepAspectEnum.Height, Near = .1f, Far = 10, Position = new Vector3(0,0,2), Current = true });
        _texture = _view.GetTexture();
    }

    async Task CreateCase()
    {
        _spanBytes = ((_maximum + 1) * _stride + 15) & ~15;
        _commandBytes = Prefix + Owners * _spanBytes;
        for (int owner = 0; owner < Owners; owner++) _materials.Add(new ShaderMaterial { Shader = _consumer });
        await OnRender(() => {
            _ids = Own(_rd.StorageBufferCreate(Owners * Capacity * 4, Words(Enumerable.Repeat(uint.MaxValue, Owners * Capacity))), Owners * Capacity * 4);
            uint[] initial = Enumerable.Repeat(Poison, _commandBytes / 4).ToArray();
            for (int owner = 0; owner < Owners; owner++) {
                if (_shared) initial[CounterOffset(owner) / 4] = 0;
                for (int record = 0; record <= _maximum; record++) {
                    uint[] legalEmpty = { 6, 0, (uint)(record % 2 * 6), (uint)(record % 2 * 4), (uint)(1 + record * 3) };
                    Array.Copy(legalEmpty, 0, initial, (CommandOffset(owner) + record * _stride) / 4, 5);
                }
            }
            _commands = Own(_rd.StorageBufferCreate((uint)_commandBytes, Words(initial), Indirect), (uint)_commandBytes);
            if (_shared) _counter = _commands;
            else {
                uint[] counters = Enumerable.Repeat(Poison, 8).ToArray(); counters[1] = 0; counters[3] = 0;
                _counter = Own(_rd.StorageBufferCreate(32, Words(counters), Indirect), 32);
            }
            _idWrapper = Wrap(_ids);
            using Variant ids = Variant.From(_idWrapper);
            for (int owner = 0; owner < Owners; owner++) {
                Rid mm = RenderingServer.MultimeshCreate(); _multimeshes.Add(mm);
                RenderingServer.MultimeshSetMesh(mm, _mesh!.GetRid());
                RenderingServer.MultimeshSetCustomAabb(mm, new Aabb(new Vector3(-Columns,-Rows,-.1f), new Vector3(Columns*2,Rows*2,.2f)));
                Error status = _counted ? AttachCount(mm, owner) : RenderingServer.MultimeshAttachSharedDrawCommandSpan(mm, Capacity,
                    _commands, CommandOffset(owner), _maximum, _stride, RenderingServer.MultimeshIndirectSubmissionMode.MultiDraw);
                if (!_supported) {
                    Check(owner == 0 && status == Error.Unavailable, "Unsupported native attach did not return Unavailable.");
                    Check(!RenderingServer.MultimeshIsUsingSharedBuffers(mm) && !RenderingServer.MultimeshGetSharedDrawCountBufferRdRid(mm).IsValid &&
                        RenderingServer.MultimeshGetSharedDrawCountByteOffset(mm) == 0, "Unsupported attach mutated view.");
                    _unavailable++; return;
                }
                Check(status == Error.Ok, "Native attach failed: " + status + "; paired control requires indirect first-instance and ordinary MDI.");
                CheckState(mm, owner, _counted);
                ShaderMaterial material = _materials[owner];
                material.SetShaderParameter("visible_ids", ids); material.SetShaderParameter("id_base", owner * Capacity);
                material.SetShaderParameter("columns", Columns); material.SetShaderParameter("rows", Rows);
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
                Rid[] buffers = { _ids, _commands, _counter };
                for (int i = 0; i < buffers.Length; i++) {
                    var uniform = new RDUniform { UniformType = RenderingDevice.UniformType.StorageBuffer, Binding = i };
                    uniform.AddId(buffers[i]); uniforms.Add(uniform); items.Add(uniform);
                }
                _set = Own(_rd.UniformSetCreate(uniforms, _producer, 0));
            } finally { foreach (var item in items) item.Dispose(); }
            _cases.Add(new { shared = _shared, stride = _stride, maximum = _maximum, counted = _counted,
                native_owners = Owners, draw_slot_capacity = Capacity, command_offset = Prefix, counter_offsets = new[] { 4,12 },
                span_bytes = _spanBytes, command_bytes = _commandBytes, counter_bytes = _shared ? 0 : 32,
                id_bytes = Owners * Capacity * 4, valid_nonempty_tail_record = true, phases = _counted ? 7 : 5,
                fixed_submission = "MULTI_DRAW", native_transform_bank = false });
        });
    }

    Error AttachCount(Rid mm, int owner) => RenderingServer.MultimeshAttachSharedDrawCommandSpanCount(mm, Capacity, _commands,
        CommandOffset(owner), _counter, CounterOffset(owner), _maximum, _stride);
    void CheckState(Rid mm, int owner, bool counted) {
        Check(RenderingServer.MultimeshGetCommandBufferRdRid(mm) == _commands && RenderingServer.MultimeshGetCommandByteOffset(mm) == CommandOffset(owner) &&
            RenderingServer.MultimeshGetSharedDrawCommandCount(mm) == _maximum && RenderingServer.MultimeshGetSharedDrawCommandStride(mm) == _stride &&
            RenderingServer.MultimeshGetSharedDrawSubmissionMode(mm) == (counted ? CountedMode : RenderingServer.MultimeshIndirectSubmissionMode.MultiDraw) &&
            RenderingServer.MultimeshGetSharedDrawCountBufferRdRid(mm) == (counted ? _counter : default) &&
            RenderingServer.MultimeshGetSharedDrawCountByteOffset(mm) == (counted ? CounterOffset(owner) : 0) &&
            RenderingServer.MultimeshGetInstanceCount(mm) == Capacity && RenderingServer.MultimeshIsShaderDriven(mm) &&
            !RenderingServer.MultimeshGetBufferRdRid(mm).IsValid && !RenderingServer.MultimeshUsesShaderMotionVectors(mm), "View metadata differs.");
    }
    void Produce(uint phase) {
        for (uint stage = 0; stage < 3; stage++) {
            long list = _rd.ComputeListBegin(); _rd.ComputeListBindComputePipeline(list, _pipeline); _rd.ComputeListBindUniformSet(list, _set, 0);
            byte[] push = Words(new uint[] { stage, phase, (uint)_maximum, (uint)_stride / 4, (uint)_spanBytes / 4, 0,0,0 });
            _rd.ComputeListSetPushConstant(list, push, 32); _rd.ComputeListDispatch(list, Owners, 1, 1); _rd.ComputeListEnd(); _dispatches++;
        }
    }
    int Admitted(uint phase) => phase == 1 || phase == 5 ? 0 : phase == 3 || phase == 6 ? 1 : phase == 2 ? Math.Min(2,_maximum) : _maximum;
    int Source(int record, int count, uint phase) => record >= count ? record : phase == 4 ? _maximum - 1 - record :
        phase == 3 || phase == 6 || (phase == 2 && record == 1) ? _maximum - 1 : record;

    async Task Numeric(uint phase)
    {
        await Frames(4);
        byte[] commands = Array.Empty<byte>(), ids = Array.Empty<byte>(), counters = Array.Empty<byte>();
        await OnRender(() => {
            commands = _rd.BufferGetData(_commands); ids = _rd.BufferGetData(_ids); _bufferReads += 2;
            counters = _shared ? commands : _rd.BufferGetData(_counter); if (!_shared) _bufferReads++;
        });
        int count = Admitted(phase);
        uint[] expectedCommands = Enumerable.Repeat(Poison, _commandBytes / 4).ToArray();
        uint[] expectedIds = Enumerable.Repeat(uint.MaxValue, Owners * Capacity).ToArray();
        uint[] expectedCounters = _shared ? expectedCommands : Enumerable.Repeat(Poison, 8).ToArray();
        var admitted = new HashSet<int>();
        for (int owner = 0; owner < Owners; owner++) {
            expectedCounters[CounterOffset(owner) / 4] = (uint)count;
            for (int source = 0; source <= _maximum; source++) expectedIds[owner * Capacity + 1 + source * 3] = (uint)(owner * Columns + source);
            for (int record = 0; record <= _maximum; record++) {
                int source = Source(record, count, phase), first = 1 + source * 3;
                bool nonempty = record < count || record == _maximum || phase >= 5;
                Check((ulong)first + (nonempty ? 1u : 0u) <= Capacity, "Producer exceeded draw-slot capacity.");
                uint[] words = { 6, nonempty ? 1u : 0u, (uint)(source % 2 * 6), (uint)(source % 2 * 4), (uint)first };
                Array.Copy(words, 0, expectedCommands, (CommandOffset(owner) + record * _stride) / 4, 5);
                if (record < count) Check(admitted.Add(owner * Columns + source), "Duplicate admitted identity.");
            }
        }
        Check(commands.SequenceEqual(Words(expectedCommands)), "Whole command/prefix/padding/tail bytes differ.");
        Check(ids.SequenceEqual(Words(expectedIds)), "Whole ID bank/guards differ.");
        Check(counters.SequenceEqual(Words(expectedCounters)), "GPU counter bytes differ after consumption.");
        using Image image = _texture!.GetImage();
        Check(!image.IsEmpty() && image.GetWidth() == Columns * CellPixels && image.GetHeight() == Rows * CellPixels, "Numeric viewport differs.");
        var samples = new List<object>();
        for (int id = 0; id < Owners * Columns; id++) for (int side = 0; side < 2; side++) {
            int x = id % Columns * CellPixels + 8 + side * 4, y = image.GetHeight() - (id / Columns * CellPixels + 8);
            bool visible = admitted.Contains(id) && (side == 0 || id % Columns % 2 == 0);
            int bits = visible ? 1 + id % 6 : 0;
            Color color = image.GetPixel(x,y); float[] actual = { color.R,color.G,color.B };
            int[] expected = Enumerable.Range(0,3).Select(channel => (bits & (1 << channel)) != 0 ? 255 : 0).ToArray();
            int[] rgb = actual.Select(value => (int)MathF.Round(value * 255)).ToArray();
            for (int channel = 0; channel < 3; channel++) Check(Math.Abs(rgb[channel] - expected[channel]) <= 4, "Identity/geometry raster probe differs.");
            samples.Add(new { id, side, x,y, expected_rgb = expected, actual_rgb = rgb }); _pixels++;
        }
        var hashes = (BytesHash(commands),BytesHash(ids),BytesHash(counters),BytesHash(image.GetData()));
        _lastRaster = hashes.Item4;
        string pair = _shared + ":" + _stride + ":" + _maximum + ":" + phase;
        if (!_counted) _fixed.Add(pair,hashes);
        if (_counted && phase < 5) Check(_fixed.TryGetValue(pair,out var reference) && hashes == reference, "Fixed/count producer data or numeric raster differs.");
        _phases.Add(new { shared = _shared, stride = _stride, maximum = _maximum, counted = _counted, phase,
            published_counts = new[] { count,count }, admitted_ids = admitted.OrderBy(value => value).ToArray(),
            command_sha256 = hashes.Item1, ids_sha256 = hashes.Item2, counter_sha256 = hashes.Item3, numeric_raster_sha256 = hashes.Item4,
            full_command_and_tail_verified = true, full_ids_verified = true, counter_verified_after_draw = true,
            paired_fixed_equality = _counted && phase < 5, excluded_nonempty_tail_probe = phase >= 5, samples });
    }

    async Task RebindChecks()
    {
        await OnRender(() => {
            Rid mm = _multimeshes[0]; var rejected = new List<string>();
            void Reject(string name, long capacity = Capacity, Rid commands = default, long offset = Prefix,
                Rid counter = default, long counterOffset = 4, long maximum = -999, long stride = -999) {
                Error error = RenderingServer.MultimeshAttachSharedDrawCommandSpanCount(mm,capacity,commands.IsValid ? commands : _commands,
                    offset,counter.IsValid ? counter : _counter,counterOffset,maximum == -999 ? _maximum : maximum,stride == -999 ? _stride : stride);
                Check(error == Error.InvalidParameter, "Invalid counted attach accepted: " + name + ":" + error);
                CheckState(mm,0,true); rejected.Add(name);
            }
            Reject("negative_capacity",capacity:-1); Reject("capacity_above_int32",capacity:(long)int.MaxValue+1);
            Reject("negative_command_offset",offset:-4); Reject("command_offset_above_int32",offset:(long)int.MaxValue+1);
            Reject("unaligned_command_offset",offset:33); Reject("negative_count_offset",counterOffset:-4);
            Reject("count_offset_above_int32",counterOffset:(long)int.MaxValue+1); Reject("unaligned_count_offset",counterOffset:5);
            Reject("zero_maximum",maximum:0); Reject("maximum_above_bound",maximum:1025);
            Reject("zero_stride",stride:0); Reject("short_stride",stride:16); Reject("unaligned_stride",stride:21); Reject("stride_above_int32",stride:(long)int.MaxValue+1);
            Reject("short_command_range",offset:_commandBytes-16); Reject("short_count_range",counterOffset:_shared?_commandBytes:32);
            Reject("commands_without_indirect",commands:_ids); Reject("counter_without_indirect",counter:_ids);
            if (_shared) Reject("overlapping_shared_counter",counterOffset:Prefix);
            Error invalidRid = RenderingServer.MultimeshAttachSharedDrawCommandSpanCount(mm,Capacity,_commands,Prefix,default,4,_maximum,_stride);
            Check(invalidRid == Error.InvalidParameter,"Invalid counter RID accepted."); CheckState(mm,0,true); rejected.Add("invalid_counter_rid");
            Check(RenderingServer.MultimeshAttachSharedDrawCommandSpanCount(mm,Capacity,default,Prefix,_counter,4,_maximum,_stride) == Error.InvalidParameter,"Invalid command RID accepted.");
            CheckState(mm,0,true); rejected.Add("invalid_command_rid");
            Check(RenderingServer.MultimeshAttachSharedDrawCommandSpanCount(default,Capacity,_commands,Prefix,_counter,4,_maximum,_stride) == Error.InvalidParameter,"Invalid MultiMesh RID accepted.");
            CheckState(mm,0,true); rejected.Add("invalid_multimesh_rid");
            // Dynamic persistence is intentionally not exposed as an enum name. Native pinned flag is 1<<2.
            Rid dynamic = Own(_rd.StorageBufferCreate((uint)_commandBytes,Array.Empty<byte>(),Indirect,(RenderingDevice.BufferCreationBits)4),(uint)_commandBytes);
            try { Reject("persistent_commands",commands:dynamic); Reject("persistent_counter",counter:dynamic); }
            finally { FreeOwned(dynamic); }
            Check(RenderingServer.MultimeshAttachSharedDrawCommandSpan(mm,Capacity,_commands,Prefix,_maximum,_stride,CountedMode) == Error.InvalidParameter,
                "Old span method accepted counted enum without counter."); CheckState(mm,0,true); rejected.Add("counted_mode_through_old_api");
            Check(RenderingServer.MultimeshAttachSharedDrawCommandSpan(mm,Capacity,_commands,Prefix,_maximum,_stride,RenderingServer.MultimeshIndirectSubmissionMode.MultiDraw) == Error.Ok,"Old span rebind failed.");
            CheckState(mm,0,false);
            Check(RenderingServer.MultimeshAttachSharedDrawBuffer(mm,Capacity,_commands,Prefix) == Error.Ok,"Old single rebind failed.");
            Check(RenderingServer.MultimeshGetSharedDrawCommandCount(mm) == 1 && RenderingServer.MultimeshGetSharedDrawCommandStride(mm) == 0 &&
                RenderingServer.MultimeshGetSharedDrawSubmissionMode(mm) == RenderingServer.MultimeshIndirectSubmissionMode.Loop &&
                !RenderingServer.MultimeshGetSharedDrawCountBufferRdRid(mm).IsValid && RenderingServer.MultimeshGetSharedDrawCountByteOffset(mm) == 0 &&
                !RenderingServer.MultimeshUsesShaderMotionVectors(mm) && !RenderingServer.MultimeshGetBufferRdRid(mm).IsValid &&
                RenderingServer.MultimeshGetInstanceCount(mm) == Capacity,"Old single API retained counted mode or changed independent ownership/history/capacity.");
            Check(AttachCount(mm,0) == Error.Ok,"Counted rebind restore failed."); CheckState(mm,0,true);
            _guards.Add(new { shared = _shared,stride = _stride,maximum = _maximum,rejected,
                transactional = true, old_span_resets = true, old_single_resets = true, restored_counted = true });
        });
    }

    async Task BorrowerChecks()
    {
        await OnRender(() => {
            Rid borrower = RenderingServer.MultimeshCreate();
            try {
                RenderingServer.MultimeshSetMesh(borrower,_mesh!.GetRid());
                RenderingServer.MultimeshSetCustomAabb(borrower,new Aabb(new Vector3(-Columns,-Rows,-.1f),new Vector3(Columns*2,Rows*2,.2f)));
                Check(AttachCount(borrower,0) == Error.Ok,"Second borrower attach failed.");
                RenderingServer.MultimeshDetachSharedBuffers(borrower);
                Check(!RenderingServer.MultimeshGetSharedDrawCountBufferRdRid(borrower).IsValid && RenderingServer.MultimeshGetSharedDrawCountByteOffset(borrower) == 0,"Detach retained counted metadata.");
                CheckState(_multimeshes[0],0,true);
                Check(AttachCount(borrower,0) == Error.Ok,"Second borrower reattach failed.");
            } finally { RenderingServer.FreeRid(borrower); }
            CheckState(_multimeshes[0],0,true); CheckState(_multimeshes[1],1,true);
            foreach (Rid bank in new[] { _commands,_counter }.Distinct()) { using var borrowed = Wrap(bank); }
        });
        // Render the surviving owners again; phase6 has deliberately nonempty excluded commands.
        await Frames(4);
        using Image image = _texture!.GetImage();
        Check(!image.IsEmpty() && BytesHash(image.GetData()) == _lastRaster,"Surviving native owner raster differs after borrower destruction.");
        _ownership.Add(new { shared = _shared,stride = _stride,maximum = _maximum,second_borrower_detach = true,
            second_borrower_destroy = true,other_view_preserved = true,caller_banks_survive = true,
            survivor_raster_sha256 = BytesHash(image.GetData()) });
    }

    async Task CleanupCase()
    {
        if (_rd == null || !_commands.IsValid) return;
        await OnRender(() => {
            foreach (Rid instance in _instances) RenderingServer.InstanceSetVisible(instance,false);
            foreach (var material in _materials) material.SetShaderParameter("visible_ids",default(Variant));
        });
        await DrainFrames();
        await OnRender(() => {
            foreach (Rid instance in _instances) RenderingServer.FreeRid(instance); _instances.Clear();
            foreach (Rid mm in _multimeshes) {
                RenderingServer.MultimeshDetachSharedBuffers(mm);
                Check(!RenderingServer.MultimeshGetSharedDrawCountBufferRdRid(mm).IsValid && RenderingServer.MultimeshGetSharedDrawCountByteOffset(mm) == 0 &&
                    RenderingServer.MultimeshGetSharedDrawCommandCount(mm) == 0,"Detach retained counted metadata.");
                RenderingServer.FreeRid(mm);
            }
            _multimeshes.Clear(); FreeOwned(_set); _set = default;
            // Explicit readback completion for the caller-owned GPU-produced data before freeing storage.
            _rd.BufferGetData(_commands); _bufferReads++;
            if (!_shared) { _rd.BufferGetData(_counter); _bufferReads++; }
        });
        _idWrapper?.Dispose(); _idWrapper = null;
        foreach (var material in _materials) material.Dispose(); _materials.Clear();
        await DrainFrames();
        await OnRender(() => {
            if (!_shared) FreeOwned(_counter); _counter = default;
            FreeOwned(_commands); _commands = default; FreeOwned(_ids); _ids = default;
        });
        _retired++;
    }

    string RuntimeAssemblyHash(Assembly assembly,string fallback) {
        string path = string.IsNullOrEmpty(assembly.Location) ? fallback : assembly.Location;
        using var stream = File.OpenRead(path); using var pe = new PEReader(stream);
        MetadataReader metadata = pe.GetMetadataReader();
        Check(metadata.GetGuid(metadata.GetModuleDefinition().Mvid) == assembly.ManifestModule.ModuleVersionId,"Loaded managed module differs from file.");
        return Hash(path);
    }
    void WriteResult()
    {
        string fixture = ProjectSettings.GlobalizePath("res://.godot/mono/temp/bin/Debug/CountedMultimeshLab.dll");
        string api = Path.Combine(Path.GetDirectoryName(OS.GetExecutablePath())!,"GodotSharp/Api/Debug/GodotSharp.dll");
        bool passed = _failure.Length == 0 && _errors.Errors.Length == 0 && _owned.Count == 0 && (!_supported || _cases.Count > 0);
        var result = new {
            contract = "counted-multimesh-numeric-contract-v1",passed,failure = _failure,
            engine_hash = Engine.GetVersionInfo()["hash"].AsString(),sdk_version = SdkVersion,
            driver = RenderingServer.GetCurrentRenderingDriverName(),renderer = RenderingServer.GetCurrentRenderingMethod(),gpu_name = RenderingServer.GetVideoAdapterName(),
            supports_draw_indirect_count = _supported,max_draw_indirect_count = _limit,native_count_qualified = passed && _supported,
            source_sha256 = Inputs.ToDictionary(name => name,name => Hash(ProjectSettings.GlobalizePath("res://"+name))),
            managed_fixture_assembly_sha256 = RuntimeAssemblyHash(typeof(CountedMultimeshContract).Assembly,fixture),
            managed_api_assembly_sha256 = RuntimeAssemblyHash(typeof(Node).Assembly,api),
            cases = _cases,phases = _phases,guards = _guards,ownership = _ownership,native_diagnostics = _errors.Errors,
            checks = _checks,producer_dispatches = _dispatches,gpu_buffer_readbacks = _bufferReads,numeric_pixel_samples = _pixels,
            retired_cases = _retired,unavailable_rejections = _unavailable,peak_owned_requested_bytes = _peakBytes,
            final_owned_rd_resource_count = _owned.Count,final_owned_requested_bytes = _owned.Sum(item => (long)item.bytes),
            image_files_written = 0,performance_window_count = 0,performance_qualified = false,
            not_qualified = new[] { "No FPS/full-frame or physical VRAM result; readbacks serialize this correctness subset",
                "No sustained lifetime plateau or general GPU fence contract; bounded caller ledger only",
                "No renderer alias/subpass/cache-recovery/excluded-pass probe", "No E8/history, shadows, transparency, XR or secondary-view qualification",
                "No wrong-device RID, transform-backed legacy-reset or ordinary-allocation live probe",
                "Paired fixed-dense control additionally requires ordinary MDI and native indirect first-instance support",
                "No game AI/animation/combat/networking or production R14 integration", "Expected native call counts are not GPU instrumentation" }
        };
        string path = Path.GetFullPath(Required("record")); Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllBytes(path,JsonSerializer.SerializeToUtf8Bytes(result,new JsonSerializerOptions { WriteIndented = true }));
        GD.Print("Counted MultiMesh numeric contract result "+path);
        OS.RemoveLogger(_errors); _errors.Dispose(); GetTree().Quit(passed ? 0 : 2);
    }
}

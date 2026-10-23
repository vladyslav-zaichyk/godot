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

// Procedural native-stream correctness only. No BufferRD, screenshots or performance window.
public partial class MeshRdContract : Node
{
    const int Width = 128, Height = 64;
    static readonly string[] Inputs = { "MeshRdLab.csproj", "project.godot", "Main.tscn", "MeshRdContract.cs",
        "MeshRdErrorLogger.cs", "Producer.glslinc", "Probe.glslinc", "Consumer.gdshader" };
    static readonly string[] StreamNames = { "vertex", "attribute", "skin", "index" };
    const string MissingStorage = "Vertex buffer supplied (binding: 0) was not created with storage flag.";
    const string InvalidStorage = "Storage buffer supplied (binding: 0) is invalid.";
    readonly Dictionary<string, string> _args = new();
    readonly List<Rid> _owned = new();
    readonly List<object> _streams = new(), _phases = new(), _pixels = new(), _retirements = new();
    readonly MeshRdErrorLogger _errors = new();
    RenderingDevice _rd = null!;
    ArrayMesh? _mesh, _unflagged, _probe;
    MeshInstance3D? _node;
    ShaderMaterial? _material;
    Shader? _shader;
    SubViewport? _view;
    ViewportTexture? _texture;
    World3D? _world;
    Rid _producerShader, _producerPipeline, _producerSet, _probeShader;
    Rid _vertex, _index;
    int _vertexStride, _vertexOffset, _indexStride;
    int _checks, _dispatches, _imageReadbacks, _bufferReadbacks, _pixelSamples, _positiveBindings;
    string _failure = "";

    public override async void _Ready()
    {
        OS.AddLogger(_errors);
        foreach (string argument in OS.GetCmdlineUserArgs()) {
            string[] pair = argument.TrimStart('-').Split('=', 2);
            _args[pair[0]] = pair.Length == 2 ? pair[1] : "true";
        }
        try {
            Check(BitConverter.IsLittleEndian, "Little-endian native streams required.");
            Check(Engine.GetVersionInfo()["hash"].AsString() == Required("expected-engine-hash"), "Native source differs.");
            Check(SdkVersion == Required("expected-sdk-version"), "Compiled SDK differs.");
            Check(RenderingServer.GetCurrentRenderingDriverName() == "vulkan" && RenderingServer.GetCurrentRenderingMethod() == "forward_plus", "Vulkan/Forward+ required.");
            _rd = RenderingServer.GetRenderingDevice() ?? throw new Exception("Global RD unavailable.");
            Engine.MaxFps = 120;
            BuildScene();
            // Materialize the ordinary mesh's native vertex/index arrays before writable SSBO binding.
            await Frames(6);
            await OnRender(() => {
                _producerShader = Compile("Producer.glslinc"); _probeShader = Compile("Probe.glslinc");
                _producerPipeline = Own(_rd.ComputePipelineCreate(_producerShader));
                CheckGettersAndLayout();
                _producerSet = Set(_producerShader, _vertex, _index);
                _positiveBindings++;
                Rid[] unflagged = GetStreams(_unflagged!);
                Check(unflagged[0].IsValid && unflagged[3].IsValid && !unflagged[1].IsValid && !unflagged[2].IsValid, "Unflagged getter presence differs.");
                _streams.Add(new { test = "unflagged_presence", storage_flag = false, rids = unflagged.Select(r => r.Id).ToArray() });
                ExpectRejected("unflagged_vertex", MissingStorage, unflagged[0]);
                Rid[] probe = GetStreams(_probe!);
                Check(probe.All(rid => rid.IsValid) && probe.Distinct().Count() == 4, "Four populated native streams required.");
                byte[][] encoded = ExpectedProbeStreams();
                for (int i = 0; i < probe.Length; i++) {
                    Rid set = Set(_probeShader, probe[i]); _positiveBindings++;
                    byte[] actual = Read(probe[i]);
                    byte[] expected = encoded[i];
                    Check(actual.SequenceEqual(expected), "Native " + StreamNames[i] + " bytes differ from packed surface.");
                    _streams.Add(new { test = "populated_stream", stream = StreamNames[i], rid = probe[i].Id,
                        bytes = actual.Length, sha256 = BytesHash(actual), storage_flag = true });
                    FreeOwned(set);
                }
            });
            for (uint phase = 0; phase < 3; phase++) {
                await OnRender(() => Produce(phase));
                await Numeric(checked((int)phase));
            }
            Rid[] retired = Array.Empty<Rid>();
            await OnRender(() => retired = GetStreams(_probe!));
            _probe!.Dispose(); _probe = null;
            await DrainFrames();
            await OnRender(() => {
                for (int i = 0; i < retired.Length; i++) {
                    ExpectRejected("freed_" + StreamNames[i], InvalidStorage, retired[i]);
                    _retirements.Add(new { stream = StreamNames[i], borrowed_rid = retired[i].Id, mesh_disposed = true,
                        consumer_uniform_set_released_before_mesh = true, stale_storage_bind_rejected = true });
                }
            });
            Check(_dispatches == 3 && _imageReadbacks == 3 && _bufferReadbacks == 10 && _pixelSamples == 12 &&
                _positiveBindings == 5 && _retirements.Count == 4 && _errors.Expected.Length == 5, "Incomplete fixed contract.");
        } catch (Exception error) {
            _failure = error.ToString(); GD.PushError(_failure);
        } finally {
            try { await Cleanup(); }
            catch (Exception error) { _failure += "\nCleanup: " + error; GD.PushError(error.ToString()); }
            try { WriteResult(); }
            catch (Exception error) {
                GD.PushError("Contract reporting failed: " + error);
                try {
                    string path = Path.GetFullPath(Required("record")); Directory.CreateDirectory(Path.GetDirectoryName(path)!);
                    File.WriteAllBytes(path, JsonSerializer.SerializeToUtf8Bytes(new { contract = "mesh-rd-numeric-contract-v1", passed = false, failure = error.ToString() }));
                } finally { OS.RemoveLogger(_errors); _errors.Dispose(); GetTree().Quit(2); }
            }
        }
    }

    string Required(string key) => _args.TryGetValue(key, out string? value) && value.Length > 0 ? value : throw new Exception("Missing --" + key);
    static string SdkVersion => typeof(MeshRdContract).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>().SingleOrDefault(a => a.Key == "GodotSdkVersion")?.Value ?? "";
    static string Hash(string path) => BytesHash(File.ReadAllBytes(path));
    static string BytesHash(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    void Check(bool condition, string message) { _checks++; if (!condition) throw new Exception(message); }
    async Task OnRender(Action action)
    {
        var done = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        RenderingServer.CallOnRenderThread(Callable.From(() => { try { action(); done.SetResult(); } catch (Exception error) { done.SetException(error); } }));
        await done.Task;
    }
    async Task Frames(int count) { for (int i = 0; i < count; i++) await ToSignal(RenderingServer.Singleton, RenderingServer.SignalName.FramePostDraw); }
    async Task DrainFrames() { if (_rd == null) return; await Frames(checked((int)_rd.GetFrameDelay() + 4)); await OnRender(() => { }); }
    Rid Own(Rid rid) { Check(rid.IsValid, "RD allocation failed."); _owned.Add(rid); return rid; }
    void FreeOwned(Rid rid)
    {
        if (!rid.IsValid) return;
        Check(_owned.Remove(rid), "Attempted to free an engine-owned borrowed RID."); _rd.FreeRid(rid);
    }
    Rid Compile(string file)
    {
        using var source = new RDShaderSource { SourceCompute = Godot.FileAccess.GetFileAsString("res://" + file) };
        using var spirv = _rd.ShaderCompileSpirVFromSource(source);
        Check(string.IsNullOrWhiteSpace(spirv.CompileErrorCompute), spirv.CompileErrorCompute);
        return Own(_rd.ShaderCreateFromSpirV(spirv));
    }
    Rid MakeSet(Rid shader, params Rid[] buffers)
    {
        var uniforms = new Godot.Collections.Array<RDUniform>();
        using var uniformsLifetime = (Godot.Collections.Array)uniforms;
        var items = new List<RDUniform>();
        try {
            for (int i = 0; i < buffers.Length; i++) {
                var uniform = new RDUniform { UniformType = RenderingDevice.UniformType.StorageBuffer, Binding = i };
                uniform.AddId(buffers[i]); items.Add(uniform); uniforms.Add(uniform);
            }
            return _rd.UniformSetCreate(uniforms, shader, 0);
        } finally { foreach (RDUniform item in items) item.Dispose(); }
    }
    Rid Set(Rid shader, params Rid[] buffers) => Own(MakeSet(shader, buffers));
    byte[] Read(Rid rid) { _bufferReadbacks++; return _rd.BufferGetData(rid); }
    void ExpectRejected(string test, string rationale, Rid rid)
    {
        _errors.Begin(test, rationale); GD.Print("MESH_RD_EXPECTED_GUARD_BEGIN=" + test);
        bool matched;
        try {
            Rid result = MakeSet(_probeShader, rid);
            if (result.IsValid) FreeOwned(Own(result));
            Check(!result.IsValid, "Invalid native storage binding accepted: " + test);
        }
        finally { matched = _errors.End(test); GD.Print("MESH_RD_EXPECTED_GUARD_END=" + test); }
        Check(matched, "Expected exactly one precise native diagnostic: " + test);
    }
    static Rid[] GetStreams(ArrayMesh mesh) => new[] {
        RenderingServer.MeshSurfaceGetVertexBufferRdRid(mesh.GetRid(), 0),
        RenderingServer.MeshSurfaceGetAttributeBufferRdRid(mesh.GetRid(), 0),
        RenderingServer.MeshSurfaceGetSkinBufferRdRid(mesh.GetRid(), 0),
        RenderingServer.MeshSurfaceGetIndexBufferRdRid(mesh.GetRid(), 0) };

    static ArrayMesh MakeMesh(bool storage, bool allStreams)
    {
        var mesh = new ArrayMesh();
        try {
            using var arrays = new Godot.Collections.Array(); arrays.Resize((int)Mesh.ArrayType.Max);
            arrays[(int)Mesh.ArrayType.Vertex] = new[] { new Vector3(-0.7f, -0.7f, 0), new Vector3(0.7f, -0.7f, 0), new Vector3(0.7f, 0.7f, 0), new Vector3(-0.7f, 0.7f, 0) };
            arrays[(int)Mesh.ArrayType.Index] = new[] { 0, 1, 2, 0, 2, 3 };
            if (allStreams) {
                arrays[(int)Mesh.ArrayType.Color] = new[] { new Color(1, 0, 0, 1), new Color(0, 1, 0, 1), new Color(0, 0, 1, 1), new Color(1, 1, 1, 1) };
                var bones = new int[16]; var weights = new float[16];
                for (int i = 0; i < 4; i++) weights[i * 4] = 1;
                arrays[(int)Mesh.ArrayType.Bones] = bones; arrays[(int)Mesh.ArrayType.Weights] = weights;
            }
            mesh.AddSurfaceFromArrays(Mesh.PrimitiveType.Triangles, arrays, flags: storage ? Mesh.ArrayFormat.FlagUseStorageBuffer : 0);
            mesh.CustomAabb = new Aabb(new Vector3(-3, -1, -0.1f), new Vector3(6, 2, 0.2f));
            return mesh;
        } catch { mesh.Dispose(); throw; }
    }
    void BuildScene()
    {
        _mesh = MakeMesh(true, false); _unflagged = MakeMesh(false, false); _probe = MakeMesh(true, true);
        _shader = GD.Load<Shader>("res://Consumer.gdshader"); _material = new ShaderMaterial { Shader = _shader };
        _world = new World3D();
        _view = new SubViewport { Size = new Vector2I(Width, Height), World3D = _world, TransparentBg = false, RenderTargetUpdateMode = SubViewport.UpdateMode.Always };
        AddChild(_view);
        _view.AddChild(new Camera3D { Projection = Camera3D.ProjectionType.Orthogonal, Size = 4,
            KeepAspect = Camera3D.KeepAspectEnum.Height, Near = 0.1f, Far = 10, Position = new Vector3(0, 0, 2), Current = true });
        _node = new MeshInstance3D { Mesh = _mesh, MaterialOverride = _material, CastShadow = GeometryInstance3D.ShadowCastingSetting.Off };
        _view.AddChild(_node); _texture = _view.GetTexture();
    }
    byte[][] ExpectedProbeStreams()
    {
        var format = (RenderingServer.ArrayFormat)(ulong)_probe!.SurfaceGetFormat(0);
        int vertexStride = checked((int)RenderingServer.MeshSurfaceGetFormatVertexStride(format, 4));
        int colorStride = checked((int)RenderingServer.MeshSurfaceGetFormatAttributeStride(format, 4));
        int skinStride = checked((int)RenderingServer.MeshSurfaceGetFormatSkinStride(format, 4));
        int indexStride = checked((int)RenderingServer.MeshSurfaceGetFormatIndexStride(format, 4));
        int Offset(Mesh.ArrayType array) => checked((int)RenderingServer.MeshSurfaceGetFormatOffset(format, 4, (int)array));
        Check(vertexStride == 12 && colorStride == 4 && skinStride == 16 && indexStride == 2 &&
            Offset(Mesh.ArrayType.Vertex) == 0 && Offset(Mesh.ArrayType.Color) == 0 &&
            Offset(Mesh.ArrayType.Bones) == 0 && Offset(Mesh.ArrayType.Weights) == 8 &&
            RenderingServer.MeshSurfaceGetFormatNormalTangentStride(format, 4) == 0, "Probe native packing differs.");
        float[] positions = { -0.7f, -0.7f, 0, 0.7f, -0.7f, 0, 0.7f, 0.7f, 0, -0.7f, 0.7f, 0 };
        byte[] colors = { 255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 255, 255, 255, 255 };
        byte[] skin = new byte[skinStride * 4];
        for (int vertex = 0; vertex < 4; vertex++) {
            // Four uint16 bone IDs are zero; first of four UNORM16 weights is one.
            int weight = vertex * skinStride + Offset(Mesh.ArrayType.Weights);
            skin[weight] = skin[weight + 1] = 255;
        }
        ushort[] indices = { 0, 1, 2, 0, 2, 3 };
        return new[] { MemoryMarshal.AsBytes(positions.AsSpan()).ToArray(), colors, skin, MemoryMarshal.AsBytes(indices.AsSpan()).ToArray() };
    }
    void CheckGettersAndLayout()
    {
        Rid[] streams = GetStreams(_mesh!);
        Check(streams[0].IsValid && streams[3].IsValid && !streams[1].IsValid && !streams[2].IsValid, "Absent native streams must return invalid RID.");
        Check(GetStreams(_mesh!).SequenceEqual(streams), "Getter identity changed without a mesh change.");
        _vertex = streams[0]; _index = streams[3];
        var format = (RenderingServer.ArrayFormat)(ulong)_mesh!.SurfaceGetFormat(0);
        _vertexStride = checked((int)RenderingServer.MeshSurfaceGetFormatVertexStride(format, 4));
        _vertexOffset = checked((int)RenderingServer.MeshSurfaceGetFormatOffset(format, 4, (int)Mesh.ArrayType.Vertex));
        _indexStride = checked((int)RenderingServer.MeshSurfaceGetFormatIndexStride(format, 4));
        Check(((ulong)format & (ulong)Mesh.ArrayFormat.FlagUseStorageBuffer) != 0 && _vertexStride == 12 && _vertexOffset == 0 && _indexStride == 2,
            "The narrow float3 / uint16 fixture ABI differs from queried layout.");
        Check(RenderingServer.MeshSurfaceGetFormatNormalTangentStride(format, 4) == 0 &&
            RenderingServer.MeshSurfaceGetFormatAttributeStride(format, 4) == 0 && RenderingServer.MeshSurfaceGetFormatSkinStride(format, 4) == 0, "Unexpected extra main-mesh streams.");
        _streams.Add(new { test = "main_presence", storage_flag = true, rids = streams.Select(r => r.Id).ToArray(),
            format = (ulong)format, vertex_count = 4, vertex_stride = _vertexStride, vertex_offset = _vertexOffset, index_stride = _indexStride });
    }
    void Produce(uint phase)
    {
        uint[] parameters = { checked((uint)(_vertexStride / 4)), checked((uint)(_vertexOffset / 4)), checked((uint)_indexStride), phase };
        long list = _rd.ComputeListBegin(); _rd.ComputeListBindComputePipeline(list, _producerPipeline);
        _rd.ComputeListBindUniformSet(list, _producerSet, 0);
        _rd.ComputeListSetPushConstant(list, MemoryMarshal.AsBytes(parameters.AsSpan()), 16);
        _rd.ComputeListDispatch(list, 1, 1, 1); _rd.ComputeListEnd(); _dispatches++;
    }
    async Task Numeric(int phase)
    {
        await Frames(checked((int)_rd.GetFrameDelay() + 4));
        await OnRender(() => {
            byte[] vertices = Read(_vertex), indices = Read(_index);
            float center = -2 + 2 * phase;
            float[] expected = { center - 0.7f, -0.7f, 0, center + 0.7f, -0.7f, 0,
                center + 0.7f, 0.7f, 0, center - 0.7f, 0.7f, 0 };
            Check(vertices.SequenceEqual(MemoryMarshal.AsBytes(expected.AsSpan()).ToArray()), "Compute did not update native float3 positions.");
            ushort[] expectedIndices = { 0, 1, 2, 0, (ushort)(phase == 1 ? 1 : 2), (ushort)(phase == 1 ? 2 : 3) };
            Check(indices.SequenceEqual(MemoryMarshal.AsBytes(expectedIndices.AsSpan()).ToArray()), "Compute did not update native uint16 indices.");
            _phases.Add(new { phase, vertex_rid = _vertex.Id, index_rid = _index.Id, vertex_bytes = vertices.Length,
                index_bytes = indices.Length, vertices_sha256 = BytesHash(vertices), indices_sha256 = BytesHash(indices) });
        });
        using Image image = _texture!.GetImage(); image.Convert(Image.Format.Rgba8);
        byte[] data = image.GetData(); _imageReadbacks++;
        Check(image.GetWidth() == Width && image.GetHeight() == Height && data.Length == Width * Height * 4, "Viewport bytes differ.");
        string hash = BytesHash(data);
        void Pixel(string kind, float worldX, float worldY, bool white)
        {
            int x = (int)MathF.Floor((worldX / 8 + 0.5f) * Width), y = (int)MathF.Floor((0.5f - worldY / 4) * Height);
            int at = (y * Width + x) * 4; int value = white ? 255 : 0;
            int[] rgb = { data[at], data[at + 1], data[at + 2] };
            Check(rgb.All(channel => Math.Abs(channel - value) <= 1), "Native mesh raster differs: " + phase + "/" + kind);
            _pixelSamples++; _pixels.Add(new { phase, kind, x, y, expected_rgb = new[] { value, value, value }, actual_rgb = rgb, image_sha256 = hash });
        }
        float centerX = -2 + 2 * phase;
        Pixel("lower_right", centerX + 0.35f, -0.35f, true);
        Pixel("upper_left", centerX - 0.35f, 0.35f, phase != 1);
        for (int other = 0; other < 3; other++) if (other != phase) Pixel("other_" + other, -2 + 2 * other, 0, false);
    }
    async Task Cleanup()
    {
        if (_node != null) { _node.Mesh = null; _node.MaterialOverride = null; _node.Free(); _node = null; }
        await DrainFrames();
        if (_rd != null) await OnRender(() => {
            FreeOwned(_producerSet); _producerSet = default;
            FreeOwned(_producerPipeline); _producerPipeline = default;
            FreeOwned(_producerShader); _producerShader = default;
            FreeOwned(_probeShader); _probeShader = default;
        });
        _mesh?.Dispose(); _mesh = null; _probe?.Dispose(); _probe = null; _unflagged?.Dispose(); _unflagged = null;
        _material?.Dispose(); _material = null; _shader?.Dispose(); _shader = null;
        _texture?.Dispose(); _texture = null; _view?.Free(); _view = null; _world?.Dispose(); _world = null;
        await DrainFrames(); Check(_owned.Count == 0, "Final owned RD ledger is not empty.");
    }
    string RuntimeAssemblyHash(Assembly assembly, string expectedPath)
    {
        string path = string.IsNullOrEmpty(assembly.Location) ? expectedPath : assembly.Location;
        using var stream = File.OpenRead(path); using var pe = new PEReader(stream);
        Check(pe.GetMetadataReader().GetGuid(pe.GetMetadataReader().GetModuleDefinition().Mvid) == assembly.ManifestModule.ModuleVersionId, "Runtime assembly differs from known file.");
        return Hash(path);
    }
    void WriteResult()
    {
        string fixture = ProjectSettings.GlobalizePath("res://.godot/mono/temp/bin/Debug/MeshRdLab.dll");
        string api = Path.Combine(Path.GetDirectoryName(OS.GetExecutablePath())!, "GodotSharp/Api/Debug/GodotSharp.dll");
        bool passed = _failure.Length == 0 && _errors.Unexpected.Length == 0 && _errors.Expected.Length == 5 && _phases.Count == 3;
        var result = new { contract = "mesh-rd-numeric-contract-v1", passed, failure = _failure,
            engine_hash = Engine.GetVersionInfo()["hash"].AsString(), sdk_version = SdkVersion,
            driver = RenderingServer.GetCurrentRenderingDriverName(), renderer = RenderingServer.GetCurrentRenderingMethod(),
            gpu_name = RenderingServer.GetVideoAdapterName(), checks = _checks,
            managed_fixture_assembly_sha256 = RuntimeAssemblyHash(typeof(MeshRdContract).Assembly, fixture),
            managed_api_assembly_sha256 = RuntimeAssemblyHash(typeof(Node).Assembly, api),
            source_sha256 = Inputs.ToDictionary(name => name, name => Hash(ProjectSettings.GlobalizePath("res://" + name))),
            stream_audits = _streams, phase_audits = _phases, pixel_audits = _pixels, retirement_audits = _retirements,
            compute_dispatches = _dispatches, positive_storage_bindings = _positiveBindings, gpu_buffer_readbacks = _bufferReadbacks,
            viewport_image_readbacks = _imageReadbacks, numeric_pixel_samples = _pixelSamples,
            expected_native_error_callbacks = _errors.Expected, unexpected_native_diagnostics = _errors.Unexpected,
            final_owned_rd_resource_count = _owned.Count, engine_owned_borrowed_buffers_freed_by_consumer = 0,
            image_files_written = 0, performance_window_count = 0, performance_qualified = false,
            exclusions = new[] { "No performance, physical VRAM or sustained lifetime qualification", "No animation cache or bone skinning",
                "No BufferRD, shader-driven MultiMesh, indirect draw or game assets", "No normals, tangent-space, shadows or temporal history",
                "Fixed topology and in-range indices only; no native GPU index-range validation claim", "32-bit index ABI is CPU-tested only; runtime uses queried uint16" } };
        string path = Path.GetFullPath(Required("record")); Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllBytes(path, JsonSerializer.SerializeToUtf8Bytes(result, new JsonSerializerOptions { WriteIndented = true }));
        GD.Print("Mesh RD contract result " + path); OS.RemoveLogger(_errors); _errors.Dispose(); GetTree().Quit(passed ? 0 : 2);
    }
}

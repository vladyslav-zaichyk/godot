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

// Numeric API correctness only. No screenshots, performance window or game assets.
public partial class InstanceIndirectionContract : Node
{
    const int Views = 8, Capacity = 8, Width = 128, Height = 64;
    const uint IdentityBytes = Capacity * 48, IdBytes = Views * Capacity * 4, PoseBytes = 2 * 64, CommandBytes = Views * 20;
    static readonly string[] Modes = { "eight_identity", "shared_identity", "shader_driven" };
    static readonly string[] Inputs = { "InstanceIndirectionLab.csproj", "project.godot", "Main.tscn",
        "InstanceIndirectionContract.cs", "FixtureErrorLogger.cs", "Producer.glslinc", "Consumer.gdshader" };
    readonly Dictionary<string, string> _args = new();
    readonly List<(Rid rid, uint bytes)> _owned = new();
    readonly List<Rid> _identities = new();
    readonly List<ShaderMaterial> _materials = new();
    readonly List<object> _modeAudits = new(), _attachAudits = new(), _pixels = new(), _rebindAudits = new();
    readonly Dictionary<int, string> _referenceImages = new();
    readonly Rid[] _multimeshes = new Rid[Views], _instances = new Rid[Views];
    readonly FixtureErrorLogger _errors = new();
    RenderingDevice _rd = null!;
    SubViewport? _view;
    ViewportTexture? _texture;
    World3D? _world;
    ArrayMesh? _mesh;
    Shader? _shader;
    BufferRD? _idWrapper, _poseWrapper;
    Rid _producerShader, _producerPipeline, _producerSet, _ids, _poses, _commands;
    string _mode = "", _failure = "";
    int _checks, _initialAttaches, _rebinds, _dispatches, _imageReadbacks, _pixelSamples, _ownershipProbes;

    public override async void _Ready()
    {
        OS.AddLogger(_errors);
        foreach (string argument in OS.GetCmdlineUserArgs()) {
            string[] pair = argument.TrimStart('-').Split('=', 2);
            _args[pair[0]] = pair.Length == 2 ? pair[1] : "true";
        }
        try {
            Check(BitConverter.IsLittleEndian, "The fixture requires little-endian words.");
            Check(Engine.GetVersionInfo()["hash"].AsString() == Required("expected-engine-hash"), "Native source identity differs.");
            Check(SdkVersion == Required("expected-sdk-version"), "Compiled SDK version differs.");
            Check(RenderingServer.GetCurrentRenderingDriverName() == "vulkan" &&
                RenderingServer.GetCurrentRenderingMethod() == "forward_plus", "Vulkan/Forward+ is required.");
            _rd = RenderingServer.GetRenderingDevice() ?? throw new Exception("Global RD unavailable.");
            Engine.MaxFps = 120;
            BuildScene();
            await OnRender(() => {
                using var source = new RDShaderSource { SourceCompute = Godot.FileAccess.GetFileAsString("res://Producer.glslinc") };
                using var spirv = _rd.ShaderCompileSpirVFromSource(source);
                Check(string.IsNullOrWhiteSpace(spirv.CompileErrorCompute), spirv.CompileErrorCompute);
                _producerShader = Own(_rd.ShaderCreateFromSpirV(spirv));
                _producerPipeline = Own(_rd.ComputePipelineCreate(_producerShader));
            });
            foreach (string mode in Modes) {
                _mode = mode;
                await CreateMode();
                for (uint phase = 0; phase < 3; phase++) {
                    await OnRender(() => Produce(phase));
                    if (phase == 2) await CheckRebind();
                    await Numeric(checked((int)phase));
                }
                await CleanupMode();
                await DrainFrames();
                Check(_owned.Count == 2, "A mode retained owned RD resources.");
            }
            Check(_initialAttaches == 24 && _rebinds == 3 && _dispatches == 9 && _pixelSamples == 144,
                "Incomplete fixed three-mode contract.");
            Check(_ownershipProbes == 18, "Incomplete caller-owned storage lifetime audit.");
        } catch (Exception error) {
            _failure = error.ToString();
            GD.PushError(_failure);
        } finally {
            try {
                await CleanupMode();
                if (_rd != null) await OnRender(() => {
                    FreeOwned(_producerPipeline); _producerPipeline = default;
                    FreeOwned(_producerShader); _producerShader = default;
                });
                _texture?.Dispose(); _texture = null;
                _view?.Free(); _view = null;
                _world?.Dispose(); _world = null;
                _mesh?.Dispose(); _mesh = null;
                _shader?.Dispose(); _shader = null;
                await DrainFrames();
                Check(_owned.Count == 0 && _materials.Count == 0 && _identities.Count == 0,
                    "Final ownership ledger is not empty.");
            } catch (Exception error) {
                _failure += "\nCleanup: " + error;
                GD.PushError(error.ToString());
            }
            try { WriteResult(); }
            catch (Exception error) {
                GD.PushError("Contract reporting failed: " + error);
                try {
                    if (_args.TryGetValue("record", out string? path)) {
                        path = Path.GetFullPath(path); Directory.CreateDirectory(Path.GetDirectoryName(path)!);
                        File.WriteAllBytes(path, JsonSerializer.SerializeToUtf8Bytes(new {
                            contract = "shader-driven-instance-contract-v1", passed = false, failure = error.ToString()
                        }));
                    }
                } catch (Exception reportingError) { GD.PushError(reportingError.ToString()); }
                finally { OS.RemoveLogger(_errors); _errors.Dispose(); GetTree().Quit(2); }
            }
        }
    }

    string Required(string key) => _args.TryGetValue(key, out string? value) && value.Length > 0
        ? value : throw new Exception("Missing --" + key);
    static string SdkVersion => typeof(InstanceIndirectionContract).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>()
        .SingleOrDefault(item => item.Key == "GodotSdkVersion")?.Value ?? "";
    static string Hash(string path) => Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant();
    static string BytesHash(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    void Check(bool condition, string message) { _checks++; if (!condition) throw new Exception(message); }

    async Task OnRender(Action action)
    {
        var done = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        RenderingServer.CallOnRenderThread(Callable.From(() => {
            try { action(); done.SetResult(); } catch (Exception error) { done.SetException(error); }
        }));
        await done.Task;
    }
    async Task Frames(int count) { for (int i = 0; i < count; i++) await ToSignal(RenderingServer.Singleton, RenderingServer.SignalName.FramePostDraw); }
    async Task DrainFrames()
    {
        if (_rd == null) return;
        await Frames(checked((int)_rd.GetFrameDelay() + 4));
        await OnRender(() => { });
    }
    Rid Own(Rid rid, uint bytes = 0) { Check(rid.IsValid, "RD allocation failed."); _owned.Add((rid, bytes)); return rid; }
    void FreeOwned(Rid rid)
    {
        if (!rid.IsValid) return;
        int index = _owned.FindIndex(item => item.rid == rid);
        Check(index >= 0, "Attempted to free an unowned RD RID.");
        _rd.FreeRid(rid); _owned.RemoveAt(index);
    }

    void BuildScene()
    {
        _shader = GD.Load<Shader>("res://Consumer.gdshader") ?? throw new Exception("Consumer shader missing.");
        _mesh = new ArrayMesh();
        using (var arrays = new Godot.Collections.Array()) {
            arrays.Resize((int)Mesh.ArrayType.Max);
            arrays[(int)Mesh.ArrayType.Vertex] = new[] { new Vector3(-1, -1, 0), new Vector3(1, -1, 0), new Vector3(1, 1, 0), new Vector3(-1, 1, 0) };
            arrays[(int)Mesh.ArrayType.Normal] = new[] { Vector3.Back, Vector3.Back, Vector3.Back, Vector3.Back };
            arrays[(int)Mesh.ArrayType.Index] = new[] { 0, 1, 2, 0, 2, 3 };
            _mesh.AddSurfaceFromArrays(Mesh.PrimitiveType.Triangles, arrays);
        }
        _world = new World3D();
        _view = new SubViewport { Size = new Vector2I(Width, Height), World3D = _world,
            TransparentBg = false, RenderTargetUpdateMode = SubViewport.UpdateMode.Always };
        AddChild(_view);
        _view.AddChild(new Camera3D { Projection = Camera3D.ProjectionType.Orthogonal, Size = 4,
            KeepAspect = Camera3D.KeepAspectEnum.KeepHeight, Near = 0.1f, Far = 10,
            Position = new Vector3(0, 0, 2), Current = true });
        _texture = _view.GetTexture();
    }

    BufferRD Wrap(Rid rid)
    {
        var wrapper = new BufferRD();
        try {
            Check(wrapper.AttachStorageBuffer(rid) == Error.Ok, "BufferRD attach failed.");
            Check(wrapper.GetRid() == rid && wrapper.GetBufferRdRid() == rid, "BufferRD changed storage identity.");
            return wrapper;
        } catch { wrapper.Dispose(); throw; }
    }
    Error Attach(int view, int capacity)
    {
        return _mode == "shader_driven"
            ? RenderingServer.MultimeshAttachSharedDrawBuffer(_multimeshes[view], capacity, _commands, view * 20)
            : RenderingServer.MultimeshAttachSharedBuffers(_multimeshes[view],
                _identities[_mode == "shared_identity" ? 0 : view], capacity, _commands, view * 20);
    }
    static Vector2 ViewOffset(int view) => new(-3 + (view % 4) * 2, view < 4 ? 1 : -1);

    async Task CreateMode()
    {
        for (int k = 0; k < Views; k++) _materials.Add(new ShaderMaterial { Shader = _shader });
        await OnRender(() => {
            _ids = Own(_rd.StorageBufferCreate(IdBytes), IdBytes);
            _poses = Own(_rd.StorageBufferCreate(PoseBytes), PoseBytes);
            // STORAGE_BUFFER_USAGE_DISPATCH_INDIRECT has native value 1.
            _commands = Own(_rd.StorageBufferCreate(CommandBytes, new byte[CommandBytes], (RenderingDevice.StorageBufferUsage)1), CommandBytes);
            _idWrapper = Wrap(_ids); _poseWrapper = Wrap(_poses);
            int pools = _mode == "eight_identity" ? Views : _mode == "shared_identity" ? 1 : 0;
            var identity = new float[Capacity * 12];
            for (int i = 0; i < Capacity; i++) identity[i * 12] = identity[i * 12 + 5] = identity[i * 12 + 10] = 1;
            byte[] bytes = MemoryMarshal.AsBytes(identity.AsSpan()).ToArray();
            for (int i = 0; i < pools; i++) _identities.Add(Own(_rd.StorageBufferCreate(IdentityBytes, bytes), IdentityBytes));
            using Variant idParameter = Variant.From(_idWrapper!);
            using Variant poseParameter = Variant.From(_poseWrapper!);
            for (int k = 0; k < Views; k++) {
                _materials[k].SetShaderParameter("visible_ids", idParameter);
                _materials[k].SetShaderParameter("poses", poseParameter);
                _materials[k].SetShaderParameter("id_base", k * Capacity);
                _materials[k].SetShaderParameter("view_offset", ViewOffset(k));
                _multimeshes[k] = RenderingServer.MultimeshCreate();
                RenderingServer.MultimeshSetMesh(_multimeshes[k], _mesh!.GetRid());
                RenderingServer.MultimeshSetCustomAabb(_multimeshes[k], new Aabb(new Vector3(-4, -2, -0.1f), new Vector3(8, 4, 0.2f)));
                Check(Attach(k, Capacity) == Error.Ok, "Shared view attach failed.");
                _initialAttaches++;
                bool driven = RenderingServer.MultimeshIsShaderDriven(_multimeshes[k]);
                Rid transform = RenderingServer.MultimeshGetBufferRdRid(_multimeshes[k]);
                Check(driven == (_mode == "shader_driven") && RenderingServer.MultimeshIsUsingSharedBuffers(_multimeshes[k]), "Native view mode differs.");
                Check(transform == (_mode == "shader_driven" ? default : _identities[_mode == "shared_identity" ? 0 : k]), "Native transform RID differs.");
                Check(RenderingServer.MultimeshGetCommandBufferRdRid(_multimeshes[k]) == _commands &&
                    RenderingServer.MultimeshGetInstanceCount(_multimeshes[k]) == Capacity, "Native command/capacity differs.");
                _instances[k] = RenderingServer.InstanceCreate();
                RenderingServer.InstanceSetBase(_instances[k], _multimeshes[k]);
                RenderingServer.InstanceSetTransform(_instances[k], Transform3D.Identity);
                RenderingServer.InstanceSetScenario(_instances[k], _world!.Scenario);
                RenderingServer.InstanceGeometrySetMaterialOverride(_instances[k], _materials[k].GetRid());
                RenderingServer.InstanceGeometrySetCastShadowsSetting(_instances[k], RenderingServer.ShadowCastingSetting.Off);
                RenderingServer.InstanceSetVisible(_instances[k], true);
                _attachAudits.Add(new { mode = _mode, view = k, capacity = Capacity, first_instance = 3,
                    multimesh = _multimeshes[k].Id, transform_rid = transform.Id, command_rid = _commands.Id, shader_driven = driven });
            }
            using var ids = new RDUniform { UniformType = RenderingDevice.UniformType.StorageBuffer, Binding = 0 };
            using var poses = new RDUniform { UniformType = RenderingDevice.UniformType.StorageBuffer, Binding = 1 };
            using var commands = new RDUniform { UniformType = RenderingDevice.UniformType.StorageBuffer, Binding = 2 };
            ids.AddId(_ids); poses.AddId(_poses); commands.AddId(_commands);
            var uniforms = new Godot.Collections.Array<RDUniform> { ids, poses, commands };
            using var uniformsLifetime = (Godot.Collections.Array)uniforms;
            _producerSet = Own(_rd.UniformSetCreate(uniforms, _producerShader, 0));
            _modeAudits.Add(new { mode = _mode, views = Views, draw_slot_capacity = Capacity, stable_agents = 2,
                caller_transform_buffer_count = _identities.Count, caller_transform_input_bytes = _identities.Count * IdentityBytes,
                exposed_transform_rid_count = _multimeshes.Select(RenderingServer.MultimeshGetBufferRdRid).Where(rid => rid.IsValid).Distinct().Count(),
                ids_bytes = IdBytes, pose_bytes = PoseBytes, command_bytes = CommandBytes,
                caller_owned_requested_buffer_bytes = _owned.Sum(item => (long)item.bytes),
                rd_logical_buffer_bytes = _rd.GetMemoryUsage(RenderingDevice.MemoryType.Buffers) });
        });
    }

    void Produce(uint phase)
    {
        foreach (ShaderMaterial material in _materials) material.SetShaderParameter("expected_phase", phase);
        long list = _rd.ComputeListBegin();
        _rd.ComputeListBindComputePipeline(list, _producerPipeline);
        _rd.ComputeListBindUniformSet(list, _producerSet, 0);
        _rd.ComputeListSetPushConstant(list, MemoryMarshal.AsBytes(new[] { phase }.AsSpan()).ToArray(), 4);
        _rd.ComputeListDispatch(list, 1, 1, 1); _rd.ComputeListEnd(); _dispatches++;
        Check(_idWrapper!.GetBufferRdRid() == _ids && _poseWrapper!.GetBufferRdRid() == _poses, "Producer changed borrowed identities.");
    }

    async Task Numeric(int phase)
    {
        await Frames(4);
        using Image image = _texture!.GetImage(); _imageReadbacks++;
        Check(!image.IsEmpty() && image.GetWidth() == Width && image.GetHeight() == Height, "Invalid numeric viewport.");
        if (image.GetFormat() != Image.Format.Rgba8) image.Convert(Image.Format.Rgba8);
        byte[] pixels = image.GetData();
        string hash = BytesHash(pixels);
        if (_mode == Modes[0]) _referenceImages[phase] = hash;
        else Check(_referenceImages[phase] == hash, "Three-mode full numeric raster differs.");
        for (int view = 0; view < Views; view++) {
            int agent = (view & 1) ^ (phase == 0 ? 0 : 1);
            float x = agent == 0 ? -0.5f : 0.5f;
            if (phase == 2) x = -x;
            int color = phase == 2 ? (agent == 0 ? 4 : 3) : (agent == 0 ? 1 : 2);
            Vector2 origin = ViewOffset(view);
            Sample(view, agent, origin.X + x, origin.Y, new[] { (color & 1) != 0 ? 255 : 0,
                (color & 2) != 0 ? 255 : 0, (color & 4) != 0 ? 255 : 0 }, "agent");
            Sample(view, agent, origin.X - x, origin.Y, new[] { 0, 0, 0 }, "vacated");
        }
        void Sample(int view, int agent, float worldX, float worldY, int[] expected, string kind)
        {
            int x = (int)Math.Floor((worldX / 8.0 + 0.5) * Width);
            int y = (int)Math.Floor((0.5 - worldY / 4.0) * Height);
            int offset = (y * Width + x) * 4;
            int[] actual = { pixels[offset], pixels[offset + 1], pixels[offset + 2] };
            for (int c = 0; c < 3; c++) Check(Math.Abs(actual[c] - expected[c]) <= 1,
                $"{_mode} phase {phase} view {view} {kind} channel {c}: {actual[c]} != {expected[c]}");
            _pixelSamples++;
            _pixels.Add(new { mode = _mode, phase, view, agent, kind, x, y, expected_rgb = expected, actual_rgb = actual, image_sha256 = hash });
        }
    }

    async Task CheckRebind()
    {
        await OnRender(() => {
            Rid transform = RenderingServer.MultimeshGetBufferRdRid(_multimeshes[0]);
            var negatives = new List<string>();
            bool Preserved() => RenderingServer.MultimeshGetBufferRdRid(_multimeshes[0]) == transform &&
                RenderingServer.MultimeshGetCommandBufferRdRid(_multimeshes[0]) == _commands &&
                RenderingServer.MultimeshGetInstanceCount(_multimeshes[0]) == Capacity &&
                RenderingServer.MultimeshIsUsingSharedBuffers(_multimeshes[0]) &&
                RenderingServer.MultimeshIsShaderDriven(_multimeshes[0]) == (_mode == "shader_driven");
            void Reject(string name, Error result)
            {
                Check(result == Error.InvalidParameter, "Invalid rebind accepted: " + name);
                Check(Preserved(), "Rejected rebind changed command/transform/capacity/mode: " + name);
                negatives.Add(name);
            }
            Reject("zero_capacity", Attach(0, 0));
            if (_mode == "shader_driven") {
                Reject("unaligned_command_offset", RenderingServer.MultimeshAttachSharedDrawBuffer(_multimeshes[0], Capacity, _commands, 1));
                Reject("short_command_range", RenderingServer.MultimeshAttachSharedDrawBuffer(_multimeshes[0], Capacity, _commands, (Views - 1) * 20 + 4));
            }
            Check(Attach(0, Capacity) == Error.Ok, "Same-resource rebind failed."); _rebinds++;
            Check(Preserved(), "Valid same-resource rebind changed the view.");
            _rebindAudits.Add(new { mode = _mode, negative_cases = negatives, invalid_capacity_rejected = true,
                previous_binding_preserved = true, valid_rebind_passed = true });
        });
        // The caller's phase-two numeric raster follows both rebind attempts.
    }

    async Task CleanupMode()
    {
        if (_rd == null) return;
        await OnRender(() => {
            foreach (Rid instance in _instances) if (instance.IsValid) RenderingServer.InstanceSetVisible(instance, false);
            foreach (ShaderMaterial material in _materials) {
                material.SetShaderParameter("visible_ids", default(Variant)); material.SetShaderParameter("poses", default(Variant));
            }
        });
        await DrainFrames();
        await OnRender(() => {
            for (int k = 0; k < Views; k++) {
                if (_instances[k].IsValid) { RenderingServer.FreeRid(_instances[k]); _instances[k] = default; }
                if (!_multimeshes[k].IsValid) continue;
                if (k != Views - 1) {
                    RenderingServer.MultimeshDetachSharedBuffers(_multimeshes[k]);
                    Check(!RenderingServer.MultimeshIsUsingSharedBuffers(_multimeshes[k]) &&
                        !RenderingServer.MultimeshIsShaderDriven(_multimeshes[k]), "Detach retained shared mode.");
                }
                // The last view tests destruction without an explicit detach.
                RenderingServer.FreeRid(_multimeshes[k]); _multimeshes[k] = default;
            }
            foreach (Rid rid in _identities.Concat(new[] { _commands, _ids, _poses }).Where(rid => rid.IsValid)) {
                using BufferRD probe = Wrap(rid); _ownershipProbes++;
            }
            FreeOwned(_producerSet); _producerSet = default;
        });
        _idWrapper?.Dispose(); _idWrapper = null; _poseWrapper?.Dispose(); _poseWrapper = null;
        foreach (ShaderMaterial material in _materials) material.Dispose(); _materials.Clear();
        await DrainFrames();
        await OnRender(() => {
            foreach (Rid rid in _identities) FreeOwned(rid); _identities.Clear();
            FreeOwned(_commands); _commands = default; FreeOwned(_ids); _ids = default; FreeOwned(_poses); _poses = default;
        });
    }

    string RuntimeAssemblyHash(Assembly assembly, string expectedPath)
    {
        string path = string.IsNullOrEmpty(assembly.Location) ? expectedPath : assembly.Location;
        using var stream = File.OpenRead(path); using var pe = new PEReader(stream);
        MetadataReader metadata = pe.GetMetadataReader();
        Check(metadata.GetGuid(metadata.GetModuleDefinition().Mvid) == assembly.ManifestModule.ModuleVersionId,
            "Runtime module differs from the known managed file.");
        return Hash(path);
    }
    void WriteResult()
    {
        string fixture = ProjectSettings.GlobalizePath("res://.godot/mono/temp/bin/Debug/InstanceIndirectionLab.dll");
        string api = Path.Combine(Path.GetDirectoryName(OS.GetExecutablePath())!, "GodotSharp/Api/Debug/GodotSharp.dll");
        string fixtureHash = RuntimeAssemblyHash(typeof(InstanceIndirectionContract).Assembly, fixture);
        string apiHash = RuntimeAssemblyHash(typeof(Node).Assembly, api);
        bool passed = _failure.Length == 0 && _errors.Errors.Length == 0 && _modeAudits.Count == 3;
        var result = new { contract = "shader-driven-instance-contract-v1", passed, failure = _failure,
            engine_hash = Engine.GetVersionInfo()["hash"].AsString(), sdk_version = SdkVersion,
            driver = RenderingServer.GetCurrentRenderingDriverName(), renderer = RenderingServer.GetCurrentRenderingMethod(),
            gpu_name = RenderingServer.GetVideoAdapterName(), checks = _checks,
            managed_fixture_assembly_sha256 = fixtureHash, managed_api_assembly_sha256 = apiHash,
            source_sha256 = Inputs.ToDictionary(name => name, name => Hash(ProjectSettings.GlobalizePath("res://" + name))),
            mode_audits = _modeAudits, attach_audits = _attachAudits, rebind_audits = _rebindAudits, pixel_audits = _pixels,
            initial_view_attaches = _initialAttaches, same_resource_rebinds = _rebinds, compute_dispatches = _dispatches,
            viewport_image_readbacks = _imageReadbacks, numeric_pixel_samples = _pixelSamples, ownership_probes = _ownershipProbes,
            final_owned_rd_resource_count = _owned.Count, final_owned_requested_buffer_bytes = _owned.Sum(item => (long)item.bytes),
            final_material_count = _materials.Count, native_diagnostics = _errors.Errors,
            image_files_written = 0, gpu_buffer_readbacks = 0, performance_window_count = 0, performance_qualified = false,
            memory_scope = "Caller-owned input storage ledger and exposed transform RID identities; raw RD logical sample only, no engine-wide allocation attribution.",
            exclusions = new[] { "No performance or physical VRAM qualification", "No motion/TAA/FSR2 or temporal history",
                "No shadow/depth/normal/TBN or pass-specific visibility proof", "No invalid-material runtime guard proof",
                "No out-of-range GPU command execution; rejected attach parameters are checked without executing them",
                "One allocation/cleanup per mode; no sustained lifetime plateau", "No gameplay, networking or game assets" } };
        string path = Path.GetFullPath(Required("record")); Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllBytes(path, JsonSerializer.SerializeToUtf8Bytes(result, new JsonSerializerOptions { WriteIndented = true }));
        GD.Print("Instance indirection contract result " + path);
        OS.RemoveLogger(_errors); _errors.Dispose(); GetTree().Quit(passed ? 0 : 2);
    }
}

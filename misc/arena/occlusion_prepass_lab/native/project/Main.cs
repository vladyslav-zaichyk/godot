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
using GDictionary = Godot.Collections.Dictionary;

// Fixed native GPU correctness stand. No game assets, image files, FPS or VRAM claim.
public partial class Main : Node
{
    const uint Guard = 0xcdcdcdcdu, PyramidWords = 2739, LogicalSlots = 16;
    static readonly uint[] Offsets = { 4, 2052, 2564, 2692, 2724, 2732, 2734 };
    static readonly int[] Columns = { 8, 16, 24, 31, 40, 48, 56, 62 };
    static readonly string[] Inputs = { "OcclusionNativeLab.csproj", "project.godot", "Main.tscn", "Main.cs", "ProbeLogger.cs",
        "Wall.gdshader", "Consumer.gdshader", "CopyDepth.comp", "Reduce.comp", "Produce.comp", "Finalize.comp", "oracle.json" };
    readonly Dictionary<string, string> _args = new();
    readonly List<(Rid rid, uint bytes)> _owned = new();
    readonly List<object> _probes = new();
    readonly List<MeshInstance3D> _walls = new();
    readonly ProbeLogger _logger = new();
    RenderingDevice _rd = null!;
    SubViewport? _view;
    Camera3D? _camera;
    World3D? _world;
    ArrayMesh? _mesh;
    Shader? _wallShader, _crowdShader;
    ShaderMaterial? _wallMaterial, _crowdMaterial;
    ViewportTexture? _texture;
    BufferRD? _idsWrapper, _slotsWrapper;
    Rid _viewport, _instance, _multimesh, _ids, _slots, _fallback, _candidate, _stats, _pyramid, _sampler;
    Rid _copyShader, _copyPipeline, _reducePipeline, _producePipeline, _finalPipeline, _reduceSet, _produceSet, _finalSet;
    Callable _producer;
    JsonDocument _oracle = null!;
    JsonElement _probe;
    object? _callbackAudit;
    Transform3D _expectedCamera;
    bool _configured;
    string _failure = "", _callbackFailure = "";
    int _checks, _callbacks, _dispatches, _barriers, _readbacks, _ownershipProbes;
    long _previousEpoch, _previousGeneration;

    public override async void _Ready()
    {
        OS.AddLogger(_logger);
        foreach (string item in OS.GetCmdlineUserArgs()) {
            string[] pair = item.TrimStart('-').Split('=', 2); _args[pair[0]] = pair.Length == 2 ? pair[1] : "true";
        }
        try {
            Check(BitConverter.IsLittleEndian, "Little-endian ABI required.");
            Check(Engine.GetVersionInfo()["hash"].AsString() == Required("expected-engine-hash"), "Native source differs.");
            Check(Hash(OS.GetExecutablePath()) == Required("expected-engine-sha256") && SdkVersion == Required("expected-sdk-version"), "Editor/SDK identity differs.");
            Check(RenderingServer.GetCurrentRenderingDriverName() == "vulkan" && RenderingServer.GetCurrentRenderingMethod() == "forward_plus", "Vulkan/Forward+ required.");
            _oracle = JsonDocument.Parse(Godot.FileAccess.GetFileAsString("res://oracle.json"));
            _rd = RenderingServer.GetRenderingDevice() ?? throw new Exception("Global RD unavailable.");
            Engine.MaxFps = 60;
            BuildScene();
            _producer = Callable.From<RenderGpuOcclusionContext, GDictionary>(Produce);
            await OnRender(CreateResources);
            foreach (JsonElement probe in _oracle.RootElement.GetProperty("cases").EnumerateArray()) await RunProbe(probe);
            Check(_probes.Count == 14 && _callbacks == 11 && _dispatches == 99 && _barriers == 88 && _readbacks == 14, "Incomplete bounded matrix.");
        } catch (Exception error) {
            _failure = error.ToString(); GD.PushError(_failure);
        } finally {
            try { await Cleanup(); Check(_logger.Entries.Length == 0 && _logger.Dropped == 0, "Unexpected native diagnostics."); }
            catch (Exception error) { _failure += "\nCleanup: " + error; GD.PushError(error.ToString()); }
            try { WriteResult(); }
            catch (Exception error) {
                GD.PushError(error.ToString());
                try { File.WriteAllBytes(Path.GetFullPath(Required("record")), JsonSerializer.SerializeToUtf8Bytes(new { contract = "native-occlusion-case-v1", passed = false, failure = error.ToString() })); }
                finally { OS.RemoveLogger(_logger); _logger.Dispose(); GetTree().Quit(2); }
            }
        }
    }
    string Required(string key) => _args.TryGetValue(key, out string? value) && value.Length > 0 ? value : throw new Exception("Missing --" + key);
    static string SdkVersion => typeof(Main).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>().SingleOrDefault(v => v.Key == "GodotSdkVersion")?.Value ?? "";
    static string Digest(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    static string Hash(string path) => Digest(File.ReadAllBytes(path));
    static byte[] Bytes(uint[] values) => MemoryMarshal.AsBytes(values.AsSpan()).ToArray();
    static uint Bits(float value) => BitConverter.SingleToUInt32Bits(value);
    static uint Low(long value) => unchecked((uint)value);
    static uint High(long value) => unchecked((uint)((ulong)value >> 32));
    void Check(bool good, string message) { _checks++; if (!good) throw new Exception(message); }
    async Task OnRender(Action action) {
        var done = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        RenderingServer.CallOnRenderThread(Callable.From(() => { try { action(); done.SetResult(); } catch (Exception error) { done.SetException(error); } }));
        await done.Task;
    }
    async Task Frames(int count) { for (int i = 0; i < count; i++) await ToSignal(RenderingServer.Singleton, RenderingServer.SignalName.FramePostDraw); }
    async Task Drain() { if (_rd != null) { await Frames(checked((int)_rd.GetFrameDelay() + 4)); await OnRender(() => { }); } }
    Rid Own(Rid rid, uint bytes = 0) { Check(rid.IsValid, "Allocation failed."); _owned.Add((rid, bytes)); return rid; }
    Rid Storage(uint words, uint[]? data = null, bool indirect = false) {
        uint[] initial = data ?? Enumerable.Repeat(Guard, checked((int)words)).ToArray();
        return Own(_rd.StorageBufferCreate(words * 4, Bytes(initial), (RenderingDevice.StorageBufferUsage)(indirect ? 1 : 0)), words * 4);
    }
    Rid Compute(string path, out Rid shader) {
        using var source = new RDShaderSource { SourceCompute = Godot.FileAccess.GetFileAsString("res://" + path) };
        using var spirv = _rd.ShaderCompileSpirVFromSource(source);
        Check(string.IsNullOrWhiteSpace(spirv.CompileErrorCompute), spirv.CompileErrorCompute);
        shader = Own(_rd.ShaderCreateFromSpirV(spirv)); return Own(_rd.ComputePipelineCreate(shader));
    }
    Rid Uniforms(Rid shader, bool cached, params (RenderingDevice.UniformType type, Rid[] ids)[] bindings) {
        var uniforms = new Godot.Collections.Array<RDUniform>(); using var lifetime = (Godot.Collections.Array)uniforms;
        var resources = new List<RDUniform>();
        try {
            for (int i = 0; i < bindings.Length; i++) {
                var uniform = new RDUniform { UniformType = bindings[i].type, Binding = i };
                foreach (Rid rid in bindings[i].ids) uniform.AddId(rid);
                resources.Add(uniform); uniforms.Add(uniform);
            }
            Rid set = cached ? UniformSetCacheRD.GetCache(shader, 0, uniforms) : Own(_rd.UniformSetCreate(uniforms, shader, 0));
            Check(set.IsValid, "Uniform set unavailable."); return set;
        } finally { foreach (RDUniform uniform in resources) uniform.Dispose(); }
    }
    BufferRD Wrap(Rid rid) {
        var wrapper = new BufferRD();
        try { Check(wrapper.AttachStorageBuffer(rid) == Error.Ok && wrapper.GetBufferRdRid() == rid, "Borrowed storage attach failed."); return wrapper; }
        catch { wrapper.Dispose(); throw; }
    }
    void BuildScene()
    {
        _mesh = new ArrayMesh();
        using (var arrays = new Godot.Collections.Array()) {
            arrays.Resize((int)Mesh.ArrayType.Max);
            arrays[(int)Mesh.ArrayType.Vertex] = new[] { new Vector3(-1,-1,0), new Vector3(1,-1,0), new Vector3(1,1,0), new Vector3(-1,1,0) };
            arrays[(int)Mesh.ArrayType.Normal] = new[] { Vector3.Back, Vector3.Back, Vector3.Back, Vector3.Back };
            // Godot FRONT is clockwise, matching native PlaneMesh FACE_Z with normal +Z.
            arrays[(int)Mesh.ArrayType.Index] = new[] { 0, 2, 1, 0, 3, 2 };
            _mesh.AddSurfaceFromArrays(Mesh.PrimitiveType.Triangles, arrays);
        }
        _wallShader = GD.Load<Shader>("res://Wall.gdshader"); _crowdShader = GD.Load<Shader>("res://Consumer.gdshader");
        Check(_wallShader != null && _crowdShader != null, "Material source missing.");
        _wallMaterial = new ShaderMaterial { Shader = _wallShader }; _crowdMaterial = new ShaderMaterial { Shader = _crowdShader };
        _world = new World3D();
        _view = new SubViewport { Size = new Vector2I(64, 32), World3D = _world, TransparentBg = false,
            // Public spatial mode at scale1 is normalized to native OFF by RendererViewport.
            Scaling3DMode = Viewport.Scaling3DModeEnum.Bilinear, Scaling3DScale = 1.0f,
            RenderTargetUpdateMode = SubViewport.UpdateMode.Disabled };
        AddChild(_view); _viewport = _view.GetViewportRid();
        _camera = new Camera3D { Projection = Camera3D.ProjectionType.Orthogonal, Size = 4,
            KeepAspect = Camera3D.KeepAspectEnum.Height, Near = 1, Far = 9, Position = new Vector3(0,0,3), CullMask = 3, Current = true };
        _view.AddChild(_camera); _texture = _view.GetTexture();
        // Interleave selected and unselected owners of exactly the same mesh/material.
        AddOrdinary(new Vector3(0, 1.625f, 0), new Vector3(0.1875f, 0.1875f, 1), false);
        AddOrdinary(new Vector3(-2, 0, 0), new Vector3(1.5f, 2, 1), true);
        AddOrdinary(new Vector3(0, -1.625f, 0), new Vector3(0.1875f, 0.1875f, 1), false);
        AddOrdinary(new Vector3(2, 0, 0), new Vector3(1.5f, 2, 1), true);
    }
    void AddOrdinary(Vector3 position, Vector3 scale, bool selected) {
        var owner = new MeshInstance3D { Mesh = _mesh, MaterialOverride = _wallMaterial, Position = position, Scale = scale, Layers = selected ? 2u : 1u,
            CastShadow = GeometryInstance3D.ShadowCastingSetting.Off };
        owner.Set("gi_mode", 0); // Explicit no-GI scope; default Static is ineligible.
        Check(owner.Get("gi_mode").AsInt32() == 0, "Ordinary owner must disable GI.");
        _view!.AddChild(owner); if (selected) _walls.Add(owner);
    }
    void CreateResources()
    {
        _copyPipeline = Compute("CopyDepth.comp", out _copyShader);
        _reducePipeline = Compute("Reduce.comp", out Rid reduce);
        _producePipeline = Compute("Produce.comp", out Rid produce);
        _finalPipeline = Compute("Finalize.comp", out Rid final);
        using (var sampler = new RDSamplerState { MinFilter = RenderingDevice.SamplerFilter.Nearest, MagFilter = RenderingDevice.SamplerFilter.Nearest }) _sampler = Own(_rd.SamplerCreate(sampler));
        uint[] bank = Enumerable.Repeat(Guard, 36).ToArray(); for (uint i = 0; i < LogicalSlots; i++) bank[i] = i;
        _ids = Storage(36, bank);
        uint[] slots = new uint[16 * 8];
        for (int i = 0; i < 16; i++) {
            int at = i * 8; slots[at] = Bits((Columns[i % 8] + 0.5f) / 8 - 4); slots[at+1] = Bits(2 - ((i < 8 ? 10 : 22) + 0.5f) / 8);
            slots[at+2] = Bits(i == 2 || i == 12 ? 0.5f : -1); slots[at+3] = Bits(0.125f);
            slots[at+4] = 1; slots[at+5] = (uint)(i == 2 || i == 12 ? 4 : (i & 1) == 0 ? 1 : 2); slots[at+6] = (uint)i; slots[at+7] = 0x413133;
        }
        _slots = Storage(128, slots);
        uint[] command = Enumerable.Repeat(Guard, 16).ToArray(); Array.Copy(new uint[] {6,16,0,0,0}, 0, command, 4, 5);
        _fallback = Storage(16, command, true); _candidate = Storage(16, indirect: true); _stats = Storage(16); _pyramid = Storage(PyramidWords);
        _reduceSet = Uniforms(reduce, false, (RenderingDevice.UniformType.StorageBuffer, new[] {_pyramid}));
        _produceSet = Uniforms(produce, false, (RenderingDevice.UniformType.StorageBuffer,new[] {_pyramid}), (RenderingDevice.UniformType.StorageBuffer,new[] {_slots}),
            (RenderingDevice.UniformType.StorageBuffer,new[] {_ids}), (RenderingDevice.UniformType.StorageBuffer,new[] {_stats}));
        _finalSet = Uniforms(final, false, (RenderingDevice.UniformType.StorageBuffer,new[] {_stats}), (RenderingDevice.UniformType.StorageBuffer,new[] {_candidate}));
        _idsWrapper = Wrap(_ids); _slotsWrapper = Wrap(_slots);
        using Variant id = Variant.From(_idsWrapper); using Variant slot = Variant.From(_slotsWrapper);
        _crowdMaterial!.SetShaderParameter("identity_bank", id); _crowdMaterial.SetShaderParameter("slots", slot);
        _multimesh = RenderingServer.MultimeshCreate();
        RenderingServer.MultimeshSetMesh(_multimesh, _mesh!.GetRid());
        RenderingServer.MultimeshSetCustomAabb(_multimesh, new Aabb(new Vector3(-4,-2,-1.5f),new Vector3(8,4,3)));
        // Both firstInstance regions must fit native E5 capacity, although the logical population is sixteen.
        Check(RenderingServer.MultimeshAttachSharedDrawBuffer(_multimesh, 32, _fallback, 16) == Error.Ok, "Fallback attachment rejected.");
        _instance = RenderingServer.InstanceCreate(); RenderingServer.InstanceSetBase(_instance, _multimesh);
        RenderingServer.InstanceSetTransform(_instance, Transform3D.Identity); RenderingServer.InstanceSetScenario(_instance, _world!.Scenario);
        RenderingServer.InstanceGeometrySetMaterialOverride(_instance, _crowdMaterial.GetRid());
        RenderingServer.InstanceGeometrySetCastShadowsSetting(_instance, RenderingServer.ShadowCastingSetting.Off);
        RenderingServer.InstanceSetVisible(_instance, true);
    }
    static uint[] MatrixWords(Projection matrix) {
        var words = new List<uint>(16);
        for (int column = 0; column < 4; column++) { Vector4 v = matrix[column]; words.AddRange(new[] { Bits(v.X), Bits(v.Y), Bits(v.Z), Bits(v.W) }); }
        return words.ToArray();
    }
    GDictionary Produce(RenderGpuOcclusionContext context)
    {
        try {
            Check(++_callbacks <= 14 && context.IsActive(), "Callback budget/scope invalid.");
            long epoch = context.GetEpoch(), generation = context.GetConfigurationGeneration();
            Check(context.GetViewport() == _viewport && context.GetViewIndex() == 0 && context.GetInternalSize() == new Vector2I(64,32), "Wrong actual native view.");
            Check(epoch > _previousEpoch && generation > 0 && generation >= _previousGeneration, "Old native recording/configuration identity.");
            Check(context.GetCameraTransform().IsEqualApprox(_expectedCamera), "Native camera transform differs.");
            _previousEpoch = epoch; _previousGeneration = generation;
            long list = context.GetComputeList(); Rid depth = context.GetDepth(); Check(depth.IsValid, "Native sampled depth unavailable.");
            Projection matrix = context.GetProjection() * new Projection(context.GetCameraTransform().AffineInverse());
            uint[] matrixWords = MatrixWords(matrix); Check(matrixWords.All(v => float.IsFinite(BitConverter.UInt32BitsToSingle(v))), "Invalid corrected projection.");
            Rid copySet = Uniforms(_copyShader, true, (RenderingDevice.UniformType.SamplerWithTexture,new[] {_sampler,depth}), (RenderingDevice.UniformType.StorageBuffer,new[] {_pyramid}));
            Dispatch(list, _copyPipeline, copySet, 8, 4); Barrier(list);
            for (int level = 1; level < 7; level++) {
                uint sw = Math.Max(1u,64u>>(level-1)), sh = Math.Max(1u,32u>>(level-1));
                uint dw = Math.Max(1u,64u>>level), dh = Math.Max(1u,32u>>level);
                Dispatch(list,_reducePipeline,_reduceSet,(dw+7)/8,(dh+7)/8,Bytes(new uint[] {Offsets[level-1],sw,sh,0,Offsets[level],dw,dh,0})); Barrier(list);
            }
            string publication = _probe.GetProperty("publication").GetString()!;
            uint capacity = publication == "overflow" ? 0u : 16u, zero = publication == "zero" ? 1u : 0u;
            uint[] push = matrixWords.Concat(new[] {capacity,zero,Low(epoch),High(epoch),Low(generation),High(generation),0u,0u}).ToArray();
            Dispatch(list,_producePipeline,_produceSet,1,1,Bytes(push)); Barrier(list);
            Dispatch(list,_finalPipeline,_finalSet,1,1);
            _callbackAudit = new { probe = _probe.GetProperty("name").GetString(), viewport = _viewport.Id, epoch, configuration_generation = generation,
                view_index = 0, internal_size = new[] {64,32}, native_depth_rid = depth.Id, world_to_clip_words = matrixWords,
                dispatches = 9, barriers = 8, depth_cpu_read_before_producer = false };
            // Only numeric evidence is copied out. Context, list token and depth RID are never retained for use.
            string ack = _probe.GetProperty("ack").GetString()!;
            if (ack == "missing") return new GDictionary();
            return new GDictionary { ["epoch"] = ack == "stale" ? epoch - 1 : epoch, ["configuration_generation"] = generation,
                ["error"] = (long)(int)(ack == "error" ? Error.Failed : Error.Ok) };
        } catch (Exception error) { _callbackFailure = error.ToString(); return new GDictionary(); }
    }
    void Dispatch(long list, Rid pipeline, Rid set, uint x, uint y, byte[]? push = null) {
        _rd.ComputeListBindComputePipeline(list,pipeline);
        Check(_rd.ComputeListBindUniformSetChecked(list,set,0) == Error.Ok, "Checked binding failed.");
        if (push != null) _rd.ComputeListSetPushConstant(list,push,(uint)push.Length);
        Check(_rd.ComputeListDispatchChecked(list,x,y,1) == Error.Ok, "Checked callback dispatch failed."); _dispatches++;
    }
    void Barrier(long list) { _rd.ComputeListAddBarrier(list); _barriers++; }
    void UploadGuard(Rid rid, uint words, uint offset = 0) {
        byte[] data = Bytes(Enumerable.Repeat(Guard,checked((int)words)).ToArray());
        Check(_rd.BufferUpdate(rid,offset,(uint)data.Length,data) == Error.Ok,"Preparing private output failed.");
    }
    object Outcome(GDictionary value) {
        bool present = value.Count > 0;
        return new { present, epoch = value.ContainsKey("epoch") ? value["epoch"].AsInt64() : 0,
            configuration_generation = value.ContainsKey("configuration_generation") ? value["configuration_generation"].AsInt64() : 0,
            selected = value.ContainsKey("selected") && value["selected"].AsBool(),
            mode = value.ContainsKey("mode") ? value["mode"].AsString() : "",
            reason = value.ContainsKey("reason") ? value["reason"].AsString() : "",
            early_surfaces = value.ContainsKey("early_surface_count") ? value["early_surface_count"].AsInt32() : -1,
            auto_instancing_disabled = value.ContainsKey("auto_instancing_disabled") && value["auto_instancing_disabled"].AsBool() };
    }
    async Task RunProbe(JsonElement probe)
    {
        Check(_probes.Count < 14,"Probe budget exceeded."); _probe = probe; _callbackAudit = null; _callbackFailure = "";
        bool enabled = probe.GetProperty("enabled").GetBoolean(), wall = probe.GetProperty("wall").GetBoolean();
        int shift = probe.GetProperty("shift").GetInt32();
        foreach (MeshInstance3D owner in _walls) owner.Visible = wall;
        _camera!.Position = new Vector3(shift/8.0f,0,3); _expectedCamera = _camera.GlobalTransform;
        int before = _callbacks;
        await OnRender(() => {
            UploadGuard(_ids,16,64); UploadGuard(_candidate,16); UploadGuard(_stats,16); UploadGuard(_pyramid,PyramidWords);
            if (!enabled && _configured) {
                Check(RenderingServer.ViewportUnregisterGpuOcclusionConsumer(_viewport,_instance) == Error.Ok,"Unregister failed.");
                Check(RenderingServer.ViewportClearGpuOcclusionPrepass(_viewport) == Error.Ok,"Clear failed."); _configured = false;
            }
            if (enabled && !_configured) {
                Check(RenderingServer.ViewportConfigureGpuOcclusionPrepass(_viewport,2,_producer) == Error.Ok,"Native configure rejected.");
                Check(RenderingServer.ViewportRegisterGpuOcclusionConsumer(_viewport,_instance,_ids,16,_candidate,16,16) == Error.Ok,"Native consumer rejected.");
                _configured = true;
            }
        });
        _view!.RenderTargetUpdateMode = SubViewport.UpdateMode.Once;
        await Frames(3);
        Check(_callbackFailure.Length == 0,_callbackFailure);
        Check(_callbacks-before == (enabled?1:0),"Callback count differs from actual one-shot view.");
        uint[] ids = Array.Empty<uint>(), candidate = ids, fallback = ids, stats = ids, pyramid = ids; object? outcome = null;
        await OnRender(() => {
            using GDictionary raw = RenderingServer.ViewportGetGpuOcclusionPrepassOutcome(_viewport);
            if (enabled) {
                Check(raw.ContainsKey("epoch") && raw.ContainsKey("selected") && raw.ContainsKey("reason") && raw.ContainsKey("early_surface_count"),"Incomplete native recording outcome.");
                Check(raw["selected"].VariantType == Variant.Type.Bool && raw["selected"].AsBool() == probe.GetProperty("selected").GetBoolean(),"Native candidate/fallback decision differs.");
                Check(raw["epoch"].AsInt64() == _previousEpoch && raw["configuration_generation"].AsInt64() == _previousGeneration,"Outcome differs from actual callback identity.");
                Check(raw["early_surface_count"].AsInt32() == (wall?2:0),$"Selected/unselected ordinary surfaces were not split correctly. Expected={(wall?2:0)}; outcome={JsonSerializer.Serialize(Outcome(raw))}");
            } else if (raw.ContainsKey("selected")) Check(!raw["selected"].AsBool(),"Disabled view reused a candidate.");
            outcome = Outcome(raw);
            // Terminal evidence only: the producer and resumed native render have already been recorded.
            ids = Read(_ids); candidate = Read(_candidate); fallback = Read(_fallback); stats = Read(_stats); pyramid = Read(_pyramid);
            Check(RenderingServer.MultimeshGetCommandBufferRdRid(_multimesh) == _fallback && !RenderingServer.MultimeshGetBufferRdRid(_multimesh).IsValid,
                "Render-local publication changed the persistent fallback attachment.");
        });
        using Image image = _texture!.GetImage(); _readbacks++;
        Check(!image.IsEmpty() && image.GetWidth() == 64 && image.GetHeight() == 32,"Wrong viewport readback.");
        if (image.GetFormat() != Image.Format.Rgba8) image.Convert(Image.Format.Rgba8);
        byte[] pixels = image.GetData(); Check(pixels.Length == 64*32*4,"Pixel byte count differs.");
        // Store numeric bytes, never an image file. Independent Python checks the exact geometry oracle.
        var samples = new List<object>();
        for (int i=0;i<16;i++) Sample("slot",i,Columns[i%8]-shift,i<8?10:22);
        Sample("ordinary_marker",0,32-shift,3); Sample("ordinary_marker",1,32-shift,29);
        Sample("wall",0,10-shift,16); Sample("wall",1,50-shift,16);
        Sample("gap",0,31-shift,16); Sample("background",0,63,0);
        _probes.Add(new { name=probe.GetProperty("name").GetString(), callback=_callbackAudit, outcome,
            id_words=ids,candidate_command_words=candidate,fallback_command_words=fallback,stats_words=stats,pyramid_words=pyramid,
            id_sha256=Digest(Bytes(ids)),candidate_command_sha256=Digest(Bytes(candidate)),fallback_command_sha256=Digest(Bytes(fallback)),
            stats_sha256=Digest(Bytes(stats)),pyramid_sha256=Digest(Bytes(pyramid)),image_sha256=Digest(pixels),samples,
            callback_completed_before_readback=true,persistent_fallback_unchanged=true });
        void Sample(string kind,int slot,int x,int y) {
            Check(x>=0 && x<64 && y>=0 && y<32,"Oracle sample outside actual viewport."); int at=(y*64+x)*4;
            samples.Add(new {kind,slot,x,y,rgb=new[]{(int)pixels[at],pixels[at+1],pixels[at+2]}});
        }
    }
    uint[] Read(Rid rid) => MemoryMarshal.Cast<byte,uint>(_rd.BufferGetData(rid).AsSpan()).ToArray();
    async Task Cleanup()
    {
        if (_rd == null) return;
        if (_view != null) _view.RenderTargetUpdateMode = SubViewport.UpdateMode.Disabled;
        await OnRender(() => {
            if (_configured) { RenderingServer.ViewportUnregisterGpuOcclusionConsumer(_viewport,_instance); RenderingServer.ViewportClearGpuOcclusionPrepass(_viewport); _configured=false; }
            if (_instance.IsValid) RenderingServer.InstanceSetVisible(_instance,false);
            _crowdMaterial?.SetShaderParameter("identity_bank",default(Variant)); _crowdMaterial?.SetShaderParameter("slots",default(Variant));
        });
        await Drain();
        await OnRender(() => {
            if (_instance.IsValid) {RenderingServer.FreeRid(_instance);_instance=default;}
            if (_multimesh.IsValid) {RenderingServer.MultimeshDetachSharedBuffers(_multimesh);RenderingServer.FreeRid(_multimesh);_multimesh=default;}
            foreach (Rid rid in new[] {_ids,_slots,_fallback,_candidate}.Where(r=>r.IsValid)) {using BufferRD probe=Wrap(rid);_ownershipProbes++;}
        });
        _idsWrapper?.Dispose();_idsWrapper=null;_slotsWrapper?.Dispose();_slotsWrapper=null;
        _texture?.Dispose();_texture=null;_view?.Free();_view=null;_walls.Clear();_camera=null;
        _world?.Dispose();_world=null;_crowdMaterial?.Dispose();_crowdMaterial=null;_wallMaterial?.Dispose();_wallMaterial=null;
        _mesh?.Dispose();_mesh=null;_crowdShader?.Dispose();_crowdShader=null;_wallShader?.Dispose();_wallShader=null;
        await Drain();
        await OnRender(() => {foreach(var item in _owned.ToArray().Reverse()){_rd.FreeRid(item.rid);_owned.Remove(item);}});
        await Drain(); Check(_owned.Count==0 && !_configured && !_instance.IsValid && !_multimesh.IsValid,"Owned resources remain.");
    }
    string AssemblyHash(Assembly assembly,string fallback) {
        string path=string.IsNullOrEmpty(assembly.Location)?fallback:assembly.Location;
        using var stream=File.OpenRead(path);using var pe=new PEReader(stream);MetadataReader metadata=pe.GetMetadataReader();
        Check(metadata.GetGuid(metadata.GetModuleDefinition().Mvid)==assembly.ManifestModule.ModuleVersionId,"Loaded assembly/file differs.");return Hash(path);
    }
    void WriteResult()
    {
        string fixture=ProjectSettings.GlobalizePath("res://.godot/mono/temp/bin/Debug/OcclusionNativeLab.dll");
        string api=Path.Combine(Path.GetDirectoryName(OS.GetExecutablePath())!,"GodotSharp/Api/Debug/GodotSharp.dll");
        bool passed=_failure.Length==0 && _logger.Entries.Length==0 && _logger.Dropped==0 && _probes.Count==14;
        var raw=new {contract="native-occlusion-case-v1",passed,failure=_failure,checks=_checks,
            engine_hash=Engine.GetVersionInfo()["hash"].AsString(),engine_sha256=Hash(OS.GetExecutablePath()),sdk_version=SdkVersion,
            managed_fixture_assembly_sha256=AssemblyHash(typeof(Main).Assembly,fixture),managed_api_assembly_sha256=AssemblyHash(typeof(Node).Assembly,api),
            source_sha256=Inputs.ToDictionary(name=>name,name=>Hash(ProjectSettings.GlobalizePath("res://"+name))),
            driver=RenderingServer.GetCurrentRenderingDriverName(),renderer=RenderingServer.GetCurrentRenderingMethod(),gpu_name=RenderingServer.GetVideoAdapterName(),
            probes=_probes,callbacks=_callbacks,compute_dispatches=_dispatches,compute_barriers=_barriers,viewport_readbacks=_readbacks,ownership_probes=_ownershipProbes,
            diagnostics=_logger.Entries,dropped_diagnostics=_logger.Dropped,caller_owned_final=_owned.Count,caller_requested_bytes_final=_owned.Sum(p=>(long)p.bytes),
            images_written=0,cpu_depth_reads_before_producer=0,native_api_present=true,performance_qualified=false,release_driver_qualified=false,
            exclusions=new[]{"One orthographic viewport, fixed64x32, sixteen immutable slots","No general animated AABB, perspective, MSAA, XR, shadow or motion proof",
                "One registered owner only; ordinary selected/unselected owners share mesh/material","One bounded lifecycle, not a native/physical VRAM plateau","No FPS or adoption claim"}};
        string path=Path.GetFullPath(Required("record"));Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllBytes(path,JsonSerializer.SerializeToUtf8Bytes(raw,new JsonSerializerOptions{WriteIndented=true}));
        _oracle?.Dispose();OS.RemoveLogger(_logger);_logger.Dispose();GetTree().Quit(passed?0:2);
    }
}

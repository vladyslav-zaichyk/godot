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

// One named case per fresh process. No timings, assets, screenshots or global diagnostic suppression.
public partial class DirectHardeningContract : Node
{
    const int Width = 32, Height = 16;
    const string AliasMessage = "A counted indirect bank cannot be bound as a shader resource in the same draw list.";
    // The pinned Windows MSVC build's FUNCTION_STR keeps the C++ class qualification.
    const string AliasFunction = "RenderingDevice::_draw_list_bind_uniform_set";
    static readonly string[] Inputs = { "DirectHardeningLab.csproj","project.godot","Main.tscn","DirectHardeningContract.cs",
        "ProbeLogger.cs","DrawVertex.glslinc","ReadVertex.glslinc","DrawFragment.glslinc","Producer.glslinc","oracle.json" };
    static readonly HashSet<string> Cases = new() {
        "shader_pending_commands","shader_pending_counter","shader_recorded_commands","shader_recorded_counter",
        "shader_after_commands","shader_after_counter","zero_shader_after_commands","zero_shader_after_counter",
        "failed_count_transaction","list_end_clear_commands","list_end_clear_counter","repeated_lists",
        "persistent_commands","persistent_counter","wrong_device_commands","wrong_device_counter",
        "subpass_retention_commands","subpass_retention_counter" };
    static readonly RenderingDevice.StorageBufferUsage Indirect = (RenderingDevice.StorageBufferUsage)1;
    readonly Dictionary<string,string> _args = new();
    readonly List<(Rid rid,uint bytes)> _owned = new();
    readonly List<string> _operations = new();
    readonly List<object> _rasters = new();
    readonly ProbeLogger _logger = new();
    RenderingDevice _rd = null!;
    RenderingDevice? _local;
    Rid _localBank,_persistent;
    Rid _computeShader,_computePipeline,_computeSet,_plainShader,_readShader,_texture,_framebuffer;
    Rid _indexBuffer,_firstIndices,_secondIndices,_commandsA,_counterA,_commandsB,_counterB,_safe;
    Rid _commandsSet,_counterSet,_safeSet;
    readonly List<Rid> _plainPipelines = new(), _readPipelines = new();
    int _checks,_localOwned,_localDevices;
    bool _listActive;
    string _case = "", _failure = "";

    public override async void _Ready()
    {
        OS.AddLogger(_logger);
        foreach (string argument in OS.GetCmdlineUserArgs()) {
            string[] pair = argument.TrimStart('-').Split('=',2);
            _args[pair[0]] = pair.Length == 2 ? pair[1] : "true";
        }
        try {
            _case = Required("case");
            Check(Cases.Contains(_case),"Unknown named direct case.");
            Check(BitConverter.IsLittleEndian,"Little-endian commands required.");
            Check(Engine.GetVersionInfo()["hash"].AsString() == Required("expected-engine-hash"),"Native source identity differs.");
            Check(SdkVersion == Required("expected-sdk-version"),"Compiled SDK differs.");
            Check(RenderingServer.GetCurrentRenderingDriverName() == "vulkan" && RenderingServer.GetCurrentRenderingMethod() == "forward_plus","Vulkan/Forward+ required.");
            _rd = RenderingServer.GetRenderingDevice() ?? throw new Exception("Global RD unavailable.");
            Engine.MaxFps = 60;
            await OnRender(() => {
                Check(_rd.HasFeature(RenderingDevice.Features.DrawIndirectCount) &&
                    _rd.LimitGet(RenderingDevice.Limit.MaxDrawIndirectCount) >= 1,"Enabled indirect count is a prerequisite; unsupported is not a positive hardening result.");
                CreateResources();
                ExecuteCase();
            });
        } catch (Exception error) { _failure = error.ToString(); GD.PushError(_failure); }
        finally {
            try {
                if (_rd != null) await OnRender(Cleanup);
                Check(_owned.Count == 0 && _localOwned == 0 && _localDevices == 0,"Final caller resource ledger is not empty.");
                Check(_logger.Dropped == 0,"Diagnostic ledger overflowed.");
                Check(_logger.Details.Length == ExpectedDiagnosticCount,"Unexpected diagnostic count.");
                Check(_logger.Details.All(item => item.operation.Length > 0 && item.kind == "error" && item.error_type == 0 &&
                    item.function == AliasFunction && item.code == AliasMessage && item.message == AliasMessage && item.rationale.Length == 0),
                    "Unexpected or unscoped diagnostic.");
            } catch (Exception error) { _failure += "\nCleanup/diagnostics: "+error; GD.PushError(error.ToString()); }
            try { WriteResult(); }
            catch (Exception error) {
                GD.PushError(error.ToString());
                try { File.WriteAllBytes(Path.GetFullPath(Required("record")),JsonSerializer.SerializeToUtf8Bytes(new {
                    contract = "e10-hardening-direct-v1",case_name = _case,passed = false,failure = error.ToString() })); }
                finally { OS.RemoveLogger(_logger); _logger.Dispose(); GetTree().Quit(2); }
            }
        }
    }

    string Required(string key) => _args.TryGetValue(key,out string? value) && value.Length > 0 ? value : throw new Exception("Missing --"+key);
    static string SdkVersion => typeof(DirectHardeningContract).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>().SingleOrDefault(item => item.Key == "GodotSdkVersion")?.Value ?? "";
    static string BytesHash(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    static string Hash(string path) => BytesHash(File.ReadAllBytes(path));
    static byte[] Words(params uint[] words) => MemoryMarshal.AsBytes(words.AsSpan()).ToArray();
    void Check(bool condition,string message) { _checks++; if (!condition) throw new Exception(message); }
    void Step(string operation) { Check(_operations.Count < 128,"Operation capacity exceeded."); _operations.Add(operation); }
    int ExpectedDiagnosticCount => _case == "repeated_lists" ? 3 : _case.Contains("shader_after_") || _case.StartsWith("subpass_retention_") ? 1 : 0;
    string Bank => _case.EndsWith("counter") ? "counter" : "commands";
    async Task OnRender(Action action) {
        var done = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        RenderingServer.CallOnRenderThread(Callable.From(() => { try { action(); done.SetResult(); } catch (Exception error) { done.SetException(error); } }));
        await done.Task;
    }
    Rid Own(Rid rid,uint bytes = 0) { Check(rid.IsValid,"RD allocation failed."); _owned.Add((rid,bytes)); return rid; }
    Rid Shader(string vertex) {
        using var source = new RDShaderSource { SourceVertex = Godot.FileAccess.GetFileAsString("res://"+vertex),
            SourceFragment = Godot.FileAccess.GetFileAsString("res://DrawFragment.glslinc") };
        using var spirv = _rd.ShaderCompileSpirVFromSource(source);
        Check(string.IsNullOrWhiteSpace(spirv.CompileErrorVertex) && string.IsNullOrWhiteSpace(spirv.CompileErrorFragment),spirv.CompileErrorVertex+spirv.CompileErrorFragment);
        return Own(_rd.ShaderCreateFromSpirV(spirv));
    }
    Rid Set(Rid shader,params Rid[] buffers) {
        var uniforms = new Godot.Collections.Array<RDUniform>(); using var lifetime = (Godot.Collections.Array)uniforms;
        var items = new List<RDUniform>();
        try {
            for (int i = 0; i < buffers.Length; i++) {
                var uniform = new RDUniform { UniformType = RenderingDevice.UniformType.StorageBuffer,Binding = i };
                uniform.AddId(buffers[i]); uniforms.Add(uniform); items.Add(uniform);
            }
            return Own(_rd.UniformSetCreate(uniforms,shader,0));
        } finally { foreach (var item in items) item.Dispose(); }
    }
    Rid Pipeline(Rid shader,uint pass) {
        using var raster = new RDPipelineRasterizationState { CullMode = RenderingDevice.PolygonCullMode.Disabled };
        using var samples = new RDPipelineMultisampleState(); using var depth = new RDPipelineDepthStencilState();
        using var attachment = new RDPipelineColorBlendStateAttachment();
        var attachments = new Godot.Collections.Array<RDPipelineColorBlendStateAttachment> { attachment }; using var lifetime = (Godot.Collections.Array)attachments;
        using var blend = new RDPipelineColorBlendState { Attachments = attachments };
        return Own(_rd.RenderPipelineCreate(shader,_rd.FramebufferGetFormat(_framebuffer),-1,RenderingDevice.RenderPrimitive.Triangles,
            raster,samples,depth,blend,(RenderingDevice.PipelineDynamicStateFlags)0,pass));
    }
    void CreateResources()
    {
        _plainShader = Shader("DrawVertex.glslinc"); _readShader = Shader("ReadVertex.glslinc");
        using (var source = new RDShaderSource { SourceCompute = Godot.FileAccess.GetFileAsString("res://Producer.glslinc") }) {
            using var spirv = _rd.ShaderCompileSpirVFromSource(source);
            Check(string.IsNullOrWhiteSpace(spirv.CompileErrorCompute),spirv.CompileErrorCompute);
            _computeShader = Own(_rd.ShaderCreateFromSpirV(spirv)); _computePipeline = Own(_rd.ComputePipelineCreate(_computeShader));
        }
        using var format = new RDTextureFormat { Format = RenderingDevice.DataFormat.R8G8B8A8Unorm,
            Width = Width,Height = Height,Depth = 1,ArrayLayers = 1,Mipmaps = 1,TextureType = RenderingDevice.TextureType.Type2D,
            UsageBits = RenderingDevice.TextureUsageBits.ColorAttachmentBit | RenderingDevice.TextureUsageBits.CanCopyFromBit };
        using var view = new RDTextureView();
        _texture = Own(_rd.TextureCreate(format,view),Width*Height*4);
        var textures = new Godot.Collections.Array<Rid> { _texture }; using var texturesLifetime = (Godot.Collections.Array)textures;
        bool subpasses = _case.StartsWith("subpass_retention_");
        if (subpasses) {
            using var first = new RDFramebufferPass { ColorAttachments = new[] { 0 },DepthAttachment = -1 };
            using var second = new RDFramebufferPass { ColorAttachments = new[] { 0 },DepthAttachment = -1 };
            var passes = new Godot.Collections.Array<RDFramebufferPass> { first,second }; using var passLifetime = (Godot.Collections.Array)passes;
            _framebuffer = Own(_rd.FramebufferCreateMultipass(textures,passes));
        } else _framebuffer = Own(_rd.FramebufferCreate(textures));
        for (uint pass = 0; pass < (subpasses ? 2u : 1u); pass++) {
            _plainPipelines.Add(Pipeline(_plainShader,pass)); _readPipelines.Add(Pipeline(_readShader,pass));
        }
        _indexBuffer = Own(_rd.IndexBufferCreate(12,RenderingDevice.IndexBufferFormat.Uint32,Words(0,1,2,3,4,5,6,7,8,9,10,11)),48);
        _firstIndices = Own(_rd.IndexArrayCreate(_indexBuffer,0,6)); _secondIndices = Own(_rd.IndexArrayCreate(_indexBuffer,6,6));
        // Legal empty initialization; GPU publication occurs before any consuming list.
        _commandsA = Own(_rd.StorageBufferCreate(32,Words(6,0,0,0,0,0,0,0),Indirect),32);
        _counterA = Own(_rd.StorageBufferCreate(16,Words(0,0,0,0),Indirect),16);
        _commandsB = Own(_rd.StorageBufferCreate(32,Words(6,0,0,0,0,0,0,0),Indirect),32);
        _counterB = Own(_rd.StorageBufferCreate(16,Words(0,0,0,0),Indirect),16);
        _safe = Own(_rd.StorageBufferCreate(16,Words(2,0,0,0)),16);
        _commandsSet = Set(_readShader,_commandsA); _counterSet = Set(_readShader,_counterA); _safeSet = Set(_readShader,_safe);
        _computeSet = Set(_computeShader,_commandsA,_counterA,_commandsB,_counterB);
    }
    Rid BankSet(string bank) => bank == "commands" ? _commandsSet : bank == "counter" ? _counterSet : _safeSet;
    void Produce(uint count) {
        Check(count <= 1,"Producer exceeds configured maximum."); Step("produce:"+count);
        long list = _rd.ComputeListBegin(); _rd.ComputeListBindComputePipeline(list,_computePipeline); _rd.ComputeListBindUniformSet(list,_computeSet,0);
        _rd.ComputeListSetPushConstant(list,Words(count,0,0,0),16); _rd.ComputeListDispatch(list,1,1,1); _rd.ComputeListEnd();
    }
    long Begin(string name) {
        Step("begin:"+name); Check(!_listActive,"A draw list was already active.");
        long list = _rd.DrawListBegin(_framebuffer,RenderingDevice.DrawFlags.ClearColorAll,new[] { Colors.Black }); _listActive = true; return list;
    }
    void End() { Step("end"); _rd.DrawListEnd(); _listActive = false; }
    void Plain(long list,int pass = 0) { Step("bind:plain:"+pass); _rd.DrawListBindRenderPipeline(list,_plainPipelines[pass]); _rd.DrawListBindIndexArray(list,_firstIndices); }
    void Read(long list,string bank,int pass = 0) {
        Step("bind:read:"+bank+":"+pass); _rd.DrawListBindRenderPipeline(list,_readPipelines[pass]); _rd.DrawListBindIndexArray(list,_secondIndices);
        _rd.DrawListBindUniformSet(list,BankSet(bank),0);
    }
    void ReadAgain(long list,string bank) { Step("bind:alias-again:"+bank); _rd.DrawListBindUniformSet(list,BankSet(bank),0); }
    void DrawRead(long list,string bank) { Step("draw:read:"+bank); _rd.DrawListDraw(list,true,1); }
    void Count(long list,string operation,Error expected,Rid commands,Rid counter) {
        Step(operation);
        Error result = _rd.DrawListDrawIndirectCount(list,true,commands,0,counter,0,1,20);
        Check(result == expected,"Counted call returned "+result+" for "+operation);
    }
    void CountPrimary(long list) => Count(list,"count:primary:ok",Error.Ok,_commandsA,_counterA);
    void CountAlternate(long list) => Count(list,"count:alternate:ok",Error.Ok,_commandsB,_counterB);
    void RejectAlias(long list,string bank) {
        string operation = "reject:alias:"+bank; Step(operation);
        int before = _logger.Details.Length;
        _logger.BeginExpected(operation);
        try { _rd.DrawListBindUniformSet(list,BankSet(bank),0); }
        finally { _logger.EndExpected(); }
        var observed = _logger.Details;
        Check(observed.Length == before+1,"Expected exactly one synchronous public alias-bind diagnostic.");
        var diagnostic = observed[^1];
        Check(diagnostic.operation == operation && diagnostic.kind == "error" && diagnostic.error_type == 0 &&
            diagnostic.function == AliasFunction && diagnostic.code == AliasMessage && diagnostic.message == AliasMessage && diagnostic.rationale.Length == 0,
            "Alias bind produced a different diagnostic.");
    }

    void ExecuteCase()
    {
        Produce(_case.StartsWith("zero_") ? 0u : 1u);
        if (_case.StartsWith("shader_pending_")) {
            long list = Begin("main");
            Read(list,Bank);
            Count(list,"count:"+Bank+":pending:invalid",Error.InvalidParameter,_commandsA,_counterA);
            ReadAgain(list,Bank); DrawRead(list,Bank); Plain(list); CountAlternate(list); End(); Snapshot("final");
        } else if (_case.StartsWith("shader_recorded_")) {
            long list = Begin("main");
            Read(list,Bank); DrawRead(list,Bank); Plain(list);
            Count(list,"count:"+Bank+":recorded:invalid",Error.InvalidParameter,_commandsA,_counterA);
            Read(list,Bank); DrawRead(list,Bank); Plain(list); CountAlternate(list); End(); Snapshot("final");
        } else if (_case.Contains("shader_after_")) {
            long list = Begin("main"); Plain(list); CountPrimary(list); Read(list,"safe"); RejectAlias(list,Bank); DrawRead(list,"safe"); End(); Snapshot("final");
        } else if (_case == "failed_count_transaction") {
            long list = Begin("main"); Plain(list);
            Count(list,"count:invalid-counter:invalid",Error.InvalidParameter,_commandsA,default);
            Read(list,"commands"); DrawRead(list,"commands"); Plain(list); CountAlternate(list); End(); Snapshot("final");
        } else if (_case.StartsWith("list_end_clear_")) {
            long list = Begin("first"); Plain(list); CountPrimary(list); End();
            list = Begin("second"); Read(list,Bank); DrawRead(list,Bank); End(); Snapshot("final");
        } else if (_case == "repeated_lists") {
            for (int iteration = 0; iteration < 3; iteration++) {
                long list = Begin("repeat"+iteration);
                // Binding this bank before reserving it must succeed again in every new list.
                Read(list,"commands"); Plain(list); CountPrimary(list); Read(list,"safe"); RejectAlias(list,"commands"); DrawRead(list,"safe"); End(); Snapshot("repeat"+iteration);
            }
        } else if (_case.StartsWith("persistent_")) {
            Step("create:persistent");
            // Native flag 1<<2 exists but is deliberately not named in ClassDB.
            _persistent = Own(_rd.StorageBufferCreate(32,Array.Empty<byte>(),Indirect,(RenderingDevice.BufferCreationBits)4),32);
            long list = Begin("main"); Plain(list);
            Count(list,"count:persistent-"+Bank+":invalid",Error.InvalidParameter,Bank == "commands" ? _persistent : _commandsA,Bank == "counter" ? _persistent : _counterA);
            CountPrimary(list); End(); Snapshot("final");
            Step("free:persistent"); FreeOwned(_persistent); _persistent = default;
        } else if (_case.StartsWith("wrong_device_")) {
            Step("create:local-device"); _local = _rd.CreateLocalDevice(); Check(_local != null,"Local RD creation failed."); _localDevices++;
            Step("create:local-bank"); _localBank = _local!.StorageBufferCreate(32,Array.Empty<byte>(),Indirect); Check(_localBank.IsValid,"Local bank allocation failed."); _localOwned++;
            long list = Begin("main"); Plain(list);
            Count(list,"count:foreign-"+Bank+":invalid",Error.InvalidParameter,Bank == "commands" ? _localBank : _commandsA,Bank == "counter" ? _localBank : _counterA);
            CountPrimary(list); End(); Snapshot("final");
            Step("free:local-bank"); _local.FreeRid(_localBank); _localBank = default; _localOwned--;
            Step("free:local-device"); _local.Free(); _local = null; _localDevices--;
        } else if (_case.StartsWith("subpass_retention_")) {
            long list = Begin("main"); Plain(list); CountPrimary(list);
            Step("next:subpass"); list = _rd.DrawListSwitchToNextPass();
            Read(list,"safe",1); RejectAlias(list,Bank); DrawRead(list,"safe"); End(); Snapshot("final");
        } else throw new Exception("Unimplemented named case.");
    }

    int[][] ExpectedPixels()
    {
        int[] red = {255,0,0,255},green = {0,255,0,255},cyan = {0,255,255,255},black = {0,0,0,255};
        int[] marker = Bank == "commands" ? cyan : red;
        if (_case.StartsWith("shader_pending_") || _case.StartsWith("shader_recorded_")) return new[] {red,marker};
        if (_case.StartsWith("list_end_clear_")) return new[] {black,marker};
        if (_case == "failed_count_transaction") return new[] {red,cyan};
        if (_case.StartsWith("persistent_") || _case.StartsWith("wrong_device_")) return new[] {red,black};
        return new[] {_case.StartsWith("zero_") ? black : red,green};
    }
    void Snapshot(string probe)
    {
        Step("readback:"+probe);
        byte[] pixels = _rd.TextureGetData(_texture,0);
        Check(pixels.Length == Width*Height*4,"RGBA8 texture dimensions differ.");
        uint counter = BitConverter.ToUInt32(_rd.BufferGetData(_counterA),0);
        Check(counter == (_case.StartsWith("zero_") ? 0u : 1u),"Published counter did not survive consumption.");
        int[][] expected = ExpectedPixels(); var samples = new List<object>();
        for (int cell = 0; cell < 2; cell++) {
            int x = 8+cell*16,y = 8,offset = (y*Width+x)*4;
            int[] actual = pixels.Skip(offset).Take(4).Select(value => (int)value).ToArray();
            Check(actual.Length == 4 && actual.Zip(expected[cell],(a,b) => Math.Abs(a-b) <= 1).All(value => value),"Valid subsequent draw numerical pixels differ.");
            samples.Add(new {x,y,actual_rgba = actual});
        }
        Check(_rasters.Count < 4,"Raster ledger capacity exceeded.");
        _rasters.Add(new {probe,samples,sha256 = BytesHash(pixels)});
    }
    void FreeOwned(Rid rid)
    {
        int index = _owned.FindIndex(item => item.rid == rid); Check(index >= 0,"Attempted global free of unowned or foreign RID.");
        _rd.FreeRid(rid); _owned.RemoveAt(index);
    }
    void Cleanup()
    {
        if (_listActive) { _rd.DrawListEnd(); _listActive = false; }
        // A foreign bank is freed exclusively through its allocating local device on this same thread.
        if (_local != null) {
            if (_localBank.IsValid) { _local.FreeRid(_localBank); _localBank = default; _localOwned--; }
            _local.Free(); _local = null; _localDevices--;
        }
        // Explicit readback drains this bounded stand. This is not a reusable caller fence API.
        if (_texture.IsValid) _rd.TextureGetData(_texture,0);
        foreach (var item in _owned.ToArray().Reverse()) FreeOwned(item.rid);
        _persistent = default;
    }
    string RuntimeAssemblyHash(Assembly assembly,string fallback)
    {
        string path = string.IsNullOrEmpty(assembly.Location) ? fallback : assembly.Location;
        using var stream = File.OpenRead(path); using var pe = new PEReader(stream);
        MetadataReader metadata = pe.GetMetadataReader();
        Check(metadata.GetGuid(metadata.GetModuleDefinition().Mvid) == assembly.ManifestModule.ModuleVersionId,"Loaded managed module differs from file.");
        return Hash(path);
    }
    void WriteResult()
    {
        string fixture = ProjectSettings.GlobalizePath("res://.godot/mono/temp/bin/Debug/DirectHardeningLab.dll");
        string api = Path.Combine(Path.GetDirectoryName(OS.GetExecutablePath())!,"GodotSharp/Api/Debug/GodotSharp.dll");
        bool passed = _failure.Length == 0 && _owned.Count == 0 && _localOwned == 0 && _localDevices == 0 && _logger.Dropped == 0;
        object[] expectedDiagnostics = ExpectedDiagnosticCount == 0 ? Array.Empty<object>() : new object[] {
            new {kind = "error",message = AliasMessage,count = ExpectedDiagnosticCount} };
        var result = new {
            contract = "e10-hardening-direct-v1",case_name = _case,passed,failure = _failure,checks = _checks,
            engine_hash = Engine.GetVersionInfo()["hash"].AsString(),sdk_version = SdkVersion,
            driver = RenderingServer.GetCurrentRenderingDriverName(),renderer = RenderingServer.GetCurrentRenderingMethod(),gpu_name = RenderingServer.GetVideoAdapterName(),
            operations = _operations,rasters = _rasters,expected_diagnostics = expectedDiagnostics,observed_diagnostics = _logger.Observed,
            observed_diagnostic_details = _logger.Details,diagnostic_ledger_dropped = _logger.Dropped,
            resources = new {caller_owned_final = _owned.Count,caller_requested_bytes_final = _owned.Sum(item => (long)item.bytes),
                local_owned_final = _localOwned,local_devices_final = _localDevices},
            source_sha256 = Inputs.ToDictionary(name => name,name => Hash(ProjectSettings.GlobalizePath("res://"+name))),
            managed_fixture_assembly_sha256 = RuntimeAssemblyHash(typeof(DirectHardeningContract).Assembly,fixture),
            managed_api_assembly_sha256 = RuntimeAssemblyHash(typeof(Node).Assembly,api),
            image_files_written = 0,performance_window_count = 0,performance_qualified = false,
            not_qualified = new[] {"No native MultiMesh renderer/pass/cache recovery qualification in this direct consumer",
                "No public vertex/index alias case: counted storage RIDs cannot be created with vertex/index owner and INDIRECT usage through the current public API",
                "No FPS, timing, physical VRAM, sustained lifetime plateau or simulation/networking result",
                "No unsupported-capability positive qualification; enabled draw count is a required prerequisite"}
        };
        string path = Path.GetFullPath(Required("record")); Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllBytes(path,JsonSerializer.SerializeToUtf8Bytes(result,new JsonSerializerOptions {WriteIndented = true}));
        GD.Print("Direct hardening result "+path);
        OS.RemoveLogger(_logger); _logger.Dispose(); GetTree().Quit(passed ? 0 : 2);
    }
}

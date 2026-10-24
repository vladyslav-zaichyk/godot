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

// Native Forward+ hardening only. Root's validator owns the independent oracle.
public partial class NativeHardeningContract : Node
{
    const string PinnedEngine = "00e1b43953e4b0bb136fe397782c9e1d030aa836";
    const string PinnedSdk = "4.7.2-arena-dev-g00e1b.43953";
    const string BindingWarning = "Forward+ skipped a draw after checked binding or counted submission failed (Error 31).";
    const uint Poison = 0xcdcdcdcd;
    const int Owners = 3, Slots = 48, K = 3, CommandBytes = 144, CounterBytes = 32, Width = 64, Height = 48;
    static readonly string[] Inputs = { "NativeHardeningLab.csproj", "project.godot", "Main.tscn", "NativeHardeningContract.cs",
        "NativeLogger.cs", "Consumer.gdshader", "Producer.glslinc", "oracle.json" };
    static readonly string[] CaseNames = { "native_self_command_alias_recovery", "native_self_counter_alias_recovery",
        "native_checked_command_bind_recovery", "native_checked_counter_bind_recovery", "native_material_replacement",
        "native_count_k_zero_one", "native_borrower_lifecycle", "native_buffer_plateau" };
    readonly Dictionary<string,string> _args = new();
    readonly List<(Rid rid,uint bytes)> _owned = new();
    readonly List<ArrayMesh> _meshes = new();
    readonly List<ShaderMaterial> _materials = new();
    readonly List<string> _operations = new();
    readonly List<object> _rasters = new(), _ordering = new();
    readonly List<ulong> _logicalSamples = new();
    readonly NativeLogger _logger = new();
    readonly Rid[] _commands = new Rid[Owners], _counters = new Rid[Owners], _multimeshes = new Rid[Owners], _instances = new Rid[Owners];
    readonly List<BufferRD> _wrappers = new();
    RenderingDevice _rd = null!;
    Shader? _shader;
    ShaderMaterial? _sharedMaterial, _borrowerMaterial;
    SubViewport? _view;
    ViewportTexture? _texture;
    World3D? _world;
    Rid _producer, _pipeline, _producerSet, _ids, _marker, _borrower, _borrowerInstance;
    BufferRD? _idWrapper, _markerWrapper;
    string _case = "", _failure = "";
    int _checks, _readbacks, _dispatches, _borrowerCycles;

    public override async void _Ready()
    {
        OS.AddLogger(_logger);
        foreach (string argument in OS.GetCmdlineUserArgs()) {
            string[] pair = argument.TrimStart('-').Split('=',2);
            _args[pair[0]] = pair.Length == 2 ? pair[1] : "true";
        }
        try {
            _case = Required("case"); Check(CaseNames.Contains(_case), "Unknown isolated case.");
            Check(BitConverter.IsLittleEndian && (int)Error.InvalidParameter == 31, "Unexpected wire/error constants.");
            Check(Required("expected-engine-hash") == PinnedEngine && Engine.GetVersionInfo()["hash"].AsString() == PinnedEngine,
                "This fixture requires the exact frozen E10b native source.");
            Check(Required("expected-sdk-version") == PinnedSdk && SdkVersion == PinnedSdk, "Exact frozen E10b SDK required.");
            Check(RenderingServer.GetCurrentRenderingDriverName() == "vulkan" && RenderingServer.GetCurrentRenderingMethod() == "forward_plus", "Vulkan/Forward+ required.");
            _rd = RenderingServer.GetRenderingDevice() ?? throw new Exception("Global RD unavailable.");
            Engine.MaxFps = 120; BuildScene();
            await OnRender(() => {
                Check(_rd.HasFeature(RenderingDevice.Features.DrawIndirectCount) && _rd.LimitGet(RenderingDevice.Limit.MaxDrawIndirectCount) >= K,
                    "The native positive subset requires enabled count drawing and maximum at least three.");
                CreateBanksAndOwners();
            });
            Step("setup");
            await Publish(K); Step("prewarm_k"); await Capture("prewarm");
            if (_case.Contains("_alias_recovery") || _case.Contains("_bind_recovery")) await AliasCase();
            else if (_case == "native_material_replacement") await MaterialReplacement();
            else if (_case == "native_count_k_zero_one") await CountTransitions();
            else if (_case == "native_borrower_lifecycle") await BorrowerLifecycle();
            else if (_case == "native_buffer_plateau") await Plateau();
        } catch (Exception error) { _failure = error.ToString(); GD.PushError(_failure); }
        finally {
            try { await Cleanup(); Step("cleanup"); }
            catch (Exception error) { _failure += "\nCleanup: " + error; GD.PushError(error.ToString()); }
            try { WriteResult(); }
            catch (Exception error) {
                GD.PushError(error.ToString());
                try { File.WriteAllBytes(Path.GetFullPath(Required("record")), JsonSerializer.SerializeToUtf8Bytes(new {
                    contract="e10-hardening-native-v1",case_name=_case,passed=false,failure=error.ToString() })); }
                finally { OS.RemoveLogger(_logger); _logger.Dispose(); GetTree().Quit(2); }
            }
        }
    }

    string Required(string key) => _args.TryGetValue(key,out string? value) && value.Length>0 ? value : throw new Exception("Missing --"+key);
    static string SdkVersion => typeof(NativeHardeningContract).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>().SingleOrDefault(a=>a.Key=="GodotSdkVersion")?.Value ?? "";
    static string BytesHash(byte[] value) => Convert.ToHexString(SHA256.HashData(value)).ToLowerInvariant();
    static string Hash(string path) => BytesHash(File.ReadAllBytes(path));
    static byte[] Words(IEnumerable<uint> words) => MemoryMarshal.AsBytes(words.ToArray().AsSpan()).ToArray();
    static uint LocalMeshIndex(ArrayMesh mesh) => (uint)(mesh.GetRid().Id & uint.MaxValue);
    void Check(bool value,string message) { _checks++; if(!value) throw new Exception(message); }
    void Step(string value) => _operations.Add(value);
    async Task OnRender(Action action) {
        var done=new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        RenderingServer.CallOnRenderThread(Callable.From(()=>{try{action();done.SetResult();}catch(Exception error){done.SetException(error);}}));
        await done.Task;
    }
    async Task Frames(int count) { for(int i=0;i<count;i++) await ToSignal(RenderingServer.Singleton,RenderingServer.SignalName.FramePostDraw); }
    // Queue pacing, never a fence claim. Capture() completes texture and storage readbacks.
    async Task Pace() { if(_rd!=null) await Frames(checked((int)_rd.GetFrameDelay()+4)); }
    Rid Own(Rid rid,uint bytes=0) { Check(rid.IsValid,"RD allocation failed."); _owned.Add((rid,bytes)); return rid; }
    void FreeOwned(Rid rid) { if(!rid.IsValid)return;int at=_owned.FindIndex(item=>item.rid==rid);Check(at>=0,"Unowned RID free.");_rd.FreeRid(rid);_owned.RemoveAt(at); }
    BufferRD Wrap(Rid rid) {
        var wrapper=new BufferRD();
        try{Check(wrapper.AttachStorageBuffer(rid)==Error.Ok && wrapper.GetBufferRdRid()==rid,"Borrowed RID differs.");_wrappers.Add(wrapper);return wrapper;}
        catch{wrapper.Dispose();throw;}
    }
    static ArrayMesh Quad() {
        var mesh=new ArrayMesh();
        try{using var arrays=new Godot.Collections.Array();arrays.Resize((int)Godot.Mesh.ArrayType.Max);
            arrays[(int)Godot.Mesh.ArrayType.Vertex]=new[]{new Vector3(-.30f,-.30f,0),new Vector3(.30f,-.30f,0),new Vector3(.30f,.30f,0),new Vector3(-.30f,.30f,0)};
            arrays[(int)Godot.Mesh.ArrayType.Index]=new[]{0,1,2,0,2,3};mesh.AddSurfaceFromArrays(Godot.Mesh.PrimitiveType.Triangles,arrays);return mesh;
        }catch{mesh.Dispose();throw;}
    }
    void BuildScene() {
        _shader=GD.Load<Shader>("res://Consumer.gdshader")??throw new Exception("Missing native consumer shader.");
        for(int i=0;i<Owners;i++)_meshes.Add(Quad());
        _meshes.Sort((a,b)=>LocalMeshIndex(a).CompareTo(LocalMeshIndex(b)));
        for(int i=1;i<Owners;i++)Check(LocalMeshIndex(_meshes[i-1])<LocalMeshIndex(_meshes[i]),"Native geometry sort indices are not strictly ordered.");
        _world=new World3D();_view=new SubViewport{Size=new Vector2I(Width,Height),World3D=_world,TransparentBg=false,RenderTargetUpdateMode=SubViewport.UpdateMode.Always};AddChild(_view);
        _view.AddChild(new Camera3D{Projection=Camera3D.ProjectionType.Orthogonal,Size=3,KeepAspect=Camera3D.KeepAspectEnum.Height,
            Near=.1f,Far=10,Position=new Vector3(0,0,2),Current=true});_texture=_view.GetTexture();
    }
    ShaderMaterial Material(BufferRD probe,int priority=0,Vector2? offset=null) {
        var material=new ShaderMaterial{Shader=_shader,RenderPriority=priority};_materials.Add(material);
        using Variant ids=Variant.From(_idWrapper!);using Variant alias=Variant.From(probe);
        material.SetShaderParameter("visible_ids",ids);material.SetShaderParameter("probe_alias",alias);
        material.SetShaderParameter("visual_offset",offset??Vector2.Zero);return material;
    }
    Error Attach(Rid mm,int owner) => RenderingServer.MultimeshAttachSharedDrawCommandSpanCount(mm,Slots,_commands[owner],16,_counters[owner],4,K,32);
    void CreateBanksAndOwners() {
        using(var source=new RDShaderSource{SourceCompute=Godot.FileAccess.GetFileAsString("res://Producer.glslinc")}) {
            using var spirv=_rd.ShaderCompileSpirVFromSource(source);Check(string.IsNullOrWhiteSpace(spirv.CompileErrorCompute),spirv.CompileErrorCompute);
            _producer=Own(_rd.ShaderCreateFromSpirV(spirv));_pipeline=Own(_rd.ComputePipelineCreate(_producer));
        }
        _ids=Own(_rd.StorageBufferCreate(Slots*4,Words(Enumerable.Repeat(uint.MaxValue,Slots))),Slots*4);
        _marker=Own(_rd.StorageBufferCreate(16,Words(Enumerable.Repeat(Poison,4))),16);
        for(int owner=0;owner<Owners;owner++) {
            uint[] commands=Enumerable.Repeat(Poison,CommandBytes/4).ToArray();
            for(int record=0;record<=K;record++)Array.Copy(new uint[]{6,0,0,0,(uint)(owner*16+1+record*3)},0,commands,4+record*8,5);
            _commands[owner]=Own(_rd.StorageBufferCreate(CommandBytes,Words(commands),(RenderingDevice.StorageBufferUsage)1),CommandBytes);
            uint[] counter=Enumerable.Repeat(Poison,CounterBytes/4).ToArray();counter[1]=0;
            _counters[owner]=Own(_rd.StorageBufferCreate(CounterBytes,Words(counter),(RenderingDevice.StorageBufferUsage)1),CounterBytes);
        }
        _idWrapper=Wrap(_ids);_markerWrapper=Wrap(_marker);
        bool checkedBind=_case.Contains("_bind_recovery");
        if(!checkedBind)_sharedMaterial=Material(_markerWrapper);
        int[] priorities={-120,-80,80};
        for(int owner=0;owner<Owners;owner++) {
            ShaderMaterial material=checkedBind?Material(_markerWrapper,priorities[owner]):_sharedMaterial!;
            Rid mm=RenderingServer.MultimeshCreate();_multimeshes[owner]=mm;
            RenderingServer.MultimeshSetMesh(mm,_meshes[owner].GetRid());
            RenderingServer.MultimeshSetCustomAabb(mm,new Aabb(new Vector3(-4,-3,-.1f),new Vector3(8,6,.2f)));
            Check(Attach(mm,owner)==Error.Ok,"Native counted attach failed.");CheckNativeState(mm,owner);
            Rid instance=RenderingServer.InstanceCreate();_instances[owner]=instance;ConfigureInstance(instance,mm,material);
        }
        _ordering.Add(new{proof="source-derived opaque sort and matching pipeline key; actual pipeline RID/order not instrumented",
            method=checkedBind?"priority high8 bits of sort_key2":"same material/shader key2; ascending mesh local geometry index in key1",
            mesh_local_indices=_meshes.Select(LocalMeshIndex).ToArray(),priorities=checkedBind?priorities:new[]{0,0,0},
            same_shader=true,same_material=!checkedBind,identical_vertex_format_and_primitive=true});
        Rid[] buffers={_ids,_commands[0],_counters[0],_commands[1],_counters[1],_commands[2],_counters[2]};
        var uniforms=new Godot.Collections.Array<RDUniform>();using var lifetime=(Godot.Collections.Array)uniforms;var items=new List<RDUniform>();
        try{for(int i=0;i<buffers.Length;i++){var uniform=new RDUniform{UniformType=RenderingDevice.UniformType.StorageBuffer,Binding=i};uniform.AddId(buffers[i]);uniforms.Add(uniform);items.Add(uniform);}
            _producerSet=Own(_rd.UniformSetCreate(uniforms,_producer,0));}finally{foreach(var item in items)item.Dispose();}
    }
    void ConfigureInstance(Rid instance,Rid mm,ShaderMaterial material) {
        RenderingServer.InstanceSetBase(instance,mm);RenderingServer.InstanceSetTransform(instance,Transform3D.Identity);
        RenderingServer.InstanceSetScenario(instance,_world!.Scenario);RenderingServer.InstanceGeometrySetMaterialOverride(instance,material.GetRid());
        RenderingServer.InstanceGeometrySetCastShadowsSetting(instance,RenderingServer.ShadowCastingSetting.Off);RenderingServer.InstanceSetVisible(instance,true);
    }
    void CheckNativeState(Rid mm,int owner) {
        Check(RenderingServer.MultimeshGetCommandBufferRdRid(mm)==_commands[owner] && RenderingServer.MultimeshGetSharedDrawCountBufferRdRid(mm)==_counters[owner]
            && RenderingServer.MultimeshGetSharedDrawCountByteOffset(mm)==4 && RenderingServer.MultimeshGetSharedDrawCommandCount(mm)==K
            && RenderingServer.MultimeshIsShaderDriven(mm) && !RenderingServer.MultimeshGetBufferRdRid(mm).IsValid
            && !RenderingServer.MultimeshUsesShaderMotionVectors(mm),"Native caller bank/history metadata differs.");
    }
    async Task Publish(int count) {
        await OnRender(()=>{for(uint stage=0;stage<3;stage++){
            long list=_rd.ComputeListBegin();_rd.ComputeListBindComputePipeline(list,_pipeline);_rd.ComputeListBindUniformSet(list,_producerSet,0);
            byte[] push=Words(new uint[]{stage,(uint)count,0,0});_rd.ComputeListSetPushConstant(list,push,16);_rd.ComputeListDispatch(list,Owners,1,1);_rd.ComputeListEnd();_dispatches++;
        }});
    }
    async Task AliasCase() {
        bool checkedBind=_case.Contains("_bind_recovery"),counter=_case.Contains("counter");
        await OnRender(()=>{
            BufferRD alias=Wrap(counter?_counters[0]:_commands[0]);
            using Variant value=Variant.From(alias);(checkedBind?_materials[1]:_sharedMaterial!).SetShaderParameter("probe_alias",value);
        });Step(checkedBind?"alias_leading_reserved_bank":"alias_own_bank");await Capture("alias_skip_recovery");
        await Capture("alias_skip_repeat");
    }
    async Task MaterialReplacement() {
        await OnRender(()=>{using Variant value=Variant.From(Wrap(_commands[0]));_sharedMaterial!.SetShaderParameter("probe_alias",value);});Step("alias_own_bank");await Capture("alias_skip_recovery");
        await OnRender(()=>{for(int owner=1;owner<Owners;owner++)RenderingServer.InstanceSetVisible(_instances[owner],false);});Step("hide_other_owners");
        await OnRender(()=>RenderingServer.InstanceGeometrySetMaterialOverride(_instances[0],Material(_markerWrapper!,-120).GetRid()));Step("replace_owner_material");await Capture("material_recovered");
    }
    async Task CountTransitions() {
        string[] names={"count_k_first","count_zero_first","count_one_first","count_k_repeat","count_zero_repeat","count_one_repeat"};
        int[] counts={K,0,1,K,0,1};for(int i=0;i<names.Length;i++){await Publish(counts[i]);Step(names[i]);await Capture(names[i]);}
    }
    async Task CreateBorrower() {
        await OnRender(()=>{
            _borrowerMaterial=Material(_markerWrapper!,110,new Vector2(0,2));_borrower=RenderingServer.MultimeshCreate();
            RenderingServer.MultimeshSetMesh(_borrower,_meshes[0].GetRid());RenderingServer.MultimeshSetCustomAabb(_borrower,new Aabb(new Vector3(-4,-3,-.1f),new Vector3(8,6,.2f)));
            Check(Attach(_borrower,0)==Error.Ok,"Borrower attach failed.");CheckNativeState(_borrower,0);
            _borrowerInstance=RenderingServer.InstanceCreate();ConfigureInstance(_borrowerInstance,_borrower,_borrowerMaterial);
        });
    }
    async Task DestroyBorrower() {
        if(!_borrower.IsValid)return;
        await OnRender(()=>RenderingServer.InstanceSetVisible(_borrowerInstance,false));await Pace();await CompleteReadbacks();
        await OnRender(()=>{
            RenderingServer.FreeRid(_borrowerInstance);_borrowerInstance=default;RenderingServer.MultimeshDetachSharedBuffers(_borrower);RenderingServer.FreeRid(_borrower);_borrower=default;
            if(_borrowerMaterial!=null){_borrowerMaterial.SetShaderParameter("visible_ids",default(Variant));_borrowerMaterial.SetShaderParameter("probe_alias",default(Variant));_materials.Remove(_borrowerMaterial);_borrowerMaterial.Dispose();_borrowerMaterial=null;}
            CheckNativeState(_multimeshes[0],0);CheckNativeState(_multimeshes[1],1);
        });_borrowerCycles++;
    }
    async Task HideThirdOwner() {await OnRender(()=>RenderingServer.InstanceSetVisible(_instances[2],false));Step("hide_third_owner");await Capture("survivors");}
    async Task BorrowerLifecycle() {
        await HideThirdOwner();await CreateBorrower();Step("borrower_attach_visible");await Capture("borrower_visible");
        await OnRender(()=>{RenderingServer.InstanceSetVisible(_borrowerInstance,false);RenderingServer.MultimeshDetachSharedBuffers(_borrower);
            Check(!RenderingServer.MultimeshGetSharedDrawCountBufferRdRid(_borrower).IsValid && RenderingServer.MultimeshGetSharedDrawCountByteOffset(_borrower)==0,"Borrower detach retained counted fields.");});
        Step("borrower_detach");await Capture("borrower_detached");
        await OnRender(()=>{Check(Attach(_borrower,0)==Error.Ok,"Borrower reattach failed.");CheckNativeState(_borrower,0);RenderingServer.InstanceSetVisible(_borrowerInstance,true);});
        Step("borrower_reattach");await Capture("borrower_reattached");await DestroyBorrower();Step("borrower_destroy");await Capture("survivor_after_destroy");
    }
    async Task Plateau() {
        await HideThirdOwner();
        for(int cycle=0;cycle<32;cycle++) {
            await CreateBorrower();await Pace();await CompleteReadbacks();await DestroyBorrower();
            GC.Collect();GC.WaitForPendingFinalizers();GC.Collect();await Pace();
            Step("retire_cycle_"+cycle.ToString("D2"));await Capture("cycle_"+cycle.ToString("D2"));
            await OnRender(()=>_logicalSamples.Add(_rd.GetMemoryUsage(RenderingDevice.MemoryType.Buffers)));
        }
    }
    async Task CompleteReadbacks() {
        await OnRender(()=>{
            foreach(Rid rid in _commands.Concat(_counters).Append(_ids).Append(_marker).Where(rid=>rid.IsValid)){
                byte[] data=_rd.BufferGetData(rid);Check(data.Length>0,"Caller bank disappeared while a borrower retired.");_readbacks++;
            }
        });
        if(_texture!=null){using Image image=_texture.GetImage();Check(!image.IsEmpty(),"Viewport readback did not complete.");}
    }
    async Task Capture(string probe) {
        await Pace();await CompleteReadbacks();using Image image=_texture!.GetImage();
        Check(image.GetWidth()==Width && image.GetHeight()==Height,"Native numeric viewport dimensions differ.");
        image.Convert(Image.Format.Rgba8);byte[] pixels=image.GetData();Check(pixels.Length==Width*Height*4,"Native RGBA8 bytes differ.");
        var samples=new List<object>();for(int row=0;row<3;row++)for(int column=0;column<4;column++){
            int x=column*16+8,y=Height-(row*16+8),at=(y*Width+x)*4;int[] rgba={pixels[at],pixels[at+1],pixels[at+2],pixels[at+3]};
            samples.Add(new{x,y,actual_rgba=rgba});for(int channel=0;channel<4;channel++)Check(rgba[channel]>=0 && rgba[channel]<=255,"Invalid byte sample.");
        }
        _rasters.Add(new{probe,samples,sha256=BytesHash(pixels)});Step("raster:"+probe);
    }
    async Task Cleanup() {
        if(_rd==null)return;await DestroyBorrower();
        await OnRender(()=>{foreach(Rid instance in _instances.Where(rid=>rid.IsValid))RenderingServer.InstanceSetVisible(instance,false);
            foreach(var material in _materials){material.SetShaderParameter("visible_ids",default(Variant));material.SetShaderParameter("probe_alias",default(Variant));}});
        await Pace();if(_commands.Any(rid=>rid.IsValid))await CompleteReadbacks();
        await OnRender(()=>{
            for(int owner=0;owner<Owners;owner++){
                if(_instances[owner].IsValid){RenderingServer.FreeRid(_instances[owner]);_instances[owner]=default;}
                if(_multimeshes[owner].IsValid){RenderingServer.MultimeshDetachSharedBuffers(_multimeshes[owner]);RenderingServer.FreeRid(_multimeshes[owner]);_multimeshes[owner]=default;}
            }
            FreeOwned(_producerSet);_producerSet=default;
        });
        foreach(var material in _materials)material.Dispose();_materials.Clear();foreach(var wrapper in _wrappers)wrapper.Dispose();_wrappers.Clear();
        await Pace();await OnRender(()=>{foreach(var item in _owned.ToArray().Reverse())FreeOwned(item.rid);});
        _texture?.Dispose();_texture=null;_view?.Free();_view=null;_world?.Dispose();_world=null;foreach(var mesh in _meshes)mesh.Dispose();_meshes.Clear();_shader?.Dispose();_shader=null;
        await Pace();Check(_owned.Count==0 && _materials.Count==0 && _wrappers.Count==0 && _meshes.Count==0
            && _multimeshes.All(rid=>!rid.IsValid) && _instances.All(rid=>!rid.IsValid) && !_borrower.IsValid && !_borrowerInstance.IsValid,
            "Final caller resource ledger not empty.");
        Check(_logger.Dropped==0,"Bounded native diagnostic ledger overflowed.");
    }
    string RuntimeAssemblyHash(Assembly assembly,string fallback) {
        string path=string.IsNullOrEmpty(assembly.Location)?fallback:assembly.Location;using var stream=File.OpenRead(path);using var pe=new PEReader(stream);
        MetadataReader metadata=pe.GetMetadataReader();Check(metadata.GetGuid(metadata.GetModuleDefinition().Mvid)==assembly.ManifestModule.ModuleVersionId,"Loaded module differs from file.");return Hash(path);
    }
    void WriteResult() {
        string fixture=ProjectSettings.GlobalizePath("res://.godot/mono/temp/bin/Debug/NativeHardeningLab.dll");
        string api=Path.Combine(Path.GetDirectoryName(OS.GetExecutablePath())!,"GodotSharp/Api/Debug/GodotSharp.dll");
        bool hasWarning=_case.Contains("_alias_recovery") || _case.Contains("_bind_recovery") || _case=="native_material_replacement";
        object[] expected=hasWarning?new object[]{new{kind="warning",message=BindingWarning,count=1}}:Array.Empty<object>();
        var result=new{contract="e10-hardening-native-v1",case_name=_case,passed=_failure.Length==0 && _owned.Count==0,failure=_failure,checks=_checks,
            engine_hash=Engine.GetVersionInfo()["hash"].AsString(),sdk_version=SdkVersion,driver=RenderingServer.GetCurrentRenderingDriverName(),renderer=RenderingServer.GetCurrentRenderingMethod(),gpu_name=RenderingServer.GetVideoAdapterName(),
            operations=_operations,rasters=_rasters,expected_diagnostics=expected,observed_diagnostics=_logger.Observed,
            diagnostic_details=_logger.Details,dropped_diagnostics=_logger.Dropped,ordering=_ordering,
            resources=new{caller_owned_final=_owned.Count,caller_bytes_final=_owned.Sum(item=>(long)item.bytes),logical_buffer_samples=_logicalSamples,
                warmup_cycles=_case=="native_buffer_plateau"?8:0,measured_cycles=_case=="native_buffer_plateau"?24:0,borrower_cycles=_borrowerCycles,
                logical_buffer_scope="RD native buffer_memory only; no physical VRAM/fence/sustained-production claim"},
            source_sha256=Inputs.ToDictionary(name=>name,name=>Hash(ProjectSettings.GlobalizePath("res://"+name))),
            managed_fixture_assembly_sha256=RuntimeAssemblyHash(typeof(NativeHardeningContract).Assembly,fixture),managed_api_assembly_sha256=RuntimeAssemblyHash(typeof(Node).Assembly,api),
            producer_dispatches=_dispatches,storage_readbacks=_readbacks,image_files_written=0,performance_qualified=false};
        string path=Path.GetFullPath(Required("record"));Directory.CreateDirectory(Path.GetDirectoryName(path)!);File.WriteAllBytes(path,JsonSerializer.SerializeToUtf8Bytes(result,new JsonSerializerOptions{WriteIndented=true}));
        OS.RemoveLogger(_logger);_logger.Dispose();GetTree().Quit(_failure.Length==0?0:2);
    }
}

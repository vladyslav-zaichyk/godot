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
using System.Threading;
using System.Threading.Tasks;

// Numeric GPU contract only. No image files, gameplay, performance window or local RD.
public partial class GpuHistoryContract : Node
{
    const int Width = 128, Height = 64, Slots = 8, MaxEpochs = 128, UboBytes = 2848, SceneBytes = UboBytes * 2;
    const uint Schema = 0xe8000001;
    const float Tolerance = 1f / 4096;
    static readonly string[] Inputs = { "GpuHistoryLab.csproj", "project.godot", "Main.tscn", "GpuHistoryContract.cs",
        "HistoryCapture.cs", "HistoryErrorLogger.cs", "Consumer.gdshader", "Producer.glslinc", "Capture.glslinc" };
    static readonly Vector3[] Vertices = { new(-.375f,-.375f,0), new(.375f,-.375f,0), new(.375f,.375f,0), new(-.375f,.375f,0) };
    static readonly int[][] Triangles = { new[] { 0, 1, 2 }, new[] { 0, 2, 3 } };
    readonly Dictionary<string,string> _args = new();
    readonly Dictionary<Rid,uint> _owned = new();
    readonly HashSet<Rid> _uniformSets = new();
    readonly Dictionary<(ulong,ulong),Rid> _captureSets = new();
    readonly List<BufferRD> _wrappers = new();
    readonly List<object> _api = new(), _epochs = new(), _pixels = new();
    readonly HistoryErrorLogger _errors = new();
    readonly TaskCompletionSource _finished = new(TaskCreationOptions.RunContinuationsAsynchronously);
    RenderingDevice _rd = null!;
    ArrayMesh? _mesh;
    Shader? _shader;
    ShaderMaterial? _material;
    SubViewport? _view;
    Camera3D? _camera;
    World3D? _world;
    HistoryCapture? _effect;
    Compositor? _compositor;
    Rid _current, _previous, _lifecycle, _header, _ids, _commands, _diagnostic;
    Rid _velocityLedger, _sceneLedger, _commandLedger, _sampler, _producerShader, _producerPipeline, _producerSet, _captureShader, _capturePipeline;
    Rid _multimesh, _instance;
    List<Sample> _cases = new();
    readonly List<Publication> _publications = new();
    Publication? _pending;
    Sample? _committed;
    int _capacity, _next, _preparedEpoch, _markedEpoch, _markerCount, _committedEpoch;
    int _checks, _copies, _productions, _captures, _readbacks, _commits, _aborts, _resetFrames, _lifecycleCycles;
    long _liveBytes, _settledBytes;
    volatile bool _running;
    bool _forceReseed = true, _subscribed, _terminalCompleted;
    string _failure = "";

    sealed record Sample(string Name, float X7=-1, float Y7=.5f, float D7=0,
        float X2=1, float Y2=-.5f, float D2=0, uint Gen7=1, uint Gen2=1,
        uint Continuity7=1, uint Continuity2=1, uint Domain=1, uint Representation=1,
        uint IdA=7, uint IdB=2, uint Count=2, uint Tick=0,
        float CameraX=0, float CameraY=0, bool Stale=false, bool Skip=false, bool Taa=false, int Scaling=0)
    {
        public Vector3 Pose(uint id) => id == 2 ? new(X2,Y2,D2) : new(X7,Y7,D7);
        public uint Generation(uint id) => id == 2 ? Gen2 : Gen7;
        public uint Continuity(uint id) => id == 2 ? Continuity2 : Continuity7;
        public uint Error(int capacity) => (Count > Slots-3 ? 1u : 0u) |
            (IdA >= capacity || (Count > 1 && IdB >= capacity) ? 2u : 0u) |
            (Gen7 == 0 || Gen2 == 0 ? 4u : 0u);
    }
    sealed record Publication(int Epoch, int Index, int PreviousEpoch, Sample State, Sample? Previous, bool Reseed)
    {
        public bool History(uint id) => !Reseed && !State.Stale && Previous != null &&
            State.Generation(id) == Previous.Generation(id) && State.Representation == Previous.Representation &&
            State.Domain == Previous.Domain && State.Continuity(id) == Previous.Continuity(id);
    }

    public override async void _Ready()
    {
        OS.AddLogger(_errors);
        foreach (string arg in OS.GetCmdlineUserArgs()) { string[] p=arg.TrimStart('-').Split('=',2); _args[p[0]]=p.Length==2?p[1]:"true"; }
        try {
            _capacity = _args.TryGetValue("capacity",out string? n) ? int.Parse(n) : 16;
            Check(_capacity >= 8 && _capacity <= 64, "Capacity must be 8..64 before any GPU allocation.");
            Check(BitConverter.IsLittleEndian, "Little-endian float32 fixture required.");
            Check(Engine.GetVersionInfo()["hash"].AsString()==Required("expected-engine-hash"), "Native source mismatch.");
            Check(SdkVersion==Required("expected-sdk-version"), "Compiled SDK mismatch.");
            Check(RenderingServer.GetCurrentRenderingDriverName()=="vulkan" && RenderingServer.GetCurrentRenderingMethod()=="forward_plus", "Vulkan/Forward+ required.");
            _rd=RenderingServer.GetRenderingDevice() ?? throw new Exception("Global RD missing.");
            Engine.MaxFps=120;
            Engine.TimeScale=0; // The shader never reads TIME; the captured UBO also records its actual behavior.
            BuildScene(); _cases=Cases(); Check(_cases.Count<=MaxEpochs, "Case ledger overflow.");
            await OnRender(Initialize);
            RenderingServer.FramePreDraw+=PreDraw; RenderingServer.FramePostDraw+=PostDraw; _subscribed=true;
            _running=true;
            Task deadline=Task.Delay(TimeSpan.FromSeconds(90));
            Check(await Task.WhenAny(_finished.Task,deadline)==_finished.Task,"Publication/POST_OPAQUE watchdog expired.");
            await _finished.Task;
            StopPublication();
            byte[] velocities=Array.Empty<byte>(), scenes=Array.Empty<byte>(), commands=Array.Empty<byte>();
            await OnRender(() => {
                velocities=Read(_velocityLedger); scenes=Read(_sceneLedger); commands=Read(_commandLedger);
                _terminalCompleted=true; // Velocity output -> capture SSBO -> this terminal readback, not a read-only pose copy.
            });
            Audit(velocities,scenes,commands);
            Check(_commits==_cases.Count(s=>!s.Skip) && _aborts==1 && _lifecycleCycles==32,"Incomplete epoch/lifecycle coverage.");
            Check(_captures==_commits && _productions==_cases.Count && _readbacks==3,"Unexpected capture/readback cadence.");
            Check(_errors.Errors.Length==0,"Native diagnostics occurred.");
        } catch(Exception error) { _failure=error.ToString(); GD.PushError(_failure); }
        finally {
            StopPublication();
            try { await Cleanup(); } catch(Exception error) { _failure+="\nCleanup: "+error; GD.PushError(error.ToString()); }
            Engine.TimeScale=1;
            try { WriteResult(); } catch(Exception error) {
                GD.PushError("History reporting failed: "+error);
                if(_args.TryGetValue("record",out string? path))File.WriteAllBytes(Path.GetFullPath(path),JsonSerializer.SerializeToUtf8Bytes(new{
                    contract="gpu-history-numeric-contract-v1",passed=false,failure=error.ToString()}));
                OS.RemoveLogger(_errors);_errors.Dispose();GetTree().Quit(2);
            }
        }
    }

    string Required(string key)=>_args.TryGetValue(key,out string? value)&&value.Length>0?value:throw new Exception("Missing --"+key);
    static string SdkVersion=>typeof(GpuHistoryContract).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>().SingleOrDefault(a=>a.Key=="GodotSdkVersion")?.Value??"";
    static string Hash(string path)=>Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant();
    void Check(bool condition,string text) { _checks++; if(!condition) throw new Exception(text); }
    async Task OnRender(Action action)
    {
        var done=new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        RenderingServer.CallOnRenderThread(Callable.From(()=>{ try{action();done.SetResult();}catch(Exception error){done.SetException(error);} }));
        await done.Task;
    }
    async Task Frames(int count) { for(int i=0;i<count;i++) await ToSignal(RenderingServer.Singleton,RenderingServer.SignalName.FramePostDraw); }
    Rid Own(Rid rid,uint bytes=0) { Check(rid.IsValid,"RD allocation failed."); _owned.Add(rid,bytes); return rid; }
    Rid Buffer(uint bytes)=>Own(_rd.StorageBufferCreate(bytes,new byte[checked((int)bytes)]),bytes);
    void Free(Rid rid)
    {
        if(!rid.IsValid)return;
        Check(_owned.Remove(rid),"Attempted to free unowned RID.");
        if(!_uniformSets.Remove(rid)||_rd.UniformSetIsValid(rid))_rd.FreeRid(rid);
    }
    Rid Compile(string file)
    {
        using var src=new RDShaderSource{SourceCompute=Godot.FileAccess.GetFileAsString("res://"+file)};
        using var spirv=_rd.ShaderCompileSpirVFromSource(src);
        Check(string.IsNullOrWhiteSpace(spirv.CompileErrorCompute),spirv.CompileErrorCompute);
        return Own(_rd.ShaderCreateFromSpirV(spirv));
    }
    Rid Set(Rid shader,params Rid[] buffers)
    {
        var uniforms=new Godot.Collections.Array<RDUniform>(); var items=new List<RDUniform>();
        using var lifetime=(Godot.Collections.Array)uniforms;
        try {
            for(int i=0;i<buffers.Length;i++) { var u=new RDUniform{UniformType=RenderingDevice.UniformType.StorageBuffer,Binding=i}; u.AddId(buffers[i]);items.Add(u);uniforms.Add(u); }
            Rid rid=Own(_rd.UniformSetCreate(uniforms,shader,0)); _uniformSets.Add(rid); return rid;
        } finally { foreach(var u in items)u.Dispose(); }
    }
    void Copy(Rid source,Rid destination,uint sourceOffset,uint destinationOffset,uint bytes)
    { Check(_rd.BufferCopy(source,destination,sourceOffset,destinationOffset,bytes)==Error.Ok,"Tracked BufferCopy failed.");_copies++; }
    byte[] Read(Rid rid) { _readbacks++;return _rd.BufferGetData(rid); }

    void BuildScene()
    {
        _mesh=new ArrayMesh();
        using(var a=new Godot.Collections.Array()) { a.Resize((int)Mesh.ArrayType.Max);a[(int)Mesh.ArrayType.Vertex]=Vertices;
            a[(int)Mesh.ArrayType.Normal]=Enumerable.Repeat(Vector3.Back,4).ToArray();a[(int)Mesh.ArrayType.Index]=new[]{0,1,2,0,2,3};
            _mesh.AddSurfaceFromArrays(Mesh.PrimitiveType.Triangles,a); }
        _shader=GD.Load<Shader>("res://Consumer.gdshader")??throw new Exception("History material missing.");
        _material=new ShaderMaterial{Shader=_shader};
        _world=new World3D(); // The main window never sees this World3D or its E8 owner.
        _view=new SubViewport{Size=new Vector2I(Width,Height),World3D=_world,RenderTargetUpdateMode=SubViewport.UpdateMode.Disabled};
        AddChild(_view);
        _effect=new HistoryCapture{Capture=Capture};
        _compositor=new Compositor{CompositorEffects=new Godot.Collections.Array<CompositorEffect>{_effect}};
        _camera=new Camera3D{Projection=Camera3D.ProjectionType.Orthogonal,Size=4,KeepAspect=Camera3D.KeepAspectEnum.Height,
            Near=.1f,Far=10,Position=new Vector3(0,0,2),Current=true,Compositor=_compositor};
        _view.AddChild(_camera);
    }
    void Initialize()
    {
        uint poseBytes=checked((uint)_capacity*64);
        _current=Buffer(poseBytes);_previous=Buffer(poseBytes);_lifecycle=Buffer(checked((uint)_capacity*16));
        _header=Buffer(16);_ids=Buffer(32);_diagnostic=Buffer(16);
        _commands=Own(_rd.StorageBufferCreate(36,new byte[36],(RenderingDevice.StorageBufferUsage)1),36);
        _velocityLedger=Buffer(MaxEpochs*Width*Height*16u);_sceneLedger=Buffer(MaxEpochs*SceneBytes);_commandLedger=Buffer(MaxEpochs*64);
        _producerShader=Compile("Producer.glslinc");_producerPipeline=Own(_rd.ComputePipelineCreate(_producerShader));
        _producerSet=Set(_producerShader,_current,_previous,_lifecycle,_header,_ids,_commands,_diagnostic);
        _captureShader=Compile("Capture.glslinc");_capturePipeline=Own(_rd.ComputePipelineCreate(_captureShader));
        using(var state=new RDSamplerState{MinFilter=RenderingDevice.SamplerFilter.Nearest,MagFilter=RenderingDevice.SamplerFilter.Nearest}) _sampler=Own(_rd.SamplerCreate(state));
        string[] names={"current_pose","previous_pose","lifecycle","header","visible_ids"};
        Rid[] rids={_current,_previous,_lifecycle,_header,_ids};
        for(int i=0;i<names.Length;i++) { var b=new BufferRD();Check(b.AttachStorageBuffer(rids[i])==Error.Ok,"BufferRD attach failed.");
            Check(b.GetRid()==rids[i],"BufferRD changed borrowed identity.");_wrappers.Add(b);
            using Variant parameter=Variant.From(b);_material!.SetShaderParameter(names[i],parameter); }
        for(int i=0;i<32;i++) Lifecycle(i);
        _multimesh=NewOwner();
        Check(Attach(_multimesh,Slots,16,true)==Error.Ok,"E8 attach failed.");
        Check(RenderingServer.MultimeshUsesShaderMotionVectors(_multimesh),"E8 history flag missing.");
        _instance=RenderingServer.InstanceCreate();RenderingServer.InstanceSetBase(_instance,_multimesh);
        RenderingServer.InstanceSetScenario(_instance,_world!.Scenario);RenderingServer.InstanceGeometrySetMaterialOverride(_instance,_material!.GetRid());
        RenderingServer.InstanceGeometrySetCastShadowsSetting(_instance,RenderingServer.ShadowCastingSetting.Off);
        _liveBytes=checked((long)_rd.GetMemoryUsage(RenderingDevice.MemoryType.Buffers));
    }
    Rid NewOwner()
    {
        Rid mm=RenderingServer.MultimeshCreate();RenderingServer.MultimeshSetMesh(mm,_mesh!.GetRid());
        RenderingServer.MultimeshSetCustomAabb(mm,new Aabb(new Vector3(-4,-2,-.1f),new Vector3(8,4,.2f)));return mm;
    }
    Error Attach(Rid mm,int capacity,int offset,bool motion)=>RenderingServer.MultimeshAttachSharedDrawBuffer(mm,capacity,_commands,offset,motion);
    void Lifecycle(int index)
    {
        Rid mm=NewOwner();
        try {
            Check(Attach(mm,Slots,16,true)==Error.Ok&&RenderingServer.MultimeshUsesShaderMotionVectors(mm),"Lifecycle attach failed.");
            Check(!RenderingServer.MultimeshGetBufferRdRid(mm).IsValid&&RenderingServer.MultimeshGetCommandBufferRdRid(mm)==_commands,"E8 retained transform storage or changed commands.");
            if(index==0)foreach(var bad in new[]{(0,16),(Slots,1),(Slots,20)}) {
                Check(Attach(mm,bad.Item1,bad.Item2,true)==Error.InvalidParameter,"Quiet invalid attach accepted.");
                Check(RenderingServer.MultimeshUsesShaderMotionVectors(mm)&&RenderingServer.MultimeshGetCommandBufferRdRid(mm)==_commands&&
                    RenderingServer.MultimeshGetInstanceCount(mm)==Slots,"Invalid rebind changed prior binding.");
            }
            if(index==0){
                Check(RenderingServer.MultimeshAttachSharedDrawBuffer(mm,Slots,_commands,16)==Error.Unavailable,"Default false flag bypassed existing motion guard.");
                Check(RenderingServer.MultimeshUsesShaderMotionVectors(mm)&&RenderingServer.MultimeshGetInstanceCount(mm)==Slots&&
                    RenderingServer.MultimeshGetCommandBufferRdRid(mm)==_commands,"Rejected false-flag rebind changed existing view.");
            }
            Check(Attach(mm,Slots,16,true)==Error.Ok,"Same-resource rebind failed.");
            RenderingServer.MultimeshDetachSharedBuffers(mm);
            Check(!RenderingServer.MultimeshUsesShaderMotionVectors(mm)&&!RenderingServer.MultimeshIsUsingSharedBuffers(mm),"Detach retained mode.");
            RenderingServer.MultimeshSetMesh(mm,default);RenderingServer.MultimeshAllocateData(mm,1,RenderingServer.MultimeshTransformFormat.Transform3D);
            RenderingServer.MultimeshSetMesh(mm,_mesh!.GetRid());Check(!RenderingServer.MultimeshUsesShaderMotionVectors(mm),"Ordinary allocation retained flag.");
            RenderingServer.MultimeshAllocateData(mm,0,RenderingServer.MultimeshTransformFormat.Transform3D);
            Check(Attach(mm,Slots,16,true)==Error.Ok,"Post-ordinary reattach failed.");
            _api.Add(new{cycle=index,multimesh=mm.Id,command_rid=_commands.Id,capacity=Slots,command_byte_offset=16,
                history_query_true=true,no_transform_pool=true,failed_rebind_preserved=index==0,default_false_guard_preserved=index==0,
                detach_reset=true,ordinary_reset=true,implicit_free=true});
            _lifecycleCycles++;
        } finally { RenderingServer.FreeRid(mm); }
    }

    void PreDraw()
    {
        if(!_running)return;
        try {
            Check(_pending==null,"Prior viewport epoch has no clean post-draw acknowledgment.");
            if(_next==_cases.Count)return;
            Sample state=_cases[_next];
            var p=new Publication(_next+1,_next,_forceReseed?_next+1:_committedEpoch,state,_committed,_forceReseed);
            _publications.Add(p);_pending=p;_next++;
            _camera!.Position=new Vector3(state.CameraX,state.CameraY,2);
            _view!.UseTaa=state.Taa;_view.Scaling3DMode=(Viewport.Scaling3DModeEnum)state.Scaling;_view.Scaling3DScale=1;
            _view.RenderTargetUpdateMode=state.Skip?SubViewport.UpdateMode.Disabled:SubViewport.UpdateMode.Always;
            RenderingServer.CallOnRenderThread(Callable.From(()=>{try{Produce(p);}catch(Exception error){Fail(error);} }));
        }catch(Exception error){Fail(error);}
    }
    static uint F(float value)=>BitConverter.SingleToUInt32Bits(value);
    void Produce(Publication p)
    {
        if(!p.Reseed)Copy(_current,_previous,0,0,checked((uint)_capacity*64));
        Sample s=p.State;
        uint[] words={checked((uint)p.Epoch),checked((uint)p.PreviousEpoch),checked((uint)_capacity),s.Domain,
            p.Reseed?1u:0u,s.IdA,s.IdB,s.Count,s.Gen7,s.Gen2,s.Continuity7,s.Continuity2,s.Representation,s.Stale?1u:0u,s.Tick,0u,
            F(s.X7),F(s.Y7),F(s.D7),0u,F(s.X2),F(s.Y2),F(s.D2),0u,0u,0u,0u,0u,0u,0u,0u,0u};
        long list=_rd.ComputeListBegin();_rd.ComputeListBindComputePipeline(list,_producerPipeline);_rd.ComputeListBindUniformSet(list,_producerSet,0);
        _rd.ComputeListSetPushConstant(list,MemoryMarshal.AsBytes(words.AsSpan()).ToArray(),128);_rd.ComputeListDispatch(list,1,1,1);_rd.ComputeListEnd();_productions++;
        if(p.Reseed){Copy(_current,_previous,0,0,checked((uint)_capacity*64));_resetFrames++;}
        Volatile.Write(ref _markerCount,0);Volatile.Write(ref _preparedEpoch,p.Epoch);
    }
    Rid CaptureSet(Rid texture,Rid depth)
    {
        var key=(texture.Id,depth.Id);
        if(_captureSets.TryGetValue(key,out Rid existing)&&_rd.UniformSetIsValid(existing))return existing;
        using var sampled=new RDUniform{UniformType=RenderingDevice.UniformType.SamplerWithTexture,Binding=0};sampled.AddId(_sampler);sampled.AddId(texture);
        using var output=new RDUniform{UniformType=RenderingDevice.UniformType.StorageBuffer,Binding=1};output.AddId(_velocityLedger);
        using var coverage=new RDUniform{UniformType=RenderingDevice.UniformType.SamplerWithTexture,Binding=2};coverage.AddId(_sampler);coverage.AddId(depth);
        var uniforms=new Godot.Collections.Array<RDUniform>{sampled,output,coverage};using var life=(Godot.Collections.Array)uniforms;
        Rid set=Own(_rd.UniformSetCreate(uniforms,_captureShader,0));_uniformSets.Add(set);_captureSets[key]=set;return set;
    }
    void Capture(RenderData data)
    {
        if(!_running)return;
        try {
            int epoch=Volatile.Read(ref _preparedEpoch);Check(epoch>=1&&epoch<=_cases.Count,"Unexpected compositor epoch.");
            Publication p=_publications[epoch-1];Check(!p.State.Skip,"Skipped viewport rendered a speculative epoch.");
            Check(Interlocked.Increment(ref _markerCount)==1,"Duplicate POST_OPAQUE marker for epoch.");
            var buffers=data.GetRenderSceneBuffers() as RenderSceneBuffersRD??throw new Exception("RD scene buffers unavailable.");
            Rid velocity=buffers.GetVelocityTexture(),depth=buffers.GetDepthTexture();Rid scene=data.GetRenderSceneData().GetUniformBuffer();
            Check(velocity.IsValid&&depth.IsValid&&scene.IsValid,"Native velocity/depth/scene UBO missing.");
            Copy(scene,_sceneLedger,0,checked((uint)(p.Index*SceneBytes)),SceneBytes);
            Copy(_diagnostic,_commandLedger,0,checked((uint)(p.Index*64)),16);
            Copy(_commands,_commandLedger,0,checked((uint)(p.Index*64+16)),36);
            uint[] push={checked((uint)p.Index),checked((uint)epoch),Width,Height};
            Rid set=CaptureSet(velocity,depth);
            long list=_rd.ComputeListBegin();_rd.ComputeListBindComputePipeline(list,_capturePipeline);_rd.ComputeListBindUniformSet(list,set,0);
            _rd.ComputeListSetPushConstant(list,MemoryMarshal.AsBytes(push.AsSpan()).ToArray(),16);
            _rd.ComputeListDispatch(list,Width/8,Height/8,1);_rd.ComputeListEnd();_captures++;
            Volatile.Write(ref _markedEpoch,epoch);
        }catch(Exception error){Fail(error);}
    }
    void PostDraw()
    {
        if(!_running||_pending==null)return;
        try {
            Publication p=_pending;
            if(p.State.Skip){Check(Volatile.Read(ref _markedEpoch)!=p.Epoch,"Skipped epoch received marker.");_aborts++;_forceReseed=true;}
            else {
                Check(Volatile.Read(ref _markedEpoch)==p.Epoch&&Volatile.Read(ref _markerCount)==1,"POST_OPAQUE/post-draw epoch mismatch.");
                Check(_errors.Errors.Length==0,"Draw emitted native diagnostics.");
                _committedEpoch=p.Epoch;_committed=p.State;_forceReseed=false;_commits++;
            }
            _pending=null;
            if(_next==_cases.Count){_running=false;_view!.RenderTargetUpdateMode=SubViewport.UpdateMode.Disabled;_finished.TrySetResult();}
        }catch(Exception error){Fail(error);}
    }
    void Fail(Exception error){_running=false;_finished.TrySetException(error);}
    void StopPublication()
    {
        _running=false;
        if(_subscribed){RenderingServer.FramePreDraw-=PreDraw;RenderingServer.FramePostDraw-=PostDraw;_subscribed=false;}
        if(_view!=null)_view.RenderTargetUpdateMode=SubViewport.UpdateMode.Disabled;
    }

    static List<Sample> Cases()
    {
        var rows=new List<Sample>();Sample s=new("seed");rows.Add(s);
        void Add(string name,Sample state){s=state with{Name=name};rows.Add(s);}
        Add("static_zero",s);Add("root_positive",s with{X7=-.875f,X2=.875f,Tick=1});
        Add("frozen_sample_first",s);Add("frozen_sample_second",s);
        Add("multiple_ticks_root_negative",s with{X7=-1.125f,X2=1.125f,Tick=5});
        Add("camera_only",s with{CameraX=.125f,CameraY=-.125f});
        Add("deformation_positive_no_time",s with{D7=.5f,D2=-.5f});
        Add("deformation_negative_no_time",s with{D7=-.5f,D2=.5f});
        Add("camera_root_deformation",s with{CameraX=-.125f,CameraY=.125f,X7=-1,X2=1,D7=0,D2=0});
        Add("compaction_reorder",s with{IdA=2,IdB=7,X7=-.875f,X2=.875f});
        Add("generation_reuse",s with{Gen7=2,X7=-1.375f,CameraX=0});
        Add("teleport_continuity",s with{Continuity7=2,X7=-.75f,CameraX=.125f});
        Add("domain_fallback",s with{Domain=2,X7=-1.125f,CameraY=0});
        Add("representation_fallback",s with{Representation=2,X7=-1});
        Add("stale_epoch_fallback",s with{Stale=true,X7=-.875f});
        Add("valid_id_zero",s with{Stale=false,IdA=0,IdB=2});
        Add("hidden_unit",s with{IdA=2,Count=1});
        Add("hidden_reentry_reset",s with{IdA=7,Count=2,Continuity7=3,X7=-1.125f});
        Add("skipped_speculative",s with{Skip=true,Domain=3,X7=-.5f});
        Add("resume_reseed",s with{Skip=false,Domain=4,X7=-1,CameraX=-.125f});
        Add("invalid_current_id",s with{IdA=uint.MaxValue});
        Add("invalid_count_overflow",s with{IdA=7,Count=6});
        Add("invalid_current_generation",s with{Count=2,Gen7=0});
        Add("bounds_recovered",s with{Gen7=3,Continuity7=4});
        Add("jitter_warmup",s with{Taa=true});Add("jitter_static",s);
        Add("jitter_camera",s with{CameraX=.125f,CameraY=-.125f});
        Add("fsr2_routing_warmup",s with{Taa=false,Scaling=2});
        Add("fsr2_routing_motion",s with{X7=-.875f,X2=.875f,CameraX=0});
        Add("stress_warmup",s with{Scaling=0,CameraY=0});
        for(int i=0;i<64;i++)Add("stress_"+i,s with{X7=(i&1)==0?-1:-.875f,X2=(i&1)==0?1:.875f,
            D7=(i&1)==0?0:.5f,D2=(i&1)==0?0:-.5f,IdA=(i&1)==0?7u:2u,IdB=(i&1)==0?2u:7u,Tick=(uint)(10+i/3)});
        return rows;
    }

    static uint Word(byte[] b,int offset)=>BitConverter.ToUInt32(b,offset);
    static float Float(byte[] b,int offset)=>BitConverter.ToSingle(b,offset);
    static float[] Floats(byte[] b,int offset,int count)=>Enumerable.Range(0,count).Select(i=>Float(b,offset+i*4)).ToArray();
    static Vector3 Point(Sample s,uint id,Vector3 vertex)
    { Vector3 p=s.Pose(id);return new(vertex.X+p.Z*vertex.Y+p.X,vertex.Y+p.Y,vertex.Z); }
    static Vector4 Clip(byte[] scene,int offset,Vector3 p)
    {
        float G(int f)=>Float(scene,offset+f*4);
        float x=G(44)*p.X+G(45)*p.Y+G(46)*p.Z+G(47),y=G(48)*p.X+G(49)*p.Y+G(50)*p.Z+G(51),z=G(52)*p.X+G(53)*p.Y+G(54)*p.Z+G(55);
        return new(G(0)*x+G(4)*y+G(8)*z+G(12),G(1)*x+G(5)*y+G(9)*z+G(13),G(2)*x+G(6)*y+G(10)*z+G(14),G(3)*x+G(7)*y+G(11)*z+G(15));
    }
    static Vector2 Ndc(Vector4 q)=>new(q.X/q.W,q.Y/q.W);
    static float Cross(Vector2 a,Vector2 b)=>a.X*b.Y-a.Y*b.X;
    static Vector3 Bary(Vector2 p,Vector2 a,Vector2 b,Vector2 c)
    { float d=Cross(b-a,c-a);float u=Cross(b-p,c-p)/d,v=Cross(c-p,a-p)/d;return new(u,v,1-u-v); }
    static float EdgeDistance(Vector2 p,Vector2 a,Vector2 b)=>Mathf.Abs(Cross(b-a,p-a))/(b-a).Length();
    sealed record Pixel(int X,int Y,int[] Triangle,Vector3 Weights);
    List<Pixel> Samples(Vector4[] clips)
    {
        Vector2[] corners=clips.Select(c=>new Vector2((c.X/c.W*.5f+.5f)*Width,(c.Y/c.W*.5f+.5f)*Height)).ToArray();
        var candidates=new List<Pixel>();
        foreach(int[] t in Triangles)for(int y=Math.Max(0,(int)Mathf.Floor(corners.Min(c=>c.Y)));y<=Math.Min(Height-1,(int)Mathf.Ceil(corners.Max(c=>c.Y)));y++)
            for(int x=Math.Max(0,(int)Mathf.Floor(corners.Min(c=>c.X)));x<=Math.Min(Width-1,(int)Mathf.Ceil(corners.Max(c=>c.X)));x++) {
                Vector2 p=new(x+.5f,y+.5f),a=corners[t[0]],b=corners[t[1]],c=corners[t[2]];Vector3 w=Bary(p,a,b,c);
                if(w.X>0&&w.Y>0&&w.Z>0&&Math.Min(EdgeDistance(p,a,b),Math.Min(EdgeDistance(p,b,c),EdgeDistance(p,c,a)))>=2)candidates.Add(new(x,y,t,w));
            }
        Check(candidates.Count>=4,"Less than four proven triangle-interior pixels.");
        var chosen=new List<Pixel>();Vector2 center=corners.Aggregate(Vector2.Zero,(a,b)=>a+b)/4;
        foreach(Vector2 delta in new[]{new Vector2(-2,-3),new Vector2(-3,-2),new Vector2(2,3),new Vector2(3,2)}) {
            Pixel p=candidates.Where(p=>chosen.All(q=>q.X!=p.X||q.Y!=p.Y)).MinBy(p=>(new Vector2(p.X+.5f,p.Y+.5f)-center-delta).LengthSquared())!;chosen.Add(p);
        }
        return chosen;
    }
    void Audit(byte[] velocity,byte[] scenes,byte[] commands)
    {
        int nonzero=0,jitter=0;
        foreach(Publication p in _publications) {
            if(p.State.Skip){_epochs.Add(new{epoch=p.Epoch,name=p.State.Name,prepared=true,post_opaque=false,committed=false,recovery="reseed_both"});continue;}
            int sceneOffset=p.Index*SceneBytes,commandOffset=p.Index*64;uint error=p.State.Error(_capacity);
            Check(Word(commands,commandOffset)==error&&Word(commands,commandOffset+4)==p.Epoch,"GPU diagnostic/epoch differs.");
            Check(Word(commands,commandOffset+16+5*4)==(error==0?p.State.Count:0)&&Word(commands,commandOffset+16+8*4)==3,"GPU command capacity/header differs.");
            for(int k=0;k<4;k++)Check(Word(commands,commandOffset+16+k*4)==0xc0ffee00u+k,"Command prefix guard changed.");
            Vector2 cj=new(Float(scenes,sceneOffset+2824),Float(scenes,sceneOffset+2828)),pj=new(Float(scenes,sceneOffset+UboBytes+2824),Float(scenes,sceneOffset+UboBytes+2828));
            if(cj!=Vector2.Zero||pj!=Vector2.Zero)jitter++;
            var identities=new List<object>();
            if(error==0)foreach(uint id in new[]{p.State.IdA,p.State.IdB}.Take(checked((int)p.State.Count))) {
                bool history=p.History(id);Sample previous=history?p.Previous!:p.State;
                Vector4[] current=Vertices.Select(v=>Clip(scenes,sceneOffset,Point(p.State,id,v))).ToArray();
                Vector4[] prev=Vertices.Select(v=>Clip(scenes,sceneOffset+UboBytes,Point(previous,id,v))).ToArray();
                foreach(Pixel sample in Samples(current)) {
                    Vector2 c=Vector2.Zero,b=Vector2.Zero;float[] weights={sample.Weights.X,sample.Weights.Y,sample.Weights.Z};
                    for(int j=0;j<3;j++){c+=Ndc(current[sample.Triangle[j]])*weights[j];b+=Ndc(prev[sample.Triangle[j]])*weights[j];}
                    Vector2 expected=((b-pj)-(c-cj))*.5f;int address=((p.Index*Height+sample.Y)*Width+sample.X)*16;
                    Vector2 actual=new(Float(velocity,address),Float(velocity,address+4));
                    Check(Word(velocity,address+8)==p.Epoch,"Missing/wrong-epoch velocity capture.");
                    float depth=Float(velocity,address+12),expectedDepth=0;
                    for(int j=0;j<3;j++)expectedDepth+=current[sample.Triangle[j]].Z/current[sample.Triangle[j]].W*weights[j];
                    Check(float.IsFinite(depth)&&depth>0&&Mathf.Abs(depth-expectedDepth)<=1e-5f,"Current geometry/depth coverage missing.");
                    Check(float.IsFinite(actual.X)&&float.IsFinite(actual.Y)&&float.IsFinite(expected.X)&&float.IsFinite(expected.Y),"Nonfinite velocity.");
                    Check(Mathf.Abs(actual.X-expected.X)<=Tolerance&&Mathf.Abs(actual.Y-expected.Y)<=Tolerance,$"Velocity {p.State.Name} ID{id} at {sample.X},{sample.Y}: {actual}!={expected}.");
                    bool designatedX=p.State.Name is "root_positive" or "multiple_ticks_root_negative" or
                        "deformation_positive_no_time" or "deformation_negative_no_time" or "camera_only";
                    if(designatedX)Check(Mathf.Abs(expected.X)>=1f/256&&Mathf.Abs(expected.X)<=1f/8,"Designated X oracle outside preregistered nonzero range.");
                    if(p.State.Name=="camera_only")Check(Mathf.Abs(expected.Y)>=1f/256&&Mathf.Abs(expected.Y)<=1f/8,"Designated Y oracle outside preregistered nonzero range.");
                    if(expected.Length()>1f/256){Check(actual.Length()>1f/512,"Nonzero oracle accepted zero output.");nonzero++;}
                    _pixels.Add(new{epoch=p.Epoch,name=p.State.Name,id,x=sample.X,y=sample.Y,triangle=sample.Triangle,
                        barycentric=weights,previous_valid=history,expected_uv=new[]{expected.X,expected.Y},actual_uv=new[]{actual.X,actual.Y},
                        expected_depth=expectedDepth,actual_depth=depth,designated_nonzero_x=designatedX,tolerance_uv=Tolerance});
                }
                Vector3 cp=p.State.Pose(id),pp=previous.Pose(id);
                identities.Add(new{id,previous_valid=history,current_pose=new[]{cp.X,cp.Y,cp.Z},previous_pose=new[]{pp.X,pp.Y,pp.Z},
                    current_clip=current.Select(v=>new[]{v.X,v.Y,v.Z,v.W}).ToArray(),previous_clip=prev.Select(v=>new[]{v.X,v.Y,v.Z,v.W}).ToArray()});
            }
            _epochs.Add(new{epoch=p.Epoch,name=p.State.Name,simulation_tick=p.State.Tick,prepared=true,post_opaque=true,committed=true,
                reseeded=p.Reseed,expected_previous_epoch=p.PreviousEpoch,diagnostic=error,instance_count=error==0?p.State.Count:0,
                camera=new[]{p.State.CameraX,p.State.CameraY},current_jitter=new[]{cj.X,cj.Y},previous_jitter=new[]{pj.X,pj.Y},
                current_scene_time=Float(scenes,sceneOffset+2816),previous_scene_time=Float(scenes,sceneOffset+UboBytes+2816),
                current_projection=Floats(scenes,sceneOffset,16),previous_projection=Floats(scenes,sceneOffset+UboBytes,16),
                current_view_transposed_3x4=Floats(scenes,sceneOffset+176,12),previous_view_transposed_3x4=Floats(scenes,sceneOffset+UboBytes+176,12),
                scene_ubo_sha256=Convert.ToHexString(SHA256.HashData(scenes.AsSpan(sceneOffset,SceneBytes))).ToLowerInvariant(),identities});
        }
        Check(nonzero>=64,"Nonzero velocity coverage missing.");Check(jitter>=2,"Native jitter gate did not produce jittered scene data.");
    }

    async Task Cleanup()
    {
        if(_rd==null)return;
        if(_effect!=null){_effect.Capture=null;_effect.Enabled=false;}
        if(_camera!=null)_camera.Compositor=null;
        await OnRender(()=>{
            if(_instance.IsValid){RenderingServer.InstanceSetVisible(_instance,false);RenderingServer.FreeRid(_instance);_instance=default;}
            if(_material!=null)foreach(string name in new[]{"current_pose","previous_pose","lifecycle","header","visible_ids"})_material.SetShaderParameter(name,default(Variant));
            if(_multimesh.IsValid){RenderingServer.MultimeshDetachSharedBuffers(_multimesh);Check(!RenderingServer.MultimeshUsesShaderMotionVectors(_multimesh),"Final detach retained flag.");RenderingServer.FreeRid(_multimesh);_multimesh=default;}
            // Native RD deferred destruction waits its frame-slot fence; no CPU/GPU-fence claim for this callback.
            foreach(Rid rid in _owned.Keys.Where(r=>_uniformSets.Contains(r)).ToArray())Free(rid);
            if(_owned.ContainsKey(_producerPipeline))Free(_producerPipeline);
            if(_owned.ContainsKey(_capturePipeline))Free(_capturePipeline);
            foreach(Rid rid in _owned.Keys.ToArray())Free(rid);
        });
        foreach(var wrapper in _wrappers)wrapper.Dispose();_wrappers.Clear();
        _material?.Dispose();_material=null;_shader?.Dispose();_shader=null;_mesh?.Dispose();_mesh=null;
        _effect?.Dispose();_effect=null;_compositor?.Dispose();_compositor=null;
        _view?.QueueFree();_view=null;_camera=null;_world?.Dispose();_world=null;
        await Frames(checked((int)_rd.GetFrameDelay()+4));
        await OnRender(()=>_settledBytes=checked((long)_rd.GetMemoryUsage(RenderingDevice.MemoryType.Buffers)));
    }
    string RuntimeAssemblyHash(Assembly assembly,string expectedPath)
    {
        string path=string.IsNullOrEmpty(assembly.Location)?expectedPath:assembly.Location;
        using var stream=File.OpenRead(path);using var pe=new PEReader(stream);
        MetadataReader metadata=pe.GetMetadataReader();
        Check(metadata.GetGuid(metadata.GetModuleDefinition().Mvid)==assembly.ManifestModule.ModuleVersionId,"Runtime managed module differs from known file.");
        return Hash(path);
    }
    void WriteResult()
    {
        string fixture=ProjectSettings.GlobalizePath("res://.godot/mono/temp/bin/Debug/GpuHistoryLab.dll");
        string api=Path.Combine(Path.GetDirectoryName(OS.GetExecutablePath())!,"GodotSharp/Api/Debug/GodotSharp.dll");
        string fixtureHash=RuntimeAssemblyHash(typeof(GpuHistoryContract).Assembly,fixture),apiHash=RuntimeAssemblyHash(typeof(Node).Assembly,api);
        bool passed=_failure.Length==0&&_errors.Errors.Length==0&&_terminalCompleted&&_owned.Count==0;
        string record=Path.GetFullPath(Required("record"));Directory.CreateDirectory(Path.GetDirectoryName(record)!);
        var result=new{contract="gpu-history-numeric-contract-v1",passed,failure=_failure,
            engine_hash=Engine.GetVersionInfo()["hash"].AsString(),engine_status=Engine.GetVersionInfo()["status"].AsString(),sdk_version=SdkVersion,
            driver=RenderingServer.GetCurrentRenderingDriverName(),renderer=RenderingServer.GetCurrentRenderingMethod(),
            source_sha256=Inputs.ToDictionary(n=>n,n=>Hash(ProjectSettings.GlobalizePath("res://"+n))),
            managed_fixture_assembly_sha256=fixtureHash,managed_api_assembly_path=typeof(Node).Assembly.Location,
            managed_api_identity_file=api,managed_api_assembly_sha256=apiHash,capacity=_capacity,viewport=new[]{Width,Height},first_instance=3,draw_slot_capacity=Slots,
            cases=_cases.Count,prepared_epochs=_productions,committed_epochs=_commits,aborted_epochs=_aborts,reseeded_epochs=_resetFrames,
            compositor_captures=_captures,gpu_buffer_readbacks=_readbacks,per_frame_gpu_readbacks=0,tracked_buffer_copies=_copies,lifecycle_cycles=_lifecycleCycles,
            requested_history_input_bytes=_capacity*144+84,pose_bank_count=2,native_transform_input_bytes=0,
            capture_capacity_epochs=MaxEpochs,capture_scene_bytes_per_epoch=SceneBytes,capture_binding_versions=_captureSets.Count,live_rd_logical_buffer_bytes=_liveBytes,
            settled_rd_logical_buffer_bytes=_settledBytes,final_owned_rd_resource_count=_owned.Count,checks=_checks,
            memory_scope="Contextual pre-render/post-cleanup logical byte endpoints; no plateau qualification",
            terminal_velocity_capture_readback_completed=_terminalCompleted,retirement_path="global RD deferred destruction after all bindings removed",
            api_audits=_api,epoch_audits=_epochs,pixel_audits=_pixels,native_diagnostics=_errors.Errors,
            image_files_written=0,performance_window_count=0,performance_qualified=false,
            not_qualified=new[]{"Rejected shader builtin/stage/mode compiler matrix is a separate native gate",
                "Unrelated-view demand, incompatible effective materials/debug/next-pass and forced ubershader matrix pending",
                "Lit TBN, shadow quality, transparency, MSAA, multiview, mutable base geometry, gameplay and FPS",
                "Physical VRAM and multi-lifecycle post-retirement memory plateau need separate evidence"}};
        File.WriteAllBytes(record,JsonSerializer.SerializeToUtf8Bytes(result,new JsonSerializerOptions{WriteIndented=true}));
        GD.Print("GPU_HISTORY_RECORD="+record);OS.RemoveLogger(_errors);_errors.Dispose();GetTree().Quit(passed?0:2);
    }
}

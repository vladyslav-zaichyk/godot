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
using GDictionary=Godot.Collections.Dictionary;

// Procedural, numerical contract. No images, local RD, gameplay, FPS or physical VRAM claim.
public partial class Main : Node
{
    const uint Guard=0xcdcdcdcd, Slots=16, RawStride=131072, UboStride=5696, ProofStride=44236, PyramidBytes=43724;
    static readonly uint[] Levels={4,8196,10244,10756,10884,10916,10924,10926};
    static readonly string[] Inputs={"OcclusionTaaLab.csproj","project.godot","Main.tscn","Main.cs","HistoryCapture.cs","ProbeLogger.cs",
        "Consumer.gdshader","Legacy.gdshader","Wall.gdshader","CopyDepth.comp","Reduce.comp","Select.comp","Finalize.comp","Capture.comp","scenarios.json","gate_plan.json","FreezeScenarios.py"};
    static readonly Vector3[] Vertices={new(-.65f,-.65f,-.22f),new(.65f,-.65f,.22f),new(.65f,.65f,.22f),new(-.65f,.65f,-.22f)};
    static readonly int[][] Triangles={new[]{0,2,1},new[]{0,3,2}};
    readonly Dictionary<string,string> _args=new(); readonly List<(Rid rid,uint bytes)> _owned=new();
    readonly List<BufferRD> _wrappers=new(); readonly List<object> _records=new(); readonly List<object> _initialization=new();
    readonly Dictionary<(ulong,ulong,ulong),Rid> _captureSets=new(); readonly HashSet<Rid> _ownedSets=new();
    readonly ProbeLogger _logger=new();
    RenderingDevice _rd=null!; SubViewport? _view; Camera3D? _camera; World3D? _world;
    MeshInstance3D? _wall; ArrayMesh? _mesh,_wallMesh; ShaderMaterial? _material,_wallMaterial,_legacyMaterial;
    Shader? _shader,_wallShader,_legacyShader; Compositor? _compositor; HistoryCapture? _effect; Godot.Environment? _environment;
    Rid _viewport,_mm,_instance,_current,_previous,_life,_state,_header,_ids,_fallback,_candidate,_stats,_pyramid;
    Rid _rawLedger,_uboLedger,_proofLedger,_sampler,_copyShader,_copyPipeline,_reducePipeline,_selectPipeline,_finalPipeline,_captureShader,_capturePipeline;
    Rid _reduceSet,_selectSet,_finalSet; Callable _producer; JsonDocument _plan=null!,_scenarios=null!;
    JsonElement _row,_scene; List<Probe> _probes=new(); object? _producerRecord;
    bool _configured,_registered,_captureArmed; int _index,_checks,_callbacks,_dispatches,_barriers,_captures,_readbacks,_commits;
    int _rowCaptures,_rowCallbacks,_rowDispatches,_rowBarriers,_capturePairsPeak; long _lastEpoch; string _failure="",_callbackFailure="";
    byte[] _raw=Array.Empty<byte>(),_ubo=Array.Empty<byte>(),_proof=Array.Empty<byte>();
    sealed record Probe(int X,int Y,string Kind,int StableId,int[] Triangle);

    public override async void _Ready()
    {
        OS.AddLogger(_logger);
        foreach(string arg in OS.GetCmdlineUserArgs()){string[] p=arg.TrimStart('-').Split('=',2);_args[p[0]]=p.Length==2?p[1]:"true";}
        try{
            Check(BitConverter.IsLittleEndian,"Float32/little-endian required.");
            Check(Engine.GetVersionInfo()["hash"].AsString()==Required("expected-engine-hash"),"Native source mismatch.");
            Check(Hash(OS.GetExecutablePath())==Required("expected-engine-sha256")&&SdkVersion==Required("expected-sdk-version"),"Editor/SDK mismatch.");
            Check(RenderingServer.GetCurrentRenderingDriverName()=="vulkan"&&RenderingServer.GetCurrentRenderingMethod()=="forward_plus","Vulkan Forward+ required.");
            _rd=RenderingServer.GetRenderingDevice()??throw new Exception("Global RD unavailable.");
            _plan=JsonDocument.Parse(Godot.FileAccess.GetFileAsString("res://gate_plan.json"));
            _scenarios=JsonDocument.Parse(Godot.FileAccess.GetFileAsString("res://scenarios.json"));
            Check(_plan.RootElement.GetProperty("records").GetArrayLength()==128&&_scenarios.RootElement.GetProperty("records").GetArrayLength()==128,"Frozen matrix differs.");
            Engine.MaxFps=120;Engine.TimeScale=0;BuildScene();
            _producer=Callable.From<RenderGpuOcclusionContext,GDictionary>(Produce);
            await OnRender(Initialize);
            await InitializationRender();
            for(_index=0;_index<128;_index++)await RunRecord();
            Check(_callbacks==125&&_captures==124&&_commits==124&&_dispatches==1240&&_barriers==1116,"Incomplete native scheduling counts.");
            await OnRender(()=>{_raw=Read(_rawLedger);_ubo=Read(_uboLedger);_proof=Read(_proofLedger);});
            Check(_readbacks==3,"Only three terminal ledger reads are permitted.");
            Check(_logger.Entries.Length==0&&_logger.Dropped==0,"Native diagnostics occurred.");
        }catch(Exception error){_failure=error.ToString();GD.PushError(_failure);}
        finally{
            _captureArmed=false;if(_view!=null)_view.RenderTargetUpdateMode=SubViewport.UpdateMode.Disabled;
            try{await Cleanup();}catch(Exception error){_failure+="\nCleanup: "+error;GD.PushError(error.ToString());}
            Engine.TimeScale=1;
            try{WriteResult();}catch(Exception error){GD.PushError(error.ToString());OS.RemoveLogger(_logger);_logger.Dispose();GetTree().Quit(2);}
        }
    }
    string Required(string name)=>_args.TryGetValue(name,out string? value)&&value.Length>0?value:throw new Exception("Missing --"+name);
    static string SdkVersion=>typeof(Main).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>().SingleOrDefault(a=>a.Key=="GodotSdkVersion")?.Value??"";
    static string Digest(byte[] value)=>Convert.ToHexString(SHA256.HashData(value)).ToLowerInvariant();
    static string Hash(string path)=>Digest(File.ReadAllBytes(path));
    static byte[] Bytes(uint[] words)=>MemoryMarshal.AsBytes(words.AsSpan()).ToArray();
    static uint F(float v)=>BitConverter.SingleToUInt32Bits(v);
    static float Float(uint v)=>BitConverter.UInt32BitsToSingle(v);
    static uint[] Words(JsonElement element)=>element.EnumerateArray().Select(e=>e.GetUInt32()).ToArray();
    static int[] Ints(JsonElement element)=>element.EnumerateArray().Select(e=>e.GetInt32()).ToArray();
    static bool Flag(JsonElement row,string key)=>row.GetProperty(key).GetBoolean();
    void Check(bool pass,string message){_checks++;if(!pass)throw new Exception(message);}
    async Task OnRender(Action action){var done=new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        RenderingServer.CallOnRenderThread(Callable.From(()=>{try{action();done.SetResult();}catch(Exception error){done.SetException(error);}}));await done.Task;}
    async Task Frames(int n){for(int i=0;i<n;i++)await ToSignal(RenderingServer.Singleton,RenderingServer.SignalName.FramePostDraw);}
    async Task Drain(){await Frames(checked((int)_rd.GetFrameDelay()+4));await OnRender(()=>{});}
    Rid Own(Rid rid,uint bytes=0){Check(rid.IsValid,"Allocation failed.");_owned.Add((rid,bytes));return rid;}
    Rid Buffer(uint bytes,bool indirect=false){uint[] words=Enumerable.Repeat(Guard,checked((int)bytes/4)).ToArray();return Own(_rd.StorageBufferCreate(bytes,Bytes(words),(RenderingDevice.StorageBufferUsage)(indirect?1:0)),bytes);}
    void Upload(Rid target,uint[] data,uint offset=0){byte[] bytes=Bytes(data);Check(_rd.BufferUpdate(target,offset,(uint)bytes.Length,bytes)==Error.Ok,"Upload failed.");}
    void Copy(Rid from,Rid to,uint source,uint destination,uint bytes){Check(_rd.BufferCopy(from,to,source,destination,bytes)==Error.Ok,"Tracked copy failed.");}
    byte[] Read(Rid rid){_readbacks++;return _rd.BufferGetData(rid);}
    Rid Pipeline(string path,out Rid shader){using var source=new RDShaderSource{SourceCompute=Godot.FileAccess.GetFileAsString("res://"+path)};
        using var spirv=_rd.ShaderCompileSpirVFromSource(source);Check(string.IsNullOrWhiteSpace(spirv.CompileErrorCompute),spirv.CompileErrorCompute);
        shader=Own(_rd.ShaderCreateFromSpirV(spirv));return Own(_rd.ComputePipelineCreate(shader));}
    Rid Set(Rid shader,bool cache,params(RenderingDevice.UniformType type,Rid[] ids)[] bindings){
        var array=new Godot.Collections.Array<RDUniform>();using var life=(Godot.Collections.Array)array;var items=new List<RDUniform>();
        try{for(int i=0;i<bindings.Length;i++){var u=new RDUniform{UniformType=bindings[i].type,Binding=i};foreach(Rid rid in bindings[i].ids)u.AddId(rid);items.Add(u);array.Add(u);}
            Rid set=cache?UniformSetCacheRD.GetCache(shader,0,array):Own(_rd.UniformSetCreate(array,shader,0));Check(set.IsValid,"Descriptor unavailable.");if(!cache)_ownedSets.Add(set);return set;
        }finally{foreach(var item in items)item.Dispose();}}
    static (RenderingDevice.UniformType,Rid[]) Storage(Rid rid)=>(RenderingDevice.UniformType.StorageBuffer,new[]{rid});
    (RenderingDevice.UniformType,Rid[]) Sample(Rid rid)=>(RenderingDevice.UniformType.SamplerWithTexture,new[]{_sampler,rid});
    ArrayMesh Mesh(Vector3[] vertices){var mesh=new ArrayMesh();using var a=new Godot.Collections.Array();a.Resize((int)Godot.Mesh.ArrayType.Max);
        a[(int)Godot.Mesh.ArrayType.Vertex]=vertices;a[(int)Godot.Mesh.ArrayType.Normal]=Enumerable.Repeat(Vector3.Back,4).ToArray();
        a[(int)Godot.Mesh.ArrayType.Index]=new[]{0,2,1,0,3,2};mesh.AddSurfaceFromArrays(Godot.Mesh.PrimitiveType.Triangles,a);return mesh;}
    void BuildScene(){
        _mesh=Mesh(Vertices);_wallMesh=Mesh(new[]{new Vector3(-1,-1,0),new Vector3(1,-1,0),new Vector3(1,1,0),new Vector3(-1,1,0)});
        _shader=GD.Load<Shader>("res://Consumer.gdshader");_wallShader=GD.Load<Shader>("res://Wall.gdshader");_legacyShader=GD.Load<Shader>("res://Legacy.gdshader");
        _material=new ShaderMaterial{Shader=_shader};_wallMaterial=new ShaderMaterial{Shader=_wallShader};_legacyMaterial=new ShaderMaterial{Shader=_legacyShader};
        _world=new World3D();_view=new SubViewport{Size=new Vector2I(128,64),World3D=_world,TransparentBg=false,
            Scaling3DMode=Viewport.Scaling3DModeEnum.Bilinear,Scaling3DScale=1,RenderTargetUpdateMode=SubViewport.UpdateMode.Disabled};AddChild(_view);_viewport=_view.GetViewportRid();
        _effect=new HistoryCapture{Capture=Capture};_compositor=new Compositor{CompositorEffects=new Godot.Collections.Array<CompositorEffect>{_effect}};
        _camera=new Camera3D{Projection=Camera3D.ProjectionType.Perspective,Fov=60,Near=.1f,Far=20,KeepAspect=Camera3D.KeepAspectEnum.Height,
            Position=new Vector3(0,0,3),CullMask=3,Current=true};_view.AddChild(_camera);
        _environment=new Godot.Environment{BackgroundMode=Godot.Environment.BGMode.Color,BackgroundColor=Colors.Black,FogEnabled=false};
        var worldEnvironment=new WorldEnvironment{Environment=_environment};_view.AddChild(worldEnvironment);
        _wall=new MeshInstance3D{Mesh=_wallMesh,MaterialOverride=_wallMaterial,Position=new Vector3(-2.25f,0,0),Scale=new Vector3(2.75f,4,1),
            Layers=2,Visible=false,CastShadow=GeometryInstance3D.ShadowCastingSetting.Off};_wall.Set("gi_mode",0);_view.AddChild(_wall);
    }
    void Initialize(){
        _current=Buffer(1024);_previous=Buffer(1024);_life=Buffer(256);_state=Buffer(256);_header=Buffer(64);_ids=Buffer(144);
        _fallback=Buffer(64,true);_candidate=Buffer(64,true);_stats=Buffer(64);_pyramid=Buffer(PyramidBytes);
        _rawLedger=Buffer(128*RawStride);_uboLedger=Buffer(128*UboStride);_proofLedger=Buffer(128*ProofStride);
        _copyPipeline=Pipeline("CopyDepth.comp",out _copyShader);_reducePipeline=Pipeline("Reduce.comp",out Rid reduce);
        _selectPipeline=Pipeline("Select.comp",out Rid select);_finalPipeline=Pipeline("Finalize.comp",out Rid final);_capturePipeline=Pipeline("Capture.comp",out _captureShader);
        _reduceSet=Set(reduce,false,Storage(_pyramid));_selectSet=Set(select,false,Storage(_pyramid),Storage(_current),Storage(_state),Storage(_header),Storage(_ids),Storage(_stats));
        _finalSet=Set(final,false,Storage(_stats),Storage(_candidate));using(var sampler=new RDSamplerState{MinFilter=RenderingDevice.SamplerFilter.Nearest,MagFilter=RenderingDevice.SamplerFilter.Nearest})_sampler=Own(_rd.SamplerCreate(sampler));
        string[] names={"current_pose","previous_pose","lifecycle","state","header","identity_bank"};Rid[] buffers={_current,_previous,_life,_state,_header,_ids};
        for(int i=0;i<names.Length;i++){var wrapper=new BufferRD();Check(wrapper.AttachStorageBuffer(buffers[i])==Error.Ok,"BufferRD attach failed.");_wrappers.Add(wrapper);using Variant value=Variant.From(wrapper);_material!.SetShaderParameter(names[i],value);}
        uint[] ids=Enumerable.Repeat(Guard,36).ToArray();for(uint i=0;i<16;i++)ids[i]=i;Upload(_ids,ids);
        uint[] cmd=Enumerable.Repeat(Guard,16).ToArray();Array.Copy(new uint[]{6,16,0,0,0},0,cmd,4,5);Upload(_fallback,cmd);
        _mm=RenderingServer.MultimeshCreate();RenderingServer.MultimeshSetMesh(_mm,_mesh!.GetRid());
        RenderingServer.MultimeshSetCustomAabb(_mm,new Aabb(new Vector3(-4,-2,-3),new Vector3(8,4,4)));
        Check(RenderingServer.MultimeshAttachSharedDrawBuffer(_mm,32,_fallback,16,true)==Error.Ok,"Initial finite E8 fallback rejected.");
        _instance=RenderingServer.InstanceCreate();RenderingServer.InstanceSetBase(_instance,_mm);RenderingServer.InstanceSetScenario(_instance,_world!.Scenario);
        RenderingServer.InstanceSetTransform(_instance,Transform3D.Identity);RenderingServer.InstanceGeometrySetMaterialOverride(_instance,_material!.GetRid());
        RenderingServer.InstanceGeometrySetCastShadowsSetting(_instance,RenderingServer.ShadowCastingSetting.Off);RenderingServer.InstanceSetVisible(_instance,true);
    }
    void Publish(JsonElement scene){Upload(_current,Words(scene.GetProperty("current_pose_words")));Upload(_previous,Words(scene.GetProperty("previous_pose_words")));
        Upload(_life,Words(scene.GetProperty("lifecycle_words")));Upload(_state,Words(scene.GetProperty("state_words")));Upload(_header,Words(scene.GetProperty("header_words")));
        Upload(_ids,Enumerable.Repeat(Guard,20).ToArray(),64);Upload(_candidate,Enumerable.Repeat(Guard,16).ToArray());
        Upload(_stats,Enumerable.Repeat(Guard,16).ToArray());Upload(_pyramid,Enumerable.Repeat(Guard,(int)PyramidBytes/4).ToArray());}
    async Task InitializationRender(){
        _scene=_scenarios.RootElement.GetProperty("records")[0];_camera!.ForceUpdateTransform();await OnRender(()=>Publish(_scene));
        Check(!_configured&&!_registered,"Initialization must precede native registration.");_view!.RenderTargetUpdateMode=SubViewport.UpdateMode.Once;await Frames(2);
        Check(_callbacks==0&&_captures==0&&_readbacks==0&&_logger.Entries.Length==0,"Initialization emitted work/diagnostics.");
        _initialization.Add(new{id="I000",preparation_serial=0,finite_e8_fallback_rendered=true,before_registration=true,observer_captures=0,gpu_readbacks=0,retry_count=0,actual_viewport_records=1});
        _camera.Compositor=_compositor;_view.UseTaa=true;
    }
    void Configure(bool observer=true){
        Check(RenderingServer.ViewportConfigureGpuOcclusionPrepass(_viewport,2,_producer,observer?_effect!.GetRid():default)==Error.Ok,"TAA admission/configuration rejected.");_configured=true;
        Check(RenderingServer.ViewportRegisterGpuOcclusionConsumer(_viewport,_instance,_ids,16,_candidate,16,16)==Error.Ok,"Registration rejected.");_registered=true;
    }
    void Unregister(){if(_registered){Check(RenderingServer.ViewportUnregisterGpuOcclusionConsumer(_viewport,_instance)==Error.Ok,"Unregister failed.");_registered=false;}}
    async Task ResetTaa(){await OnRender(()=>{RenderingServer.ViewportSetUseTaa(_viewport,false);RenderingServer.ViewportSetUseTaa(_viewport,true);});}
    async Task RunRecord(){
        _row=_plan.RootElement.GetProperty("records")[_index];_scene=_scenarios.RootElement.GetProperty("records")[_index];
        _rowCaptures=_rowCallbacks=_rowDispatches=_rowBarriers=0;_producerRecord=null;_probes=new();_callbackFailure="";
        int serial=_index+1;_view!.RenderTargetUpdateMode=SubViewport.UpdateMode.Disabled;
        if(serial==60){
            _camera!.Compositor=null;await OnRender(()=>{RenderingServer.ViewportSetUseTaa(_viewport,false);Unregister();
                Check(RenderingServer.MultimeshAttachSharedDrawBuffer(_mm,32,_fallback,16,false)==Error.Ok,"Real false flag control failed.");
                Check(RenderingServer.ViewportConfigureGpuOcclusionPrepass(_viewport,2,_producer)==Error.Ok,"Legacy configure failed.");
                Check(RenderingServer.ViewportRegisterGpuOcclusionConsumer(_viewport,_instance,_ids,16,_candidate,16,16)==Error.Ok,"Legacy registration failed.");_registered=true;
                RenderingServer.ViewportSetUseTaa(_viewport,true);});
        }else if(serial==61){
            await OnRender(()=>{RenderingServer.ViewportSetUseTaa(_viewport,false);Unregister();RenderingServer.InstanceGeometrySetMaterialOverride(_instance,_material!.GetRid());
                Check(RenderingServer.MultimeshAttachSharedDrawBuffer(_mm,32,_fallback,16,true)==Error.Ok,"E8 fallback recovery failed.");RenderingServer.ViewportSetUseTaa(_viewport,true);});
            _camera!.Compositor=_compositor;await OnRender(()=>Configure());
        }else if(serial==62){await OnRender(()=>RenderingServer.InstanceGeometrySetMaterialOverride(_instance,_legacyMaterial!.GetRid()));
        }else if(serial==63){await OnRender(()=>{Unregister();RenderingServer.InstanceGeometrySetMaterialOverride(_instance,_material!.GetRid());Configure();});}
        if(Flag(_row,"native_taa_reset_before")&&serial!=60&&serial!=61)await ResetTaa();
        int[] extent=Ints(_scene.GetProperty("internal_extent"));_view.Size=new Vector2I(extent[0],extent[1]);
        JsonElement camera=_scene.GetProperty("camera");float[] pos=camera.GetProperty("position").EnumerateArray().Select(x=>x.GetSingle()).ToArray();
        _camera!.Position=new Vector3(pos[0],pos[1],pos[2]);_camera.Rotation=new Vector3(0,camera.GetProperty("yaw").GetSingle(),0);_camera.Fov=camera.GetProperty("fov").GetSingle();
        _camera.ForceUpdateTransform();_wall!.Visible=_scene.GetProperty("wall_visible").GetBoolean();
        await OnRender(()=>{Publish(_scene);if(serial==1)Configure();});
        _captureArmed=Flag(_row,"observer_capture");
        _view.RenderTargetUpdateMode=Flag(_row,"viewport_recorded")?SubViewport.UpdateMode.Once:SubViewport.UpdateMode.Disabled;
        await Frames(2);_captureArmed=false;Check(_callbackFailure.Length==0,_callbackFailure);
        Dictionary<string,object?> outcome=new();await OnRender(()=>{using GDictionary raw=RenderingServer.ViewportGetGpuOcclusionPrepassOutcome(_viewport);outcome=Outcome(raw);});
        Check(_rowCallbacks==(Flag(_row,"producer_invoked")?1:0),"Producer occurrence mismatch "+serial);
        Check(_rowCaptures==(Flag(_row,"observer_capture")?1:0),"Observer occurrence mismatch "+serial);
        bool recorded=Flag(_row,"viewport_recorded"),complete=recorded&&outcome.TryGetValue("frame_recording_completed",out object? c)&&c is true;
        Check(!recorded||complete==Flag(_row,"frame_recording_completed"),"Completed recording differs.");
        bool commit=recorded&&complete&&_rowCaptures==1&&_logger.Entries.Length==0;
        Check(commit==Flag(_row,"history_commit"),"Rendered history commit differs "+serial);if(commit)_commits++;
        _records.Add(new{id=_row.GetProperty("id").GetString(),index=_index,preparation_serial=serial,scene=_scene,
            viewport_recorded=recorded,producer_invoked=_rowCallbacks==1,culler_complete=_rowDispatches==10,observer_capture=_rowCaptures==1,
            history_commit=commit,taa_process_recorded=recorded&&outcome.TryGetValue("taa_process_recorded",out object? t)&&t is true,
            frame_recording_completed=complete,frame_rejected=recorded&&outcome.TryGetValue("mode",out object? m)&&Equals(m,"FRAME_REJECTED"),
            previous_rendered_preparation=_scene.GetProperty("previous_rendered_preparation").GetInt32(),outcome=recorded?outcome:null,producer=_producerRecord,
            capture_count=_rowCaptures,probes=_probes.Select(p=>new{x=p.X,y=p.Y,kind=p.Kind,stable_id=p.StableId,triangle=p.Triangle}).ToArray()});
    }
    static Dictionary<string,object?> Outcome(GDictionary raw){var output=new Dictionary<string,object?>();foreach(Variant k in raw.Keys){string key=k.AsString();Variant v=raw[k];
        output[key]=v.VariantType switch{Variant.Type.Bool=>v.AsBool(),Variant.Type.Int=>v.AsInt64(),Variant.Type.Float=>v.AsDouble(),Variant.Type.String=>v.AsString(),_=>v.ToString()};}return output;}
    static uint[] MatrixWords(Projection projection){var words=new List<uint>();for(int c=0;c<4;c++){Vector4 v=projection[c];words.AddRange(new[]{F(v.X),F(v.Y),F(v.Z),F(v.W)});}return words.ToArray();}
    GDictionary Produce(RenderGpuOcclusionContext context){try{
        _callbacks++;_rowCallbacks++;Check(context.IsActive()&&context.GetViewport()==_viewport&&context.GetViewIndex()==0,"Unexpected native context.");
        long epoch=context.GetEpoch(),generation=context.GetConfigurationGeneration();Check(epoch>_lastEpoch&&generation>0,"Native identity not monotonic.");_lastEpoch=epoch;
        Projection matrix=context.GetProjection()*new Projection(context.GetCameraTransform().AffineInverse());uint[] matrixWords=MatrixWords(matrix);
        Check(matrixWords.All(x=>float.IsFinite(Float(x))),"Nonfinite native projection.");
        _producerRecord=new{invoked=true,complete=false,epoch,configuration_generation=generation,world_to_clip_words=matrixWords,dispatches=0,barriers=0};
        if(_index==62){Check(_rd.ComputeListBegin()==-1,"Nested begin was not quietly rejected.");return new GDictionary();}
        _probes=Probes(matrix);
        long list=context.GetComputeList();Vector2I size=context.GetInternalSize();Rid depth=context.GetDepth();
        Check(size==_view!.Size&&depth.IsValid,"Depth/extent mismatch.");Rid copy=Set(_copyShader,true,Sample(depth),Storage(_pyramid));
        Dispatch(list,_copyPipeline,copy,16,8,Bytes(new[]{(uint)size.X,(uint)size.Y,0u,0u}));Barrier(list);
        for(int level=1;level<8;level++){uint sw=Math.Max(1u,128u>>(level-1)),sh=Math.Max(1u,64u>>(level-1)),dw=Math.Max(1u,128u>>level),dh=Math.Max(1u,64u>>level);
            Dispatch(list,_reducePipeline,_reduceSet,(dw+7)/8,(dh+7)/8,Bytes(new[]{Levels[level-1],sw,sh,0u,Levels[level],dw,dh,0u}));Barrier(list);}
        uint[] push=matrixWords.Concat(new[]{_index==18?0u:16u,_index==19?1u:0u,(uint)epoch,(uint)((ulong)epoch>>32),(uint)generation,(uint)((ulong)generation>>32),0u,0u,(uint)size.X,(uint)size.Y,0u,0u}).ToArray();
        Dispatch(list,_selectPipeline,_selectSet,1,1,Bytes(push));Barrier(list);Dispatch(list,_finalPipeline,_finalSet,1,1);
        _producerRecord=new{invoked=true,complete=true,epoch,configuration_generation=generation,world_to_clip_words=matrixWords,dispatches=10,barriers=9};
        string ack=_row.GetProperty("producer_ack").GetString()!;if(ack=="missing")return new GDictionary();
        return new GDictionary{["epoch"]=ack=="stale"?epoch-1:epoch,["configuration_generation"]=generation,["error"]=(long)(int)(ack=="error"?Error.Failed:Error.Ok)};
    }catch(Exception error){_callbackFailure=error.ToString();return new GDictionary();}}
    void Dispatch(long list,Rid pipeline,Rid set,uint x,uint y,byte[]? push=null){_rd.ComputeListBindComputePipeline(list,pipeline);
        Check(_rd.ComputeListBindUniformSetChecked(list,set,0)==Error.Ok,"Checked set rejected.");if(push!=null)_rd.ComputeListSetPushConstant(list,push,(uint)push.Length);
        Check(_rd.ComputeListDispatchChecked(list,x,y,1)==Error.Ok,"Checked dispatch rejected.");_dispatches++;_rowDispatches++;}
    void Barrier(long list){_rd.ComputeListAddBarrier(list);_barriers++;_rowBarriers++;}
    void Capture(RenderData data){if(!_captureArmed)return;try{
        Check(++_rowCaptures==1,"Duplicate native observer.");var buffers=data.GetRenderSceneBuffers() as RenderSceneBuffersRD??throw new Exception("RD buffers missing.");
        Rid velocity=buffers.GetVelocityTexture(),depth=buffers.GetDepthTexture(),color=buffers.GetColorTexture(),ubo=data.GetRenderSceneData().GetUniformBuffer();
        Check(velocity.IsValid&&depth.IsValid&&color.IsValid&&ubo.IsValid,"Native observer inputs missing.");
        uint row=(uint)_index,proof=row*ProofStride;Copy(ubo,_uboLedger,0,row*UboStride,UboStride);
        Copy(_ids,_proofLedger,0,proof,144);Copy(_fallback,_proofLedger,0,proof+144,64);Copy(_candidate,_proofLedger,0,proof+208,64);
        Copy(_stats,_proofLedger,0,proof+272,64);Copy(_header,_proofLedger,0,proof+336,64);
        Copy(_state,_proofLedger,0,proof+400,16);Copy(_state,_proofLedger,32,proof+416,16);Copy(_state,_proofLedger,112,proof+432,16);
        Copy(_pyramid,_proofLedger,0,proof+512,PyramidBytes);
        var key=(velocity.Id,depth.Id,color.Id);if(!_captureSets.TryGetValue(key,out Rid set)||!_rd.UniformSetIsValid(set)){
            Check(_index<64,"Stress tail changed native capture identities/descriptors.");
            Check(_captureSets.Count<16,"Capture descriptor cache overflow.");set=Set(_captureShader,false,Sample(velocity),Sample(depth),Sample(color),Storage(_rawLedger),Storage(_proofLedger));_captureSets[key]=set;_capturePairsPeak=Math.Max(_capturePairsPeak,_captureSets.Count);}
        uint[] push=Enumerable.Repeat(uint.MaxValue,20).ToArray();push[0]=row;push[1]=row+1;push[2]=(uint)_view!.Size.X;push[3]=(uint)_view.Size.Y;
        Check(_probes.Count<=16,"Probe capacity overflow.");for(int i=0;i<_probes.Count;i++)push[4+i]=(uint)_probes[i].X|((uint)_probes[i].Y<<16);
        long list=_rd.ComputeListBegin();Check(list!=-1,"Observer list unavailable.");_rd.ComputeListBindComputePipeline(list,_capturePipeline);_rd.ComputeListBindUniformSet(list,set,0);
        _rd.ComputeListSetPushConstant(list,Bytes(push),80);_rd.ComputeListDispatch(list,16,8,1);_rd.ComputeListEnd();_captures++;
    }catch(Exception error){_callbackFailure=error.ToString();}}
    static double[] Point(uint[] pose,int id,Vector3 v){int at=id*16;double x=v.X+(double)Float(pose[at+14])*(v.Y+.5),y=v.Y,z=v.Z;
        double Dot(int r)=>Float(pose[at+r])*x+Float(pose[at+r+1])*y+Float(pose[at+r+2])*z+Float(pose[at+r+3]);return new[]{Dot(0),Dot(4),Dot(8),1.};}
    static double Cross(double[] a,double[] b)=>a[0]*b[1]-a[1]*b[0];
    static double[] Sub(double[] a,double[] b)=>new[]{a[0]-b[0],a[1]-b[1]};
    static double Edge(double[] p,double[] a,double[] b){double[] ab=Sub(b,a);return Math.Abs(Cross(ab,Sub(p,a)))/Math.Sqrt(ab[0]*ab[0]+ab[1]*ab[1]);}
    List<Probe> Probes(Projection matrix){var result=new List<Probe>();int[] extent=Ints(_scene.GetProperty("internal_extent"));uint[] pose=Words(_scene.GetProperty("current_pose_words"));
        if(_index!=19)foreach(int id in Ints(_scene.GetProperty("expected_visible_ids"))){
            uint[] matrixWords=MatrixWords(matrix);double[] m=matrixWords.Select(v=>(double)Float(v)).ToArray();
            double[][] points=Vertices.Select(v=>{double[] p=Point(pose,id,v);double[] q=Enumerable.Range(0,4).Select(r=>Enumerable.Range(0,4).Sum(c=>m[c*4+r]*p[c])).ToArray();
                return new[]{(q[0]/q[3]*.5+.5)*extent[0],(q[1]/q[3]*.5+.5)*extent[1]};}).ToArray();
            double[] center={points.Average(p=>p[0]),points.Average(p=>p[1])};var choices=new List<(double distance,int y,int x,int t)>();
            for(int t=0;t<2;t++){int[] tri=Triangles[t];double[] a=points[tri[0]],b=points[tri[1]],c=points[tri[2]];double area=Cross(Sub(b,a),Sub(c,a));
                for(int y=0;y<extent[1];y++)for(int x=0;x<extent[0];x++){double[] p={x+.5,y+.5};double u=Cross(Sub(b,p),Sub(c,p))/area,v=Cross(Sub(c,p),Sub(a,p))/area;
                    if(u>0&&v>0&&1-u-v>0&&Math.Min(Edge(p,a,b),Math.Min(Edge(p,b,c),Edge(p,c,a)))>=2){double dx=p[0]-center[0],dy=p[1]-center[1];choices.Add((Math.Round((dx*dx+dy*dy)*1024,MidpointRounding.ToEven),y,x,t));}}}
            var selected=choices.OrderBy(c=>c.distance).ThenBy(c=>c.y).ThenBy(c=>c.x).ThenBy(c=>c.t).DistinctBy(c=>(c.x,c.y)).Take(4).ToArray();
            Check(selected.Length==4,"Four robust interior pixels missing for ID"+id);foreach(var p in selected)result.Add(new(p.x,p.y,"identity",id,Triangles[p.t]));}
        foreach(JsonElement p in _scenarios.RootElement.GetProperty("background_pixels").EnumerateArray())result.Add(new(p[0].GetInt32(),p[1].GetInt32(),"background",-1,Array.Empty<int>()));return result;}
    async Task Cleanup(){if(_rd==null)return;
        if(_effect!=null){_effect.Capture=null;_effect.Enabled=false;}if(_camera!=null)_camera.Compositor=null;
        await OnRender(()=>{Unregister();if(_configured){Check(RenderingServer.ViewportClearGpuOcclusionPrepass(_viewport)==Error.Ok,"Clear failed.");_configured=false;}
            if(_instance.IsValid)RenderingServer.InstanceSetVisible(_instance,false);
            foreach(string name in new[]{"current_pose","previous_pose","lifecycle","state","header","identity_bank"})_material?.SetShaderParameter(name,default(Variant));});
        await Drain();await OnRender(()=>{if(_instance.IsValid){RenderingServer.FreeRid(_instance);_instance=default;}if(_mm.IsValid){RenderingServer.MultimeshDetachSharedBuffers(_mm);RenderingServer.FreeRid(_mm);_mm=default;}});
        foreach(var wrapper in _wrappers)wrapper.Dispose();_wrappers.Clear();_view?.Free();_view=null;_camera=null;_wall=null;
        _world?.Dispose();_world=null;_environment?.Dispose();_environment=null;_effect?.Dispose();_compositor?.Dispose();_material?.Dispose();_wallMaterial?.Dispose();_legacyMaterial?.Dispose();_mesh?.Dispose();_wallMesh?.Dispose();_shader?.Dispose();_wallShader?.Dispose();_legacyShader?.Dispose();
        await Drain();await OnRender(()=>{foreach(var owned in _owned.ToArray().Reverse()){if(!_ownedSets.Contains(owned.rid)||_rd.UniformSetIsValid(owned.rid))_rd.FreeRid(owned.rid);_owned.Remove(owned);}_ownedSets.Clear();_captureSets.Clear();});
        await Drain();Check(_owned.Count==0&&!_registered&&!_configured,"Caller resources remain.");}
    object Payload(string name,byte[] bytes){string path=Path.Combine(Path.GetDirectoryName(Path.GetFullPath(Required("record")))!,name);File.WriteAllBytes(path,bytes);return new{path=name,bytes=bytes.Length,sha256=Digest(bytes)};}
    string RuntimeAssemblyHash(Assembly assembly,string fallback){string path=string.IsNullOrEmpty(assembly.Location)?fallback:assembly.Location;
        using var stream=File.OpenRead(path);using var pe=new PEReader(stream);MetadataReader metadata=pe.GetMetadataReader();
        Check(metadata.GetGuid(metadata.GetModuleDefinition().Mvid)==assembly.ManifestModule.ModuleVersionId,"Managed runtime/file module mismatch.");return Hash(path);}
    void WriteResult(){string path=Path.GetFullPath(Required("record"));Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        bool passed=_failure.Length==0&&_records.Count==128&&_logger.Entries.Length==0&&_logger.Dropped==0&&_owned.Count==0;
        var payloads=new Dictionary<string,object>();if(_raw.Length>0){payloads["raw"]=Payload("raw.bin",_raw);payloads["ubo"]=Payload("ubo.bin",_ubo);payloads["proof"]=Payload("proof.bin",_proof);}
        var receipt=new{contract="arena-e13c-native-taa-receipt-v1",passed,failure=_failure,checks=_checks,gpu_executed=_records.Count>0,
            plan_sha256=Hash(ProjectSettings.GlobalizePath("res://gate_plan.json")),capture_descriptor_pairs_peak=_capturePairsPeak,unlisted_viewport_renders=0,
            engine_hash=Engine.GetVersionInfo()["hash"].AsString(),engine_sha256=Hash(OS.GetExecutablePath()),sdk_version=SdkVersion,
            driver=RenderingServer.GetCurrentRenderingDriverName(),renderer=RenderingServer.GetCurrentRenderingMethod(),gpu_name=RenderingServer.GetVideoAdapterName(),
            source_sha256=Inputs.ToDictionary(n=>n,n=>Hash(ProjectSettings.GlobalizePath("res://"+n))),
            managed_fixture_assembly_sha256=RuntimeAssemblyHash(typeof(Main).Assembly,ProjectSettings.GlobalizePath("res://.godot/mono/temp/bin/Debug/OcclusionTaaLab.dll")),
            managed_api_assembly_sha256=RuntimeAssemblyHash(typeof(Node).Assembly,Path.Combine(Path.GetDirectoryName(OS.GetExecutablePath())!,"GodotSharp/Api/Debug/GodotSharp.dll")),
            initialization_records=_initialization,records=_records,payloads,callbacks=_callbacks,compute_dispatches=_dispatches,compute_barriers=_barriers,
            observer_dispatches=_captures,observer_captures=_captures,history_commits=_commits,gpu_readbacks=_readbacks,
            diagnostics=_logger.Entries,dropped_diagnostics=_logger.Dropped,caller_owned_final=_owned.Count,caller_requested_bytes_final=_owned.Sum(p=>(long)p.bytes),
            images_written=0,cpu_depth_reads_before_producer=0,per_frame_gpu_readbacks=0,native_api_present=true,
            numerical_validation_required=true,performance_qualified=false,physical_vram_qualified=false,general_compositor_qualified=false,real_vat_qualified=false,resolved_taa_image_quality_qualified=false};
        File.WriteAllBytes(path,JsonSerializer.SerializeToUtf8Bytes(receipt,new JsonSerializerOptions{WriteIndented=true}));
        _plan?.Dispose();_scenarios?.Dispose();OS.RemoveLogger(_logger);_logger.Dispose();GetTree().Quit(passed?0:2);}
}

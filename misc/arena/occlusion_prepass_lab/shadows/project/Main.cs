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

// Source-only reference/contract gate. A boundary PASS is never a combined shadow/occlusion PASS.
public partial class Main : Node
{
    const uint Guard=0xcdcdcdcd, Rows=30, EarlyStride=65600, RawStride=524352, UboStride=5696, ProofStride=2944;
    static readonly string[] Inputs={"ShadowContractLab.csproj","project.godot","Main.tscn","Main.cs","HistoryCapture.cs","ProbeLogger.cs",
        "Consumer.gdshader","Receiver.gdshader","Wall.gdshader","Publish.comp","Capture.comp","gate_plan.json"};
    readonly Dictionary<string,string> _args=new();readonly List<(Rid rid,uint bytes)> _owned=new();readonly HashSet<Rid> _sets=new();
    readonly List<BufferRD> _wrappers=new();readonly Dictionary<(ulong,ulong,ulong),Rid> _captures=new();readonly List<object> _records=new();readonly ProbeLogger _logger=new();
    RenderingDevice _rd=null!;SubViewport? _view;Camera3D? _camera;DirectionalLight3D? _light;World3D? _world;
    BoxMesh? _casterMesh,_wallMesh;PlaneMesh? _groundMesh;Shader? _consumerShader,_receiverShader,_wallShader;
    ShaderMaterial? _consumerMaterial,_receiverMaterial,_wallMaterial;Godot.Environment? _environment;HistoryCapture? _effect;Compositor? _compositor;
    Rid _viewport,_mm,_instance,_current,_previous,_life,_state,_header,_ids,_fallback,_candidate;
    Rid _early,_raw,_ubo,_proof,_sampler,_publishShader,_publishPipeline,_captureShader,_capturePipeline;
    JsonDocument _plan=null!;JsonElement _row;Callable _producer;bool _configured,_registered,_armed,_combined;
    int _index,_checks,_callbacks,_captureCount,_readbacks,_rowCallbacks,_rowCaptures;long _lastEpoch;object? _producerRecord;
    uint[] _previousPoses=Array.Empty<uint>(),_previousActive=new uint[16],_previousGenerations=new uint[16];uint _previousSerial;
    uint[] _publishedCurrent=Array.Empty<uint>(),_publishedActive=Array.Empty<uint>(),_publishedGenerations=Array.Empty<uint>();
    byte[] _earlyBytes=Array.Empty<byte>(),_rawBytes=Array.Empty<byte>(),_uboBytes=Array.Empty<byte>(),_proofBytes=Array.Empty<byte>();
    string _failure="",_callbackFailure="";

    public override async void _Ready(){
        OS.AddLogger(_logger);foreach(string arg in OS.GetCmdlineUserArgs()){string[] p=arg.TrimStart('-').Split('=',2);_args[p[0]]=p.Length==2?p[1]:"true";}
        try{
            string gate=Required("gate");Check(gate is "reference-boundary" or "combined-required","Unknown gate mode.");_combined=gate=="combined-required";
            Check(BitConverter.IsLittleEndian,"Float32 little-endian required.");
            Check(Engine.GetVersionInfo()["hash"].AsString()==Required("expected-engine-hash"),"Native source mismatch.");
            Check(Hash(OS.GetExecutablePath())==Required("expected-engine-sha256")&&SdkVersion==Required("expected-sdk-version"),"Editor/SDK mismatch.");
            Check(RenderingServer.GetCurrentRenderingDriverName()=="vulkan"&&RenderingServer.GetCurrentRenderingMethod()=="forward_plus","Vulkan Forward+ required.");
            _rd=RenderingServer.GetRenderingDevice()??throw new Exception("Global RD unavailable.");
            _plan=JsonDocument.Parse(Godot.FileAccess.GetFileAsString("res://gate_plan.json"));Check(_plan.RootElement.GetProperty("records").GetArrayLength()==Rows,"Frozen matrix differs.");
            Engine.MaxFps=120;BuildScene();_producer=Callable.From<RenderGpuOcclusionContext,GDictionary>(Produce);await OnRender(Initialize);
            _row=_plan.RootElement.GetProperty("initial");ApplyScene(_row);await OnRender(()=>Publish(_row,0));
            // I000: finite E8 full fallback before registration, one explicitly accounted native TAA bootstrap view.
            _view!.RenderTargetUpdateMode=SubViewport.UpdateMode.Once;await Frames(2);Check(_callbacks==0&&_captureCount==0,"Initialization emitted callbacks.");CommitHistory(0);
            _camera!.Compositor=_compositor;
            for(_index=0;_index<Rows;_index++)await RunRecord();
            Check(_captureCount==Rows&&_callbacks==(_combined?10:0),"Native scheduling count differs.");
            await OnRender(()=>{_earlyBytes=Read(_early);_rawBytes=Read(_raw);_uboBytes=Read(_ubo);_proofBytes=Read(_proof);});
            Check(_readbacks==4&&_logger.Entries.Length==0&&_logger.Dropped==0,"Terminal readback/diagnostic contract failed.");
        }catch(Exception error){_failure=error.ToString();GD.PushError(_failure);}
        finally{
            _armed=false;if(_view!=null)_view.RenderTargetUpdateMode=SubViewport.UpdateMode.Disabled;
            try{await Cleanup();}catch(Exception error){_failure+="\nCleanup: "+error;GD.PushError(error.ToString());}
            try{WriteResult();}catch(Exception error){GD.PushError(error.ToString());OS.RemoveLogger(_logger);_logger.Dispose();GetTree().Quit(2);}
        }
    }
    string Required(string key)=>_args.TryGetValue(key,out string? value)&&value.Length>0?value:throw new Exception("Missing --"+key);
    static string SdkVersion=>typeof(Main).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>().SingleOrDefault(a=>a.Key=="GodotSdkVersion")?.Value??"";
    static string Digest(byte[] bytes)=>Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();static string Hash(string path)=>Digest(File.ReadAllBytes(path));
    static byte[] Bytes(uint[] words)=>MemoryMarshal.AsBytes(words.AsSpan()).ToArray();static uint F(float value)=>BitConverter.SingleToUInt32Bits(value);
    static Vector3 Point(JsonElement data)=>new(data[0].GetSingle(),data[1].GetSingle(),data[2].GetSingle());
    void Check(bool condition,string message){_checks++;if(!condition)throw new Exception(message);}
    async Task OnRender(Action action){var done=new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        RenderingServer.CallOnRenderThread(Callable.From(()=>{try{action();done.SetResult();}catch(Exception error){done.SetException(error);}}));await done.Task;}
    async Task Frames(int count){for(int i=0;i<count;i++)await ToSignal(RenderingServer.Singleton,RenderingServer.SignalName.FramePostDraw);}
    async Task Drain(){await Frames(checked((int)_rd.GetFrameDelay()+4));await OnRender(()=>{});}
    Rid Own(Rid rid,uint bytes=0){Check(rid.IsValid,"Allocation failed.");_owned.Add((rid,bytes));return rid;}
    Rid Buffer(uint bytes,bool indirect=false)=>Own(_rd.StorageBufferCreate(bytes,Bytes(Enumerable.Repeat(Guard,(int)bytes/4).ToArray()),(RenderingDevice.StorageBufferUsage)(indirect?1:0)),bytes);
    void Upload(Rid rid,uint[] words,uint offset=0){byte[] bytes=Bytes(words);Check(_rd.BufferUpdate(rid,offset,(uint)bytes.Length,bytes)==Error.Ok,"Upload failed.");}
    void Copy(Rid from,Rid to,uint destination,uint bytes)=>Check(_rd.BufferCopy(from,to,0,destination,bytes)==Error.Ok,"Tracked proof copy failed.");
    byte[] Read(Rid rid){_readbacks++;return _rd.BufferGetData(rid);}
    Rid Pipeline(string path,out Rid shader){using var source=new RDShaderSource{SourceCompute=Godot.FileAccess.GetFileAsString("res://"+path)};
        using var spirv=_rd.ShaderCompileSpirVFromSource(source);Check(string.IsNullOrWhiteSpace(spirv.CompileErrorCompute),spirv.CompileErrorCompute);
        shader=Own(_rd.ShaderCreateFromSpirV(spirv));return Own(_rd.ComputePipelineCreate(shader));}
    Rid Set(Rid shader,bool cached,params(RenderingDevice.UniformType type,Rid[] ids)[] bindings){
        var array=new Godot.Collections.Array<RDUniform>();using var life=(Godot.Collections.Array)array;var uniforms=new List<RDUniform>();
        try{for(int i=0;i<bindings.Length;i++){var uniform=new RDUniform{UniformType=bindings[i].type,Binding=i};foreach(Rid rid in bindings[i].ids)uniform.AddId(rid);uniforms.Add(uniform);array.Add(uniform);}
            Rid set=cached?UniformSetCacheRD.GetCache(shader,0,array):Own(_rd.UniformSetCreate(array,shader,0));Check(set.IsValid,"Descriptor failed.");if(!cached)_sets.Add(set);return set;
        }finally{foreach(var uniform in uniforms)uniform.Dispose();}}
    static(RenderingDevice.UniformType,Rid[]) Storage(Rid rid)=>(RenderingDevice.UniformType.StorageBuffer,new[]{rid});
    (RenderingDevice.UniformType,Rid[]) Sample(Rid rid)=>(RenderingDevice.UniformType.SamplerWithTexture,new[]{_sampler,rid});
    void BuildScene(){
        _casterMesh=new BoxMesh{Size=new Vector3(.6f,1.4f,.6f)};_wallMesh=new BoxMesh{Size=new Vector3(2,3.6f,.2f)};_groundMesh=new PlaneMesh{Size=new Vector2(20,16)};
        _consumerShader=GD.Load<Shader>("res://Consumer.gdshader");_receiverShader=GD.Load<Shader>("res://Receiver.gdshader");_wallShader=GD.Load<Shader>("res://Wall.gdshader");
        _consumerMaterial=new ShaderMaterial{Shader=_consumerShader};_receiverMaterial=new ShaderMaterial{Shader=_receiverShader};_wallMaterial=new ShaderMaterial{Shader=_wallShader};
        _world=new World3D();_view=new SubViewport{World3D=_world,Size=new Vector2I(128,128),UseTaa=true,TransparentBg=false,
            Scaling3DMode=Viewport.Scaling3DModeEnum.Bilinear,Scaling3DScale=1,RenderTargetUpdateMode=SubViewport.UpdateMode.Disabled};AddChild(_view);_viewport=_view.GetViewportRid();
        _camera=new Camera3D{Projection=Camera3D.ProjectionType.Perspective,Fov=45,Near=.1f,Far=40,KeepAspect=Camera3D.KeepAspectEnum.Height,CullMask=3,Current=true};_view.AddChild(_camera);
        _environment=new Godot.Environment{BackgroundMode=Godot.Environment.BGMode.Color,BackgroundColor=Colors.Black,FogEnabled=false};
        _view.AddChild(new WorldEnvironment{Environment=_environment});
        var wall=new MeshInstance3D{Mesh=_wallMesh,MaterialOverride=_wallMaterial,Position=new Vector3(-1.5f,1.8f,0),Layers=2,CastShadow=GeometryInstance3D.ShadowCastingSetting.Off};wall.Set("gi_mode",0);_view.AddChild(wall);
        var ground=new MeshInstance3D{Mesh=_groundMesh,MaterialOverride=_receiverMaterial,Position=new Vector3(0,0,-2),Layers=1,CastShadow=GeometryInstance3D.ShadowCastingSetting.Off};ground.Set("gi_mode",0);_view.AddChild(ground);
        _light=new DirectionalLight3D{ShadowEnabled=true,DirectionalShadowMode=DirectionalLight3D.ShadowMode.Orthogonal,LightEnergy=1,LightColor=Colors.White};
        _light.Set("directional_shadow_pancake_size",0.0f);_light.Set("directional_shadow_max_distance",40.0f);_light.Set("directional_shadow_fade_start",.95f);
        _light.Set("light_angular_distance",0.0f);_light.Set("shadow_bias",.01f);_light.Set("shadow_normal_bias",0.0f);_view.AddChild(_light);
        _effect=new HistoryCapture{Capture=Capture};var effects=new Godot.Collections.Array<CompositorEffect>{_effect};
        using(var effectsLife=(Godot.Collections.Array)effects)_compositor=new Compositor{CompositorEffects=effects};
    }
    void Initialize(){
        _current=Buffer(1024);_previous=Buffer(1024);_life=Buffer(256);_state=Buffer(256);_header=Buffer(64);_ids=Buffer(144);_fallback=Buffer(64,true);_candidate=Buffer(64,true);
        _early=Buffer(Rows*EarlyStride);_raw=Buffer(Rows*RawStride);_ubo=Buffer(Rows*UboStride);_proof=Buffer(Rows*ProofStride);
        _publishPipeline=Pipeline("Publish.comp",out _publishShader);_capturePipeline=Pipeline("Capture.comp",out _captureShader);
        using(var sampler=new RDSamplerState{MinFilter=RenderingDevice.SamplerFilter.Nearest,MagFilter=RenderingDevice.SamplerFilter.Nearest})_sampler=Own(_rd.SamplerCreate(sampler));
        string[] names={"current_pose","previous_pose","lifecycle","state","header","identity_bank"};Rid[] buffers={_current,_previous,_life,_state,_header,_ids};
        for(int i=0;i<names.Length;i++){var wrapper=new BufferRD();Check(wrapper.AttachStorageBuffer(buffers[i])==Error.Ok,"BufferRD attach failed.");_wrappers.Add(wrapper);using Variant value=Variant.From(wrapper);_consumerMaterial!.SetShaderParameter(names[i],value);}
        uint[] ids=Enumerable.Repeat(Guard,36).ToArray();for(uint id=0;id<16;id++)ids[id]=id;Upload(_ids,ids);
        uint[] command=Enumerable.Repeat(Guard,16).ToArray();Array.Copy(new uint[]{36,16,0,0,0},0,command,4,5);Upload(_fallback,command);
        _mm=RenderingServer.MultimeshCreate();RenderingServer.MultimeshSetMesh(_mm,_casterMesh!.GetRid());
        RenderingServer.MultimeshSetCustomAabb(_mm,new Aabb(new Vector3(-8,-1,-3),new Vector3(12,4,6)));
        Check(RenderingServer.MultimeshAttachSharedDrawBuffer(_mm,32,_fallback,16,true)==Error.Ok,"Full E8 fallback rejected.");
        _instance=RenderingServer.InstanceCreate();RenderingServer.InstanceSetBase(_instance,_mm);RenderingServer.InstanceSetScenario(_instance,_world!.Scenario);
        RenderingServer.InstanceSetTransform(_instance,Transform3D.Identity);RenderingServer.InstanceGeometrySetMaterialOverride(_instance,_consumerMaterial!.GetRid());
        RenderingServer.InstanceGeometrySetCastShadowsSetting(_instance,RenderingServer.ShadowCastingSetting.On);RenderingServer.InstanceSetVisible(_instance,true);
    }
    void ApplyScene(JsonElement row){int[] extent=row.GetProperty("internal_extent").EnumerateArray().Select(v=>v.GetInt32()).ToArray();Check(extent.All(v=>v>0&&v<=128),"Extent capacity exceeded.");_view!.Size=new Vector2I(extent[0],extent[1]);
        _camera!.LookAtFromPosition(Point(row.GetProperty("camera_position")),Point(row.GetProperty("target")),Vector3.Up);_camera.ForceUpdateTransform();
        Vector3 lightRay=Point(row.GetProperty("light_ray"));_light!.LookAtFromPosition(new Vector3(0,8,0),new Vector3(0,8,0)+lightRay,Vector3.Up);_light.ForceUpdateTransform();}
    void Publish(JsonElement row,uint serial){
        _publishedCurrent=Enumerable.Repeat(Guard,256).ToArray();_publishedActive=new uint[16];_publishedGenerations=row.GetProperty("generations").EnumerateArray().Select(v=>v.GetUInt32()).ToArray();
        foreach(JsonElement id in row.GetProperty("active_ids").EnumerateArray())_publishedActive[id.GetInt32()]=1;
        for(int id=0;id<16;id++){Vector3 center=Point(row.GetProperty("pose_centers")[id]);uint[] pose={F(1),0,0,F(center.X),0,F(1),0,F(center.Y),0,0,F(1),F(center.Z),_publishedGenerations[id],(uint)id+1,0,serial};Array.Copy(pose,0,_publishedCurrent,id*16,16);}
        if(_previousPoses.Length==0){_previousPoses=(uint[])_publishedCurrent.Clone();_previousActive=(uint[])_publishedActive.Clone();_previousGenerations=(uint[])_publishedGenerations.Clone();}
        uint[] life=new uint[64],state=new uint[64];for(int id=0;id<16;id++){Array.Copy(new[]{_publishedGenerations[id],_previousGenerations[id],_publishedActive[id],_previousActive[id]},0,life,id*4,4);
            Array.Copy(new[]{_publishedActive[id],_publishedActive[id],(uint)id,_publishedGenerations[id]},0,state,id*4,4);}
        uint[] header=Enumerable.Repeat(Guard,16).ToArray();Array.Copy(new uint[]{serial,_previousSerial,16,0xe8000001},0,header,0,4);
        Upload(_current,_publishedCurrent);Upload(_previous,_previousPoses);Upload(_life,life);Upload(_state,state);Upload(_header,header);
        Upload(_ids,Enumerable.Repeat(Guard,20).ToArray(),64);uint[] candidate=Enumerable.Repeat(Guard,16).ToArray();Array.Copy(new uint[]{36,0,0,0,16},0,candidate,4,5);Upload(_candidate,candidate);
        bool casts=!row.TryGetProperty("mode",out JsonElement mode)||mode.GetString()!="counterfactual";
        RenderingServer.InstanceGeometrySetCastShadowsSetting(_instance,casts?RenderingServer.ShadowCastingSetting.On:RenderingServer.ShadowCastingSetting.Off);
    }
    void CommitHistory(uint serial){_previousPoses=(uint[])_publishedCurrent.Clone();_previousActive=(uint[])_publishedActive.Clone();_previousGenerations=(uint[])_publishedGenerations.Clone();_previousSerial=serial;}
    void Configure(){Check(RenderingServer.ViewportConfigureGpuOcclusionPrepass(_viewport,2,_producer,_effect!.GetRid())==Error.Ok,"Configure rejected.");_configured=true;
        Check(RenderingServer.ViewportRegisterGpuOcclusionConsumer(_viewport,_instance,_ids,16,_candidate,16,16)==Error.Ok,"Registration rejected.");_registered=true;}
    void Clear(){if(_registered){Check(RenderingServer.ViewportUnregisterGpuOcclusionConsumer(_viewport,_instance)==Error.Ok,"Unregister failed.");_registered=false;}
        if(_configured){Check(RenderingServer.ViewportClearGpuOcclusionPrepass(_viewport)==Error.Ok,"Clear failed.");_configured=false;}}
    async Task RunRecord(){
        _row=_plan.RootElement.GetProperty("records")[_index];_rowCallbacks=_rowCaptures=0;_producerRecord=null;_callbackFailure="";_view!.RenderTargetUpdateMode=SubViewport.UpdateMode.Disabled;ApplyScene(_row);
        bool requested=_row.GetProperty("mode").GetString()=="requested";await OnRender(()=>{Clear();Publish(_row,(uint)_index+1);if(requested)Configure();});
        _armed=true;_view.RenderTargetUpdateMode=SubViewport.UpdateMode.Once;await Frames(2);_armed=false;Check(_callbackFailure.Length==0,_callbackFailure);Check(_rowCaptures==1,"Exactly one native observer required.");
        Dictionary<string,object?>? outcome=null;if(requested)await OnRender(()=>{using GDictionary data=RenderingServer.ViewportGetGpuOcclusionPrepassOutcome(_viewport);outcome=Outcome(data);});
        Check(_rowCallbacks==(requested&&_combined?1:0),"Producer occurrence differs from selected gate mode.");
        if(requested){bool selected=outcome!.TryGetValue("selected",out object? selectedValue)&&selectedValue is true;
            bool validAck=_row.GetProperty("producer_ack").GetString()=="valid";Check(selected==(_combined&&validAck),"Candidate selection differs.");
            foreach(string key in new[]{"opaque_recorded","taa_process_recorded","frame_recording_completed"})Check(outcome.TryGetValue(key,out object? value)&&value is true,"Native recording incomplete: "+key);
            if(!_combined)Check(outcome.TryGetValue("reason",out object? reason)&&Equals(reason,"unsupported_view"),"Reference did not expose unsupported shadow boundary.");}
        _records.Add(new{index=_index,id=_row.GetProperty("id").GetString(),state_id=_row.GetProperty("state_id").GetString(),mode=_row.GetProperty("mode").GetString(),preparation_serial=_index+1,
            scene=_row,outcome,producer=_producerRecord,capture_count=_rowCaptures,producer_count=_rowCallbacks,frame_recording_completed=true,taa_reset_before=false});
        CommitHistory((uint)_index+1);
    }
    static Dictionary<string,object?> Outcome(GDictionary raw){var result=new Dictionary<string,object?>();foreach(Variant key in raw.Keys){Variant value=raw[key];result[key.AsString()]=value.VariantType switch{
        Variant.Type.Bool=>value.AsBool(),Variant.Type.Int=>value.AsInt64(),Variant.Type.Float=>value.AsDouble(),_=>value.ToString()};}return result;}
    static uint[] MatrixWords(Projection matrix){var result=new List<uint>();for(int i=0;i<4;i++){Vector4 v=matrix[i];result.AddRange(new[]{F(v.X),F(v.Y),F(v.Z),F(v.W)});}return result.ToArray();}
    GDictionary Produce(RenderGpuOcclusionContext context){try{
        Check(_armed&&++_rowCallbacks==1&&context.IsActive()&&context.GetViewport()==_viewport&&context.GetViewIndex()==0,"Unexpected producer.");
        long epoch=context.GetEpoch(),generation=context.GetConfigurationGeneration();Check(epoch>_lastEpoch&&generation>0&&context.GetInternalSize()==_view!.Size,"Invalid native context.");_lastEpoch=epoch;
        Rid set=Set(_publishShader,true,Sample(context.GetDepth()),Storage(_early),Storage(_ids),Storage(_candidate));long list=context.GetComputeList();
        uint[] candidates=_row.GetProperty("candidate_ids").EnumerateArray().Select(v=>v.GetUInt32()).ToArray();Check(candidates.Length<=3,"Candidate capacity exceeded.");
        uint[] push={(uint)_index,(uint)_index+1,(uint)epoch,(uint)((ulong)epoch>>32),(uint)generation,(uint)((ulong)generation>>32),(uint)_view!.Size.X,(uint)_view!.Size.Y,
            (uint)candidates.Length,candidates.Length>0?candidates[0]:Guard,candidates.Length>1?candidates[1]:Guard,candidates.Length>2?candidates[2]:Guard};
        _rd.ComputeListBindComputePipeline(list,_publishPipeline);Check(_rd.ComputeListBindUniformSetChecked(list,set,0)==Error.Ok,"Producer descriptor rejected.");byte[] bytes=Bytes(push);_rd.ComputeListSetPushConstant(list,bytes,(uint)bytes.Length);
        Check(_rd.ComputeListDispatchChecked(list,16,16,1)==Error.Ok,"Producer dispatch rejected.");_callbacks++;
        _producerRecord=new{epoch,configuration_generation=generation,world_to_clip_words=MatrixWords(context.GetProjection()*new Projection(context.GetCameraTransform().AffineInverse())),dispatches=1};
        if(_row.GetProperty("producer_ack").GetString()=="missing")return new GDictionary();
        return new GDictionary{["epoch"]=epoch,["configuration_generation"]=generation,["error"]=(long)(int)Error.Ok};
    }catch(Exception error){_callbackFailure=error.ToString();return new GDictionary();}}
    void Capture(RenderData data){if(!_armed)return;try{
        Check(++_rowCaptures==1,"Duplicate native observer.");using var buffers=data.GetRenderSceneBuffers() as RenderSceneBuffersRD??throw new Exception("RD attachments unavailable.");using var scene=data.GetRenderSceneData();
        Rid velocity=buffers.GetVelocityTexture(),depth=buffers.GetDepthTexture(),color=buffers.GetColorTexture();Check(velocity.IsValid&&depth.IsValid&&color.IsValid,"Native attachments missing.");
        uint proof=(uint)_index*ProofStride;Copy(scene.GetUniformBuffer(),_ubo,(uint)_index*UboStride,UboStride);
        Copy(_ids,_proof,proof,144);Copy(_fallback,_proof,proof+144,64);Copy(_candidate,_proof,proof+208,64);Copy(_header,_proof,proof+272,64);
        Copy(_life,_proof,proof+336,256);Copy(_state,_proof,proof+592,256);Copy(_current,_proof,proof+848,1024);Copy(_previous,_proof,proof+1872,1024);
        var key=(velocity.Id,depth.Id,color.Id);if(!_captures.TryGetValue(key,out Rid set)||!_rd.UniformSetIsValid(set)){
            Check(_captures.Count<4,"Descriptor identity capacity exceeded.");set=Set(_captureShader,false,Sample(velocity),Sample(depth),Sample(color),Storage(_raw));_captures[key]=set;}
        long list=_rd.ComputeListBegin();Check(list!=-1,"Observer list unavailable.");try{
            _rd.ComputeListBindComputePipeline(list,_capturePipeline);Check(_rd.ComputeListBindUniformSetChecked(list,set,0)==Error.Ok,"Observer descriptor rejected.");
            _rd.ComputeListSetPushConstant(list,Bytes(new[]{(uint)_index,(uint)_index+1,(uint)_view!.Size.X,(uint)_view.Size.Y}),16);
            Check(_rd.ComputeListDispatchChecked(list,16,16,1)==Error.Ok,"Observer dispatch rejected.");
        }finally{_rd.ComputeListEnd();}_captureCount++;
    }catch(Exception error){_callbackFailure=error.ToString();}}
    async Task Cleanup(){if(_rd==null)return;if(_effect!=null){_effect.Capture=null;_effect.Enabled=false;}if(_camera!=null)_camera.Compositor=null;
        await OnRender(()=>{Clear();if(_instance.IsValid)RenderingServer.InstanceSetVisible(_instance,false);foreach(string name in new[]{"current_pose","previous_pose","lifecycle","state","header","identity_bank"})_consumerMaterial?.SetShaderParameter(name,default(Variant));});
        await Drain();await OnRender(()=>{if(_instance.IsValid){RenderingServer.FreeRid(_instance);_instance=default;}if(_mm.IsValid){RenderingServer.MultimeshDetachSharedBuffers(_mm);RenderingServer.FreeRid(_mm);_mm=default;}});
        foreach(var wrapper in _wrappers)wrapper.Dispose();_wrappers.Clear();_view?.Free();_view=null;_camera=null;_light=null;_world?.Dispose();_environment?.Dispose();_effect?.Dispose();_compositor?.Dispose();
        _consumerMaterial?.Dispose();_receiverMaterial?.Dispose();_wallMaterial?.Dispose();_casterMesh?.Dispose();_wallMesh?.Dispose();_groundMesh?.Dispose();_consumerShader?.Dispose();_receiverShader?.Dispose();_wallShader?.Dispose();
        await Drain();await OnRender(()=>{foreach(var own in _owned.AsEnumerable().Reverse())if(!_sets.Contains(own.rid)||_rd.UniformSetIsValid(own.rid))_rd.FreeRid(own.rid);_owned.Clear();_sets.Clear();_captures.Clear();});
        await Drain();Check(!_configured&&!_registered&&_owned.Count==0&&_wrappers.Count==0,"Caller resources remain.");}
    object Payload(string name,byte[] bytes){File.WriteAllBytes(Path.Combine(Path.GetDirectoryName(Path.GetFullPath(Required("record")))!,name),bytes);return new{path=name,bytes=bytes.Length,sha256=Digest(bytes)};}
    string RuntimeHash(Assembly assembly,string fallback){string path=string.IsNullOrEmpty(assembly.Location)?fallback:assembly.Location;using var stream=File.OpenRead(path);using var pe=new PEReader(stream);var metadata=pe.GetMetadataReader();
        Check(metadata.GetGuid(metadata.GetModuleDefinition().Mvid)==assembly.ManifestModule.ModuleVersionId,"Runtime/file assembly mismatch.");return Hash(path);}
    void WriteResult(){string path=Path.GetFullPath(Required("record"));Directory.CreateDirectory(Path.GetDirectoryName(path)!);bool passed=_failure.Length==0&&_records.Count==Rows&&_logger.Entries.Length==0&&_logger.Dropped==0&&_owned.Count==0;
        var payloads=new Dictionary<string,object>();if(_rawBytes.Length>0){payloads["early"]=Payload("early.bin",_earlyBytes);payloads["raw"]=Payload("raw.bin",_rawBytes);payloads["ubo"]=Payload("ubo.bin",_uboBytes);payloads["proof"]=Payload("proof.bin",_proofBytes);}
        var receipt=new{contract="arena-e13e-directional-shadow-receipt-v1",gate=Required("gate"),passed,failure=_failure,checks=_checks,gpu_executed=_records.Count>0,
            engine_hash=Engine.GetVersionInfo()["hash"].AsString(),engine_sha256=Hash(OS.GetExecutablePath()),sdk_version=SdkVersion,
            source_sha256=Inputs.ToDictionary(name=>name,name=>Hash(ProjectSettings.GlobalizePath("res://"+name))),plan_sha256=Hash(ProjectSettings.GlobalizePath("res://gate_plan.json")),
            managed_fixture_assembly_sha256=RuntimeHash(typeof(Main).Assembly,ProjectSettings.GlobalizePath("res://.godot/mono/temp/bin/Debug/ShadowContractLab.dll")),
            managed_api_assembly_sha256=RuntimeHash(typeof(Node).Assembly,Path.Combine(Path.GetDirectoryName(OS.GetExecutablePath())!,"GodotSharp/Api/Debug/GodotSharp.dll")),
            driver=RenderingServer.GetCurrentRenderingDriverName(),renderer=RenderingServer.GetCurrentRenderingMethod(),gpu_name=RenderingServer.GetVideoAdapterName(),
            initialization_viewport_records=1,actual_viewport_records=_records.Count+1,records=_records,payloads,callbacks=_callbacks,observer_captures=_captureCount,gpu_readbacks=_readbacks,
            caller_owned_final=_owned.Count,caller_requested_bytes_final=_owned.Sum(p=>(long)p.bytes),registered_consumers=0,diagnostics=_logger.Entries,dropped_diagnostics=_logger.Dropped,
            images_written=0,per_frame_gpu_readbacks=0,unlisted_viewport_renders=0,taa_resets=0,ordinary_shadow_casters=0,logical_slots=16,draw_slots=32,
            requested_ledger_bytes=Rows*(EarlyStride+RawStride+UboStride+ProofStride),native_qualified=false,combined_qualified=false,performance_qualified=false,physical_vram_qualified=false};
        File.WriteAllBytes(path,JsonSerializer.SerializeToUtf8Bytes(receipt,new JsonSerializerOptions{WriteIndented=true}));_plan?.Dispose();OS.RemoveLogger(_logger);_logger.Dispose();GetTree().Quit(passed?0:2);}
}

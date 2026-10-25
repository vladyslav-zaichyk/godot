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

// Ordinary native transforms only. Numerical capture is not an FPS or E8 gate.
public partial class Main : Node
{
    const uint Guard=0xcdcdcdcd, Rows=24, EarlyStride=32832, RawStride=131136, UboStride=5696;
    static readonly string[] Inputs={"BatchingLab.csproj","project.godot","Main.tscn","Main.cs","HistoryCapture.cs","ProbeLogger.cs","Ordinary.gdshader","Early.comp","Capture.comp","gate_plan.json"};
    readonly Dictionary<string,string> _args=new(); readonly List<(Rid rid,uint bytes)> _owned=new();
    readonly HashSet<Rid> _sets=new(); readonly Dictionary<(ulong,ulong,ulong),Rid> _captures=new();
    readonly List<object> _records=new(); readonly ProbeLogger _logger=new(); readonly List<MeshInstance3D> _objects=new();
    RenderingDevice _rd=null!; SubViewport? _view; World3D? _world; Camera3D? _camera;
    ArrayMesh? _mesh; Shader? _shader; ShaderMaterial? _material; Godot.Environment? _environment;
    HistoryCapture? _effect; Compositor? _compositor; Rid _viewport,_early,_raw,_ubo,_sampler,_earlyShader,_earlyPipeline,_captureShader,_capturePipeline;
    JsonDocument _plan=null!; Callable _producer; int _index,_checks,_callbacks,_capturesCount,_readbacks,_rowCallbacks,_rowCaptures;
    bool _armed,_configured; long _lastEpoch; string _failure="",_callbackFailure=""; object? _producerRecord;
    byte[] _earlyBytes=Array.Empty<byte>(),_rawBytes=Array.Empty<byte>(),_uboBytes=Array.Empty<byte>();

    public override async void _Ready(){
        OS.AddLogger(_logger);
        foreach(string arg in OS.GetCmdlineUserArgs()){string[] p=arg.TrimStart('-').Split('=',2);_args[p[0]]=p.Length==2?p[1]:"true";}
        try{
            Check(BitConverter.IsLittleEndian,"Float32 little-endian required.");
            Check(Engine.GetVersionInfo()["hash"].AsString()==Required("expected-engine-hash"),"Native hash mismatch.");
            Check(Hash(OS.GetExecutablePath())==Required("expected-engine-sha256")&&SdkVersion==Required("expected-sdk-version"),"Editor/SDK mismatch.");
            Check(RenderingServer.GetCurrentRenderingDriverName()=="vulkan"&&RenderingServer.GetCurrentRenderingMethod()=="forward_plus","Vulkan Forward+ required.");
            _rd=RenderingServer.GetRenderingDevice()??throw new Exception("Global RD unavailable.");
            _plan=JsonDocument.Parse(Godot.FileAccess.GetFileAsString("res://gate_plan.json"));
            Check(_plan.RootElement.GetProperty("records").GetArrayLength()==Rows,"Frozen row count differs.");
            Engine.MaxFps=120;BuildScene();_producer=Callable.From<RenderGpuOcclusionContext,GDictionary>(Produce);
            await OnRender(Initialize);Publish(_plan.RootElement.GetProperty("initial"),63);
            // One TAA bootstrap render initializes ordinary native previous transforms.
            _view!.RenderTargetUpdateMode=SubViewport.UpdateMode.Once;await Frames(2);
            Check(_callbacks==0&&_capturesCount==0,"Initialization recorded callbacks.");
            _camera!.Compositor=_compositor;
            await OnRender(()=>{Check(RenderingServer.ViewportConfigureGpuOcclusionPrepass(_viewport,2,_producer,_effect!.GetRid())==Error.Ok,"Configure rejected.");_configured=true;});
            for(_index=0;_index<Rows;_index++)await RunRecord();
            Check(_callbacks==Rows&&_capturesCount==Rows,"Incomplete native scheduling.");
            await OnRender(()=>{_earlyBytes=Read(_early);_rawBytes=Read(_raw);_uboBytes=Read(_ubo);});
            Check(_readbacks==3&&_logger.Entries.Length==0&&_logger.Dropped==0,"Readback/diagnostic contract failed.");
        }catch(Exception error){_failure=error.ToString();GD.PushError(_failure);}
        finally{
            _armed=false;if(_view!=null)_view.RenderTargetUpdateMode=SubViewport.UpdateMode.Disabled;
            try{await Cleanup();}catch(Exception error){_failure+="\nCleanup: "+error;GD.PushError(error.ToString());}
            try{WriteResult();}catch(Exception error){GD.PushError(error.ToString());OS.RemoveLogger(_logger);_logger.Dispose();GetTree().Quit(2);}
        }
    }
    string Required(string key)=>_args.TryGetValue(key,out string? value)&&value.Length>0?value:throw new Exception("Missing --"+key);
    static string SdkVersion=>typeof(Main).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>().SingleOrDefault(a=>a.Key=="GodotSdkVersion")?.Value??"";
    static string Digest(byte[] bytes)=>Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    static string Hash(string path)=>Digest(File.ReadAllBytes(path));
    static byte[] Bytes(uint[] words)=>MemoryMarshal.AsBytes(words.AsSpan()).ToArray();
    static uint F(float value)=>BitConverter.SingleToUInt32Bits(value);
    void Check(bool condition,string message){_checks++;if(!condition)throw new Exception(message);}
    async Task OnRender(Action action){var done=new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        RenderingServer.CallOnRenderThread(Callable.From(()=>{try{action();done.SetResult();}catch(Exception error){done.SetException(error);}}));await done.Task;}
    async Task Frames(int count){for(int i=0;i<count;i++)await ToSignal(RenderingServer.Singleton,RenderingServer.SignalName.FramePostDraw);}
    async Task Drain(){await Frames(checked((int)_rd.GetFrameDelay()+4));await OnRender(()=>{});}
    Rid Own(Rid rid,uint bytes=0){Check(rid.IsValid,"Allocation failed.");_owned.Add((rid,bytes));return rid;}
    Rid Buffer(uint bytes)=>Own(_rd.StorageBufferCreate(bytes,Bytes(Enumerable.Repeat(Guard,(int)bytes/4).ToArray())),bytes);
    byte[] Read(Rid rid){_readbacks++;return _rd.BufferGetData(rid);}
    Rid Pipeline(string path,out Rid shader){using var source=new RDShaderSource{SourceCompute=Godot.FileAccess.GetFileAsString("res://"+path)};
        using var spirv=_rd.ShaderCompileSpirVFromSource(source);Check(string.IsNullOrWhiteSpace(spirv.CompileErrorCompute),spirv.CompileErrorCompute);
        shader=Own(_rd.ShaderCreateFromSpirV(spirv));return Own(_rd.ComputePipelineCreate(shader));}
    Rid Set(Rid shader,bool cached,params (RenderingDevice.UniformType type,Rid[] ids)[] bindings){
        var array=new Godot.Collections.Array<RDUniform>();using var arrayLife=(Godot.Collections.Array)array;var uniforms=new List<RDUniform>();
        try{for(int i=0;i<bindings.Length;i++){var uniform=new RDUniform{UniformType=bindings[i].type,Binding=i};foreach(Rid rid in bindings[i].ids)uniform.AddId(rid);uniforms.Add(uniform);array.Add(uniform);}
            Rid set=cached?UniformSetCacheRD.GetCache(shader,0,array):Own(_rd.UniformSetCreate(array,shader,0));Check(set.IsValid,"Descriptor failed.");if(!cached)_sets.Add(set);return set;
        }finally{foreach(var uniform in uniforms)uniform.Dispose();}}
    (RenderingDevice.UniformType,Rid[]) Sample(Rid rid)=>(RenderingDevice.UniformType.SamplerWithTexture,new[]{_sampler,rid});
    static (RenderingDevice.UniformType,Rid[]) Storage(Rid rid)=>(RenderingDevice.UniformType.StorageBuffer,new[]{rid});
    static Vector3 Point(JsonElement data)=>new(data[0].GetSingle(),data[1].GetSingle(),data[2].GetSingle());
    static Transform3D Affine(JsonElement data){float[] a=data.EnumerateArray().Select(v=>v.GetSingle()).ToArray();return new Transform3D(
        new Basis(new Vector3(a[0],a[4],a[8]),new Vector3(a[1],a[5],a[9]),new Vector3(a[2],a[6],a[10])),new Vector3(a[3],a[7],a[11]));}
    void BuildScene(){
        _mesh=new ArrayMesh();using(var arrays=new Godot.Collections.Array()){arrays.Resize((int)Godot.Mesh.ArrayType.Max);
            arrays[(int)Godot.Mesh.ArrayType.Vertex]=_plan.RootElement.GetProperty("vertices").EnumerateArray().Select(Point).ToArray();
            arrays[(int)Godot.Mesh.ArrayType.Normal]=Enumerable.Repeat(Vector3.Back,4).ToArray();arrays[(int)Godot.Mesh.ArrayType.Index]=new[]{0,2,1,0,3,2};_mesh.AddSurfaceFromArrays(Godot.Mesh.PrimitiveType.Triangles,arrays);}
        _shader=GD.Load<Shader>("res://Ordinary.gdshader");_material=new ShaderMaterial{Shader=_shader};_world=new World3D();
        _view=new SubViewport{World3D=_world,Size=new Vector2I(128,64),TransparentBg=false,UseTaa=true,
            Scaling3DMode=Viewport.Scaling3DModeEnum.Bilinear,Scaling3DScale=1,RenderTargetUpdateMode=SubViewport.UpdateMode.Disabled};AddChild(_view);_viewport=_view.GetViewportRid();
        _camera=new Camera3D{Projection=Camera3D.ProjectionType.Perspective,Fov=60,Near=.1f,Far=200,
            KeepAspect=Camera3D.KeepAspectEnum.Height,CullMask=3,Current=true};_view.AddChild(_camera);
        _environment=new Godot.Environment{BackgroundMode=Godot.Environment.BGMode.Color,BackgroundColor=Colors.Black,FogEnabled=false};
        _view.AddChild(new WorldEnvironment{Environment=_environment});
        for(int id=0;id<6;id++){var mesh=new MeshInstance3D{Mesh=_mesh,MaterialOverride=_material,Layers=2,CastShadow=GeometryInstance3D.ShadowCastingSetting.Off};
            mesh.Set("gi_mode",0);_view.AddChild(mesh);mesh.SetInstanceShaderParameter("identity",(id+1)/32.0f);_objects.Add(mesh);}
        _effect=new HistoryCapture{Capture=Capture};var effects=new Godot.Collections.Array<CompositorEffect>{_effect};
        using(var effectsLife=(Godot.Collections.Array)effects)_compositor=new Compositor{CompositorEffects=effects};
    }
    void Publish(JsonElement row,int phaseMask){
        _camera!.Position=Point(row.GetProperty("camera"));_camera.ForceUpdateTransform();
        JsonElement roots=row.GetProperty("roots");for(int id=0;id<6;id++){_objects[id].Transform=Affine(roots[id]);_objects[id].Layers=(phaseMask&(1<<id))!=0?2u:1u;_objects[id].ForceUpdateTransform();}
    }
    void Initialize(){_early=Buffer(Rows*EarlyStride);_raw=Buffer(Rows*RawStride);_ubo=Buffer(Rows*UboStride);
        _earlyPipeline=Pipeline("Early.comp",out _earlyShader);_capturePipeline=Pipeline("Capture.comp",out _captureShader);
        using var sampler=new RDSamplerState{MinFilter=RenderingDevice.SamplerFilter.Nearest,MagFilter=RenderingDevice.SamplerFilter.Nearest};_sampler=Own(_rd.SamplerCreate(sampler));}
    async Task RunRecord(){
        JsonElement row=_plan.RootElement.GetProperty("records")[_index];_rowCallbacks=_rowCaptures=0;_producerRecord=null;_callbackFailure="";
        _view!.RenderTargetUpdateMode=SubViewport.UpdateMode.Disabled;Publish(row,row.GetProperty("phase_mask").GetInt32());
        // Settle queued owner metadata before the measured render; the viewport remains disabled.
        await OnRender(()=>{});_armed=true;_view.RenderTargetUpdateMode=SubViewport.UpdateMode.Once;await Frames(2);_armed=false;
        Check(_callbackFailure.Length==0,_callbackFailure);Check(_rowCallbacks==1&&_rowCaptures==1,"Exactly one producer/observer required.");
        Dictionary<string,object?> outcome=new();await OnRender(()=>{using GDictionary data=RenderingServer.ViewportGetGpuOcclusionPrepassOutcome(_viewport);outcome=Outcome(data);});
        Check(outcome.TryGetValue("selected",out object? selected)&&selected is true,"Native candidate acknowledgment missing.");
        foreach(string key in new[]{"opaque_recorded","taa_process_recorded","frame_recording_completed"})Check(outcome.TryGetValue(key,out object? value)&&value is true,"Native recording incomplete: "+key);
        Check(outcome.TryGetValue("auto_instancing_disabled",out object? disabled)&&disabled is false,"Opaque batching still disabled.");
        Check(Convert.ToInt64(outcome["original_opaque_repeat_group_count"])==1,"Fixture failed to form one original compatible repeat group.");
        _records.Add(new{index=_index,serial=_index+1,id=row.GetProperty("id").GetString(),outcome,producer=_producerRecord});
    }
    static Dictionary<string,object?> Outcome(GDictionary raw){var result=new Dictionary<string,object?>();foreach(Variant key in raw.Keys){Variant value=raw[key];result[key.AsString()]=value.VariantType switch{
        Variant.Type.Bool=>value.AsBool(),Variant.Type.Int=>value.AsInt64(),Variant.Type.Float=>value.AsDouble(),_=>value.ToString()};}return result;}
    static uint[] MatrixWords(Projection matrix){var result=new List<uint>();for(int i=0;i<4;i++){Vector4 v=matrix[i];result.AddRange(new[]{F(v.X),F(v.Y),F(v.Z),F(v.W)});}return result.ToArray();}
    GDictionary Produce(RenderGpuOcclusionContext context){try{
        Check(_armed&&++_rowCallbacks==1&&context.IsActive()&&context.GetViewport()==_viewport&&context.GetViewIndex()==0,"Unexpected producer.");
        long epoch=context.GetEpoch(),generation=context.GetConfigurationGeneration();Check(epoch>_lastEpoch&&generation>0&&context.GetInternalSize()==new Vector2I(128,64),"Invalid native context.");_lastEpoch=epoch;
        uint[] matrix=MatrixWords(context.GetProjection()*new Projection(context.GetCameraTransform().AffineInverse()));
        Rid set=Set(_earlyShader,true,Sample(context.GetDepth()),Storage(_early));long list=context.GetComputeList();
        Dispatch(list,_earlyPipeline,set,new[]{(uint)_index,(uint)_index+1,(uint)epoch,(uint)((ulong)epoch>>32),(uint)generation,(uint)((ulong)generation>>32),128u,64u});
        _callbacks++;_producerRecord=new{epoch,configuration_generation=generation,world_to_clip_words=matrix};
        return new GDictionary{["epoch"]=epoch,["configuration_generation"]=generation,["error"]=(long)(int)Error.Ok};
    }catch(Exception error){_callbackFailure=error.ToString();return new GDictionary();}}
    void Dispatch(long list,Rid pipeline,Rid set,uint[] push){_rd.ComputeListBindComputePipeline(list,pipeline);
        Check(_rd.ComputeListBindUniformSetChecked(list,set,0)==Error.Ok,"Checked descriptor rejected.");byte[] bytes=Bytes(push);_rd.ComputeListSetPushConstant(list,bytes,(uint)bytes.Length);
        Check(_rd.ComputeListDispatchChecked(list,16,8,1)==Error.Ok,"Checked dispatch rejected.");}
    void Capture(RenderData data){if(!_armed)return;try{
        Check(++_rowCaptures==1,"Duplicate observer.");using var buffers=data.GetRenderSceneBuffers() as RenderSceneBuffersRD??throw new Exception("RD buffers unavailable.");
        using var scene=data.GetRenderSceneData();Rid velocity=buffers.GetVelocityTexture(),depth=buffers.GetDepthTexture(),color=buffers.GetColorTexture();
        Check(velocity.IsValid&&depth.IsValid&&color.IsValid,"Native attachments unavailable.");
        Check(_rd.BufferCopy(scene.GetUniformBuffer(),_ubo,0,(uint)_index*UboStride,UboStride)==Error.Ok,"Native UBO capture failed.");
        var key=(velocity.Id,depth.Id,color.Id);if(!_captures.TryGetValue(key,out Rid set)||!_rd.UniformSetIsValid(set)){
            Check(_captures.Count<4,"Descriptor identity capacity exceeded.");set=Set(_captureShader,false,Sample(velocity),Sample(depth),Sample(color),Storage(_raw));_captures[key]=set;}
        long list=_rd.ComputeListBegin();Check(list!=-1,"Observer list unavailable.");try{Dispatch(list,_capturePipeline,set,new[]{(uint)_index,(uint)_index+1,128u,64u});}finally{_rd.ComputeListEnd();}_capturesCount++;
    }catch(Exception error){_callbackFailure=error.ToString();}}
    async Task Cleanup(){if(_rd==null)return;
        if(_effect!=null){_effect.Capture=null;_effect.Enabled=false;}if(_camera!=null)_camera.Compositor=null;
        await OnRender(()=>{if(_configured){Check(RenderingServer.ViewportClearGpuOcclusionPrepass(_viewport)==Error.Ok,"Native clear failed.");_configured=false;}});
        await Drain();_view?.Free();_view=null;_objects.Clear();_camera=null;_world?.Dispose();_environment?.Dispose();_effect?.Dispose();_compositor?.Dispose();_material?.Dispose();_mesh?.Dispose();_shader?.Dispose();
        await Drain();await OnRender(()=>{foreach(var own in _owned.AsEnumerable().Reverse()){if(!_sets.Contains(own.rid)||_rd.UniformSetIsValid(own.rid))_rd.FreeRid(own.rid);}_owned.Clear();_sets.Clear();_captures.Clear();});
        await Drain();Check(!_configured&&_owned.Count==0,"Caller resources remain.");}
    object Payload(string name,byte[] bytes){File.WriteAllBytes(Path.Combine(Path.GetDirectoryName(Path.GetFullPath(Required("record")))!,name),bytes);return new{path=name,bytes=bytes.Length,sha256=Digest(bytes)};}
    string RuntimeHash(Assembly assembly,string fallback){string path=string.IsNullOrEmpty(assembly.Location)?fallback:assembly.Location;
        using var stream=File.OpenRead(path);using var pe=new PEReader(stream);var metadata=pe.GetMetadataReader();Check(metadata.GetGuid(metadata.GetModuleDefinition().Mvid)==assembly.ManifestModule.ModuleVersionId,"Runtime/file assembly mismatch.");return Hash(path);}
    void WriteResult(){string path=Path.GetFullPath(Required("record"));Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        bool passed=_failure.Length==0&&_records.Count==Rows&&_logger.Entries.Length==0&&_logger.Dropped==0&&_owned.Count==0;
        var payloads=new Dictionary<string,object>();if(_earlyBytes.Length>0){payloads["early"]=Payload("early.bin",_earlyBytes);payloads["raw"]=Payload("raw.bin",_rawBytes);payloads["ubo"]=Payload("ubo.bin",_uboBytes);}
        var receipt=new{contract="arena-e13d-opaque-batching-receipt-v1",passed,failure=_failure,checks=_checks,gpu_executed=_records.Count>0,
            engine_hash=Engine.GetVersionInfo()["hash"].AsString(),engine_sha256=Hash(OS.GetExecutablePath()),sdk_version=SdkVersion,
            source_sha256=Inputs.ToDictionary(name=>name,name=>Hash(ProjectSettings.GlobalizePath("res://"+name))),plan_sha256=Hash(ProjectSettings.GlobalizePath("res://gate_plan.json")),
            managed_fixture_assembly_sha256=RuntimeHash(typeof(Main).Assembly,ProjectSettings.GlobalizePath("res://.godot/mono/temp/bin/Debug/BatchingLab.dll")),
            managed_api_assembly_sha256=RuntimeHash(typeof(Node).Assembly,Path.Combine(Path.GetDirectoryName(OS.GetExecutablePath())!,"GodotSharp/Api/Debug/GodotSharp.dll")),
            driver=RenderingServer.GetCurrentRenderingDriverName(),renderer=RenderingServer.GetCurrentRenderingMethod(),gpu_name=RenderingServer.GetVideoAdapterName(),
            initialization_viewport_records=1,records=_records,payloads,callbacks=_callbacks,observer_captures=_capturesCount,gpu_readbacks=_readbacks,
            caller_owned_final=_owned.Count,diagnostics=_logger.Entries,dropped_diagnostics=_logger.Dropped,images_written=0,per_frame_gpu_readbacks=0,
            registered_consumers=0,ordinary_owners=6,requested_ledger_bytes=Rows*(EarlyStride+RawStride+UboStride),native_qualified=false,performance_qualified=false,physical_vram_qualified=false,e8_qualified=false};
        File.WriteAllBytes(path,JsonSerializer.SerializeToUtf8Bytes(receipt,new JsonSerializerOptions{WriteIndented=true}));
        _plan?.Dispose();OS.RemoveLogger(_logger);_logger.Dispose();GetTree().Quit(passed?0:2);}
}

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

public partial class Main : Node
{
    const string NativeCommit="7d59f5064a6cd40a1cf2aaa578b8e90d29d1cbe8";
    const uint Guard=0xcdcdcdcdu, PyramidWords=2739;
    static readonly uint[] Offsets={4,2052,2564,2692,2724,2732,2734};
    static readonly string[] Inputs={"OcclusionReferenceLab.csproj","project.godot","Main.tscn","Main.cs","ProbeLogger.cs","Depth.vert","Depth.frag","CopyDepth.comp","Reduce.comp","Produce.comp","oracle.json"};
    readonly Dictionary<string,string> _args=new();
    readonly List<(Rid rid,uint bytes)> _owned=new();
    readonly List<object> _probes=new(),_cycles=new();
    readonly ProbeLogger _logger=new();
    RenderingDevice _rd=null!;
    Rid _depth,_fb,_sampler,_pyramid,_candidates,_visible,_commands;
    Rid _rasterPipeline,_copyPipeline,_reducePipeline,_producePipeline,_copySet,_reduceSet,_produceSet;
    bool _computeActive,_drawActive,_overflow;
    string _case="",_mode="reference",_failure="";
    int _checks;
    JsonDocument _oracle=null!;
    public override async void _Ready()
    {
        OS.AddLogger(_logger);
        foreach(string item in OS.GetCmdlineUserArgs()) { string[] pair=item.TrimStart('-').Split('=',2); _args[pair[0]]=pair.Length==2?pair[1]:"true"; }
        try {
            _case=Required("case"); _mode=Required("mode");
            Check(_mode is "reference" or "native","Unknown route.");
            Check(BitConverter.IsLittleEndian,"Little endian ABI required.");
            Check(Required("expected-engine-hash")==NativeCommit && Engine.GetVersionInfo()["hash"].AsString()==NativeCommit,"Wrong immutable editor.");
            Check(Hash(OS.GetExecutablePath())==Required("expected-engine-sha256") && SdkVersion==Required("expected-sdk-version"),"Wrong editor/SDK payload.");
            _oracle=JsonDocument.Parse(Godot.FileAccess.GetFileAsString("res://oracle.json"));
            Check(_oracle.RootElement.GetProperty("cases").TryGetProperty(_case,out _),"Unknown case.");
            if(_mode=="reference") {
                Check(RenderingServer.GetCurrentRenderingDriverName()=="vulkan" && RenderingServer.GetCurrentRenderingMethod()=="forward_plus","Vulkan/Forward+ required.");
                _rd=RenderingServer.GetRenderingDevice() ?? throw new Exception("Global RD unavailable."); Engine.MaxFps=60;
                await OnRender(()=> {
                    bool cycle=_case=="lifetime"; if(!cycle) CreateResources();
                    foreach(JsonElement probe in _oracle.RootElement.GetProperty("cases").GetProperty(_case).EnumerateArray()) {
                        if(cycle) CreateResources(); RunProbe(probe);
                        if(cycle) { Cleanup(); Check(_owned.Count==0,"Cycle resources remain."); _cycles.Add(new {name=probe.GetProperty("name").GetString(),caller_owned_final=_owned.Count,caller_requested_bytes_final=_owned.Sum(p=>(long)p.bytes)}); }
                    }
                });
            }
        } catch(Exception error) { _failure=error.ToString(); GD.PushError(_failure); }
        finally {
            try {
                if(_rd!=null) await OnRender(Cleanup);
                await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame); await OnRender(()=>{});
                await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
                Check(_owned.Count==0 && !_computeActive && !_drawActive,"Resource/list ledger not drained.");
                Check(_logger.Entries.Length==0 && _logger.Dropped==0,"Unexpected native diagnostics.");
            } catch(Exception error) { _failure+="\nCleanup: "+error; GD.PushError(error.ToString()); }
            try { WriteResult(); }
            catch(Exception error) {
                GD.PushError(error.ToString());
                try { File.WriteAllBytes(Path.GetFullPath(Required("record")),JsonSerializer.SerializeToUtf8Bytes(new {contract="proxy-occlusion-case-v1",case_name=_case,passed=false,failure=error.ToString()})); }
                finally { OS.RemoveLogger(_logger); _logger.Dispose(); GetTree().Quit(2); }
            }
        }
    }
    string Required(string key)=>_args.TryGetValue(key,out string? v)&&v.Length>0?v:throw new Exception("Missing --"+key);
    static string SdkVersion=>typeof(Main).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>().SingleOrDefault(a=>a.Key=="GodotSdkVersion")?.Value??"";
    static string Hash(string path)=>Digest(File.ReadAllBytes(path));
    static string Digest(byte[] bytes)=>Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    static byte[] Bytes(uint[] words)=>MemoryMarshal.AsBytes(words.AsSpan()).ToArray();
    static byte[] Words(params uint[] words)=>Bytes(words);
    void Check(bool good,string message) { _checks++; if(!good) throw new Exception(message); }
    async Task OnRender(Action action) {
        var done=new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        RenderingServer.CallOnRenderThread(Callable.From(()=>{try{action();done.SetResult();}catch(Exception e){done.SetException(e);}})); await done.Task;
    }
    Rid Own(Rid rid,uint bytes=0) { Check(rid.IsValid,"RD allocation failed."); _owned.Add((rid,bytes)); return rid; }
    Rid Compute(string file,out Rid shader) {
        using var source=new RDShaderSource{SourceCompute=Godot.FileAccess.GetFileAsString("res://"+file)};
        using var spirv=_rd.ShaderCompileSpirVFromSource(source); Check(string.IsNullOrWhiteSpace(spirv.CompileErrorCompute),spirv.CompileErrorCompute);
        shader=Own(_rd.ShaderCreateFromSpirV(spirv)); return Own(_rd.ComputePipelineCreate(shader));
    }
    Rid Storage(uint words) { uint[] data=Enumerable.Repeat(Guard,(int)words).ToArray(); return Own(_rd.StorageBufferCreate(words*4,Bytes(data)),words*4); }
    Rid Set(Rid shader,params (RenderingDevice.UniformType type,Rid[] ids)[] bindings) {
        var uniforms=new Godot.Collections.Array<RDUniform>(); using var lifetime=(Godot.Collections.Array)uniforms; var items=new List<RDUniform>();
        try { for(int i=0;i<bindings.Length;i++) { var item=new RDUniform{UniformType=bindings[i].type,Binding=i}; foreach(Rid id in bindings[i].ids) item.AddId(id); uniforms.Add(item);items.Add(item); }
            return Own(_rd.UniformSetCreate(uniforms,shader,0));
        } finally {foreach(var item in items)item.Dispose();}
    }
    void CreateResources() {
        Check(_owned.Count==0,"Repeated creation without cleanup.");
        var usage=RenderingDevice.TextureUsageBits.DepthStencilAttachmentBit|RenderingDevice.TextureUsageBits.SamplingBit;
        Check(_rd.TextureIsFormatSupportedForUsage(RenderingDevice.DataFormat.D32Sfloat,usage),"D32 sampled depth unavailable.");
        using(var format=new RDTextureFormat{Format=RenderingDevice.DataFormat.D32Sfloat,Width=64,Height=32,Depth=1,ArrayLayers=1,Mipmaps=1,TextureType=RenderingDevice.TextureType.Type2D,UsageBits=usage})
        using(var view=new RDTextureView()) _depth=Own(_rd.TextureCreate(format,view),64*32*4);
        var attachments=new Godot.Collections.Array<Rid>{_depth}; using(var lifetime=(Godot.Collections.Array)attachments) _fb=Own(_rd.FramebufferCreate(attachments));
        using(var sampler=new RDSamplerState{MinFilter=RenderingDevice.SamplerFilter.Nearest,MagFilter=RenderingDevice.SamplerFilter.Nearest}) _sampler=Own(_rd.SamplerCreate(sampler));
        using(var source=new RDShaderSource{SourceVertex=Godot.FileAccess.GetFileAsString("res://Depth.vert"),SourceFragment=Godot.FileAccess.GetFileAsString("res://Depth.frag")}) {
            using var spirv=_rd.ShaderCompileSpirVFromSource(source); Check(string.IsNullOrWhiteSpace(spirv.CompileErrorVertex)&&string.IsNullOrWhiteSpace(spirv.CompileErrorFragment),spirv.CompileErrorVertex+spirv.CompileErrorFragment);
            Rid shader=Own(_rd.ShaderCreateFromSpirV(spirv)); using var raster=new RDPipelineRasterizationState{CullMode=RenderingDevice.PolygonCullMode.Disabled};
            using var msaa=new RDPipelineMultisampleState(); using var depth=new RDPipelineDepthStencilState{EnableDepthTest=true,EnableDepthWrite=true,DepthCompareOperator=RenderingDevice.CompareOperator.Greater}; using var blend=new RDPipelineColorBlendState();
            _rasterPipeline=Own(_rd.RenderPipelineCreate(shader,_rd.FramebufferGetFormat(_fb),-1,RenderingDevice.RenderPrimitive.Triangles,raster,msaa,depth,blend));
        }
        _pyramid=Storage(PyramidWords); _candidates=Storage(16*8); _visible=Storage(28);
        _commands=Own(_rd.StorageBufferCreate(64,Bytes(Enumerable.Repeat(Guard,16).ToArray()),RenderingDevice.StorageBufferUsage.Indirect),64);
        _copyPipeline=Compute("CopyDepth.comp",out Rid copy); _reducePipeline=Compute("Reduce.comp",out Rid reduce); _producePipeline=Compute("Produce.comp",out Rid produce);
        _copySet=Set(copy,(RenderingDevice.UniformType.SamplerWithTexture,new[]{_sampler,_depth}),(RenderingDevice.UniformType.StorageBuffer,new[]{_pyramid}));
        _reduceSet=Set(reduce,(RenderingDevice.UniformType.StorageBuffer,new[]{_pyramid}));
        _produceSet=Set(produce,(RenderingDevice.UniformType.StorageBuffer,new[]{_pyramid}),(RenderingDevice.UniformType.StorageBuffer,new[]{_candidates}),
            (RenderingDevice.UniformType.StorageBuffer,new[]{_visible}),(RenderingDevice.UniformType.StorageBuffer,new[]{_commands}));
    }
    void Upload(Rid rid,uint[] words) { Check(!_drawActive&&!_computeActive,"Upload inside list."); byte[] data=Bytes(words); Check(_rd.BufferUpdate(rid,0,(uint)data.Length,data)==Error.Ok,"Buffer update failed."); }
    void Dispatch(Rid pipeline,Rid set,uint x,uint y=1,byte[]? pc=null) {
        long list=_rd.ComputeListBegin(); _computeActive=true; _rd.ComputeListBindComputePipeline(list,pipeline);
        Check(_rd.ComputeListBindUniformSetChecked(list,set,0)==Error.Ok,"Checked binding failed.");
        if(pc!=null) _rd.ComputeListSetPushConstant(list,pc,(uint)pc.Length);
        Check(_rd.ComputeListDispatchChecked(list,x,y,1)==Error.Ok,"Checked dispatch failed.");
        _rd.ComputeListEnd(); _computeActive=false;
    }
    void RunProbe(JsonElement probe) {
        Check(_probes.Count<4,"Probe capacity exceeded."); int shift=probe.GetProperty("camera_shift").GetInt32();
        uint[] candidates=new uint[16*8]; int ordinal=0;
        foreach(JsonElement item in _oracle.RootElement.GetProperty("candidates").EnumerateArray()) {
            int at=ordinal++*8; int[] r=item.GetProperty("rect").EnumerateArray().Select(v=>v.GetInt32()).ToArray();
            candidates[at]=item.GetProperty("id").GetUInt32(); candidates[at+1]=unchecked((uint)(r[0]-shift)); candidates[at+2]=(uint)r[1];
            candidates[at+3]=unchecked((uint)(r[2]-shift)); candidates[at+4]=(uint)r[3]; candidates[at+5]=BitConverter.SingleToUInt32Bits(item.GetProperty("depth").GetSingle()); candidates[at+6]=item.GetProperty("flags").GetUInt32();
        }
        Check(ordinal==16,"Exactly sixteen candidates required."); Upload(_candidates,candidates);
        Upload(_visible,Enumerable.Repeat(Guard,28).ToArray()); Upload(_commands,Enumerable.Repeat(Guard,16).ToArray()); Upload(_pyramid,Enumerable.Repeat(Guard,(int)PyramidWords).ToArray());
        long draw=_rd.DrawListBegin(_fb,RenderingDevice.DrawFlags.ClearDepth,Array.Empty<Color>(),0.0f); _drawActive=true;
        if(probe.GetProperty("wall").GetBoolean()) {
            _rd.DrawListBindRenderPipeline(draw,_rasterPipeline);
            foreach((float x0,float x1) in new[]{(4.0f-shift,28.0f-shift),(36.0f-shift,60.0f-shift)}) {
                _rd.DrawListSetPushConstant(draw,Words(BitConverter.SingleToUInt32Bits(x0),0,BitConverter.SingleToUInt32Bits(x1),BitConverter.SingleToUInt32Bits(32.0f)),16);
                _rd.DrawListDraw(draw,false,1,6);
            }
        }
        _rd.DrawListEnd(); _drawActive=false;
        Dispatch(_copyPipeline,_copySet,8,4);
        for(int level=1;level<7;level++) {
            uint sw=Math.Max(1u,64u>>(level-1)),sh=Math.Max(1u,32u>>(level-1)),dw=Math.Max(1u,64u>>level),dh=Math.Max(1u,32u>>level);
            Dispatch(_reducePipeline,_reduceSet,(dw+7)/8,(dh+7)/8,Words(Offsets[level-1],sw,sh,0,Offsets[level],dw,dh,0));
        }
        uint capacity=probe.GetProperty("capacity").GetUInt32(),de=probe.GetProperty("depth_epoch").GetUInt32(),qe=probe.GetProperty("query_epoch").GetUInt32();
        Check(capacity<=16,"Capacity outside bounded bank."); Dispatch(_producePipeline,_produceSet,1,pc:Words(16,capacity,de,qe));
        // No readback happens before the producer. Every following read is terminal evidence.
        byte[] vb=_rd.BufferGetData(_visible),cb=_rd.BufferGetData(_commands),hb=_rd.BufferGetData(_pyramid);
        Check(vb.Length==112 && cb.Length==64 && hb.Length==PyramidWords*4,"Readback byte size differs.");
        uint[] v=MemoryMarshal.Cast<byte,uint>(vb.AsSpan()).ToArray(),c=MemoryMarshal.Cast<byte,uint>(cb.AsSpan()).ToArray(),h=MemoryMarshal.Cast<byte,uint>(hb.AsSpan()).ToArray();
        bool overflow=v[6]!=0; _overflow|=overflow;
        Check(v.Take(4).Concat(v.Skip(24)).All(x=>x==Guard)&&c.Take(4).Concat(c.Skip(9)).All(x=>x==Guard)&&h.Take(4).Concat(h.Skip(2735)).All(x=>x==Guard),"Guard corruption.");
        Check(v[4]<=capacity && v[5]<=16 && v[7]==qe && c[4]==6 && c[5]==v[4] && c[6]==0 && c[7]==0 && c[8]==0,"Publication fields differ.");
        Check(!overflow || (v[4]==0 && c[5]==0),"Overflow must reject, not truncate units.");
        _probes.Add(new {name=probe.GetProperty("name").GetString(),visible_words=v,command_words=c,pyramid_words=h,
            visible_sha256=Digest(vb),command_sha256=Digest(cb),pyramid_sha256=Digest(hb),producer_completed_before_readback=true});
    }
    void Cleanup() {
        if(_drawActive){_rd.DrawListEnd();_drawActive=false;} if(_computeActive){_rd.ComputeListEnd();_computeActive=false;}
        if(_visible.IsValid) _rd.BufferGetData(_visible);
        foreach(var item in _owned.ToArray().Reverse()){_rd.FreeRid(item.rid);_owned.Remove(item);} _visible=default;_pyramid=default;_depth=default;
    }
    string AssemblyHash(Assembly assembly,string fallback) {
        string path=string.IsNullOrEmpty(assembly.Location)?fallback:assembly.Location; using var stream=File.OpenRead(path);using var pe=new PEReader(stream);MetadataReader metadata=pe.GetMetadataReader();
        Check(metadata.GetGuid(metadata.GetModuleDefinition().Mvid)==assembly.ManifestModule.ModuleVersionId,"Loaded module/file mismatch.");return Hash(path);
    }
    void WriteResult() {
        bool failed=_failure.Length>0,unavailable=_mode=="native",rejected=_overflow;
        string status=failed?"failed":unavailable?"unavailable":rejected?"capacity_rejected":"reference_complete";
        bool passed=!failed&&!unavailable&&!rejected;
        string fixture=ProjectSettings.GlobalizePath("res://.godot/mono/temp/bin/Debug/OcclusionReferenceLab.dll"),api=Path.Combine(Path.GetDirectoryName(OS.GetExecutablePath())!,"GodotSharp/Api/Debug/GodotSharp.dll");
        var raw=new {contract="proxy-occlusion-case-v1",case_name=_case,mode=_mode,status,passed,failure=_failure,checks=_checks,
            proxy_reference_qualified=passed,native_qualified=false,performance_qualified=false,release_driver_qualified=false,
            engine_hash=Engine.GetVersionInfo()["hash"].AsString(),engine_sha256=Hash(OS.GetExecutablePath()),sdk_version=SdkVersion,
            managed_fixture_assembly_sha256=AssemblyHash(typeof(Main).Assembly,fixture),managed_api_assembly_sha256=AssemblyHash(typeof(Node).Assembly,api),
            source_sha256=Inputs.ToDictionary(name=>name,name=>Hash(ProjectSettings.GlobalizePath("res://"+name))),
            driver=RenderingServer.GetCurrentRenderingDriverName(),renderer=RenderingServer.GetCurrentRenderingMethod(),gpu_name=RenderingServer.GetVideoAdapterName(),
            probes=_probes,lifetime_cycles=_cycles,diagnostics=_logger.Entries,dropped_diagnostics=_logger.Dropped,
            resources=new {caller_owned_final=_owned.Count,caller_requested_bytes_final=_owned.Sum(p=>(long)p.bytes),open_lists_final=(_drawActive?1:0)+(_computeActive?1:0)},
            images_written=0,native_api_present=false};
        string path=Path.GetFullPath(Required("record"));Directory.CreateDirectory(Path.GetDirectoryName(path)!);File.WriteAllBytes(path,JsonSerializer.SerializeToUtf8Bytes(raw,new JsonSerializerOptions{WriteIndented=true}));
        _oracle?.Dispose();OS.RemoveLogger(_logger);_logger.Dispose();GetTree().Quit(failed?2:unavailable?4:rejected?3:0);
    }
}

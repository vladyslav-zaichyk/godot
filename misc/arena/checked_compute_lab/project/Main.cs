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
    const string NativeCommit = "7d59f5064a6cd40a1cf2aaa578b8e90d29d1cbe8";
    const uint Guard = 0xcdcdcdcdu;
    const uint Salt = 0xa511e9b3u;
    const uint RecordCount = 8, OutputWords = 40;
    const RenderingDevice.StorageBufferUsage Indirect = RenderingDevice.StorageBufferUsage.Indirect;
    static readonly string[] Inputs = { "CheckedComputeLab.csproj", "project.godot", "Main.tscn", "Main.cs", "ProbeLogger.cs", "oracle.json" };
    static readonly string[] Cases = { "metadata_recovery", "pending_and_recorded_aliases", "arguments_first_recovery",
        "dormant_pipeline_activation", "barriers_end_repeat_zero", "push_constant_history" };
    readonly Dictionary<string, string> _args = new();
    readonly List<(Rid rid, uint bytes)> _owned = new();
    readonly List<object> _calls = new(), _probes = new();
    readonly ProbeLogger _logger = new();
    RenderingDevice _rd = null!;
    RenderingDevice? _local;
    Rid _foreign;
    Rid _arguments, _plainArguments, _persistent, _ubo, _safeArguments, _output;
    Rid _consumer, _consumerB, _read, _write, _dormant, _producer, _noPc;
    Rid _consumerSet, _consumerBSet, _readSet, _writeSet, _dormantOutput, _dormantArgs, _dormantSafe, _producerSet, _noPcSet;
    string _case = "", _failure = "";
    bool _listActive;
    int _checks;

    const string Header = "#version 450\nlayout(local_size_x=4,local_size_y=1,local_size_z=1) in;\n";
    const string Output = "layout(set=0,binding=0,std430) buffer Result { uint words[]; } result;\n";
    const string Pc = "layout(push_constant,std430) uniform Params { uint seed; uint unused1; uint unused2; uint unused3; } pc;\n";
    const string Emit = """
void emit_record(uint seed, uint value) {
    uint i = gl_GlobalInvocationID.x;
    if (i >= 8u || gl_GlobalInvocationID.y != 0u || gl_GlobalInvocationID.z != 0u) {
        atomicAdd(result.words[36], 1u);
        return;
    }
    uint at = 4u + i * 4u;
    atomicAdd(result.words[at], seed + i * 17u);
    atomicAdd(result.words[at + 1u], i);
    atomicAdd(result.words[at + 2u], seed ^ 0xa511e9b3u ^ i ^ value);
    atomicAdd(result.words[at + 3u], 1u);
}
""";

    public override async void _Ready()
    {
        OS.AddLogger(_logger);
        foreach (string argument in OS.GetCmdlineUserArgs()) {
            string[] pair = argument.TrimStart('-').Split('=', 2);
            _args[pair[0]] = pair.Length == 2 ? pair[1] : "true";
        }
        try {
            _case = Required("case");
            Check(Cases.Contains(_case), "Unknown case.");
            Check(BitConverter.IsLittleEndian, "Little-endian numeric ABI required.");
            Check(Required("expected-engine-hash") == NativeCommit && Engine.GetVersionInfo()["hash"].AsString() == NativeCommit, "Wrong native source.");
            Check(SdkVersion == Required("expected-sdk-version"), "Wrong compiled SDK.");
            Check(Hash(OS.GetExecutablePath()) == Required("expected-engine-sha256"), "Wrong executable payload.");
            Check(RenderingServer.GetCurrentRenderingDriverName() == "vulkan" && RenderingServer.GetCurrentRenderingMethod() == "forward_plus", "Vulkan/Forward+ required.");
            _rd = RenderingServer.GetRenderingDevice() ?? throw new Exception("Global RD unavailable.");
            Engine.MaxFps = 60;
            await OnRender(() => { CreateResources(); Produce(); ExecuteCase(); });
        } catch (Exception error) { _failure = error.ToString(); GD.PushError(_failure); }
        finally {
            try {
                if (_rd != null) await OnRender(Cleanup);
                // Observe late callbacks while our logger is still installed.
                await ToSignal(GetTree(), SceneTree.SignalName.ProcessFrame);
                await OnRender(() => { });
                await ToSignal(GetTree(), SceneTree.SignalName.ProcessFrame);
                Check(_owned.Count == 0 && _local == null && !_foreign.IsValid && !_listActive, "Caller resources remain.");
                Check(_logger.Dropped == 0 && _logger.Entries.Length == 0, "Unexpected native diagnostic.");
            } catch (Exception error) { _failure += "\nCleanup: " + error; GD.PushError(error.ToString()); }
            try { WriteResult(); }
            catch (Exception error) {
                GD.PushError(error.ToString());
                try { File.WriteAllBytes(Path.GetFullPath(Required("record")), JsonSerializer.SerializeToUtf8Bytes(new {
                    contract = "checked-compute-case-v1", case_name = _case, passed = false, failure = error.ToString() })); }
                finally { OS.RemoveLogger(_logger); _logger.Dispose(); GetTree().Quit(2); }
            }
        }
    }

    string Required(string key) => _args.TryGetValue(key, out string? value) && value.Length > 0 ? value : throw new Exception("Missing --" + key);
    static string SdkVersion => typeof(Main).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>().SingleOrDefault(a => a.Key == "GodotSdkVersion")?.Value ?? "";
    static string BytesHash(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    static string Hash(string path) => BytesHash(File.ReadAllBytes(path));
    static byte[] Bytes(uint[] words) => MemoryMarshal.AsBytes(words.AsSpan()).ToArray();
    static byte[] Words(params uint[] words) => Bytes(words);
    void Check(bool value, string message) { _checks++; if (!value) throw new Exception(message); }
    void Call(string operation, Error actual, Error expected = Error.Ok) {
        Check(_calls.Count < 128, "Call ledger capacity exceeded."); _calls.Add(new { operation, error = (int)actual });
        Check(actual == expected, operation + " returned " + actual + ", expected " + expected);
    }
    async Task OnRender(Action action) {
        var done = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        RenderingServer.CallOnRenderThread(Callable.From(() => { try { action(); done.SetResult(); } catch (Exception error) { done.SetException(error); } }));
        await done.Task;
    }
    Rid Own(Rid rid, uint bytes = 0) { Check(rid.IsValid, "RD allocation failed."); _owned.Add((rid, bytes)); return rid; }
    Rid Shader(string source) {
        using var code = new RDShaderSource { SourceCompute = source };
        using var spirv = _rd.ShaderCompileSpirVFromSource(code);
        Check(string.IsNullOrWhiteSpace(spirv.CompileErrorCompute), spirv.CompileErrorCompute);
        return Own(_rd.ShaderCreateFromSpirV(spirv));
    }
    Rid Pipeline(string source, out Rid shader) { shader = Shader(source); return Own(_rd.ComputePipelineCreate(shader)); }
    Rid Set(Rid shader, uint index, params Rid[] buffers) {
        var uniforms = new Godot.Collections.Array<RDUniform>(); using var lifetime = (Godot.Collections.Array)uniforms;
        var items = new List<RDUniform>();
        try {
            for (int i = 0; i < buffers.Length; i++) {
                var item = new RDUniform { UniformType = RenderingDevice.UniformType.StorageBuffer, Binding = i };
                item.AddId(buffers[i]); uniforms.Add(item); items.Add(item);
            }
            return Own(_rd.UniformSetCreate(uniforms, shader, index));
        } finally { foreach (var item in items) item.Dispose(); }
    }
    void CreateResources() {
        Check(_rd.LimitGet(RenderingDevice.Limit.MaxComputeWorkgroupCountX) >= 2, "Two legal compute groups required.");
        _arguments = Own(_rd.StorageBufferCreate(64, new byte[64], Indirect), 64);
        _plainArguments = Own(_rd.StorageBufferCreate(64, new byte[64]), 64);
        _safeArguments = Own(_rd.StorageBufferCreate(64, Words(0,0,0,0,2,1,1,0,2,1,1,0,0,1,1,0)), 64);
        _output = Own(_rd.StorageBufferCreate(OutputWords * 4, InitialOutput()), OutputWords * 4);
        string consumerSource = Header + Output + Pc + Emit + "void main() { emit_record(pc.seed, 0u); }\n";
        _consumer = Pipeline(consumerSource, out Rid consumerShader); _consumerSet = Set(consumerShader, 0, _output);
        // Distinct shader RID forces native shader-change push-constant invalidation.
        _consumerB = Pipeline(consumerSource, out Rid consumerBShader); _consumerBSet = Set(consumerBShader, 0, _output);
        string aliasDeclaration = "layout(set=0,binding=1,std430) QUALIFIER buffer Args { uint words[]; } args;\n";
        _read = Pipeline(Header + Output + aliasDeclaration.Replace("QUALIFIER", "readonly") + Pc + Emit +
            "void main() { emit_record(pc.seed, args.words[4]); }\n", out Rid readShader);
        _write = Pipeline(Header + Output + aliasDeclaration.Replace("QUALIFIER", "") + Pc + Emit +
            "void main() { emit_record(pc.seed, args.words[4]); if (gl_GlobalInvocationID.x == 0u) args.words[7] = pc.seed; }\n", out Rid writeShader);
        _readSet = Set(readShader, 0, _output, _arguments); _writeSet = Set(writeShader, 0, _output, _arguments);
        _dormant = Pipeline(Header + Output + Pc + Emit +
            "layout(set=1,binding=0,std430) readonly buffer Args { uint words[]; } args;\nvoid main() { emit_record(pc.seed, args.words[4]); }\n", out Rid dormantShader);
        _dormantOutput = Set(dormantShader, 0, _output); _dormantArgs = Set(dormantShader, 1, _arguments); _dormantSafe = Set(dormantShader, 1, _safeArguments);
        string producerSource = "#version 450\nlayout(local_size_x=1) in;\nlayout(set=0,binding=0,std430) buffer Args { uint words[]; } args;\n" + Pc +
            "void main() { args.words[4]=2u; args.words[5]=1u; args.words[6]=1u; args.words[7]=pc.seed; args.words[8]=2u; args.words[9]=1u; args.words[10]=1u; args.words[12]=0u; args.words[13]=1u; args.words[14]=1u; }\n";
        _producer = Pipeline(producerSource, out Rid producerShader); _producerSet = Set(producerShader, 0, _arguments);
        _noPc = Pipeline(Header + Output + Emit + "void main() { emit_record(63u, 0u); }\n", out Rid noPcShader); _noPcSet = Set(noPcShader, 0, _output);
    }
    static byte[] InitialOutput() {
        uint[] words = Enumerable.Repeat(Guard, (int)OutputWords).ToArray();
        Array.Clear(words, 4, (int)RecordCount * 4);
        return Bytes(words);
    }
    void ResetOutput() { Check(!_listActive, "Reset inside list."); byte[] bytes = InitialOutput(); Check(_rd.BufferUpdate(_output, 0, (uint)bytes.Length, bytes) == Error.Ok, "Output reset failed."); }
    long Begin() { Check(!_listActive, "Nested compute list."); long list = _rd.ComputeListBegin(); Check(list >= 0, "Compute list unavailable."); _listActive = true; return list; }
    void End() { _rd.ComputeListEnd(); _listActive = false; }
    void Bind(long list, Rid pipeline, Rid set, uint seed) { _rd.ComputeListBindComputePipeline(list, pipeline); Call("bind", _rd.ComputeListBindUniformSetChecked(list, set, 0)); PcValue(list, seed); }
    void PcValue(long list, uint seed) => _rd.ComputeListSetPushConstant(list, Words(seed,0,0,0), 16);
    void Direct(long list, string operation, Error expected = Error.Ok, uint groups = 2) => Call(operation, _rd.ComputeListDispatchChecked(list, groups, 1, 1), expected);
    void IndirectCall(long list, string operation, Error expected = Error.Ok, long offset = 16, Rid bank = default) =>
        Call(operation, _rd.ComputeListDispatchIndirectChecked(list, bank.IsValid ? bank : _arguments, offset), expected);
    void Produce() { long list = Begin(); Bind(list, _producer, _producerSet, 0); Direct(list, "produce", groups: 1); End(); }

    void ExecuteCase() {
        if (_case == "metadata_recovery") Metadata();
        else if (_case == "pending_and_recorded_aliases") Aliases();
        else if (_case == "arguments_first_recovery") ArgumentsFirst();
        else if (_case == "dormant_pipeline_activation") Dormant();
        else if (_case == "barriers_end_repeat_zero") Barriers();
        else if (_case == "push_constant_history") PushConstants();
        else throw new Exception("Unimplemented case.");
    }
    void Metadata() {
        _persistent = Own(_rd.StorageBufferCreate(64, Array.Empty<byte>(), Indirect, (RenderingDevice.BufferCreationBits)4), 64);
        _ubo = Own(_rd.UniformBufferCreate(16, new byte[16]), 16);
        _local = _rd.CreateLocalDevice() ?? throw new Exception("Local device unavailable.");
        _foreign = _local.StorageBufferCreate(64, new byte[64], Indirect); Check(_foreign.IsValid, "Foreign allocation failed.");
        long list = Begin(); Bind(list, _consumer, _consumerSet, 11);
        Call("wrong_list", _rd.ComputeListDispatchIndirectChecked(-1, _arguments, 16), Error.InvalidParameter);
        Call("missing_rid", _rd.ComputeListDispatchIndirectChecked(list, default, 16), Error.InvalidParameter);
        foreach ((string label, long offset) in new[] { ("negative", -4L), ("unaligned", 1L), ("past_end", 56L),
            ("wrap", (long)uint.MaxValue - 3), ("wide", (long)uint.MaxValue + 1), ("signed_max", long.MaxValue) })
            Call(label, _rd.ComputeListDispatchIndirectChecked(list, _arguments, offset), Error.InvalidParameter);
        foreach ((string label, Rid bank) in new[] { ("no_indirect", _plainArguments), ("persistent", _persistent), ("wrong_owner", _ubo), ("foreign_device", _foreign) })
            Call(label, _rd.ComputeListDispatchIndirectChecked(list, bank, 16), Error.InvalidParameter);
        Call("bad_index", _rd.ComputeListBindUniformSetChecked(list, _consumerSet, uint.MaxValue), Error.InvalidParameter);
        Direct(list, "zero_direct", Error.InvalidParameter, 0);
        Call("failed_candidate_not_reserved", _rd.ComputeListBindUniformSetChecked(list, _readSet, 0));
        Call("restore_consumer", _rd.ComputeListBindUniformSetChecked(list, _consumerSet, 0));
        IndirectCall(list, "valid_recovery"); End(); Snapshot("recovered", (11, 0, 1));
    }
    void Aliases() {
        foreach ((string kind, Rid pipeline, Rid set) in new[] { ("ro", _read, _readSet), ("rw", _write, _writeSet) }) {
            ResetOutput(); long list = Begin(); Bind(list, pipeline, set, 21);
            IndirectCall(list, kind + "_pending_reject", Error.InvalidParameter);
            Direct(list, kind + "_pending_recovery"); End(); Snapshot(kind + "_pending", (21, 2, 1));
            ResetOutput(); list = Begin(); Bind(list, pipeline, set, 22); Direct(list, kind + "_record_shader");
            Bind(list, _consumer, _consumerSet, 23);
            IndirectCall(list, kind + "_recorded_reject", Error.InvalidParameter);
            Direct(list, kind + "_recorded_recovery"); End(); Snapshot(kind + "_recorded", (22, 2, 1), (23, 0, 1));
        }
    }
    void ArgumentsFirst() {
        long list = Begin(); Bind(list, _consumer, _consumerSet, 31); IndirectCall(list, "reserve");
        Call("reject_ro_bind", _rd.ComputeListBindUniformSetChecked(list, _readSet, 0), Error.InvalidParameter);
        Direct(list, "retained_direct");
        Call("reject_rw_bind", _rd.ComputeListBindUniformSetChecked(list, _writeSet, 0), Error.InvalidParameter);
        IndirectCall(list, "retained_indirect"); End(); Snapshot("retained", (31, 0, 3));
    }
    void Dormant() {
        long list = Begin(); Bind(list, _consumer, _consumerSet, 41);
        Call("stage_dormant", _rd.ComputeListBindUniformSetChecked(list, _dormantArgs, 1));
        IndirectCall(list, "reserve_with_dormant"); _rd.ComputeListBindComputePipeline(list, _dormant); PcValue(list, 42);
        Direct(list, "activated_direct_reject", Error.InvalidParameter);
        IndirectCall(list, "activated_indirect_reject", Error.InvalidParameter);
        Call("replace_dormant", _rd.ComputeListBindUniformSetChecked(list, _dormantSafe, 1));
        Direct(list, "activated_recovery"); End(); Snapshot("dormant_recovered", (41, 0, 1), (42, 2, 1));
    }
    void Barriers() {
        long list = Begin(); Bind(list, _producer, _producerSet, 0); Direct(list, "producer_before_barrier", groups: 1);
        _rd.ComputeListAddBarrier(list); Bind(list, _consumer, _consumerSet, 51);
        IndirectCall(list, "zero_first", offset: 48);
        Call("zero_reserved_bind_reject", _rd.ComputeListBindUniformSetChecked(list, _readSet, 0), Error.InvalidParameter);
        IndirectCall(list, "first"); IndirectCall(list, "repeated_offset", offset: 32); IndirectCall(list, "zero_groups", offset: 48);
        End(); Snapshot("repeat_zero", (51, 0, 2));
        ResetOutput(); list = Begin(); Bind(list, _write, _writeSet, 52); Direct(list, "end_reuse"); End(); Snapshot("end_reuse", (52, 2, 1));
        ResetOutput(); list = Begin(); Bind(list, _producer, _producerSet, 0); Direct(list, "producer_for_restore", groups: 1);
        _rd.ComputeListAddBarrier(list); IndirectCall(list, "restored_producer_reject", Error.InvalidParameter);
        Direct(list, "restored_producer_direct", groups: 1); Bind(list, _consumer, _consumerSet, 53);
        IndirectCall(list, "recorded_after_barrier_reject", Error.InvalidParameter);
        _rd.ComputeListAddBarrier(list); IndirectCall(list, "after_second_barrier"); End(); Snapshot("restored_producer", (53, 0, 1));
        ResetOutput(); list = Begin(); Bind(list, _consumer, _consumerSet, 54); IndirectCall(list, "before_role_change");
        _rd.ComputeListAddBarrier(list); Bind(list, _write, _writeSet, 55); Direct(list, "barrier_role_change");
        End(); Snapshot("barrier_reuse", (54, 0, 1), (55, 2, 1));
    }
    void PushConstants() {
        long list = Begin(); Bind(list, _consumer, _consumerSet, 61); IndirectCall(list, "pc_a");
        _rd.ComputeListBindComputePipeline(list, _consumerB); Call("bind_b", _rd.ComputeListBindUniformSetChecked(list, _consumerBSet, 0));
        Direct(list, "missing_pc", Error.InvalidParameter); _rd.ComputeListAddBarrier(list);
        Direct(list, "missing_pc_after_barrier", Error.InvalidParameter);
        IndirectCall(list, "missing_pc_indirect", Error.InvalidParameter);
        PcValue(list, 62); Direct(list, "pc_recovery"); _rd.ComputeListAddBarrier(list); Direct(list, "supplied_pc_restore");
        End(); Snapshot("pc_recovered", (61, 0, 1), (62, 0, 2));
        ResetOutput(); list = Begin(); Bind(list, _consumer, _consumerSet, 64); Direct(list, "before_zero_pc");
        _rd.ComputeListBindComputePipeline(list, _noPc); Call("bind_zero_pc", _rd.ComputeListBindUniformSetChecked(list, _noPcSet, 0));
        _rd.ComputeListAddBarrier(list); Direct(list, "zero_pc_after_barrier"); End(); Snapshot("zero_pc_pipeline", (64, 0, 1), (63, 0, 1));
    }
    void Snapshot(string name, params (uint seed, uint value, uint count)[] contributions) {
        Check(!_listActive && _probes.Count < 8, "Invalid readback point/capacity.");
        byte[] bytes = _rd.BufferGetData(_output); Check(bytes.Length == OutputWords * 4, "Output byte size differs.");
        uint[] actual = MemoryMarshal.Cast<byte, uint>(bytes.AsSpan()).ToArray();
        uint[] expected = MemoryMarshal.Cast<byte, uint>(InitialOutput().AsSpan()).ToArray();
        foreach (var (seed, value, count) in contributions) {
            for (uint i = 0; i < RecordCount; i++) {
                expected[4 + i * 4] = unchecked(expected[4 + i * 4] + (seed + i * 17) * count);
                expected[5 + i * 4] = unchecked(expected[5 + i * 4] + i * count);
                expected[6 + i * 4] = unchecked(expected[6 + i * 4] + (seed ^ Salt ^ i ^ value) * count);
                expected[7 + i * 4] += count;
            }
        }
        Check(actual.SequenceEqual(expected), "Numerical output/count/sentinel mismatch: " + name);
        _probes.Add(new { name, words = actual, sha256 = BytesHash(bytes) });
    }
    void Cleanup() {
        if (_listActive) End();
        // Terminal readback drains this bounded stand, not a general-purpose caller fence.
        if (_output.IsValid) _rd.BufferGetData(_output);
        if (_local != null) { if (_foreign.IsValid) { _local.FreeRid(_foreign); _foreign = default; } _local.Free(); _local = null; }
        foreach (var item in _owned.ToArray().Reverse()) { _rd.FreeRid(item.rid); _owned.Remove(item); }
    }
    string AssemblyHash(Assembly assembly, string fallback) {
        string path = string.IsNullOrEmpty(assembly.Location) ? fallback : assembly.Location;
        using var stream = File.OpenRead(path); using var pe = new PEReader(stream);
        MetadataReader metadata = pe.GetMetadataReader();
        Check(metadata.GetGuid(metadata.GetModuleDefinition().Mvid) == assembly.ManifestModule.ModuleVersionId, "Loaded module differs from its hashed file.");
        return Hash(path);
    }
    void WriteResult() {
        string fixture = ProjectSettings.GlobalizePath("res://.godot/mono/temp/bin/Debug/CheckedComputeLab.dll");
        string api = Path.Combine(Path.GetDirectoryName(OS.GetExecutablePath())!, "GodotSharp/Api/Debug/GodotSharp.dll");
        bool passed = _failure.Length == 0 && _owned.Count == 0 && _local == null && _logger.Entries.Length == 0 && _logger.Dropped == 0;
        var result = new {
            contract = "checked-compute-case-v1", case_name = _case, passed, failure = _failure, checks = _checks,
            engine_hash = Engine.GetVersionInfo()["hash"].AsString(), engine_sha256 = Hash(OS.GetExecutablePath()), sdk_version = SdkVersion,
            driver = RenderingServer.GetCurrentRenderingDriverName(), renderer = RenderingServer.GetCurrentRenderingMethod(), gpu_name = RenderingServer.GetVideoAdapterName(),
            source_sha256 = Inputs.ToDictionary(name => name, name => Hash(ProjectSettings.GlobalizePath("res://" + name))),
            managed_fixture_assembly_sha256 = AssemblyHash(typeof(Main).Assembly, fixture), managed_api_assembly_sha256 = AssemblyHash(typeof(Node).Assembly, api),
            calls = _calls, probes = _probes, diagnostics = _logger.Entries, dropped_diagnostics = _logger.Dropped,
            resources = new { caller_owned_final = _owned.Count, caller_requested_bytes_final = _owned.Sum(item => (long)item.bytes), local_devices_final = _local == null ? 0 : 1,
                foreign_buffers_final = _foreign.IsValid ? 1 : 0, open_lists_final = _listActive ? 1 : 0 },
            performance_qualified = false, release_driver_qualified = false
        };
        string path = Path.GetFullPath(Required("record")); Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllBytes(path, JsonSerializer.SerializeToUtf8Bytes(result, new JsonSerializerOptions { WriteIndented = true }));
        GD.Print("Checked compute receipt " + path); OS.RemoveLogger(_logger); _logger.Dispose(); GetTree().Quit(passed ? 0 : 2);
    }
}

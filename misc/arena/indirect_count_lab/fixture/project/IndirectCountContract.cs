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

// Direct RD correctness subset. No MultiMesh, screenshots or timing acceptance.
public partial class IndirectCountContract : Node
{
    const int Width = 80, Height = 16, Cells = 5, CommandOffset = 32;
    const uint Poison = 0xcdcdcdcd;
    static readonly string[] Inputs = { "IndirectCountLab.csproj", "project.godot", "Main.tscn", "IndirectCountContract.cs",
        "ContractErrorLogger.cs", "DrawVertex.glslinc", "DrawFragment.glslinc", "Producer.glslinc" };
    readonly Dictionary<string, string> _args = new();
    readonly List<(Rid rid, uint bytes)> _owned = new();
    readonly List<object> _cases = new(), _phases = new(), _guards = new();
    readonly Dictionary<int, string> _rasters = new();
    readonly ContractErrorLogger _errors = new();
    RenderingDevice _rd = null!;
    Rid _computeShader, _computePipeline, _drawShader, _texture, _framebuffer, _drawPipeline, _indexBuffer, _indexArray;
    Rid _commands, _counter, _set;
    bool _supported, _indexed, _shared;
    uint _deviceLimit;
    int _maximum, _stride, _countOffset, _commandBytes, _counterBytes;
    int _checks, _dispatches, _drawCalls, _unavailable, _bufferReadbacks, _textureReadbacks, _pixelSamples;
    int _retiredCases, _ownedBaseline;
    string _failure = "";

    public override async void _Ready()
    {
        OS.AddLogger(_errors);
        foreach (string argument in OS.GetCmdlineUserArgs()) {
            string[] pair = argument.TrimStart('-').Split('=', 2);
            _args[pair[0]] = pair.Length == 2 ? pair[1] : "true";
        }
        try {
            Check(BitConverter.IsLittleEndian, "Little-endian indirect words required.");
            Check(Engine.GetVersionInfo()["hash"].AsString() == Required("expected-engine-hash"), "Native source differs.");
            Check(SdkVersion == Required("expected-sdk-version"), "Compiled SDK differs.");
            Check(RenderingServer.GetCurrentRenderingDriverName() == "vulkan" && RenderingServer.GetCurrentRenderingMethod() == "forward_plus",
                "The fixture requires Vulkan/Forward+.");
            _rd = RenderingServer.GetRenderingDevice() ?? throw new Exception("Global RD unavailable.");
            Engine.MaxFps = 120;
            await OnRender(() => {
                _supported = _rd.HasFeature(RenderingDevice.Features.SupportsDrawIndirectCount);
                _deviceLimit = checked((uint)_rd.LimitGet(RenderingDevice.Limit.MaxDrawIndirectCount));
                Check(_supported == (_deviceLimit != 0), "Enabled count feature/limit disagree.");
                CreatePersistent(); _ownedBaseline = _owned.Count;
            });
            if (_supported) {
                foreach (bool indexed in new[] { false, true }) foreach (bool shared in new[] { false, true })
                foreach (int stride in new[] { 20, 32 }) foreach (int maximum in new[] { 1, (int)Math.Min(4u, _deviceLimit) }.Distinct()) {
                    _indexed = indexed; _shared = shared; _stride = stride; _maximum = maximum;
                    await RunCase();
                }
            } else {
                _indexed = true; _shared = false; _stride = 20; _maximum = 1;
                await RunCase();
            }
        } catch (Exception error) { _failure = error.ToString(); GD.PushError(_failure); }
        finally {
            try {
                await CleanupCase();
                if (_rd != null) {
                    await Frames(checked((int)_rd.GetFrameDelay() + 4));
                    await OnRender(() => { foreach (var item in _owned.ToArray().Reverse()) FreeOwned(item.rid); });
                }
                Check(_owned.Count == 0, "Final caller-owned RD ledger is not empty.");
            } catch (Exception error) { _failure += "\nCleanup: " + error; GD.PushError(error.ToString()); }
            try { WriteResult(); }
            catch (Exception error) {
                GD.PushError(error.ToString());
                try { File.WriteAllBytes(Path.GetFullPath(Required("record")), JsonSerializer.SerializeToUtf8Bytes(new {
                    contract = "indirect-count-numeric-contract-v1", passed = false, failure = error.ToString() })); }
                finally { OS.RemoveLogger(_errors); _errors.Dispose(); GetTree().Quit(2); }
            }
        }
    }

    string Required(string key) => _args.TryGetValue(key, out string? value) && value.Length > 0 ? value : throw new Exception("Missing --" + key);
    static string SdkVersion => typeof(IndirectCountContract).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>().SingleOrDefault(a => a.Key == "GodotSdkVersion")?.Value ?? "";
    static string BytesHash(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    static string Hash(string path) => BytesHash(File.ReadAllBytes(path));
    static byte[] Words(params uint[] values) => MemoryMarshal.AsBytes(values.AsSpan()).ToArray();
    void Check(bool value, string message) { _checks++; if (!value) throw new Exception(message); }
    async Task OnRender(Action action) {
        var done = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        RenderingServer.CallOnRenderThread(Callable.From(() => { try { action(); done.SetResult(); } catch (Exception error) { done.SetException(error); } }));
        await done.Task;
    }
    async Task Frames(int count) { for (int i = 0; i < count; i++) await ToSignal(RenderingServer.Singleton, RenderingServer.SignalName.FramePostDraw); }
    Rid Own(Rid rid, uint bytes = 0) { Check(rid.IsValid, "RD allocation failed."); _owned.Add((rid, bytes)); return rid; }
    void FreeOwned(Rid rid) {
        if (!rid.IsValid) return;
        int index = _owned.FindIndex(item => item.rid == rid); Check(index >= 0, "Unowned RID free.");
        _rd.FreeRid(rid); _owned.RemoveAt(index);
    }
    void CreatePersistent()
    {
        using (var source = new RDShaderSource { SourceCompute = Godot.FileAccess.GetFileAsString("res://Producer.glslinc") }) {
            using var spirv = _rd.ShaderCompileSpirVFromSource(source);
            Check(string.IsNullOrWhiteSpace(spirv.CompileErrorCompute), spirv.CompileErrorCompute);
            _computeShader = Own(_rd.ShaderCreateFromSpirV(spirv)); _computePipeline = Own(_rd.ComputePipelineCreate(_computeShader));
        }
        using (var source = new RDShaderSource { SourceVertex = Godot.FileAccess.GetFileAsString("res://DrawVertex.glslinc"),
            SourceFragment = Godot.FileAccess.GetFileAsString("res://DrawFragment.glslinc") }) {
            using var spirv = _rd.ShaderCompileSpirVFromSource(source);
            Check(string.IsNullOrWhiteSpace(spirv.CompileErrorVertex) && string.IsNullOrWhiteSpace(spirv.CompileErrorFragment),
                spirv.CompileErrorVertex + spirv.CompileErrorFragment);
            _drawShader = Own(_rd.ShaderCreateFromSpirV(spirv));
        }
        using var format = new RDTextureFormat { Format = RenderingDevice.DataFormat.R8G8B8A8Unorm,
            Width = Width, Height = Height, Depth = 1, ArrayLayers = 1, Mipmaps = 1,
            TextureType = RenderingDevice.TextureType.Type2D,
            UsageBits = RenderingDevice.TextureUsageBits.ColorAttachmentBit | RenderingDevice.TextureUsageBits.CanCopyFromBit };
        using var view = new RDTextureView();
        _texture = Own(_rd.TextureCreate(format, view), Width * Height * 4);
        var textures = new Godot.Collections.Array<Rid> { _texture }; using var texturesLifetime = (Godot.Collections.Array)textures;
        _framebuffer = Own(_rd.FramebufferCreate(textures));
        using var raster = new RDPipelineRasterizationState { CullMode = RenderingDevice.PolygonCullMode.Disabled };
        using var samples = new RDPipelineMultisampleState(); using var depth = new RDPipelineDepthStencilState();
        using var attachment = new RDPipelineColorBlendStateAttachment();
        var attachments = new Godot.Collections.Array<RDPipelineColorBlendStateAttachment> { attachment };
        using var attachmentsLifetime = (Godot.Collections.Array)attachments;
        using var blend = new RDPipelineColorBlendState { Attachments = attachments };
        _drawPipeline = Own(_rd.RenderPipelineCreate(_drawShader, _rd.FramebufferGetFormat(_framebuffer), -1,
            RenderingDevice.RenderPrimitive.Triangles, raster, samples, depth, blend));
        _indexBuffer = Own(_rd.IndexBufferCreate(6, RenderingDevice.IndexBufferFormat.Uint32, Words(0, 1, 2, 3, 4, 5)), 24);
        _indexArray = Own(_rd.IndexArrayCreate(_indexBuffer, 0, 6));
    }

    async Task RunCase()
    {
        await OnRender(CreateCase);
        uint[] counts = _supported ? new uint[] { (uint)_maximum, 0, 1 } : new uint[] { 1 };
        var phases = counts.ToList();
        if (_supported && (uint)_maximum < _deviceLimit) phases.Add((uint)_maximum + 1);
        for (int phase = 0; phase < phases.Count; phase++) {
            uint raw = phases[phase]; int epoch = phase;
            await OnRender(() => Draw(raw, epoch == 0));
            await Frames(2);
            await OnRender(() => Numeric(raw, epoch));
        }
        _cases.Add(new { indexed = _indexed, shared = _shared, stride = _stride, maximum = _maximum,
            command_offset = CommandOffset, count_offset = _countOffset, valid_nonempty_records = _maximum + 1,
            phases = phases.Count, caller_requested_command_bytes = _commandBytes,
            caller_requested_counter_bytes = _shared ? 0 : _counterBytes });
        await CleanupCase();
        Check(_owned.Count == _ownedBaseline, "Case retained an RD resource."); _retiredCases++;
    }
    void CreateCase()
    {
        _countOffset = _shared ? 4 : 12;
        _commandBytes = (CommandOffset + _maximum * _stride + (_indexed ? 20 : 16) + 15) & ~15;
        _counterBytes = _shared ? _commandBytes : 32;
        uint[] initial = Enumerable.Repeat(Poison, _commandBytes / 4).ToArray();
        for (int i = 0; i <= _maximum; i++) {
            int word = (CommandOffset + i * _stride) / 4;
            uint[] command = _indexed ? new uint[] { 6, 0, 0, (uint)i * 6, 0 } : new uint[] { 6, 0, (uint)i * 6, 0 };
            Array.Copy(command, 0, initial, word, command.Length);
        }
        if (_shared) initial[_countOffset / 4] = 0;
        _commands = Own(_rd.StorageBufferCreate((uint)_commandBytes, Words(initial), (RenderingDevice.StorageBufferUsage)1), (uint)_commandBytes);
        if (_shared) _counter = _commands;
        else {
            uint[] counter = Enumerable.Repeat(Poison, _counterBytes / 4).ToArray(); counter[_countOffset / 4] = 0;
            _counter = Own(_rd.StorageBufferCreate((uint)_counterBytes, Words(counter), (RenderingDevice.StorageBufferUsage)1), (uint)_counterBytes);
        }
        using var commands = new RDUniform { UniformType = RenderingDevice.UniformType.StorageBuffer, Binding = 0 };
        using var count = new RDUniform { UniformType = RenderingDevice.UniformType.StorageBuffer, Binding = 1 };
        commands.AddId(_commands); count.AddId(_counter);
        var uniforms = new Godot.Collections.Array<RDUniform> { commands, count }; using var lifetime = (Godot.Collections.Array)uniforms;
        _set = Own(_rd.UniformSetCreate(uniforms, _computeShader, 0));
    }
    void Produce(uint raw, uint operation)
    {
        long list = _rd.ComputeListBegin(); _rd.ComputeListBindComputePipeline(list, _computePipeline);
        _rd.ComputeListBindUniformSet(list, _set, 0);
        _rd.ComputeListSetPushConstant(list, Words((uint)CommandOffset / 4, (uint)_countOffset / 4, (uint)_stride / 4,
            (uint)_maximum + 1, raw, operation, _indexed ? 1u : 0u, 0), 32);
        _rd.ComputeListDispatch(list, 1, 1, 1); _rd.ComputeListEnd(); _dispatches++;
    }
    void Draw(uint raw, bool guards)
    {
        Produce(raw, 0); Produce(raw, 1);
        long list = _rd.DrawListBegin(_framebuffer, RenderingDevice.DrawFlags.ClearColorAll, new[] { Colors.Black });
        try {
            _rd.DrawListBindRenderPipeline(list, _drawPipeline);
            if (_indexed) _rd.DrawListBindIndexArray(list, _indexArray);
            if (guards && _supported) CheckGuards(list);
            Error result = _rd.DrawListDrawIndirectCount(list, _indexed, _commands, CommandOffset, _counter, _countOffset, _maximum, _stride);
            Check(result == (_supported ? Error.Ok : Error.Unavailable), "Count draw returned " + result);
            if (_supported) _drawCalls++; else _unavailable++;
        } finally { _rd.DrawListEnd(); }
    }
    void CheckGuards(long list)
    {
        var rejected = new List<string>();
        void Reject(string name, long drawList, long offset, Rid counter, long countOffset, long maximum, long stride) {
            Error result = _rd.DrawListDrawIndirectCount(drawList, _indexed, _commands, offset, counter, countOffset, maximum, stride);
            Check(result == Error.InvalidParameter, "Invalid count draw accepted: " + name + " / " + result); rejected.Add(name);
        }
        Reject("wrong_list", list + 1, CommandOffset, _counter, _countOffset, _maximum, _stride);
        Reject("negative_command_offset", list, -4, _counter, _countOffset, _maximum, _stride);
        Reject("command_offset_above_uint32", list, (long)uint.MaxValue + 1, _counter, _countOffset, _maximum, _stride);
        Reject("negative_count_offset", list, CommandOffset, _counter, -4, _maximum, _stride);
        Reject("count_offset_above_uint32", list, CommandOffset, _counter, (long)uint.MaxValue + 1, _maximum, _stride);
        Reject("zero_maximum", list, CommandOffset, _counter, _countOffset, 0, _stride);
        Reject("maximum_above_uint32", list, CommandOffset, _counter, _countOffset, (long)uint.MaxValue + 1, _stride);
        Reject("zero_stride", list, CommandOffset, _counter, _countOffset, _maximum, 0);
        Reject("stride_above_uint32", list, CommandOffset, _counter, _countOffset, _maximum, (long)uint.MaxValue + 1);
        Reject("short_command_range", list, _commandBytes - 12, _counter, _countOffset, 1, _stride);
        Reject("short_counter_range", list, CommandOffset, _counter, _counterBytes, _maximum, _stride);
        Reject("invalid_counter_rid", list, CommandOffset, default, _countOffset, _maximum, _stride);
        if (_shared) Reject("shared_counter_overlap", list, CommandOffset, _counter, CommandOffset, _maximum, _stride);
        _guards.Add(new { indexed = _indexed, shared = _shared, stride = _stride, maximum = _maximum, rejected,
            subsequent_valid_count_draw = true });
    }
    void Numeric(uint raw, int phase)
    {
        byte[] commandBytes = _rd.BufferGetData(_commands); _bufferReadbacks++;
        byte[] counterBytes = _shared ? commandBytes : _rd.BufferGetData(_counter); if (!_shared) _bufferReadbacks++;
        uint[] expected = Enumerable.Repeat(Poison, _commandBytes / 4).ToArray();
        for (int i = 0; i <= _maximum; i++) {
            int word = (CommandOffset + i * _stride) / 4;
            uint[] command = _indexed ? new uint[] { 6, 1, 0, (uint)i * 6, 0 } : new uint[] { 6, 1, (uint)i * 6, 0 };
            Array.Copy(command, 0, expected, word, command.Length);
        }
        if (_shared) expected[_countOffset / 4] = raw;
        Check(commandBytes.SequenceEqual(Words(expected)), "Producer command/header/prefix/padding or legal tail differs.");
        uint[] counter = Enumerable.Repeat(Poison, _counterBytes / 4).ToArray(); counter[_countOffset / 4] = raw;
        Check(_shared ? BitConverter.ToUInt32(counterBytes, _countOffset) == raw : counterBytes.SequenceEqual(Words(counter)),
            "GPU count word/prefix/padding differs.");
        byte[] pixels = _rd.TextureGetData(_texture, 0); _textureReadbacks++;
        Check(pixels.Length == Width * Height * 4, "Numeric RGBA8 texture size differs.");
        int effective = _supported ? (int)Math.Min(raw, (uint)_maximum) : 0;
        string raster = BytesHash(pixels);
        if (_rasters.TryGetValue(effective, out string? reference)) Check(reference == raster, "Equivalent count draws changed full numeric texture bytes.");
        else _rasters.Add(effective, raster);
        var samples = new List<object>();
        for (int cell = 0; cell < Cells; cell++) {
            int x = cell * 16 + 8, y = 8, at = (y * Width + x) * 4;
            uint color = cell < effective ? (uint)cell + 1 : 0;
            int[] actual = { pixels[at], pixels[at + 1], pixels[at + 2] };
            int[] wanted = Enumerable.Range(0, 3).Select(channel => (color & (1u << channel)) != 0 ? 255 : 0).ToArray();
            Check(actual.Zip(wanted, (a, b) => Math.Abs(a - b) <= 1).All(value => value), "Actual count-draw raster differs.");
            samples.Add(new { cell, x, y, expected_rgb = wanted, actual_rgb = actual }); _pixelSamples++;
        }
        _phases.Add(new { indexed = _indexed, shared = _shared, stride = _stride, maximum = _maximum, phase,
            raw_gpu_count = raw, executed_draws = effective, valid_nonempty_records = _maximum + 1,
            full_command_and_tail_verified = true, counter_verified_after_draw = true,
            command_sha256 = BytesHash(commandBytes), counter_sha256 = BytesHash(counterBytes),
            numeric_texture_sha256 = raster, samples });
    }
    async Task CleanupCase()
    {
        if (_rd == null) return;
        await Frames(checked((int)_rd.GetFrameDelay() + 4));
        await OnRender(() => {
            FreeOwned(_set); _set = default;
            if (_counter.IsValid && _counter != _commands) FreeOwned(_counter); _counter = default;
            FreeOwned(_commands); _commands = default;
        });
    }
    string RuntimeAssemblyHash(Assembly assembly, string fallback) {
        string path = string.IsNullOrEmpty(assembly.Location) ? fallback : assembly.Location;
        using var stream = File.OpenRead(path); using var pe = new PEReader(stream);
        MetadataReader metadata = pe.GetMetadataReader();
        Check(metadata.GetGuid(metadata.GetModuleDefinition().Mvid) == assembly.ManifestModule.ModuleVersionId, "Loaded assembly differs from file.");
        return Hash(path);
    }
    void WriteResult()
    {
        string fixture = ProjectSettings.GlobalizePath("res://.godot/mono/temp/bin/Debug/IndirectCountLab.dll");
        string api = Path.Combine(Path.GetDirectoryName(OS.GetExecutablePath())!, "GodotSharp/Api/Debug/GodotSharp.dll");
        bool passed = _failure.Length == 0 && _errors.Errors.Length == 0 && _cases.Count != 0 && _owned.Count == 0;
        var result = new { contract = "indirect-count-numeric-contract-v1", passed, failure = _failure,
            engine_hash = Engine.GetVersionInfo()["hash"].AsString(), sdk_version = SdkVersion,
            driver = RenderingServer.GetCurrentRenderingDriverName(), renderer = RenderingServer.GetCurrentRenderingMethod(),
            gpu_name = RenderingServer.GetVideoAdapterName(), supports_draw_indirect_count = _supported,
            max_draw_indirect_count = _deviceLimit, count_draw_qualified = _supported && passed,
            source_sha256 = Inputs.ToDictionary(name => name, name => Hash(ProjectSettings.GlobalizePath("res://" + name))),
            managed_fixture_assembly_sha256 = RuntimeAssemblyHash(typeof(IndirectCountContract).Assembly, fixture),
            managed_api_assembly_sha256 = RuntimeAssemblyHash(typeof(Node).Assembly, api), checks = _checks,
            cases = _cases, phases = _phases, guards = _guards, native_diagnostics = _errors.Errors,
            command_producer_dispatches = _dispatches / 2, counter_producer_dispatches = _dispatches / 2,
            count_draw_calls = _drawCalls, unavailable_rejections = _unavailable,
            gpu_buffer_readbacks = _bufferReadbacks, numeric_texture_readbacks = _textureReadbacks,
            numeric_pixel_samples = _pixelSamples, retired_cases = _retiredCases,
            final_owned_rd_resource_count = _owned.Count, final_owned_requested_bytes = _owned.Sum(item => (long)item.bytes),
            image_files_written = 0, performance_window_count = 0, performance_qualified = false,
            not_qualified = new[] { "No FPS, full-frame or physical VRAM result", "No sustained resource/memory plateau; bounded caller-owned retirement only",
                "No MultiMesh integration, AI, animation, combat or networking", "No motion vectors, E8, shadows or auxiliary-pass proof",
                "No runtime bidirectional shader/vertex/index alias-guard qualification", "Readbacks occur only after count draw and exclude performance conclusions",
                "Unsupported feature path qualifies only unavailable fail-closed behavior, never positive GPU count drawing" } };
        string path = Path.GetFullPath(Required("record")); Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllBytes(path, JsonSerializer.SerializeToUtf8Bytes(result, new JsonSerializerOptions { WriteIndented = true }));
        GD.Print("Indirect count contract result " + path);
        OS.RemoveLogger(_errors); _errors.Dispose(); GetTree().Quit(passed ? 0 : 2);
    }
}

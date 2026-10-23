using Godot;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Json;
using System.Threading.Tasks;

// A graphics API correctness fixture, not a performance or screenshot test.
public partial class BufferRdContract : Node
{
    const int WarmupCycles = 8, SettledCycles = 32, RecordCount = 4, BufferBytes = 64;
    readonly Dictionary<string, string> _args = new();
    readonly List<(Rid rid, string kind, uint bytes)> _owned = new();
    readonly List<BufferRD> _wrappers = new();
    readonly List<ShaderMaterial> _materials = new();
    readonly List<object> _negativeAudits = new(), _pixelAudits = new(), _cycles = new(), _identityAudits = new(), _bindingGuardAudits = new();
    readonly List<ulong> _settledBytes = new();
    readonly List<long> _settledResources = new();
    readonly ContractErrorLogger _errors = new();
    readonly SubViewport[] _views = new SubViewport[2];
    readonly MeshInstance3D[] _instances = new MeshInstance3D[2];
    readonly ViewportTexture[] _viewTextures = new ViewportTexture[2];
    readonly ShaderMaterial?[] _consumers = new ShaderMaterial?[2];
    RenderingDevice _rd = null!;
    ArrayMesh? _quad;
    Shader? _consumerShader;
    Rid _producerShader, _producerPipeline, _a, _b, _setA, _setB;
    BufferRD? _wrapperA, _wrapperB;
    int _checks, _attachCalls, _wrapperCreates, _wrapperDisposes, _bindings;
    int _dispatches, _imageReadbacks, _bufferReadbacks, _pixelSamples, _rasterAudits;
    int _completedCycles, _currentCycle = -1;
    string _failure = "";

    public override async void _Ready()
    {
        OS.AddLogger(_errors);
        foreach (string argument in OS.GetCmdlineUserArgs()) {
            string[] pair = argument.TrimStart('-').Split('=', 2);
            _args[pair[0]] = pair.Length == 2 ? pair[1] : "true";
        }
        try {
            Check(BitConverter.IsLittleEndian, "The numeric byte oracle requires little-endian words.");
            Check(Engine.GetVersionInfo()["hash"].AsString() == Required("expected-engine-hash"), "Native engine identity mismatch.");
            Check(SdkVersion == Required("expected-sdk-version"), "Compiled SDK identity mismatch.");
            Check(RenderingServer.GetCurrentRenderingDriverName() == "vulkan" &&
                RenderingServer.GetCurrentRenderingMethod() == "forward_plus", "A real Vulkan/Forward+ window is required.");
            _rd = RenderingServer.GetRenderingDevice() ?? throw new Exception("Global RenderingDevice unavailable.");
            Engine.MaxFps = 120; // A correctness-only workload; no FPS acceptance claim.
            BuildViews();
            await OnRender(() => {
                using var source = new RDShaderSource { SourceCompute = Godot.FileAccess.GetFileAsString("res://Producer.glslinc") };
                using var spirv = _rd.ShaderCompileSpirVFromSource(source);
                Check(string.IsNullOrWhiteSpace(spirv.CompileErrorCompute), spirv.CompileErrorCompute);
                _producerShader = Own(_rd.ShaderCreateFromSpirV(spirv), "compute_shader");
                _producerPipeline = Own(_rd.ComputePipelineCreate(_producerShader), "compute_pipeline");
            });
            for (_currentCycle = 0; _currentCycle < WarmupCycles + SettledCycles; _currentCycle++) {
                int firstCheck = _checks, firstRaster = _rasterAudits;
                await RunCycle(_currentCycle);
                await CleanupCycle();
                await DrainFrames();
                ulong bytes = 0;
                long resources = 0;
                await OnRender(() => {
                    bytes = _rd.GetMemoryUsage(RenderingDevice.MemoryType.Buffers);
                    resources = checked((long)Performance.GetMonitor(Performance.Monitor.ObjectCount));
                });
                Check(_wrappers.Count == 0 && _materials.Count == 0 && OwnedBuffers == 0, "A cycle retained owned consumers or buffers.");
                Check(_rasterAudits - firstRaster == (_currentCycle == WarmupCycles ? 16 : 14), "A cycle did not execute its fixed numeric viewport audits.");
                _cycles.Add(new { cycle = _currentCycle, warmup = _currentCycle < WarmupCycles,
                    checks = _checks - firstCheck, raster_audits = _rasterAudits - firstRaster,
                    rd_logical_buffer_bytes = bytes, native_object_count = resources,
                    final_owned_buffers = OwnedBuffers, final_wrappers = _wrappers.Count, final_materials = _materials.Count });
                if (_currentCycle >= WarmupCycles) { _settledBytes.Add(bytes); _settledResources.Add(resources); }
                _completedCycles++;
            }
            Check(_settledBytes.Count == SettledCycles && _settledResources.Count == SettledCycles, "Incomplete settled samples.");
            Check(_settledBytes.Skip(SettledCycles / 2).Distinct().Count() == 1, "Logical RD buffer bytes did not reach an exact latter-half plateau.");
            Check(_settledResources.Skip(SettledCycles / 2).Distinct().Count() == 1, "Native objects did not reach an exact latter-half plateau.");
            Check(_wrapperCreates == _wrapperDisposes && _attachCalls == 3 * (WarmupCycles + SettledCycles) + 1, "Wrapper lifecycle conservation failed.");
            Check(_negativeAudits.Count == 8, "Incomplete rejection audit.");
            Check(_bindingGuardAudits.Count == 4 && _errors.ExpectedCallbacks.Length == 4 && _errors.TotalErrors == 4,
                "Incomplete exact ShaderMaterial guard callback ledger.");
        } catch (Exception exception) {
            _failure = exception.ToString();
            GD.PushError(_failure);
        } finally {
            try {
                await CleanupCycle();
                if (_rd != null) await OnRender(() => {
                    FreeOwned(_producerPipeline); _producerPipeline = default;
                    FreeOwned(_producerShader); _producerShader = default;
                });
                await ReleaseViews();
                Check(_owned.Count == 0 && _wrappers.Count == 0 && _materials.Count == 0, "Final ownership ledger is not empty.");
            } catch (Exception exception) {
                _failure += "\nCleanup: " + exception;
                GD.PushError(exception.ToString());
            }
            WriteResult();
        }
    }

    string Required(string key) => _args.TryGetValue(key, out string? value) && value.Length > 0
        ? value : throw new Exception("Missing --" + key + ".");
    static string SdkVersion => typeof(BufferRdContract).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>()
        .SingleOrDefault(item => item.Key == "GodotSdkVersion")?.Value ?? "";
    static string Hash(string path) => Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant();
    int OwnedBuffers => _owned.Count(item => item.kind == "storage_buffer");
    void Check(bool condition, string reason) { _checks++; if (!condition) throw new Exception(reason); }

    async Task OnRender(Action action)
    {
        var completion = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        RenderingServer.CallOnRenderThread(Callable.From(() => {
            try { action(); completion.SetResult(); }
            catch (Exception exception) { completion.SetException(exception); }
        }));
        await completion.Task;
    }

    async Task Frames(int count)
    {
        for (int i = 0; i < count; i++)
            await ToSignal(RenderingServer.Singleton, RenderingServer.SignalName.FramePostDraw);
    }

    async Task DrainFrames()
    {
        if (_rd == null) return;
        await Frames(checked((int)_rd.GetFrameDelay() + 4));
        await OnRender(() => { }); // Drain queued render-thread material changes as well.
    }

    Rid Own(Rid rid, string kind, uint bytes = 0)
    {
        Check(rid.IsValid, "Failed to create " + kind + ".");
        _owned.Add((rid, kind, bytes));
        return rid;
    }

    void FreeOwned(Rid rid)
    {
        if (!rid.IsValid) return;
        int index = _owned.FindIndex(item => item.rid == rid);
        Check(index >= 0, "Attempted to free a RID outside the fixture ledger.");
        _rd.FreeRid(rid);
        _owned.RemoveAt(index);
    }

    BufferRD NewWrapper()
    {
        var wrapper = new BufferRD();
        _wrappers.Add(wrapper); _wrapperCreates++;
        return wrapper;
    }

    void DisposeWrapper(BufferRD? wrapper)
    {
        if (wrapper == null) return;
        Check(_wrappers.Remove(wrapper), "Wrapper was released twice.");
        wrapper.Dispose(); _wrapperDisposes++;
    }

    void Attach(BufferRD wrapper, Rid storage)
    {
        Check(wrapper.AttachStorageBuffer(storage) == Error.Ok, "Valid render-thread attach failed.");
        _attachCalls++;
        Check(wrapper.GetBufferRdRid() == storage && wrapper.GetRid() == storage, "Borrowed RD RID identity changed.");
    }

    void BuildViews()
    {
        _consumerShader = GD.Load<Shader>("res://Consumer.gdshader") ?? throw new Exception("Consumer shader missing.");
        _quad = new ArrayMesh();
        using (var arrays = new Godot.Collections.Array()) {
            arrays.Resize((int)Mesh.ArrayType.Max);
            arrays[(int)Mesh.ArrayType.Vertex] = new[] { new Vector3(-1, -1, 0), new Vector3(1, -1, 0), new Vector3(1, 1, 0), new Vector3(-1, 1, 0) };
            arrays[(int)Mesh.ArrayType.Normal] = new[] { Vector3.Back, Vector3.Back, Vector3.Back, Vector3.Back };
            arrays[(int)Mesh.ArrayType.Index] = new[] { 0, 1, 2, 0, 2, 3 };
            _quad.AddSurfaceFromArrays(Mesh.PrimitiveType.Triangles, arrays);
        }
        for (int i = 0; i < 2; i++) {
            _views[i] = new SubViewport { Size = new Vector2I(16, 16), OwnWorld3D = true,
                TransparentBg = false, RenderTargetUpdateMode = SubViewport.UpdateMode.Always };
            AddChild(_views[i]);
            var camera = new Camera3D { Projection = Camera3D.ProjectionType.Orthogonal,
                Size = 2, Near = 0.1f, Far = 10, Position = new Vector3(0, 0, 2), Current = true };
            _views[i].AddChild(camera);
            _instances[i] = new MeshInstance3D { Mesh = _quad, Visible = false,
                CastShadow = GeometryInstance3D.ShadowCastingSetting.Off };
            _views[i].AddChild(_instances[i]);
            _viewTextures[i] = _views[i].GetTexture();
        }
    }

    Rid ProducerSet(Rid storage)
    {
        using var uniform = new RDUniform { UniformType = RenderingDevice.UniformType.StorageBuffer, Binding = 0 };
        uniform.AddId(storage);
        var uniforms = new Godot.Collections.Array<RDUniform> { uniform };
        using var uniformsLifetime = (Godot.Collections.Array)uniforms;
        return Own(_rd.UniformSetCreate(uniforms, _producerShader, 0), "compute_uniform_set");
    }

    void Produce(Rid set, uint seed)
    {
        long list = _rd.ComputeListBegin();
        _rd.ComputeListBindComputePipeline(list, _producerPipeline);
        _rd.ComputeListBindUniformSet(list, set, 0);
        _rd.ComputeListSetPushConstant(list, MemoryMarshal.AsBytes(new[] { seed }.AsSpan()).ToArray(), 4);
        _rd.ComputeListDispatch(list, 1, 1, 1);
        _rd.ComputeListEnd(); _dispatches++;
    }

    async Task Bind(int consumer, BufferRD? wrapper, int seed, int index, bool oob = false, int expectedLength = RecordCount)
    {
        ShaderMaterial material = _consumers[consumer] ?? throw new Exception("Missing material.");
        _instances[consumer].Visible = wrapper != null;
        await OnRender(() => {
            using Variant parameter = wrapper == null ? default(Variant) : Variant.From(wrapper);
            material.SetShaderParameter("data", parameter);
            material.SetShaderParameter("seed", seed);
            material.SetShaderParameter("record_index", index);
            material.SetShaderParameter("expected_length", expectedLength);
            material.SetShaderParameter("expect_oob", oob);
            _bindings++;
            using Variant cached = material.GetShaderParameter("data");
            Check(cached.AsGodotObject() == wrapper, "Material cache does not retain the selected wrapper.");
        });
    }

    void Identity(string name, BufferRD wrapper, Rid source)
    {
        Check(wrapper.GetRid() == source && wrapper.GetBufferRdRid() == source, "Producer update changed borrowed storage identity.");
        _identityAudits.Add(new { cycle = _currentCycle, test = name, caller_storage_rid = source.Id,
            wrapper_resource_id = wrapper.GetInstanceId(), wrapper_get_rid = wrapper.GetRid().Id,
            wrapper_buffer_rd_rid = wrapper.GetBufferRdRid().Id });
    }

    async Task Numeric(string name, int[] active, byte[] expected)
    {
        await Frames(3);
        foreach (int consumer in active) {
            using Image image = _viewTextures[consumer].GetImage();
            _imageReadbacks++;
            Check(!image.IsEmpty() && image.GetWidth() == 16 && image.GetHeight() == 16, "Invalid numeric viewport output.");
            if (image.GetFormat() != Image.Format.Rgba8) image.Convert(Image.Format.Rgba8);
            byte[] pixels = image.GetData();
            var observed = new List<int>();
            foreach (Vector2I point in new[] { new Vector2I(4, 4), new Vector2I(8, 8), new Vector2I(11, 11) }) {
                int offset = (point.Y * 16 + point.X) * 4;
                for (int channel = 0; channel < 3; channel++) {
                    int value = pixels[offset + channel]; observed.Add(value);
                    Check(Math.Abs(value - expected[channel]) <= 1,
                        $"{name} consumer {consumer} pixel {point} channel {channel}: {value} != {expected[channel]}.");
                }
                _pixelSamples++;
            }
            _rasterAudits++;
            if (_currentCycle == WarmupCycles) _pixelAudits.Add(new { test = name, consumer,
                expected_rgb = expected.Select(value => (int)value).ToArray(), observed_rgb = observed,
                format = image.GetFormat().ToString(), size = new[] { 16, 16 } });
        }
    }

    async Task RunCycle(int cycle)
    {
        int seedA = 7 + cycle, updatedA = 71 + cycle, seedB = 151 + cycle;
        _wrapperA = NewWrapper();
        await OnRender(() => {
            _a = Own(_rd.StorageBufferCreate(BufferBytes), "storage_buffer", BufferBytes);
            _b = Own(_rd.StorageBufferCreate(BufferBytes), "storage_buffer", BufferBytes);
            _setA = ProducerSet(_a); _setB = ProducerSet(_b);
            Attach(_wrapperA, _a);
            Attach(_wrapperA, _a); // Idempotent, with the identical borrowed RID.
            Produce(_setA, (uint)seedA);
        });
        for (int i = 0; i < 2; i++) {
            var material = new ShaderMaterial { Shader = _consumerShader };
            _materials.Add(material); _consumers[i] = material;
            _instances[i].MaterialOverride = material;
            await Bind(i, _wrapperA, seedA, i == 0 ? 0 : 3);
        }
        Identity("initial", _wrapperA, _a);
        await Numeric("initial-compute", new[] { 0, 1 }, new byte[] { 255, 255, 255 });
        await OnRender(() => Produce(_setA, (uint)updatedA));
        await Bind(0, _wrapperA, updatedA, 0); await Bind(1, _wrapperA, updatedA, 3);
        Identity("producer-update", _wrapperA, _a);
        await Numeric("same-rid-producer-update", new[] { 0, 1 }, new byte[] { 255, 255, 255 });
        if (cycle == WarmupCycles) await BindingGuardCases(_wrapperA, _a);
        // This deliberately wrong oracle must fail in pixels. A missing mesh or
        // a default/fallback white material therefore cannot pass the fixture.
        await Bind(0, _wrapperA, updatedA + 1, 0); await Bind(1, _wrapperA, updatedA + 1, 3);
        await Numeric("oracle-negative-control", new[] { 0, 1 }, new byte[] { 0, 0, 255 });
        await Bind(0, _wrapperA, updatedA, RecordCount, true);
        await Bind(1, _wrapperA, updatedA, -1, true); // uint(-1) = UINT_MAX.
        await Numeric("oob-length-and-uint-max", new[] { 0, 1 }, new byte[] { 255, 255, 255 });
        if (cycle == WarmupCycles) await NegativeCases(_wrapperA, _a, _b);
        await Bind(0, null, 0, 0); await Bind(1, _wrapperA, updatedA, 3);
        await Numeric("one-consumer-detached", new[] { 1 }, new byte[] { 255, 255, 255 });
        await Bind(0, null, 0, 0, true, 1);
        _instances[0].Visible = true;
        await Numeric("missing-buffer-zero-record", new[] { 0 }, new byte[] { 255, 255, 255 });
        _instances[0].Visible = false;
        _wrapperB = NewWrapper();
        await OnRender(() => { Attach(_wrapperB, _b); Produce(_setB, (uint)seedB); });
        await Bind(0, _wrapperB, seedB, 0);
        await Numeric("replacement-and-original-independent", new[] { 0, 1 }, new byte[] { 255, 255, 255 });
        await Bind(1, _wrapperB, seedB, 3);
        Identity("replacement", _wrapperB, _b);
        await Numeric("replacement-shared-consumers", new[] { 0, 1 }, new byte[] { 255, 255, 255 });
        await DrainFrames(); // Both materials have ceased borrowing wrapper A.
        await OnRender(() => {
            DisposeWrapper(_wrapperA); _wrapperA = null;
            CheckProducerBytes(_a, (uint)updatedA); // Completion fence, and proof wrapper disposal did not free caller storage.
            FreeOwned(_setA); _setA = default;
            FreeOwned(_a); _a = default;
        });
    }

    void CheckProducerBytes(Rid storage, uint seed)
    {
        byte[] data = _rd.BufferGetData(storage); _bufferReadbacks++;
        Check(data.Length == BufferBytes, "Caller storage disappeared or changed size.");
        var actual = new uint[BufferBytes / 4]; Buffer.BlockCopy(data, 0, actual, 0, data.Length);
        for (uint i = 0; i < RecordCount; i++) {
            uint[] expected = { seed + i * 17, seed ^ (i * 11 + 3),
                BitConverter.SingleToUInt32Bits((seed + i) * 0.125f), 0x12340000u + i };
            Check(actual.AsSpan((int)i * 4, 4).SequenceEqual(expected), "Compute producer bytes disagree with the independent CPU oracle.");
        }
    }

    async Task BindingGuardCases(BufferRD original, Rid storage)
    {
        const string invalidMessage = "Read-only buffer shader parameters require a live, attached BufferRD resource.";
        const string threadMessage = "BufferRD shader parameter assignments require the render thread.";
        ShaderMaterial material = _consumers[0] ?? throw new Exception("Missing guard consumer.");
        BufferRD empty = NewWrapper(), stale = NewWrapper();
        Rid retired = default;
        try {
            await OnRender(() => {
                retired = Own(_rd.StorageBufferCreate(16), "storage_buffer", 16);
                Attach(stale, retired);
                FreeOwned(retired); // Stale wrapper has never been bound to a material.
            });
            async Task Reject(string name, Variant parameter, string rationale, bool worker = false)
            {
                int before = _errors.Errors;
                _errors.BeginExpected(name, rationale);
                GD.Print("BUFFER_RD_EXPECTED_GUARD_BEGIN=" + name);
                try {
                    if (worker) await Task.Run(() => material.SetShaderParameter("data", parameter));
                    else await OnRender(() => material.SetShaderParameter("data", parameter));
                } finally {
                    bool exactlyOne = _errors.EndExpected(name);
                    GD.Print("BUFFER_RD_EXPECTED_GUARD_END=" + name);
                    Check(exactlyOne, name + " did not emit exactly one matching native guard callback.");
                }
                bool preserved = false;
                await OnRender(() => {
                    using Variant cached = material.GetShaderParameter("data");
                    preserved = cached.AsGodotObject() == original && original.GetRid() == storage && original.GetBufferRdRid() == storage;
                });
                Check(preserved && before == _errors.Errors, name + " changed the previous wrapper or emitted an unexpected error.");
                _bindingGuardAudits.Add(new { test = name, cached_previous_wrapper_preserved = preserved,
                    previous_wrapper_resource_id = original.GetInstanceId(), previous_storage_rid = storage.Id,
                    retired_storage_rid = name == "freed-before-first-bind" ? retired.Id : 0UL,
                    backing_freed_before_first_material_bind = name == "freed-before-first-bind",
                    unexpected_error_delta = _errors.Errors - before });
            }
            using (Variant bare = Variant.From(storage)) await Reject("bare-rid", bare, invalidMessage);
            using (Variant unattached = Variant.From(empty)) await Reject("empty-wrapper", unattached, invalidMessage);
            using (Variant freed = Variant.From(stale)) await Reject("freed-before-first-bind", freed, invalidMessage);
            using (Variant valid = Variant.From(original)) await Reject("worker-thread-binding", valid, threadMessage, true);
            // No successful rebind occurs between the four rejected mutations
            // and this extra phase: both spatial consumers must still read A.
            await Numeric("binding-guards-preserve-a", new[] { 0, 1 }, new byte[] { 255, 255, 255 });
        } finally {
            await OnRender(() => {
                DisposeWrapper(stale); DisposeWrapper(empty);
                if (_owned.Any(item => item.rid == retired)) FreeOwned(retired);
            });
        }
    }

    async Task NegativeCases(BufferRD wrapper, Rid original, Rid different)
    {
        void Reject(string name, Rid candidate, Error expected)
        {
            int errors = _errors.Errors;
            Error actual = wrapper.AttachStorageBuffer(candidate);
            bool preserved = wrapper.GetBufferRdRid() == original && wrapper.GetRid() == original;
            Check(actual == expected && preserved && errors == _errors.Errors, name + " did not reject quietly and preserve the existing binding.");
            _negativeAudits.Add(new { name, expected_error = expected.ToString(), actual_error = actual.ToString(),
                preserved, error_delta = _errors.Errors - errors });
        }
        await OnRender(() => {
            Rid uniform = default, small = default, uneven = default, freed = default;
            RenderingDevice? local = null;
            Rid localStorage = default;
            try {
                uniform = Own(_rd.UniformBufferCreate(16), "negative_uniform_buffer", 16);
                small = Own(_rd.StorageBufferCreate(4), "storage_buffer", 4);
                uneven = Own(_rd.StorageBufferCreate(20), "storage_buffer", 20);
                freed = Own(_rd.StorageBufferCreate(16), "storage_buffer", 16); FreeOwned(freed);
                Reject("invalid-rid", default, Error.InvalidParameter);
                Reject("freed-rid", freed, Error.InvalidParameter);
                Reject("uniform-buffer", uniform, Error.InvalidParameter);
                Reject("undersized-storage", small, Error.InvalidParameter);
                Reject("non-record-aligned-storage", uneven, Error.InvalidParameter);
                Reject("different-valid-storage", different, Error.AlreadyInUse);
                local = RenderingServer.CreateLocalRenderingDevice() ?? throw new Exception("Local RenderingDevice unavailable for the rejection proof.");
                localStorage = local.StorageBufferCreate(16);
                Check(localStorage.IsValid, "Local-device negative buffer creation failed.");
                Reject("local-device-storage", localStorage, Error.InvalidParameter);
            } finally {
                if (local != null) { if (localStorage.IsValid) local.FreeRid(localStorage); local.Free(); }
                FreeOwned(uneven); FreeOwned(small); FreeOwned(uniform);
            }
        });
        int before = _errors.Errors;
        Error offThread = await Task.Run(() => wrapper.AttachStorageBuffer(original));
        bool unchanged = wrapper.GetBufferRdRid() == original && wrapper.GetRid() == original;
        Check(offThread == Error.Unavailable && unchanged && before == _errors.Errors, "Off-render-thread attachment was not quietly rejected.");
        _negativeAudits.Add(new { name = "off-render-thread", expected_error = Error.Unavailable.ToString(),
            actual_error = offThread.ToString(), preserved = unchanged, error_delta = _errors.Errors - before });
    }

    async Task CleanupCycle()
    {
        if (_rd == null) return;
        for (int i = 0; i < 2; i++) {
            if (_instances[i] != null) { _instances[i].Visible = false; _instances[i].MaterialOverride = null; }
        }
        await OnRender(() => {
            foreach (ShaderMaterial material in _materials) { material.SetShaderParameter("data", default(Variant)); _bindings++; }
        });
        await DrainFrames();
        await OnRender(() => {
            // The synchronous caller-buffer reads occur after material detach
            // and deferred frame drainage. There is no timed workload here.
            foreach (Rid storage in new[] { _a, _b }) if (storage.IsValid) { _rd.BufferGetData(storage); _bufferReadbacks++; }
            foreach (ShaderMaterial material in _materials) material.Dispose();
            _materials.Clear(); _consumers[0] = _consumers[1] = null;
            foreach (BufferRD wrapper in _wrappers.ToArray()) DisposeWrapper(wrapper);
            _wrapperA = _wrapperB = null;
            FreeOwned(_setB); FreeOwned(_setA); _setA = _setB = default;
            FreeOwned(_b); FreeOwned(_a); _a = _b = default;
        });
    }

    async Task ReleaseViews()
    {
        foreach (SubViewport view in _views) if (view != null) view.QueueFree();
        if (_rd != null) await DrainFrames();
        foreach (ViewportTexture texture in _viewTextures) texture?.Dispose();
        _quad?.Dispose(); _quad = null;
        _consumerShader?.Dispose(); _consumerShader = null;
    }

    void WriteResult()
    {
        bool passed = _failure.Length == 0 && _errors.Errors == 0 && _completedCycles == WarmupCycles + SettledCycles;
        string assembly = typeof(BufferRdContract).Assembly.Location;
        string managedApi = typeof(Node).Assembly.Location;
        var result = new {
            contract = "buffer-rd-numeric-contract-v1", passed, failure = _failure, errors = _errors.Errors,
            total_native_error_callbacks = _errors.TotalErrors, expected_native_error_callbacks = _errors.ExpectedCallbacks,
            material_binding_guard_audits = _bindingGuardAudits,
            cycles = SettledCycles, warmup_cycles = WarmupCycles, completed_cycles = _completedCycles,
            engine = Engine.GetVersionInfo()["string"].AsString(), engine_hash = Engine.GetVersionInfo()["hash"].AsString(),
            sdk_version = SdkVersion, managed_fixture_assembly_sha256 = Hash(assembly), managed_api_assembly_sha256 = Hash(managedApi),
            driver = RenderingServer.GetCurrentRenderingDriverName(), renderer = RenderingServer.GetCurrentRenderingMethod(),
            gpu_name = RenderingServer.GetVideoAdapterName(), checks = _checks,
            attach_ok_calls = _attachCalls, wrapper_creations = _wrapperCreates, wrapper_disposals = _wrapperDisposes,
            material_binding_calls = _bindings, compute_dispatches = _dispatches,
            viewport_image_readbacks = _imageReadbacks, completion_and_oracle_buffer_readbacks = _bufferReadbacks,
            numeric_viewport_audits = _rasterAudits, numeric_pixel_samples = _pixelSamples,
            negative_audits = _negativeAudits, representative_numeric_audits = _pixelAudits, cycle_audits = _cycles,
            borrowed_identity_audits = _identityAudits,
            settled_rd_logical_buffer_bytes = _settledBytes, settled_native_object_counts = _settledResources,
            final_owned_gpu_buffer_count = OwnedBuffers, final_owned_rd_resource_count = _owned.Count,
            final_wrapper_count = _wrappers.Count, final_material_count = _materials.Count,
            final_owned_requested_gpu_buffer_bytes = _owned.Where(item => item.kind.Contains("buffer")).Sum(item => (long)item.bytes),
            leak_growth_tolerance_bytes = 0, latter_half_resource_growth_tolerance = 0,
            max_live_positive_storage_bytes = 2 * BufferBytes,
            image_files_written = 0, performance_window_count = 0,
            note = "Procedural quad only. GetImage bytes are a numerical oracle, never screenshots or image files. All GPU readbacks occur outside timing; no FPS qualification. Logical-byte and native ObjectDB object plateaus plus zero owned resources form a bounded lifetime screen, not physical VRAM residency proof."
        };
        string path = Path.GetFullPath(Required("record"));
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllBytes(path, JsonSerializer.SerializeToUtf8Bytes(result, new JsonSerializerOptions { WriteIndented = true }));
        GD.Print("BufferRD contract result " + path);
        OS.RemoveLogger(_errors);
        _errors.Dispose();
        GetTree().Quit(passed ? 0 : 2);
    }
}

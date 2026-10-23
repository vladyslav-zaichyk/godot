# BufferRD numeric contract

Standalone Godot .NET fixture: one generated quad, two independent 16x16
SubViewports, and no Arena code/assets. Paths are script-relative so this folder
can live at `misc/arena/buffer_rd_lab` in the engine fork. Do not build or run
until the coordinator provides the matching native editor and generated SDK.

The typed API is `BufferRD.AttachStorageBuffer(Rid)`, `GetBufferRdRid()` and
`GetRid()`. Spatial shaders use `uniform readonly_buffer data`,
`buffer_load(data, uint_index)`, and `buffer_length(data)` with 16-byte `uvec4`
records. Floats are reinterpreted with `uintBitsToFloat`.
Attachment and every non-NIL BufferRD material parameter binding execute on the
render thread. Material detach is serialized there before deferred frame drains.

## Run after the matching SDK exists

Provide the complete .NET editor bundle, its exact local NuGet feed and native
source commit. No engine build, export or packaging happens here.

```powershell
$env:ArenaGodotSdkVersion = '4.7.2-arena-buffer-rd.1'
python -B ./run_contract.py --engine <editor.exe> --sdk-feed <local-feed> --engine-hash <full-native-sha> --output <fresh-evidence-directory>
```

The runner restores/builds only this fixture, then launches its real visible
Vulkan/Forward+ window. It isolates NuGet, dotnet home, application data and temp
under the output directory. It does not modify global package sources, move
focus, close applications or install helpers. The SDK property is
`ArenaGodotSdkVersion`; `--sdk-version` can supply its value explicitly.
The coordinator runs one fixture build/contract at a time per checkout.

## Proof and limits

Each of eight warmup and 32 settled cycles performs thirteen numeric viewport
audits. A compute shader writes four records. Vertex and fragment consumers
check exact words, float reinterpretation and length. RGB output contains only
zero/one endpoints; three interior pixels are inspected through `GetImage`
bytes. Nothing saves an image or performs a screenshot/visual test. A deliberate
wrong expected seed must produce blue instead of white, rejecting default white
materials and stale data. Changing the producer without replacing its RID must
change the expected GPU result. Two consumers share a wrapper, one detaches,
then materials bind a new wrapper and a different caller buffer. Length and
UINT_MAX out-of-range loads must return zero.

Eight attach rejections cover invalid/freed RIDs, a uniform buffer, undersized
and non-record-aligned storage, another valid buffer, local-device storage and
an actual worker-thread call. Every rejection must return its exact Error,
preserve both getters and emit no native error. Negative shader compiler and
invalid material-parameter tests belong to the separate engine tests.

All material references detach before a deferred frame drain and synchronous
caller-buffer completion readbacks. Wrapper disposal must leave caller storage
alive. Producer uniform sets and caller storage are then freed. Every cycle
ends with zero owned buffers/wrappers/materials. The last sixteen settled global
logical-RD-byte and native ObjectDB object samples must plateau exactly. This is a
bounded lifetime screen, not a physical/resident VRAM guarantee. No performance
window, FPS claim, animation or gameplay qualification is included.

`contract.json` retains raw counters and representative numeric samples;
`shutdown.json` scans the full runtime log including errors after JSON output;
`acceptance.json` independently checks fixed counts, errors, identities and
plateaus. Only a complete PASS qualifies this restricted API path.

## Output ownership

The explicit output directory owns work caches, temporary files, build/runtime
logs and receipts. The project also generates `.godot/` and possibly Godot `.uid`
files; authored files are the seven paths listed in `run_contract.py:INPUTS`.
Retain designated raw receipts and exact consumer builds before verified cleanup.
No cleanup command is supplied and no other checkout/cache is touched.

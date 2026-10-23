# E8 procedural GPU history consumer

Source consumer for the [GPU history contract](../instance_indirection_lab/GPU_HISTORY_CONTRACT.md).
The new native API, generated managed SDK and this consumer must come from a
matching E8 bundle. Authoring and static inspection do not constitute compilation
or GPU qualification. The E5/E6a-only SDK cannot compile this consumer.

The project creates one independent World3D and one always-updating 128x64
orthographic SubViewport. The main window does not share that world or E8 owner.
An immutable indexed quad and identity native owner isolate shader deformation.
There are no imported assets, screenshots, image exports, gameplay or FPS tests.

## Implemented subset

- The real typed attach API passes `use_shader_motion_vectors=true`, checks its
  query, borrowed command identity and absence of a native transform pool.
  Thirty-two temporary owners, before the first draw, exercise same-resource rebind, detach, ordinary
  allocation/reset, reattach and implicit destruction. Quiet negative parameters
  check zero capacity, misalignment, short command range and default-false motion
  rejection without changing the true-flag view's capacity, command RID or flag.
  These checks preserve the
  existing binding. They do not prove repeated rendered-resource lifetime.
  The main owner is a separate real rendered consumer reused across 64 stress epochs.
- Two fixed-bound 64-byte pose banks, lifecycle metadata and packed current IDs
  use the consumer ABI `144N+84` input bytes. Capacity defaults to 16 and is capped
  at 64 before allocation. `firstInstance=3`, two instances and IDs 7/2 exercise
  compaction. Slot zero is also a valid identity; unused draw slots are poisoned.
- FramePreDraw queues a tracked copy/producer before native depth. The GPU writes
  state, metadata, bounded IDs and a 20-byte indirect command after a 16-byte
  prefix. Both shader evaluations resolve the same current stable ID. Previous
  validity checks generation, representation, epoch, domain and continuity;
  fallback uses the current owner-local pose with contextual native matrices.
- Exactly one POST_OPAQUE marker is paired with each clean post-draw CPU ledger
  commit. The deliberate skipped viewport prepares but never commits its sample;
  resume changes the domain and reseeds both banks. CPU signals are not GPU fences.
- There are **95 prepared epochs: 94 captured/committed and one skipped**. This
  includes **64 consecutive two-bank stress epochs**, static/root/camera/shear
  deformation and combined motion, reordered IDs, generation reuse, teleport,
  domain/representation/stale-epoch fallback, valid ID zero, hidden/reentry,
  frozen samples, several sim ticks per render, jitter and FSR2 routing cases.
- Three deliberately invalid current payloads (ID, count overflow, generation)
  must write a zero instance count plus an exact diagnostic. These are bounded
  cosmetic omissions, never a change to authoritative combat.

The compositor samples the raw RG16F velocity and native depth into a fixed GPU
ledger, copies the entire current/previous scene UBO contents immediately, and
copies diagnostic/command contents. Saving native RIDs for a later read is not
used. Only three final BufferGetData calls read the ledgers after all cases;
there are **zero per-frame GPU readbacks**. Capture capacity is 128 epochs:
16 MiB velocity/depth ledger, 729088-byte UBO ledger and 8192-byte command ledger.
Temporary capture descriptors are cached by texture identities; no pose bank is
allocated per frame. These capture outputs are excluded from the input-byte ABI.

The CPU oracle uses the captured float32 native UBO layout (2848-byte scene,
5696-byte current/previous block; MAX_RENDER_VIEWS=2), actual matrices and jitter.
For each valid instance it selects four current triangle-interior pixel centers
at least two pixels from triangle edges. Barycentric correspondence gives both
clip positions; expected motion is previous UV minus current UV after one jitter
removal. Maximum component error is fixed at `2^-12` UV. Independent depth must
match projected current geometry within `1e-5`, so an empty/omitted draw cannot
pass a zero-velocity test. Nonzero expectations greater than `2^-8` must not
produce zero output. Root/deformation/camera designated components additionally
must each lie in `[2^-8, 2^-3]` UV. All numbers, decoded current/previous native
projection/view matrices, pose inputs, coverage weights and clip vertices are retained
in JSON; no threshold is widened after a failure.

Engine.TimeScale is set to zero in this disposable process; state samples and
camera changes are explicit. The shader never reads TIME. Captured actual scene
times are reported rather than assuming all renderer clocks follow that scale.
The jitter cases inspect pre-temporal raw vectors, not TAA output quality. FSR2
cases exercise its separate motion routing at scale 1, not upscaling quality.

On success, terminal readback depends on produced velocity/capture writes. On
both success and failure, the consumer removes publication, compositor and native
material/view bindings, then uses RD's native deferred destruction path. Subsequent
frame-slot drainage permits deferred resources to settle; frame counting itself
is not asserted to be a completion fence. The fixture never frees mesh-owned RIDs.

## Not qualified by this consumer

The compiler's rejected stage/write/mode/backend matrix is a separate native
gate. Late motion activation, unrelated-view demand, effective-material/override/debug/next-pass guards, forced
ubershader routing and repeated whole-fixture memory plateaus still need explicit
evidence. The only logical memory samples are contextual pre-render and
post-cleanup endpoints; they do not prove an allocation plateau. They and ownership counters do not qualify
physical VRAM, performance or leaks across arbitrary production workloads.
Lit TBN, shadows, transparency, MSAA, multiview, mutable base geometry, billboard
history, TAA/FSR2 visual quality and gameplay remain outside this fixture.

## Build and run

`run_contract.py` defaults to **compile only** and never builds the engine. It
requires an exact E8 editor, adjacent GodotSharp tree and generated local NuGet
feed. It rejects an SDK without the new history query before building. NuGet,
dotnet, temp, generated project and logs all stay below one fresh external output.

```powershell
python -B misc/arena/gpu_history_lab/run_contract.py `
  --engine D:/Bundles/E8/godot.windows.editor.x86_64.arena_dev.mono.exe `
  --sdk-feed D:/Bundles/E8/NuGetFeed --sdk-version <exact-E8-version> `
  --engine-hash <full-native-source-SHA> --output D:/CodexScratch/e8-compile-01 `
  --compile-only
```

After the renderer A/B owner releases the GPU, use a **new** output directory and
add `--run-gpu`. This opens a small real Vulkan window; ordinary headless mode
cannot qualify global RD. No launch is implicit in compilation or `--help`.
The JSON records native/managed/source/package identities. Runtime assembly
identity also checks PE MVID when Godot loads an assembly from a stream.

`acceptance.json` from compile-only says `gpu_executed=false, passed=null`.
A GPU PASS means only the numerical subset above; inspect `not_qualified` and
retain native compiler/API and other fixture receipts separately. The runner's
strict diagnostics scan also covers process shutdown. Owned child processes are
bounded by a timeout; the runner never stops the author's applications.

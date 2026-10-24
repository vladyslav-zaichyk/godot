# E13a current-frame GPU visibility experiment

Status: experimental native implementation and separate controlled consumers,
2026-10-24. Native compilation, managed compilation and GPU qualification have
separate receipts. The renderer agent's E12b editor and earlier controls remain
immutable. This source alone does not establish a successful build or GPU result.

Prepared matrix: 7 cases / 12 probes, 16 identities and 64x32 proxy depth.
The C# consumer compiles against the exact E12b SDK; 25 CPU geometry/receipt
checks pass. Compile-only native mode returns UNAVAILABLE (exit 4), and GPU mode
without the A/B-release flag is rejected before creating output. These checks
launch no Godot process. GLSL compilation and all GPU/native behavior are pending.

[CONTRACT.md](CONTRACT.md) defines the accepted pass order, per-instance command
publication, conservative depth proof, ownership, fallback and native acceptance.
The native patch adds selected native occluder depth followed by compute BEFORE
remaining crowd depth. Existing depth getters and the ordinary global
render-thread compute route do not supply that pass split on their own.

```text
selected opaque occluders depth
-> current depth hierarchy and bounded GPU visibility producer
-> remaining/crowd depth
-> normal opaque color
```

Start with one orthographic main view, Vulkan Forward+, static opaque occluders,
no MSAA/TAA, screen-space/GI effects, shadows or probes. Keep uncertain visibility.
The caller's fixed ID bank contains immutable fallback and exclusive per-owner
candidate regions; firstInstance selects them without rebinding materials.
Native render-local command selection must start with fallback on every view.
Persistent MultiMesh attachments stay on uncull commands. No old candidate may
survive a skipped/failed callback. The contract also addresses native repeated
draw batching and InstanceData index preservation.

The small [reference fixture](fixture/README.md) uses existing E12b RD calls and
caller-owned depth. It is intended to isolate reverse-Z hierarchy, full-resolution
visibility oracle and bounded publication before any native pipeline surgery.
Native mode is explicitly unavailable. Its deterministic orthographic screen
cases do not qualify general 3D projection, native rendering, animated bounds,
shadows or motion vectors.

Compilation is the default and never launches Godot. Run the documented CPU
validator checks and exact-SDK compile first. Actual GPU execution requires
explicit flags after the renderer A/B slot is released. A prepared shader or
compiled project is not a numerical GPU pass.

The native patch must prove zero false-negative visibility, correct
current view/epoch, fallback on callback failures, safe scope closure, resumed
depth/color equality, unchanged ordinary rendering and bounded lifetime. Compare
occlusion-rich and open controls against the best existing uncull renderer and
the proxy route, counting all producer/hierarchy/frame work and storage.
This stage claims no FPS, physical VRAM or million-unit qualification.

The new [native stand](native/README.md) exercises the actual pass split and
render-local command selection. It prepares 14 probes, one registered crowd
owner, 16 logical slots, a 64x32 native viewport and 11 synchronous producers.
Its compile-only runner requires the new editor's successful build receipt and
exact generated SDK, and never launches Godot by default. Real-GPU work still
requires both explicit flags after the renderer A/B slot is released.

The first native scope admits at most 64 registered consumers and 4096 selected
occluder surfaces in one Windows x64 Vulkan Forward+ orthographic viewport.
It excludes temporal effects, MSAA, transparency, shadows, probes and
screen-space/GI effects. Unsupported views use the complete fallback. Invalid
fallback resources or producer scope/scene mutation reject the frame. A normal
missing, stale or error acknowledgment retains the complete fallback commands.

An admitted split disables opaque auto-instancing for that viewport to preserve
original InstanceData indices across mixed early/remaining owners. This cost is
part of the later full-frame comparison against the best existing uncull path.
The callback is a restricted recording contract, not a sandbox for arbitrary
scene, shader or native vertex-buffer mutations. No native shader execution,
barrier correctness, pixels, lifetime, FPS or physical VRAM claim follows from
CPU or managed compilation.

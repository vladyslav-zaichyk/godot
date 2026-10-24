# E13a current-frame GPU visibility experiment

Status: proposed native contract and separate controlled reference source,
2026-10-24. No native hook, new engine build or GPU result is supplied at this
stage. The renderer agent's E12b editor and earlier controls remain immutable.

[CONTRACT.md](CONTRACT.md) defines the proposed pass order, per-instance command
publication, conservative depth proof, ownership, fallback and native acceptance.
The key missing capability is selected native occluder depth followed by compute
BEFORE remaining crowd depth. Existing depth getters and the ordinary global
render-thread compute route do not supply that pass split.

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

The future native patch must prove zero false-negative visibility, correct
current view/epoch, fallback on callback failures, safe scope closure, resumed
depth/color equality, unchanged ordinary rendering and bounded lifetime. Compare
occlusion-rich and open controls against the best existing uncull renderer and
the proxy route, counting all producer/hierarchy/frame work and storage.
This stage claims no FPS, physical VRAM or million-unit qualification.

# E9 source audit and implementation decision

Audit date: 2026-10-24. Engine reference:
`faf0e74ef180ad16bf5950057284b6af3c80f30a`.
Renderer reference: `b7722f41821be13292a2812faf1b42cf72898433`.
Relevant renderer sources were clean and 34 delivered C#/GLSL source files
matched the retained R11 fixture hashes. This is a source/metadata audit, not
a new execution, frame profile, Vulkan result or integration acceptance.

## Current renderer, distinguished from older experiments

R10/R11 explicitly use buffer-poses, shader-driven instances, animation LOD
and gpu-full. `CreateNativeVisual` allocates eight crowd MultiMeshes and eight
native instance owners. Each owner selects its own LOD mesh and shares a
conservative world bound. Animated tiers also have per-model VAT bindings and
update policy; the proxy has a separate material. These eight global groups
are not eight same-state commands that the proposed API can combine.

Each owner borrows one 20-byte indexed command, at byte offset 0, with
firstInstance 0 and draw capacity N. Its visible-ID buffer occupies
`16 * ceil(N / 4)` bytes. Vertex lookup resolves that tier's draw slot to a
stable ID and then to the common 64-byte pose record. The shader-driven path
allocates no native transform pool. Eight crowd groups do not mean eight
whole-frame calls: environment geometry and render passes add their own work.

GPU selection classifies once and performs eight full-N scatter dispatches.
Each 64-lane workgroup uses six local prefix steps and one atomic reservation
when it has output. There is no global histogram/prefix-offset packing pass
to remove in this path. Command batching alone leaves those eight scatters
and all animated vertex work unchanged.

Do not substitute one of these other cases for that renderer:

| Case | Meaning for E9 |
| --- | --- |
| Old R2.2 CPU cell-by-LOD path | Can create many lazy native banks; different grouping/producer, not evidence of hundreds of current GPU draws. |
| Default animation shortcut | Pins the frozen older R4 export/implementation; its launch manifest does not identify the latest renderer work. |
| R9 shared-pose experiment | Separate one-LOD/one-phase cache experiment; not a many-command production workload. |
| R12 engine-contract-01 | Procedural E8 GPU History Lab consumer, not proof that crowd E8 integration has completed. |

Local evidence pins source hashes, relevant status, the R11 manifest and launcher
identity. Game source/assets stay local; no source or asset files were copied
into this engine fork. Another agent owns renderer integration and GPU work.

## Native gap and independent prerequisite

Forward+ currently calls `draw_list_draw_indirect` with `count=1, stride=0`.
RD, the render graph and Vulkan already carry count/stride. E9 therefore needs
a new MultiMesh range description and submission branch, not a new render graph,
shader stable-ID builtin or animation cache. It still shares the owner,
bindings, bounds, light state and push constants across all commands.

RD currently validates only `offset+20` or `offset+16`, with 32-bit addition,
after descriptor preparation/binding. It does not validate the final command,
full-span arithmetic, aligned stride or enabled multidraw maximum. Vulkan
conditionally enables multiDrawIndirect, while the driver interface lacks the
corresponding effective maximum. These gaps must be repaired before exposing
a native batched consumer; merely changing the Forward+ count is insufficient.

E9a is the independent prerequisite: overflow-safe whole-command-span checks,
explicit count 0/count 1 behavior, conservative enabled-feature/limit plumbing,
and validation before state/graph mutation. It is a correctness patch. Its value
does not depend on increasing the crowd's group count, and it has no FPS claim.

## Decision and next work

**Defer the native E9 batching implementation for the audited current renderer.**
The API opportunity is real, but no same-state segmented workload or measured
submission bottleneck has been established. Do not atlas distinct LOD geometry,
change materials/visibility or add artificial partitions just to justify it.

Keep the [proposed contract](README.md) ready for a future compatible-bucket use
case. Reopen implementation when the renderer owner supplies actual compatible
command counts, segmentation requirements and a cost breakdown. Compare both
LOOP/MULTI_DRAW in one binary with identical owners/data, and retain the globally
compacted single-command control including its producer cost. A synthetic
LOOP-to-batch win cannot establish improvement over the current eight-group path.

Recommended next native work is E9a, reviewed and built separately if taken into
implementation. Full E9 remains conditional; shader/cache/compaction algorithms
remain with the renderer agent. Five existing editor/SDK packages are immutable.
This phase changes documentation only, creates no extra worktree or disposable
project copy, and starts no build, editor or GPU test.

## Pinned engine references

- [Forward+ command count](../../../servers/rendering/renderer_rd/forward_clustered/render_forward_clustered.cpp#L646).
- [RD validation and descriptor work](../../../servers/rendering/rendering_device.cpp#L6169).
- [Render-graph forwarding](../../../servers/rendering/rendering_device_graph.cpp#L2237).
- [Conditional Vulkan features](../../../drivers/vulkan/rendering_device_driver_vulkan.cpp#L841).
- [Vulkan indexed submission](../../../drivers/vulkan/rendering_device_driver_vulkan.cpp#L5743).
- [Driver interface](../../../servers/rendering/rendering_device_driver.h).
- [Vulkan span/feature requirements](https://docs.vulkan.org/refpages/latest/refpages/source/vkCmdDrawIndexedIndirect.html).

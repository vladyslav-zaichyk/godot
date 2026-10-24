# E9: bounded MultiMesh indirect command spans

Status: **experimental native prototype; real GPU and performance qualification pending**.
Pre-E9 native reference: `faf0e74ef180ad16bf5950057284b6af3c80f30a`.
Use a matching E9 editor/SDK and the bundled source consumer. Source inspection,
compilation and CPU contracts do not establish Vulkan correctness or more FPS.
See [AUDIT.md](AUDIT.md) for the initial grouping audit and its limited scope.

## Purpose and go/no-go gate

Allow one shader-driven MultiMesh to consume several independently produced
instance-list segments without issuing one RD call per command. A command span
keeps one mesh, effective material, native geometry owner and binding state.
It does not combine arbitrary meshes, LODs, materials or lighting owners.

The audited current GPU renderer already has eight global LOD owners with
different meshes. The prototype does not combine those owners. The author has
authorized an experiment for the future renderer: many archetypes, LODs and
independently produced spatial segments. A representative fixture can qualify
the API and isolate costs before that production workload exists. It cannot
establish an FPS benefit for the current renderer.

Before production adoption, identify the real compatible state groups and why
global single-command compaction is insufficient. Include the producer cost and
actual command/pass counts. A large synthetic count alone proves no bottleneck.
Behavior differences can remain stable-ID data without changing rendering state.
Different models can share a span only if their geometry is packed into the same
immutable indexed surface with compatible bindings/materials; commands then
select index/vertex ranges. This prototype adds no native geometry allocator or
arbitrary external-buffer attachment. The packed fixture is a separate probe.

The RD whole-span validation defect described below is independently actionable.
Repairing it does not require adopting the MultiMesh API or promise more FPS.

## Experimental API and defaults

Use a new RenderingServer method instead of extending the existing signature:

```cpp
Error multimesh_attach_shared_draw_command_span(
    RID multimesh,
    int64_t draw_slot_capacity,
    RID indirect_command_buffer,
    int64_t command_byte_offset,
    int64_t command_count,
    int64_t command_byte_stride,
    MultimeshIndirectSubmissionMode submission_mode,
    bool use_shader_motion_vectors = false);
```

`submission_mode` is explicitly `LOOP` or `MULTI_DRAW`. It has no automatic
fallback. For K>1, unsupported MULTI_DRAW returns `ERR_UNAVAILABLE` before
mutation. MULTI_DRAW K=1 is allowed without the multidraw feature, since it is
one ordinary indirect call. LOOP K<=1024 also remains legal when the multidraw
maximum is 1: every submitted call has count 1. Read-only
`multimesh_get_command_byte_offset`, `multimesh_get_shared_draw_command_count`,
`multimesh_get_shared_draw_command_stride` and
`multimesh_get_shared_draw_submission_mode` queries verify transactional rebinds.
Non-shared views report count/stride 0 and LOOP. Old shared attachments report
count 1, stride 0 and LOOP. Detach resets all new metadata.

Initial scope: Windows x64, Vulkan/Forward+, 3D, the existing E5 draw-only mode,
one immutable indexed triangle surface, opaque material and one view timeline.
Preserve the E5 restrictions on native mesh LOD, shadow mesh, blend shapes,
colors/custom data and finite conservative bounds. Transform-backed sharing,
ordinary MultiMesh behavior and the old single-command API stay unchanged.

Select these limits for the experiment:

- `1 <= command_count <= 1024`, including disabled slots. The bound limits CPU
  LOOP work and command storage; it is not a frame-time guarantee.
- Nonnegative offset, positive draw-slot capacity and stride >= 20 bytes,
  divisible by 4. Use padded 32-byte command records by default. The new signed
  64-bit public arguments must fit the existing signed 32-bit field/getter range
  before narrowing; count has the tighter 1024 bound. This preserves original
  input values long enough to reject negative or excessive new-API arguments.
- Fixed CPU-known command count; a GPU producer writes `instanceCount=0` for
  empty groups. No GPU count-buffer, readback or automatic command compaction.
- Capacity overflow aborts attachment. Producer overflow is an explicit failed
  run, not silent loss of units. Cosmetic/gameplay overflow policy belongs to
  the consuming renderer/core, outside this API.

The new API uses the same stride rule even at count 1, for a predictable range
description. The generic RD operation distinguishes count 0/count 1 as below;
do not impose the new attachment rule on existing single-command consumers.

## Command contents and identity

Each indexed command begins with five 32-bit words: indexCount, instanceCount,
firstIndex, signed vertexOffset, firstInstance. Padding is not command data.
The compute producer owns contents. Engine validation does not read them back.
The producer must keep geometry ranges legal and guarantee, using wide
arithmetic, `firstInstance + instanceCount <= draw_slot_capacity` for every slot.
Capacity covers the highest addressed slot, not the sum of instance counts.
The numerical fixture also requires each admitted visible identity exactly once.

Keep `INSTANCE_ID` as Vulkan's instance index, including the command's
firstInstance. Do not add a second base offset or replace the geometry-owner
index in native scene data. No new DrawID builtin, per-command material binding,
native ID buffer, native transform bank or animation-state ABI is introduced.
Shaders that require native DrawIndex differences between LOOP and MULTI_DRAW
are outside the equivalence contract.

## State shared by every command

All commands retain the same owner transform/history, push constants, vertex
and index bindings, material/uniform sets, light/GI/lightmap state, masks, native
AABB and per-pass pipeline. Command records cannot select any of these states.
The native owner has one conservative AABB. GPU group visibility is the caller's
responsibility; splitting commands does not create native per-group culling.
Different states form separate spans, including distinct LOD meshes.

The renderer must preserve appropriate visibility for each consumer pass. A
camera-compacted list does not automatically contain off-camera shadow casters.
The first performance subset disables shadows and temporal effects. Numerical
depth/shadow/E8 checks are separate gates before claiming those combinations.

E8 remains an independent opt-in. With the history flag false preserve the old
motion guards. With it true retain the E8 owner/material checks and motion-list
routing. Both vertex evaluations use the current command's current draw slot ->
current stable ID -> that identity's current/previous render state. Reordering
commands or segments must not turn previous draw position into previous identity.
E8 GPU qualification is still pending and is not inherited by this design.

## Whole-span validation and capability plumbing

Before E9, Forward+ submitted `count=1, stride=0`; RD/graph/Vulkan already
forwarded a larger count. RD checked only the first command's byte range with
32-bit addition after descriptor preparation. The independent E9a patch now
validates the complete backend-effective range before draw-state mutation.

For E9 attachment validate type/global RD ownership, indirect usage, alignment,
mode, capacity, whole range, topology and device support before changing any
view state. Resolve signed arguments before narrowing. Use 64-bit operands
before arithmetic, with indexed command size 20:

```cpp
uint64_t end = uint64_t(offset)
    + uint64_t(count - 1) * uint64_t(stride)
    + 20;
// Require end <= actual_buffer_size, before storing or recording the span.
```

For the generic RD entry point:

- Check both indexed 20-byte and nonindexed 16-byte commands. Validation must
  precede descriptor preparation/binding, graph commands, counters and trackers.
- Handle count 0 separately, without `count-1` underflow: after render-thread,
  active-list, resource/usage and offset-alignment checks, return without
  draw-state/graph/counter mutation. It must not require one command to fit.
  This is an explicit RD wrapper policy, not a recorded Vulkan count 0 call;
  therefore no pipeline/index binding is needed for the no-op itself.
- For count 1, stride is ignored. Validate aligned offset and the one-command
  range with wide arithmetic; retain existing callers' stride 0 behavior.
- For Vulkan count > 1, validate stride alignment/minimum size, complete range
  and the actually enabled multiDrawIndirect feature/device maximum.
- Existing public RD offset/count/stride bind directly to uint32. E9a keeps
  that signature and validates the values received after conversion. It cannot
  claim to reject an original GDScript negative/oversized integer before
  truncation. Such a guarantee needs a checked wider binding adapter and an
  explicit managed/native compatibility plan, separately reviewed. The new E9
  signed 64-bit API above is checked before narrowing from its first version.

Add internal capability/limit plumbing with an explicit known/unknown state.
Vulkan reports the physical maxDrawIndirectCount when multiDrawIndirect is
enabled on the logical device, otherwise its effective multidraw maximum is 1.
Physical support alone is not sufficient. Reuse the existing indirect-first-
instance support/attachment guard. Do not assume every Vulkan device supports
either feature. The new E9 attachment remains Vulkan-only.

Whole-range checks use each backend's actual stride: Vulkan uses the validated
requested stride, D3D12 uses packed 16/20-byte command signatures regardless of
requested stride, and Metal advances by the requested stride, including zero.
Do not impose Vulkan's minimum stride rule on existing non-Vulkan calls.
Initially the new enabled-feature/maximum gate applies only to verified Vulkan
capabilities. An unimplemented backend capability is unknown, not a false maximum
1 that rejects existing working calls. D3D12 and Metal execution still require
their own regression gates; this delivery's native build targets Vulkan.
Before extending that gate to another backend, implement truthful limits and
regression coverage for its current multi-command path.
If a public feature/limit enum is added, append values to preserve existing IDs.
Vulkan generic RD callers must also honor that maximum; do not repair only E9.

## Lifetime and submission

The view borrows its command buffer. Caller retains it, the mesh and every
BufferRD dependency until dependent draws and in-flight uses have retired.
Detach never frees caller RIDs. Shared consumers have shared retirement duties.
Callbacks at render-thread/frame boundaries are not GPU fences.

All failed attach/rebind operations preserve the old buffer, count, stride,
submission mode and history flag. Successful old single-command rebind resets
span metadata to its old count 1/stride 0 behavior. Detach and ordinary allocation
clear new fields and preserve existing dependency/cache invalidation.

Publish coherent contents on the global RD queue before their consumers. End
producer lists and use tracked dependencies/interstage compute barriers where
needed. This feature adds no new queue, readback or blanket barrier API.

After identical material/geometry binding and push constants, use:

```cpp
// LOOP: K calls, same owner and frozen command buffer.
for (uint32_t i = 0; i < count; i++) {
    rd->draw_list_draw_indirect(list, true, buffer, offset + i * stride, 1, 0);
}
// MULTI_DRAW: one call; all other work is identical.
rd->draw_list_draw_indirect(list, true, buffer, offset, count, stride);
```

The earlier wide validation must prove every LOOP offset is representable by
the RD entry point; do not let the illustrative uint32 expression wrap.
Preserve validation of each effective material/pass, including debug, override,
overlay, next-pass and ubershader paths. Do not silently route unsupported
geometry through a different material or submission mode.

## Required controls and acceptance

Use three controls, with the best applicable existing path retained:

| Control | Producer/output | Submission | Purpose |
| --- | --- | --- | --- |
| C0 | Existing global compacted IDs per compatible mesh/material | One indirect draw | Current best consumer reference; producer cost included. |
| C1 | K independent fixed ID segments and identical command span | LOOP, same native owner | Isolate submission overhead without changing grouping/state. |
| C2 | Exactly C1's producer, IDs, command contents and scene | MULTI_DRAW, same owner | Measure only the batching change against C1. |

C1/C2 use the same new binary/SDK and an explicit mode toggle. Creating K native
owners only for C1 is not an admissible control. C0 may use a different producer
algorithm, so report that comparison separately and include its total cost.
If segmentation changes visible geometry, bounds, shadows or quality, qualify
that change separately; it cannot be credited to draw submission alone.

Planned fixture counts: 1, 64, 1024 commands; packed 20 and padded 32-byte strides,
nonzero buffer prefix, nonzero/sparse firstInstance ranges, early/middle/last
active slots, empty slots, ID/command reorder and explicit all-empty output.
Check the final command numerically, not only the first. A count 0 RD case is
separate from zero instanceCount in valid E9 slots.

CPU contracts cover full-range boundaries/overflow, alignment/capabilities,
count 0/count 1/count > 1, all-or-nothing rebind and old-API resets. GPU gates cover
numeric identity/coverage/positions, full pass semantics where claimed, material
fail-closed behavior, tracked compute-to-draw ordering and repeated rendered
resource retirement with a sustained logical-memory/object plateau. Standalone
include/editor validation and temporal image quality are not implied.

Record actual nonempty/allocated command slots, native owner count, compatible
state groups, RD submission calls and pass counts. Measure render-thread CPU
recording, complete GPU preparation/inclusive GPU work, full-frame distributions
and owned requested RD bytes. A single owner in both C1/C2 earns no owner-memory
saving. Physical VRAM requires separate evidence. Report GPU timestamps with
their actual frame IDs and pass coverage; viewport-only GPU time omits producers.

After the renderer's current A/B and E8 gate, use repeated paired/interleaved
windows under fixed scene/camera/grouping/build/settings. Acceptance requires
repeatable full-frame improvement against the strongest applicable control,
without worse tails, errors, leaks or quality loss. Lower call counts or a
synthetic C1 win alone do not establish an improvement to the current renderer.
Keep the prototype experimental if C0 remains faster or no real segmented
workload requires it. A serial correctness-only fixture compactor is not the
optimized production C0; qualify that producer before using it for performance
acceptance. No E9 FPS or memory result is claimed by this document.

## Bundled numerical consumer

`run_contract.py` defaults to compile-only. It requires one exact E9 editor,
adjacent GodotSharp tree, local NuGet feed, SDK version and full native source SHA.
It stages only seven project inputs into one fresh external output, isolates
NuGet/temp/user data, verifies Debug runtime and Release package API identities,
and never launches Godot unless `--run-gpu` is explicitly supplied.

```powershell
python -B misc/arena/multicommand_lab/run_contract.py `
  --engine D:/Bundles/E9/godot.windows.editor.x86_64.arena_dev.mono.exe `
  --sdk-feed D:/Bundles/E9/NuGetFeed --sdk-version <exact-E9-version> `
  --engine-hash <full-native-SHA> --output D:/CodexScratch/e9-compile-01 `
  --compile-only
```

Default inputs are 32 procedural archetypes, four LOD variants, 16 independently
produced segments and four bodies per segment (8192 bodies). Arguments bound the
population to one million and each owner to at most 1024 commands. This is not
an animated game crowd or an FPS benchmark. Data-only identity/color differences
stand in for behavior; no gameplay, simulation or networking is implemented.

After the renderer A/B owner releases the GPU, use a fresh output and `--run-gpu`.
Run `--layout separate` and `--layout packed` as separate experiments. Separate
uses one mesh owner per archetype/LOD. Packed puts the immutable procedural shape
ranges in a common indexed surface and uses one C1/C2 owner per archetype; those
commands vary firstIndex/vertexOffset. C0 retains one command/owner per shape,
including in the packed experiment. Its different topology and serial producer
are disclosed rather than credited to batching alone.

For distinct segment counts 1/default, both strides 20/32 and C0/C1/C2, four
phases cover all visible, interior empty segments, reversed command order and
all-empty output. Legal zero-instance commands are initialized before exposing
any native owner, so an intervening frame cannot consume poisoned command data.
The consumer reads every command/ID, including prefix/padding,
the final record, sparse firstInstance ranges, duplicate/missing IDs and guard
slots. C1/C2 compare complete ID/command/raster hashes. Up to three distinct
identity pixel probes per phase independently check expected admission/color;
no screenshot or image artifact is saved. These are partial raster probes,
not a numerical proof for every rendered identity.

Fourteen quiet invalid/narrowing/range cases preserve complete view metadata,
history false and absence of a transform pool. Successful old-API rebind resets
count/stride/mode. Case teardown hides consumers, clears bindings, uses native
deferred resource destruction and checks caller-owned storage is still valid.
Frame drainage is not asserted to be a GPU fence. Owned logical byte endpoints
do not qualify a sustained allocation plateau or physical VRAM.

The runner checks exact case/rebind/phase identities, independently predicted
owner/command/call counts, numerical sampling totals, source/SDK/runtime assembly
identities and shutdown diagnostics. Predicted RD calls are source metadata,
not measured GPU instrumentation. Readbacks serialize the correctness subset.
Compile-only reports `compiled=true, gpu_executed=false, passed=null`; a numerical
PASS still leaves motion vectors/E8, shadows, auxiliary passes, temporal quality,
whole-workload lifetime and performance unqualified.

## Sources

- [Godot RenderingDevice API](https://docs.godotengine.org/en/stable/classes/class_renderingdevice.html#class-renderingdevice-method-draw-list-draw-indirect).
- [Vulkan indexed indirect specification](https://docs.vulkan.org/refpages/latest/refpages/source/vkCmdDrawIndexedIndirect.html).
- [Vulkan indexed command layout](https://docs.vulkan.org/refpages/latest/refpages/source/VkDrawIndexedIndirectCommand.html).
- [Forward+ submission](../../../servers/rendering/renderer_rd/forward_clustered/render_forward_clustered.cpp#L646).
- [Current RD range checks](../../../servers/rendering/rendering_device.cpp#L6169).
- [Vulkan device feature enablement](../../../drivers/vulkan/rendering_device_driver_vulkan.cpp#L841).
- [Driver feature/limit interface](../../../servers/rendering/rendering_device_driver.h).
- [E8 history contract](../instance_indirection_lab/GPU_HISTORY_CONTRACT.md).

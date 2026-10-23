# Stable GPU IDs: source audit and E5 prototype boundary

Status: native prototype, 2026-10-23, based on the source audit at
`4c138f2fe2e7794ad53fd7bff850adf2d6c0174d`. The shader-driven API below is
implemented for a separate candidate; GPU correctness and performance remain
unqualified. Existing editor/SDK bundles remain immutable. Game source and
assets stay outside this engine repository; only a procedural contract fixture
belongs here.

## Decision

BufferRD already supports `draw slot -> stable ID -> pose/state` in a custom
spatial shader. It does not require a new stable-ID builtin. ID compaction can
replace per-visible transform scatter today. A procedural consumer inspected
locally already implements that path; duplicating it is not the next engine task.

Two distinct native additions are possible:

1. A **shader-driven indirect MultiMesh** with no native per-instance transform
   storage or indexed transform fetch. This is the narrower proposed spike for
   a consumer which already owns its pose shader.
2. Native **ID-to-transform gather before the material vertex function**, for
   native transform semantics and ordinary materials. This is a separate,
   broader capability, not a prerequisite for shader-side stable IDs.

First use the existing shared-buffer API to share one identity pool between
eligible LOD views. Compare any native prototype against that optimized control.
Measured frame or memory benefit is still required before adoption.

## What already works, and what belongs to the consumer

| Requirement | Current capability / remaining responsibility |
| --- | --- |
| Stable GPU slot across compaction and LOD changes | Store a uint source slot in each compact visible list; fetch state by that slot in the material shader. BufferRD already permits this. |
| Small compaction output | Four uint IDs fit in one 16-byte `uvec4` record. Use `slot / 4` and `slot % 4`; pad allocations, keep a separate active draw count. |
| GPU-only selection and counts | Compute can write visible IDs and the existing indirect command buffer on the global RD. End the producer list before consumption. |
| Shared simulation/animation state | Bind one material-local BufferRD pose pool to multiple consumers. No attach-time copy or CPU readback is needed. |
| Identity pool shared across views | Existing `multimesh_attach_shared_buffers` accepts the same transform RID for multiple views; all views must satisfy its mesh/lifetime restrictions. |
| Arbitrary unit lifetime | Consumer must add spawn/despawn, slot retirement, generation/history reset and an explicit overflow policy. A fixed initial array index is not a network entity ID. |
| Per-view visibility | Consumer must maintain camera/light visibility contracts. One main-camera list does not include all off-camera shadow casters. |
| Correct custom deformation | Consumer owns VERTEX, normal/tangent/binormal transforms, animation decoding and conservative custom AABB. |
| Native transform storage removed | Not provided by BufferRD. Current shared attach requires native transform storage covering every addressed draw slot. |
| Native material transform remap | Requires a new native addressing mode before the user vertex function. |
| Shared-mode motion history | Currently unsupported; stable-ID reads do not enable TAA/FSR2/motion vectors automatically. |

BufferRD emits readonly std430 `uvec4[]` helpers in
[`shader_compiler.cpp:578`](../../../servers/rendering/shader_compiler.cpp#L578).
`buffer_length` counts records, not uint lanes or live agents. OOB record loads
return zero; zero is a valid stable ID, so this does not substitute for bounds
checks, sentinels or capacity discipline. Validate an ID before multiplying it
by a pose-record stride. The packed lookup is conceptually:

```glsl
// Addressing sketch, not a standalone tested material.
uint draw_slot = uint(INSTANCE_ID); // already includes indirect firstInstance
uvec4 id_record = buffer_load(visible_ids, draw_slot >> 2u);
uint stable_slot = id_record[int(draw_slot & 3u)];
// Validate slot/ID, then decode pose_pool[stable_slot * pose_stride + row].
```

Do not add `firstInstance` again. Khronos defines
[`InstanceIndex`](https://docs.vulkan.org/refpages/latest/refpages/source/InstanceIndex.html)
as beginning at that value; the current Forward+ triangle path maps INSTANCE_ID
directly to it. Lists may be reordered by atomics without changing the stable
state slot. Reusing a slot while old GPU work still references it needs a
consumer generation/retirement policy; BufferRD does not own that lifecycle.

## Verified native boundary

- [`scene_shader_forward_clustered.cpp:729`](../../../servers/rendering/renderer_rd/forward_clustered/scene_shader_forward_clustered.cpp#L729)
  maps INSTANCE_ID to INSTANCE_INDEX;
  [`scene_forward_clustered_inc.glsl:164`](../../../servers/rendering/renderer_rd/shaders/forward_clustered/scene_forward_clustered_inc.glsl#L164)
  maps that to `gl_InstanceIndex` for normal triangle draws.
- [`scene_forward_clustered.glsl:313`](../../../servers/rendering/renderer_rd/shaders/forward_clustered/scene_forward_clustered.glsl#L313)
  loads the native MultiMesh transform at `stride * (INSTANCE_INDEX + offset)`.
  The user vertex function is inserted later, at line 450. The API must keep
  that earlier access valid even if a particular compiler could eliminate it.
- [`shader_types.cpp:115`](../../../servers/rendering/shader_types.cpp#L115)
  makes MODEL_MATRIX and MODEL_NORMAL_MATRIX readonly. MODELVIEW_MATRIX and
  MODELVIEW_NORMAL_MATRIX are writable. Custom shaders can override view-space
  transforms, but this is not an automatic replacement of all native builtins.
- [`mesh_storage.cpp:1572`](../../../servers/rendering/renderer_rd/storage_rd/mesh_storage.cpp#L1572)
  checks buffer identity/type/size, not exclusive use by one view. At lines
  1605-1613 the existing mode couples instance capacity to transform capacity
  and fixes the layout to 48-byte 3D transforms, without colors/custom data.
- [`scene_forward_clustered.glsl:770`](../../../servers/rendering/renderer_rd/shaders/forward_clustered/scene_forward_clustered.glsl#L770)
  indexes scene geometry-owner data separately. Clearing the MultiMesh flag to
  skip transforms would incorrectly add INSTANCE_INDEX to this owner index.
  A stable agent ID must never replace `draw_call.instance_index`.

Native per-instance non-uniform normal correction must not be assumed: the
current path multiplies by `mat3(matrix)` at line 346. Keep the first prototype
to rigid/positive uniform-scale transforms. Bounds remain per MultiMesh, not
per stable ID; see the
[MultiMesh documentation](https://docs.godotengine.org/en/stable/classes/class_multimesh.html).

## Fair memory control

Let N be allocated capacity, with eight equal-capacity LOD views and
`firstInstance = 0` in each view. Count native transform bytes separately from
poses, IDs, selection state, commands, mesh/material data and allocator overhead.

| Case | Native transform bytes | At N = 1,000,000 |
| --- | ---: | ---: |
| Eight independent identity pools | `8 * N * 48` | 366.21 MiB |
| Existing API: one borrowed identity pool | `N * 48` | 45.78 MiB |
| Proposed shader-driven mode | `0` per instance | 0 per-instance bytes |

The existing-API consumer optimization accounts for `336N` bytes; only the
remaining `48N` is the proposed engine mode's incremental target. A fixed
fallback descriptor is not zero total GPU allocation. These are source-derived
requested sizes, not measured resident VRAM or an FPS prediction. With different
grouping, shared capacity must cover the maximum addressed draw span. Packed
nonzero firstInstance ranges may need a larger span than one view's count.

## Implemented narrow native contract (GPU qualification pending)

Call `RenderingServer.multimesh_attach_shared_draw_buffer(multimesh,
draw_slot_capacity, indirect_command_buffer, command_byte_offset)` on the render
thread. It returns an `Error`; validate success before using the view. The view
has an independent positive draw capacity and a borrowed command range, with no
native per-instance transform allocation. The existing transform-backed attach
is unchanged. Caller provides BufferRD ID/state bindings through its materials;
the new operation does not define a game-state format or own ID/pose buffers.
Every effective spatial material must declare
`render_mode shader_driven_instances` and must not use `POINT_SIZE`. This is an
explicit capability: the same material can still render ordinary or
transform-backed control views without enabling the new native mode.

`multimesh_is_shader_driven` distinguishes this view from the existing shared
mode. `multimesh_get_buffer_rd_rid` intentionally returns an invalid RID here;
the command getter exposes caller-owned storage. Use the existing
`multimesh_detach_shared_buffers` to detach either mode. Missing or incompatible
effective materials and runtime motion requirements skip drawing with bounded
diagnostics. Cache rebuild rejects incompatible in-place mesh topology, but the
caller must still keep mesh contents immutable: changing compatible index data
can invalidate command fields that are not read back for validation.

Preserve current scope: 3D, Windows x64 Vulkan/Forward+ qualification, one indexed
triangle surface, no built-in mesh LOD/shadow mesh/blend shapes, colors/custom
data, CPU interpolation or motion vectors. Require finite nonempty local bounds.
The producer bounds append/count and guarantees, using overflow-safe arithmetic,
`firstInstance + instanceCount <= draw_slot_capacity`. Invalid attach/rebind
must preserve the previous view. Detach destroys view caches, not caller RIDs;
retain all borrowed storage until every consumer and in-flight use is finished.

Implementation:

- Extend existing RenderingServer/storage routing and MultiMesh mode state.
  Reuse shared-mode CPU-operation guards and dependency invalidation.
- Keep MultiMesh/indirect identity and original INSTANCE_ID. Add a separate
  specialization to skip **all** indexed transform/color/custom reads. Initialize
  temporary matrix state or guard its double-precision uses as well.
- Bind the existing default set-2 descriptor explicitly: render loop lines
  563-565 bind only valid changed sets, so an empty RID can leave a prior draw's
  binding. The default buffer at
  [`scene_shader_forward_clustered.cpp:999`](../../../servers/rendering/renderer_rd/forward_clustered/scene_shader_forward_clustered.cpp#L999)
  is a 256-byte allocation without supplied identity payload; never read it as
  an identity matrix. No additional native ID binding/layout is needed.
- Require an explicit shader capability/render-mode opt-in. Generic vertex
  writes alone do not prove the shader owns instance transforms. Validate the
  effective override/overlay/next-pass/debug shader. Missing/incompatible/default
  material must skip the draw with a bounded diagnostic.
- Depth/shadow variants must retain the custom pose vertex path instead of the
  shared default shadow material. Handle specialized and ubershader paths
  together. Pass-specific visibility remains a separate consumer responsibility.
- Keep motion excluded, including when enabled after attachment. Current shared
  attach rejects motion users at mesh_storage.cpp:1596; offsets are zero at 1749.

The broader native gather alternative would bind an ID SSBO in addition to
transforms at set 2, changing default/skeleton/particle layouts or requiring a
separate compatible layout. It also needs distinct ID-list and stable-pool
capacities. A 64-byte pose containing three transform rows plus metadata could
be reused if that alternative supports stride 64/custom data; an additional
48-byte stable pool is **not inevitable**. Choose it only for a concrete need
for native transform semantics/ordinary materials.

## Synchronization, history and next evidence

The follow-up [E8 GPU history and motion-vector contract](GPU_HISTORY_CONTRACT.md)
defines an opt-in previous-state evaluation for shader-driven BufferRD poses.
It is a design only: the API is not implemented, existing E5 motion restrictions
remain in effect, and all current editor/SDK bundles stay unchanged. Native Mesh
RD geometry overwritten in place is outside that contract's first phase.

Existing RD reflection tracks readonly SSBO reads and compute writes
(`rendering_device.cpp:4708`, graph dependency logic at
`rendering_device_graph.cpp:648`). BufferRD needs no special new Vulkan barrier.
The caller still schedules the global-RD producer before consumers, ends its
list, and separates dependent compute stages with `compute_list_add_barrier`.
Deprecated `barrier`/`full_barrier` only warn in this source; they are not fixes
for missing producer scheduling.

For future history, current draw slot -> current stable ID -> previous state of
**that same ID** is required. After list `[7,2]` becomes `[2,7]`, slot zero must
use ID 2's previous pose, not slot zero's former ID 7. The native velocity pass
replays the shader with previous/current scene data, but does not switch custom
SSBO state. Animation history, generation resets and visibility history need
their own contract; they are outside the first narrow spike.

Next evidence is a procedural three-case fixture: independent identities,
existing shared identity, proposed draw-only. Keep scene/grouping, exact ID
sets, geometry, animation, viewport and features identical. Check numerical
position/TBN and ID reorder/LOD transitions, nonzero firstInstance, invalid
material/ranges, rebind/detach and bounded lifetime. Compare full-frame tails,
GPU preparation and total requested RD bytes; physical VRAM needs separate
measurement. Verify shared/default/depth/shadow behavior before claiming it.

Use immutable build folders with native/source/SDK/toolchain manifests and a
compatible retained control. Rebuild the control only for a concrete mismatch
or attribution question; a paired CI rebuild is not a mandatory step. GPU runs
wait until the renderer's current A/B is complete. Source calculations alone
select a narrower experiment, not integration or performance acceptance.

## Procedural contract fixture

`project/` and `run_contract.py` provide a small new-API consumer. It uses eight
fresh MultiMesh views, eight draw slots per view, two stable poses and one
indexed procedural quad. Compute writes IDs, poses and commands; every command
uses nonzero `firstInstance = 3`. Three modes run in the **same new editor**:
eight borrowed identity banks, one borrowed identity bank, and shader-driven
views without native transform inputs. A pre-feature editor cannot run this
fixture because its SDK and shader language lack the new API/capability.

The future GPU contract requires 24 actual attaches, nine numerical viewport
readbacks and 144 interior pixel samples. It checks ID reorder, pose/color
updates, equal per-phase rasters across modes, zero capacity, unaligned/short
command ranges, failed-rebind preservation, detach and destruction without
freeing borrowed buffers. The vertex path reads MODEL_MATRIX so native transform
semantics remain observable rather than unused shader inputs. Eighteen
live-storage probes check caller ownership.
No image files are written. Input allocation ledgers are 3072/384/0 transform
bytes at this tiny scale, plus identical IDs/poses/commands. These ledgers are
caller-owned requested input sizes, not measured resident VRAM or an audit of
every hidden allocation. The raw RD logical allocation sample is contextual.

The runner explicitly restores/builds and **starts the GPU**. Wait until the
parallel renderer A/B releases the GPU before using it. Supply the candidate's
exact executable, native commit and nuspec-derived SDK/feed; use a fresh output
directory outside this fixture. All generated project files, logs and caches
stay under that output. From this directory:

```text
python -B run_contract.py --engine EDITOR_EXE --sdk-feed LOCAL_FEED --sdk-version EXACT_SDK --engine-hash FULL_NATIVE_SHA --output FRESH_OUTPUT
```

Passing this small contract would establish only its listed numerical/API
behavior. It supplies no performance window, physical-VRAM result, sustained
lifetime plateau, invalid-material runtime guard proof, depth/shadow/TBN/motion
proof, gameplay or network evidence. The source is provided separately from
native compilation; compile-only packaging must never be labeled a GPU pass.

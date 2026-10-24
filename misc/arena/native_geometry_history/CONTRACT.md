# Native previous geometry binding contract

Status: proposed, 2026-10-24; not implemented. Audit base:
`43904a9c33f45378dbdb62e0158fa82fb30505a4`.
This optional extension preserves a previous shape when compute overwrites an
E6a native base vertex buffer. BufferRD/E8 remains the route for per-unit pose
selection; [README.md](README.md) describes that boundary and the alternatives.

## First implementation scope

Windows x64, Vulkan, Forward+, 3D, one main viewport and one render-history
timeline. One immutable indexed triangle surface, uncompressed float32 positions
only, no normals/tangents/skin/blend shapes, no LODs or shadow mesh. The complete
native vertex stream must match the current stream's layout and consumed range;
its allocation must cover the current native `vertex_buffer_size` at offset zero.
No std430 float4 assumption is permitted. Use a fixed conservative owner/mesh
AABB covering both shapes; compute writes do not recalculate bounds.

First numerical fixture: opaque unshaded geometry, fixed vertex correspondence,
no MSAA, transparency, shadows, probes, multiview, billboards or changing vertex
attributes. Normals/tangents and compressed formats require a later explicit
extension with matching complete streams and an immutable decode basis. Fixed
UV/custom/skin/index history is not supplied by this API.

Qualify ordinary non-deforming MeshInstance3D first, then native MultiMesh and
an E5/E8 composed consumer as separate gates. The binding belongs to the mesh
surface, so every owner of that surface sees the same pair. It must not relax
any shared-transform, E5 material, E8 history or E10 pass/capability guard.
Unsupported owners or passes fail closed with bounded diagnostics; they must
not silently draw a mismatched history pair.

## Proposed RenderingServer API

Names are proposed for the patch; no current SDK exposes these methods.

```cpp
Error mesh_surface_attach_previous_vertex_buffer(RID mesh, int surface,
        RID previous_vertex_buffer, bool history_valid = false);
Error mesh_surface_detach_previous_vertex_buffer(RID mesh, int surface);
RID mesh_surface_get_previous_vertex_buffer_rd_rid(RID mesh, int surface) const;
bool mesh_surface_uses_previous_vertex_buffer(RID mesh, int surface) const;
```

The getter returns the borrowed attachment even when `history_valid` is false.
The bool reports whether previous inputs select that attachment; it is metadata,
not proof of GPU completion or correct payload. Detached state returns an invalid
RID and false. Invalid owner/surface queries use the fork's bounded diagnostics.

Attach also republishes the RID/validity pair transactionally. Invalid RID does
not mean detach; callers use the explicit detach operation. Repeating the same
state is idempotent. Validity changes are explicit at a render boundary; the
engine does not infer them from simulation ticks, visibility or raw GPU writes.
Regenerate the matching .NET SDK with Error-returning methods.

| State | Current input | Previous input | Dynamic classification |
| --- | --- | --- | --- |
| Detached | Existing native stream | Existing native behavior | Existing behavior |
| Attached, history false | Existing native stream | Same current stream | Forced for opted surface owners |
| Attached, history true | Existing native stream | Borrowed previous stream | Forced for opted surface owners |

Default false makes first publication and discontinuity reset safe for native
geometry. E8 shader deformation, if present, must independently choose its
current state during a reset; selecting current native attributes alone cannot
reset shader-owned pose or root history.

## Validation and ownership

Validate all arguments and supported state before changing any surface metadata,
version cache or dependency. Return a quiet Error on expected invalid input;
failure preserves the entire previous attachment and drawing state. Detach is
idempotent for a valid surface.

The previous RID must be a live **global RD vertex buffer** created with storage
usage, offset zero, sufficient native byte size and no dynamic persistent mapping.
Reject local-RD buffers, storage-only/index/uniform owners, undersized buffers,
unsupported formats/backends and aliasing the current base buffer. Require E6a
storage opt-in for the current surface too. Use checked arithmetic for byte size.
Do not reuse `storage_buffer_is_valid`: vertex arrays require vertex ownership.
A list-independent checked vertex-owner/usage/size validator is part of the patch.

The mesh owns its existing current base stream. The caller owns the previous
buffer and producer pipelines/uniform sets. Neither attach, detach, mesh free,
surface removal nor cache invalidation frees that borrowed buffer. The mesh
does not allocate history, snapshot/copy vertices, adopt a RID, swap the current
buffer or create a per-instance transform bank.

The caller detaches before freeing/recycling the previous RID and keeps it alive
until recorded GPU readers retire through RD's tracked resource lifetime. A
render callback or `frame_post_draw` is not a GPU completion fence. A stale
attachment encountered by rendering must skip the affected draw and report a
bounded diagnostic; no fallback may claim that a freed payload is valid history.

Reject conflict with native skeleton/blend-shape MeshInstance deformation.
Attachment and later owner/deformation changes must both enforce this rule:
the renderer's existing MeshInstance path takes precedence over base streams.
Do not accept a binding that is then silently ignored. First scope rejects skin
and blend-shape formats, topology/count/format changes, ordinary CPU vertex-region
updates and incompatible mesh substitutions while attached. Detach, mutate or
recreate, reacquire E6a RIDs and attach with history false. Raw E6a compute writes
remain the caller's responsibility; the engine cannot verify their payload.

## Publication and the meaning of previous

Previous means the complete owner-local geometry used by this viewport's last
acknowledged render recording, paired with a clean end of that enclosing draw.
It is not the last simulation tick, the last visible draw, proof of display or a
GPU-completed sample. All owners and passes share this single publication. A
second viewport with a different cadence needs a different history design.

Until the next render recording, current must still contain the last acknowledged
rendered shape, or that shape must already be retained elsewhere. Simulation can
advance in separate staging. If compute overwrites current while the viewport is
paused/hidden and the old shape was not retained, the next copy cannot recover
it: publish history false instead.

For each render recording, on the render thread before the earliest depth/color
consumer:

1. Preserve the last rendered shape in the borrowed stream when history is valid.
   A tracked RD copy from current to previous before overwriting current is one
   producer implementation. Copy only at a new rendered sample, not each sim tick.
2. Produce the complete current shape, then publish the intended RID/validity
   metadata before draw-list construction. Keep the pair stable across all passes.
3. After clean recording, advance the consumer's render epoch. On failed/skipped
   recording or a discontinuity, reset continuity before the next publication.

With a frozen simulation, the second rendered sample must use previous=current;
refresh the previous bytes or explicitly publish history false. Repeating the
same attach metadata does not advance history or fix stale previous contents.
Old deformation velocity must not repeat. When the viewport does not render,
simulation may progress without advancing render history. Hidden geometry must
either maintain this viewport's rendered-sample history within a fixed capacity
or reset on reappearance. There is no implicit visibility-based capture.

Current/previous work must be in tracked RD lists on the same supported queue;
enforce compute/copy writes before vertex reads and old readers before overwrite
through graph RAW/WAR/WAW dependencies. Reject bound-resource read/write alias
hazards. CPU mapped writes and independent queues are outside scope. Do not
force a CPU/GPU sync per frame. Two buffers can suffice for a correctly ordered
current-plus-copy scheme; a ring needs explicit retirement and capacity rules.

Scheduling at PRE_OPAQUE is too late when the depth prepass consumed geometry.
Reuse E8's render-thread `frame_pre_draw` publication seam before `_draw`, with
one complete producer recording. If that seam cannot provide the required graph
ordering for this consumer, stop qualification rather than add an implicit fence
or accept mixed depth/color shapes.

Spawn, teleport, generation/page reuse, changed topology/schema, viewport
pause/history reset and unknown prior sample select history false for the next
recording. This removes native deformation velocity only. Native previous camera
and owner matrices still apply. Coordinate camera/upscaler history resets
separately; no disocclusion mask or TAA accumulation reset is provided.

## Renderer changes

MeshStorage stores borrowed RID and selection state per surface. When building
motion inputs, base VERTEX selects this RID only for valid previous history.
Ordinary current inputs and non-motion passes retain the existing current RID.
Native MeshInstance deformation must never use the new base attachment.

Attachment changes, rebind, validity toggles and detach invalidate dependent
vertex-array versions before reuse. Surface removal/mesh free clear references
and free only engine-owned arrays. Use a bounded version key or invalidate old
versions; do not retain one version per publication. Notify geometry dependencies
so warmed owner/material/input-mask caches rebuild correctly.

Opted owners remain dynamic with stationary CPU transforms, no TIME reads and
unchanged materials. Feed both ordinary motion output and the separate upscaling
motion list, including when motion demand turns on after cache warm-up. Use the
current viewport's compositor/TAA/upscaling demand. Specialized and ubershader
paths must agree. Validity false still needs this routing so deformation reset
is explicit. Classification tests attachment presence, not the public selection
query, which is false during reset. Detach must restore the ordinary
classification and cache behavior.

## Small stand and acceptance gates

Build a separate exact-source editor/SDK only after implementing this contract.
Use the existing E8 numerical conventions: 128 by 64 orthographic target,
unjittered UV velocity `0.5 * (previous_ndc - current_ndc)`, deliberate displacement
between `2^-8` and `2^-3`, maximum vector error `2^-12`. Check covered interior
pixels independently of edges. Readback is for correctness and excluded from
performance measurements.

| Gate | Required evidence |
| --- | --- |
| API and compile | Wrong owner/device/usage/size/format/alias rejected without partial state; idempotence; exact .NET API and old consumers compile |
| Native geometry | Compute-only deformation with static CPU owner and no TIME; previous stream differs numerically; reset, frozen render, skipped render and camera-only motion |
| Cache and routing | A/B RID rebind, validity toggle, detach/reattach, warm motion off then compositor/TAA and upscaling on; material/input-mask changes and incompatible owner guard |
| Composition | Ordinary MeshInstance, native MultiMesh and E5/E8 tested separately; two units with the same current page and different previous pages demonstrate why per-ID lookup is still required |
| Lifetime | Detach before caller free, stale attachment fails closed, mesh/surface free leaves borrowed RID live, bounded repeated cycles return logical allocation counts to baseline |
| Regression | Feature off preserves ordinary geometry/material behavior; all old shared-path and unsupported-pass guards remain |

The old contract alone cannot qualify the previous-only BufferRD workaround.
That consumer needs the same numerical gates: its previous branch must reproduce
the full current owner-local result on reset, not merely leave base VERTEX
unchanged if current vertex code applies additional deformation. Supply historical
normal/tangent inputs if code uses them to change position. Apply native owner and
camera transforms once; do not copy those transforms into pose data as well.

Performance follows correctness and waits for the parallel renderer A/B release.
Compare identical geometry, grouping, counts, animation samples and build flags
against the best applicable BufferRD/E8 route, GPU-copy workaround and feature-off
control. Report full-frame distributions, producer/copy/draw GPU time, logical
bytes and physical VRAM separately. Previous streams add memory and bandwidth;
their initial benefit is correct temporal geometry. Accept as an optimization
only with repeatable frame improvement or material memory saving without frame
regression, corruption or leaks. Do not infer million-unit or multiplayer
qualification from the small stand.

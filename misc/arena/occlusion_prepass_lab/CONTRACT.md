# E13a current-frame occlusion prepass contract

Status: experimental native implementation with a separate E13b perspective
admission extension, 2026-10-25. Original audit base:
`8b31ae443769b6147f7db26e77bb930de549c6d3`.
The corrected no-GI native orthographic stand passed 14 GPU views / 821 checks
on native `4010f6133b6e7ea4473250c739a012fa33a2d16a`. This does not qualify the
perspective extension or animated bounds on a new editor. Those need their own
exact-build regressions and independent numerical GPU gate.
The historical `fixture/` proxy reference remains pinned to the immutable E12b
editor, native `7d59f5064a6cd40a1cf2aaa578b8e90d29d1cbe8`, SDK
`4.7.2-arena-dev-g7d59f5064a`; proxy success cannot qualify native ordering.

## Purpose and first boundary

Expose an opt-in native pass boundary for selected current opaque occluders,
then let caller compute produce bounded camera-visible instance commands before
the remaining scene depth is recorded. The engine owns pass selection, actual
view identity, ordering and render-local publication. The caller owns Hi-Z,
animated bounds, stable-ID selection, command contents and capacity policy.

First native scope: Windows x64, Vulkan Forward+, one opted main viewport and
one view, full internal render region, ordinary depth prepass enabled, 1:1
internal resolution. Disable MSAA, TAA, temporal upscaling, motion-vector demand,
SSAO/SSIL/SSR, GI, volumetric fog, stencil, transparency, shadows, reflection
probes, XR and wireframe/debug replacements. Admitted cameras are orthographic
or ordinary centered perspective. Off-center/custom perspective frusta remain
unsupported and select complete fallback. Nonfinite/singular native camera or
projection and invalid depth-plane order reject recording before the E13 split;
this does not validate arbitrary caller GPU data or precede all renderer setup.
Perspective/animated-bound GPU correctness remains a separate gate. This is a
bounded capability experiment, not a replacement scene renderer or simulation.

Eligible occluders are explicitly selected ordinary static MeshInstance3D
surfaces with an immutable triangle mesh, fixed owner transform and supported
opaque material. No MultiMesh, particles, skin/blend shapes, compute-written
native vertices, shader vertex deformation, billboards, grow, alpha/discard,
stencil, depth overrides, next passes or overlays. Surface eligibility uses the
effective material and final native transform. A selected ineligible surface
stays in ordinary remaining depth and cannot contribute occlusion proof.
An empty eligible set produces clear depth and keeps candidates visible.
Early occluders also exclude shadow meshes and native mesh LODs: their actual
depth geometry must not close a hole that remains open in color geometry.

Consumers initially use E5 shader-driven indexed MultiMeshes with one immutable
surface and one fixed E9 command per owner. The persistent attachment is always
the valid uncull fallback. E10 GPU command-count attachment, native mesh LODs,
shadow meshes, multi-command spans and independent secondary views are later
extensions. Existing E5/E8/E9/E10 restrictions remain effective.

## Required recording order

```text
publish current simulation/render snapshot and valid uncull fallback
-> start actual viewport render; select FALLBACK for every registration
-> finalize view state, conservative native owner culling and render lists
-> clear native depth once to reverse-Z 0; draw eligible occluders only
-> close draw list; engine-owned compute scope
-> caller depth-to-Hi-Z, conservative test, bounded ID compaction, finalization
-> engine closes compute scope; validate current acknowledgment
-> freeze per-render FALLBACK/CANDIDATE selection
-> LOAD same depth, draw remaining opaque depth including admitted crowd
-> ordinary opaque color using the same owner/pose/ID/command snapshot
```

The selected opaque occluders remain in color. They are not redrawn in remaining
depth. Preserve the full ordinary-depth shader/coverage of every surface; color
uses the normal depth-equality pipeline. Do not clear depth at resume or insert
crowd only after depth. Restore raster state and render-pass uniform bindings.
The first scope has no normal/roughness/GI outputs to merge; supporting those
requires an explicit attachment clear/load and coverage extension.

The early depth must be the same native depth image/subresource used by remaining
depth, with supported sampled depth format and no MSAA resolve. A different proxy
depth is the reference route, not native depth reuse. Required dependencies are
depth attachment write -> compute sample -> attachment read/write, plus producer
storage write -> shader read/indirect command read. Use the existing RD graph;
callback completion is CPU recording completion, never a GPU fence.

## Metadata API

These signatures are implemented in the separate experimental native SDK:

```cpp
Error viewport_configure_gpu_occlusion_prepass(
        RID viewport, uint32_t occluder_layer_mask, Callable producer);
Error viewport_register_gpu_occlusion_consumer(
        RID viewport, RID geometry_instance, RID identity_bank,
        int64_t candidate_identity_base, RID candidate_commands,
        int64_t candidate_command_byte_offset, int64_t logical_slot_capacity);
Error viewport_unregister_gpu_occlusion_consumer(RID viewport, RID geometry_instance);
Error viewport_clear_gpu_occlusion_prepass(RID viewport);
```

Configuration is one producer per viewport and at most 64 consumer registrations.
That bounds engine metadata and callbacks, not GPU work or frame time. Registration
is keyed by the RenderingServer INSTANCE RID and generation, not by MultiMesh:
several owners may share a MultiMesh but have different transforms/materials.
Viewport owns metadata/Callable references; no new public registration RID and
no ownership of caller storage. Unregister/clear/destruction drops metadata only.

Registration snapshots and validates the instance/base allocation, immutable
geometry/material/layout, existing one-command LOOP fallback and draw capacity.
Candidate is one 20-byte indexed command in live global-RD nonpersistent storage
with INDIRECT usage; signed offset remains signed until alignment/range and
existing E9 limits pass. Preserve complete command range validation and enabled
first-instance capability. Candidate and fallback command intervals cannot
overlap; their banks cannot alias ID/pose shader storage. Identity storage is a
declared live nonpersistent global-RD storage bank of uint32 slot identities,
read through the fixed effective BufferRD binding. Metadata cannot prove shader
semantics or GPU bytes; the caller guarantees that declared binding and layout.
Registration capacity `N` is positive. In signed64/widened arithmetic validate
candidate base `C >= N`, `C + N`, `(C + N) * 4`, actual bank size, E5 draw-slot
capacity and its signed32 domain before narrowing. Both commands retain the
same geometry and fixed material/BufferRD sets.

Candidate command intervals and candidate ID intervals are exclusive writers
across registrations and cannot overlap any registered fallback region.
Immutable fallback regions may be shared. A second instance of one MultiMesh
does not automatically have private candidate IDs: supply a separate candidate
region/base or reject the registration. One fixed effective ID bank can serve
several owners with firstInstance selecting their distinct regions. Do not
infer these dependencies from instance RID alone.

Validate all metadata before replacing a registration/configuration. Expected
bad input returns quiet `ERR_INVALID_PARAMETER`, unsupported configuration
`ERR_UNAVAILABLE`, capacity/ownership conflict `ERR_ALREADY_IN_USE` as appropriate.
Failed operations retain the previous complete state; no GPU payload readback.
Revalidate generation and live resources at recording. Attachment/base/material
changes invalidate registration for the next recording. Callback code cannot
reattach/detach, change scene objects or mutate these bindings mid-recording.

The new transient context provides viewport RID, monotonically increasing
per-viewport render epoch, configuration generation, view index 0, internal
extent/render region, actual corrected projection, camera transform, borrowed
read-only current depth RID and the engine-owned compute-list token. Epochs
advance for actual viewport recording, not simulation ticks or global draws;
skipped viewports produce no callback/ack. Projection already contains Godot's
depth/Y correction; do not apply it again. Epoch wrap disables/reconfigures the
feature rather than accepting an old acknowledgment.

Producer returns `{epoch: int64, configuration_generation: int64, error: int64}`.
Only the exact current identity and `OK`, normal callback return, intact scope
and revalidated registrations allow all candidates to be selected. Missing,
wrong-typed/stale acknowledgment, exception or reported error selects fallback
for all registrations. There is no API to commit then throw. An optional bounded
per-render outcome records epoch, selected mode and reason; it certifies only
recording decisions. Context getters become unavailable after callback return.

## Publication, fresh fallback and partial failure

Never reuse a prior candidate. Select fallback at the very start of EVERY actual
viewport render, before feature eligibility and before any early-return branch.
Feature-off/unregistered/secondary ordinary rendering keeps the persistent
fallback attachment. Candidate selection is render-local; it never calls
`multimesh_attach_*`, changes material RIDs or emits mid-list dependency rebuilds.

One fixed caller-owned BufferRD ID bank contains disjoint regions per consumer:

```text
[0, N)    uncull identities, optionally shared immutably by owners
[C, C+N) candidate identities, exclusive to one registration; C >= N
```

For a single consumer choose C=N, giving the original two-bank layout. Both
commands address the same ID bank. Vulkan firstInstance (already included
in INSTANCE_ID) chooses a region; never add it twice. The first safe fixture's
fallback region is immutable slot identity `i -> i`, command firstInstance=0,
instanceCount=N. Consumer shader reads current authoritative per-slot generation,
active state and group/mesh membership, and suppresses inactive/nonmember slots
before geometry access. Thus camera motion, a missing callback, dead/refilled
slots or a previous candidate cannot leave a stale dense ID prefix. Current
poses/root/active state must already form one valid published snapshot. N is
the entire addressed stable-slot space, not the population of one mesh group;
every shader-addressed slot must lie in [0,N). Arbitrary external IDs require an
explicit immutable local-to-global lookup instead of assuming dense IDs. A
consumer without this complete fallback rule is not admitted. A denser uncull
fallback requires its own fresh-publication protocol in a later extension.

Inactive/nonmember slots write neither depth nor color. Check that state before
per-ID pose/cache loads; native vertex fetch and vertex invocations for the
N-slot fallback still cost work. This stage can save culled vertex-shader
deformation, not a shared pose-cache compute job that already ran earlier.

Producer may write only private hierarchy/scratch, candidate ID region and
candidate command; fallback and current pose/root state remain immutable. On
success, candidate firstInstance=C and instanceCount=visible_count. Every live
identity occurs at most once and counts/ranges remain within registered N.

Use separate dispatch stages for bounded reservation and command finalization.
For GPU-detected candidate overflow or unavailable conservative proof, the
finalizer emits a legal fallback command firstInstance=0, instanceCount=N.
This GPU fallback needs no CPU readback and cannot depend on CPU acknowledgment
seeing an overflow flag. Diagnostics record fallback occurrence/cost. A source
population beyond N cannot be repaired by truncation: reject that run. A standalone
reference probe that publishes count zero and reports capacity rejection verifies
its guard only; it does not prove the native GPU fallback above.

The engine begins and closes the producer compute list through a scope guard,
including exceptions. Producer uses the supplied token, checked dispatch/bind,
push constants and supported compute barriers; it must not begin/end another
list, draw, submit/sync, read back, free resources, retain depth/context/token or
dispatch asynchronous continuation. A barrier may change the graph segment;
the engine still owns the final public list scope. Partial recorded work is not
rolled back and may execute against private candidate outputs. An unrecoverable
RD scope violation aborts/rejects the frame/run; detecting an open list alone is
not proof that ordinary rendering can safely resume.

FALLBACK_SELECTED requires a valid inherited draw path. If fallback itself fails
E5 owner/material/motion/capability/bank guards, report FRAME_REJECTED with the
existing bounded diagnostic; do not bypass those guards or claim conservative
visible output. Enabling TAA does not make a non-history E5 fallback compatible.
Safe visibility claims require valid fallback, resources and published state.

## Depth proof and conservative visibility

Reverse-Z depth clears to 0 (far); closer surfaces have larger values. Hierarchy
stores min over EVERY covered base pixel, including clear/uncovered pixels.
Pad odd-size reductions conservatively with clear 0. A candidate may be rejected
only if its nearest possible reverse-Z bound is strictly below the sampled
farthest occluder depth minus a finite nonnegative bias over its ENTIRE projected
rectangle. Enlarge projection/texel coverage conservatively. Sample all intersected
cells at the chosen level with integer/nearest access; no center-only, average,
bilinear, representative MSAA sample or heuristic previous-frame depth proof.

For perspective, project all eight conservative 3D bound corners using the
actual corrected context matrix and current camera. Do not apply depth/Y
correction twice or extract near/far with original-projection formulas from
that corrected matrix. Unsafe/nonpositive clip W, near-plane crossing, a camera
inside the bound or an uncertified proof keeps the identity visible or uses
complete fallback; a four-flat-corner screen rectangle is not a general proof.
Start the independent one-owner VAT gate with the certified all-frame animation
envelope, transformed by the published root with conservative numerical padding.
Caller owns certificates, active/generation state, VAT samples and root/bound
snapshot consistency; invalid rendered GPU geometry requires caller rejection.

Bounds include the entire current animated pose. Near-plane intersections,
uncertified deformation, nonfinite values, empty/ambiguous projection, unknown
camera/extent/resource identity or unsupported format keep the unit visible or
select the complete fallback. Current-frame depth is rebuilt for camera cuts,
camera shifts, no-wall frames and extent changes; old depth/IDs are not a
substitute. False positives cost work and are reported; false negatives fail.

Only camera rendering is culled. Gameplay, damage, networking, active population
and pose histories are not advanced or deleted by visibility. Later shadow/view
support needs independent visibility. A camera-occluded unit may cast a visible
shadow. E8 history follows the same stable identity/current render snapshot
across all eventual depth/color/motion consumers; the early callback is not a
history-epoch acknowledgment or a separate simulation step.

## Native batching and identity requirements

Early/remaining membership must be homogeneous inside every auto-instancing
repeat group. Include phase in batching compatibility or split repeats before
submission; a per-owner `continue` over an existing mixed repeat is unsafe.
Regression: selected and unselected ordinary owners sharing mesh/material, in
both sorted orders, and another transformed owner of the same MultiMesh.

`push_constant.base_index = i + element_offset` indexes native InstanceData.
Preserve original source indices for phase spans, or rebuild and upload a
matching complete remap. Copying only element pointers into new arrays is
insufficient. Color and resumed depth must preserve the same native transforms,
material state and INSTANCE_ID firstInstance semantics. Rebind cached native
state after the compute callback without modifying persistent attachments.

## Ownership and work limits

Engine owns viewport metadata, transient context/scope and native depth. Caller
owns meshes/materials, fixed ID/pose data, two command intervals, hierarchy and
scratch. Borrowed RIDs cannot be freed or repurposed while attached or recorded
readers may use them. Unregister consumers, remove bindings and use the existing
RD retirement/lifetime discipline before freeing. Metadata detach/CPU callback
return is not a GPU fence. The reference terminal readback drains only its own
bounded work; final caller counters are not native leak or physical VRAM proof.

Bound occluder surfaces to 4096 per render. If exceeded, skip the split entirely,
select fallback and perform ordinary depth; no partial early occluder draw before
this decision. Caller fixes N, hierarchy dimensions and scratch capacity.
Callback count is at most one per recorded opted view. Overflow does not grow
storage, allocate per unit or create an unbounded retry queue.

## Controlled reference and native acceptance

The source fixture under `fixture/` targets the unchanged E12b editor. It owns a
small depth image, rasterizes a procedural wall with a gap and builds/tests its
own current-frame hierarchy. It is a proxy REFERENCE implementation, with a
deterministic orthographic screen-domain candidate set. It does not implement
native stage selection, native depth reuse, a MultiMesh consumer or general 3D
perspective/animated-bound projection. Native mode must be explicitly unavailable.

The independent full-resolution oracle requires every potentially visible
identity to remain present, allows/reports conservative extra identities and
also checks command bounds, admitted uniqueness, sentinels, overflow, diagnostics
and identity. Cover a fully blocked rectangle, foreground, gap, edge, equality/
bias, uncertain/near flag, open frame, shifted camera and old-wall -> current-clear
sequence. Numerical readbacks happen only after producer recording, never to
choose GPU work. Synthetic receipt tests qualify validation logic only.

Before native implementation acceptance, add actual registration/recovery,
missing/throwing/stale callback, unsupported-but-drawable fallback, invalid
fallback, partial producer, GPU overflow fallback, mixed batching/source-index,
feature-off, second owner and lifetime probes. Qualify depth/color equality,
exact current view/epoch and absence of repeated occluder depth draws. Reference
results cannot substitute for these tests. GPU runs wait for A/B coordination.

Compare open and occlusion-rich scenes using identical geometry/grouping/build
flags. Include best existing uncull compaction/animation-LOD control and the
caller-owned proxy route, not only the deliberately conservative N-slot safety
fallback. Account full frame, occluder depth, hierarchy, producer/finalizer,
remaining depth/color, CPU recording and all scratch/ID/command/history bytes.
Reject false-negative visibility and capacity loss. Adopt only repeatable total
frame improvement or material memory benefit without frame regression. No FPS,
million-unit capacity, full release-driver or physical VRAM claim exists here.

## Source seams and primary references

- `_render_scene`, Forward+ depth block and PRE_OPAQUE: split native depth before
  the existing callback, preserving resume LOAD and normal opaque color.
- `_fill_instance_data`, `_render_list_template`: phase-homogeneous repeats and
  original InstanceData/base-index identity; render-local command selection.
- `_multimesh_commit_shared_view`, MeshStorage: attachment clears uniform caches
  and notifies dependencies, so it is not a per-render publication operation.
- RendererViewport/RenderDataRD: carry actual viewport RID, configuration/recording
  epochs and transient context rather than inferring them from global frame count.
- RenderSceneDataRD getters: corrected projection already includes depth/Y and
  jitter. RenderSceneBuffersRD depth getters already exist.
- [CompositorEffect](https://docs.godotengine.org/en/latest/classes/class_compositoreffect.html):
  PRE_OPAQUE follows depth; RenderData lifetime is callback-scoped.
- [Rendering Compositor proposal 7916](https://github.com/godotengine/godot-proposals/issues/7916):
  related custom-pass selection design; this is not an implementation of that proposal.
- [MultiMesh culling/LOD proposal 10669](https://github.com/godotengine/godot-proposals/issues/10669):
  related individual-instance workload; no performance figure is inherited.

The pinned source audit and unchanged editor identities accompany each task's
handoff. Native API code and exact editor/SDK identities are separate from
real-driver acceptance. Run the corrected 14 orthographic controls on every new
perspective editor before the integration owner's P01-P08 perspective/VAT gate.
TAA/E8 and valid fallback history follow independently; eight LOD owners/ranges
are a later integration experiment. No editor-default or performance promotion
follows from source/build success.

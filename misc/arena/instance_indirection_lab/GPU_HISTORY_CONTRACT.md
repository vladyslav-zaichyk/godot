# E8: GPU render-state history and motion vectors

Status: native implementation candidate, 2026-10-24. Contract source audit:
`820f0af33323e4871448fd940e2806930803745b` (Arena Godot 4.7.2).
The opt-in API and shader context are implemented in the isolated E8 candidate.
Its procedural consumer and separate editor/SDK are tracked by
[E8](https://github.com/vladyslav-zaichyk/project-arena-tracking/issues/124).
Native/managed compilation and GPU qualification are separate gates. GPU tests
wait for the parallel renderer A/B; existing editor/SDK bundles are immutable.

## Decision and limits

Add an opt-in evaluation context to E5 shader-driven MultiMesh materials so
their vertex function can read the previous rendered state of the same stable
identity. Reuse BufferRD, native camera/owner history and the native velocity
target. The renderer owns snapshots, generations and animation data; the engine
does not allocate, copy or infer a per-instance history pool.

First qualification: Windows x64, Vulkan, Forward+, 3D, one viewport/history
timeline, opaque indexed triangles, one immutable mesh surface, fixed vertex
correspondence and an unshaded procedural fixture. Retain E5 bounds, ownership
and unsupported-feature restrictions except its explicit motion-vector ban.
Different-cadence viewports cannot share one history timeline in this phase.

E6a writes into a native mesh's base geometry. Current and previous attributes
can alias that same buffer (`mesh_storage.cpp:1538-1548`); overwriting it loses
the old shape. This contract therefore covers shader deformation from immutable
geometry and retained BufferRD/immutable animation data. Native vertex-buffer
mutation needs a separate previous-geometry design. It is not enabled by E8.

This enables correct raw velocity inputs, not a guaranteed FPS increase. Motion
evaluation can execute the vertex function twice and adds history bandwidth and
storage. TAA/FSR2 image quality, physical VRAM, performance, lit TBN, shadows,
transparency, MSAA and multiview require separate qualification. No multiplayer
authority, combat or network identity semantics change.

## Experimental engine API

Append one optional argument to the existing experimental operation:

```cpp
Error multimesh_attach_shared_draw_buffer(RID multimesh,
        int draw_slot_capacity, RID indirect_command_buffer,
        int command_byte_offset, bool use_shader_motion_vectors = false);
bool multimesh_uses_shader_motion_vectors(RID multimesh) const;
```

Default false preserves old source behavior and attach-time motion rejection.
True declares a caller-owned shader history contract and bypasses only that
attach-time ban. All other validation remains. Validate before mutation: a
failed attach/rebind preserves capacity, borrowed RIDs and the previous flag.
Successful legacy attachment, detach or ordinary allocation clears the flag.
The existing transform-backed shared API remains unchanged.
`multimesh_get_buffer_rd_rid` still returns an invalid RID for shader-driven
views; no native transform/history bank is introduced. Regenerate and ship the
matching .NET SDK; old precompiled C# binary compatibility is not claimed.

The effective material must also opt in:

```glsl
shader_type spatial;
render_mode shader_driven_instances, shader_driven_motion_vectors;

void vertex() {
    // Sketch only: validate the current slot and identity before buffer loads.
    // Resolve CURRENT visible_ids[INSTANCE_ID] in both evaluations.
    // Select that identity's current or valid previous rendered pose.
    // On missing history, evaluate the current pose in both evaluations.
    bool previous = IS_PREVIOUS_FRAME;
}
```

`IS_PREVIOUS_FRAME` is a new readonly, vertex-only bool. It is true only during
the previous vertex evaluation of the motion-vector pipeline, and false during
the current evaluation and ordinary color/depth/shadow vertex evaluations.
It is usable by vertex helper functions. These are two calls within the same
GPU vertex invocation, not two draws. User vertex/helper evaluation must be
pure with respect to mutable shader globals: previous evaluation must not
change the subsequent current result. Current output must match the same
shader's ordinary non-motion evaluation.
Reject assignment, fragment/light access, missing required render modes and
unsupported backends. A valid analytic/static material need not read the bool;
the capability is an explicit promise, not proof of correct history.

Keep `INSTANCE_ID` unchanged: it is the current draw slot and already includes
`firstInstance`. Never use a stable ID as the native geometry-owner index.
`TIME` retains its existing current/previous semantics; it is not a reliable
context detector. A fallback involving analytic time must explicitly evaluate
the current render sample even in the previous invocation.
Pose output is an owner-local deformed vertex; apply the contextual native
owner/view/projection transforms exactly once. World-space pose data needs an
identity owner for the first fixture. No previous-context guarantee is added
for every camera builtin: MAIN_CAM_INV_VIEW_MATRIX can still refer to current
camera data. Billboarding remains outside first qualification.

For shader-driven owners requesting history, require the effective-material
capability, including overrides, overlays, next passes and debug replacements.
Incompatibility skips the affected draw with bounded diagnostics. Preserve
ordinary/native material reuse and non-opted E5 guards:

| Owner | History-capable material | Behavior |
| --- | --- | --- |
| Ordinary native mesh/MultiMesh | Allowed | Retain native transforms and motion behavior; no E5 bypass is inferred from the material. |
| Legacy transform-backed shared view | Allowed | Retain its existing motion restriction and borrowed-transform rules. E8 does not lift this ban. |
| Shader-driven, new flag false | Allowed | Retain the existing E5 guard, including its conservative global-motion rejection. |
| Shader-driven, new flag true | Required | Accept only selected materials with both modes; use this viewport's motion demand. |

For the new true-flag path, missing material capability always skips drawing,
even when motion is off. With a capable material, local motion off draws only
the current pose regardless of unrelated viewport demand; local motion on
evaluates both poses, also regardless of unrelated demand. Unrelated viewports
do not share this history pair. False-flag and legacy shared paths retain their
existing guards, including conservative global rejection; E8 does not loosen
them. Ordinary native owners retain ordinary native behavior.

The native opt-in must
force motion/dynamic classification even with no TIME, CPU transform updates or
material changes. BufferRD writes do not themselves invalidate those caches.
Use the current viewport's motion demand, including compositor/TAA/upscaling
demand, rather than global viewport counters or only the opaque color-pass flag.
Support specialized and ubershader paths and the separate upscaling motion list.

## Identity and the meaning of previous

Both evaluations follow this addressing chain:

```text
CURRENT draw slot -> CURRENT stable (slot, generation)
                 -> current / previous snapshot of THAT SAME identity
```

For `[7, 2] -> [2, 7]`, draw slot zero reads ID 2's previous pose. Never read
the previous compact visible list. Generation zero is invalid; slot reuse
changes generation. Stable slots are not automatically network entity IDs.

The previous snapshot is the state used by the previous acknowledged render
recording of **this viewport**, paired with a clean end of the enclosing draw,
including interpolation phase, selected pose samples and blend parameters.
It is not the previous simulation tick, the
last frame in which this unit was visible, a GPU-completed frame or proof of
physical display. Multiple simulation ticks do not advance render history.
Two renders with no simulation tick still advance it: after the first frozen
render, object velocity must stop repeating.

A previous pose is valid only when generation, expected previous viewport
render epoch, history domain, continuity token and representation/schema match.
Tokens must not silently wrap into valid old history: retire/reset the domain
before reuse. Snapshot validity includes everything needed to reproduce the
pose. A pointer to an overwritten animation-cache page is not history. Pin
referenced pages/palettes until both snapshots and in-flight GPU work release
them; immutable VAT data can be shared without copying its contents.

| Event | Required consumer action |
| --- | --- |
| Normal motion or animation-rate change, same representation | Preserve the last actually rendered sample, including interpolation. |
| Spawn, generation reuse, teleport, missing/stale history | Evaluate the current shader-deformed owner-local vertex in both contexts for one frame. |
| Changed mesh/LOD correspondence, animation schema or asset revision | Invalidate history; no cross-LOD correspondence is inferred. |
| Unit hidden while the viewport renders | Advance its render-state history within a declared capacity, or explicitly reset it on reappearance. |
| Viewport pause/skip, camera cut, resize or history-domain change | Reset consumer continuity before reuse; coordinate the viewport's temporal-history reset separately. |
| Invalid CURRENT identity, capacity, payload or nonfinite position | Reject/suppress the affected batch, report a bounded diagnostic; never treat it as missing previous history. |

Resetting previous shader pose to current removes only shader-state motion.
Previous camera and native owner matrices still apply, so velocity can be
nonzero. With the fixture's constant identity owner, this yields camera-only
velocity. It does not emit a per-ID disocclusion mask, clear TAA accumulation or
guarantee no ghosting. Native `instance_teleport`/motion reset does not reset
BufferRD history. Never rely on out-of-range BufferRD returning zero: zero is
a valid ID. Validate before stride multiplication and command publication.
If a teleport changes both the native owner and shader pose, reset those two
histories separately.

## Publication, synchronization and ownership

Default consumer: two fixed-bound BufferRD pose banks, a fixed metadata binding,
one current ID list and indirect commands. Simulation storage is separate.
All accesses use the global RD on its render thread and tracked GPU commands
on the same queue. Do not publish several material-parameter changes as if
they were one atomic snapshot.

For each actual viewport render, publish one coherent epoch before its first
consuming pass:

1. Copy committed current pose/metadata into previous storage using tracked GPU
   operations. Produce the new rendered sample, identities, lifecycle metadata
   and bounded indirect command into current storage.
2. End producer lists and order dependent compute stages. Freeze the pair,
   list, metadata and commands across depth, color and motion consumers.
3. Match this viewport's POST_OPAQUE recording marker to clean completion of the
   enclosing draw, observed through a matching post-draw acknowledgment. This
   commits only the CPU scheduling ledger; do not mutate the frozen GPU tuple.

If preparation occurred without a matching clean render acknowledgment, do not
copy that prepared Current as committed history. The default recovery changes
history domain, builds the next valid current sample, then seeds Previous from
that new sample before consumption; both contexts use current pose for that
reset frame. Never promote an unrendered sample. A more advanced consumer can
explicitly retain/recover the last committed bank instead.

An initial fixture can synchronously enqueue a render-thread producer from
`RenderingServer.frame_pre_draw`, before `_draw` is queued, using an
always-updating SubViewport. Do not await another frame signal inside that
callback. Verify exactly one publication and a matching POST_OPAQUE callback
marker per viewport render and pair it with post-draw completion; freeze the
camera/owner plan for that render. PRE_OPAQUE is after the depth prepass and is
too late to publish state that depth already consumed. POST_OPAQUE follows the separate upscaling motion
pass and is suitable for numerical capture/recording markers. It precedes
end_frame and submission, so it alone is not a clean end-of-draw acknowledgment.

The RD graph tracks BufferRD storage reads, copies and compute writes across
commands and frames. RAW/WAR/WAW dependencies make two fixed banks a sufficient
minimum under the strict same-queue, tracked-GPU-only contract. A larger ring
may improve scheduling but is not an automatic correctness requirement. This
is a source-based design; the later GPU stress gate must verify it. Manual
deprecated `barrier`/`full_barrier` calls cannot repair missing publication order.

Render-thread callback completion, `frame_post_draw` and a fixed number of
frames are not GPU completion fences. They do not authorize CPU mapped writes,
untracked storage reuse or freeing borrowed resources. Tracked GPU overwrites
of the fixed banks follow the graph dependencies above. A CPU acknowledgment
commits a render epoch for scheduling, not GPU completion.

The consumer owns all BufferRD backing storage and cache pages. Wrappers and
MultiMesh views borrow it. On replacement, validate a complete bundle, retire
all its consumers, publish the replacement on the render thread and reset its
history domain. Preserve the old complete bundle on failure, or quarantine the
whole batch; never render a partially rebound current/previous pair.
On shutdown remove producers, effects and material/view bindings, then retire
in-flight uses through a verified completion/deferred-destruction path before
freeing caller storage. An untimed fixture readback may verify completion of a
terminal tracked operation dependent on the last consumer's produced velocity
output/capture ledger. A copy of a pose buffer which the draw only read is not
such a completion proof. Frame counting alone does not prove retirement.
Never free engine-owned Mesh RD RIDs.

## Numerical oracle and bounded fixture

Use a procedural 128x64 viewport, fixed quad topology, identity native owner,
no lighting, shadows, transparency or MSAA, and deterministic root transforms
plus one deformation coefficient. No TIME or CPU owner-transform animation is
needed to exercise custom motion. Request raw velocities with an existing
CompositorEffect's `needs_motion_vectors`; capture `get_velocity_texture` and
`RenderSceneData.get_uniform_buffer` contents at POST_OPAQUE using tracked GPU
copies/sample extraction. Keeping just their RIDs for a later read is unsafe
because the contents can change. Do not judge temporal output
images or write screenshots. Readback is outside performance measurements.

The existing Forward+ RG16F output is:

```text
current_ndc  = current_clip.xy / current_clip.w - current_taa_jitter
previous_ndc = previous_clip.xy / previous_clip.w - previous_taa_jitter
velocity     = 0.5 * (previous_ndc - current_ndc)
```

Thus the raw convention is previous UV minus current UV. Use the actual captured
current/previous matrices and jitter; do not add a second Y flip, pixel scale or
jitter correction. Use power-of-two orthographic displacements, with every
designated nonzero sample component between `2^-8` and `2^-3` UV units. The
minimum is 16 times the error budget; a zero-output implementation must fail.
Take four samples per instance at least two pixels inside a proven covered
current triangle. Find each sample's current-triangle barycentric coordinates
and use the corresponding vertices' current/previous clip positions to derive
the deformation oracle. Reject NaN/Inf before comparing. Preregister maximum
absolute component error `2^-12` UV units (1/32 horizontal pixel at width 128).
This budget includes RG16F quantization and float32 projection for the fixture's
small displacements. Larger-range/perspective tests need their own documented
budget before execution; never loosen a threshold after seeing a failure.

Default capacity is 16 stable slots, eight draw slots, `firstInstance = 3` and
`instanceCount = 2`. Reorder IDs 7/2 with opposite motions; poison unused slots
and add a valid ID-zero case. Cap the fixture at 64 stable slots; reject larger
configuration without allocating. GPU-invalid current IDs or count overflow
write a zero instance count for the batch and a bounded diagnostic counter.
Cosmetic omission never changes authoritative combat state.

The example fixture ABI is consumer data, not a native allocation requirement:

| Requested input | Bytes |
| --- | ---: |
| Current and previous pose records: 48-byte transform plus 16-byte auxiliary data each | `128N` |
| Lifecycle metadata | `16N` |
| Epoch/capacity header | 16 |
| Eight packed uint draw-slot IDs | 32 |
| Indirect command with 16-byte prefix | 36 |

The auxiliary data can hold generation, representation revision, deformation
coefficient bits and sample epoch; lifecycle metadata holds current/previous
domain and continuity tokens (zero invalid). The header holds current/expected
previous epoch, stable capacity and schema. Draw-slot capacity is the fixed
eight-slot fixture configuration. This supplies the validity checks
above without a native per-instance pool. Default inputs total 2388 bytes;
the hard fixture maximum totals 9300 bytes. For the same 64-byte current-pose
control, history plus lifecycle adds `80N` bytes: 76.29 MiB at one million slots.
Both full banks plus lifecycle total 137.33 MiB at that capacity. These are
logical requested input sizes, excluding meshes, render targets, fixed native
descriptors, diagnostic/capture outputs, allocator overhead and in-flight
retirement. They are not resident VRAM. Production capacity, history retention
and overflow are renderer policy.

## Qualification gates

| Gate | Required evidence |
| --- | --- |
| Shader/API contract | Readonly vertex context, helper use, rejected writes/stages/backends/missing modes; optional default and query; transactional failure; detach/allocation reset. |
| Existing behavior | Feature-off shader behavior and BufferRD/re-spirv/E5/MeshRD contracts retained; matching generated .NET SDK and both compile-only consumers. |
| Basic velocity | Static zero, camera only, root only, deformation only without TIME/CPU changes, and combined camera/deformation; both signs and magnitudes match the oracle. |
| Identity | Reorder 7/2, nonzero firstInstance, poisoned unused slots, valid ID zero; both contexts resolve the same current identity. |
| Cadence | Multiple renders per simulation tick, multiple ticks per render, frozen time, hidden-unit reentry and skipped viewport/resume. |
| Discontinuity | Spawn/despawn/reuse, teleport, stale epoch/domain and representation change; current-pose fallback under static and moving cameras; poisoned old history never sampled. |
| Jitter | Static jitter-only geometry has zero de-jittered velocity; moving camera plus jitter matches the same oracle. |
| Render routing | Enable motion after attach, compositor demand, unrelated viewport demand, owner/material capability combinations, overrides/next passes/debug replacements, specialized/ubershader and separate upscaling motion list. |
| Consistent current pose | Depth and color consume the same frozen current epoch; ordinary vertex contexts are false. No claim of lit/shadow quality. |
| Bounds and lifetime | Overflow/current-ID rejection, failed rebind preservation, repeated detach/free/recreate and at least 32 epochs with two fixed banks; logical RD/object allocations plateau after verified retirement. |

The default two-bank stress sequence records 64 epochs without per-frame
synchronous readback or GPU waits. A bounded GPU capture ledger holds numerical
samples and their matching scene-UBO snapshots; read it once after terminal
completion. Reject ledger overflow. Account for capture bytes separately from
render-input sizes above. Per-frame readback would drain the queue and conceal
the in-flight hazards this gate is intended to detect.

The first GPU gate uses raw compositor-requested motion without temporal
accumulation. A separate temporal-plumbing smoke gate checks routing with TAA
and FSR2 enabled; it does not qualify their visual quality. Full-frame tails,
GPU preparation, resident memory and performance adoption need the renderer's
later controlled experiment. Missing/noisy evidence remains inconclusive.

Delivery sequence: this contract; isolated native/compiler/API patch and CPU
contracts; procedural numerical consumer; separate immutable Windows .NET
editor/SDK with native/fixture hashes; GPU correctness after the parallel A/B
owner releases the test window. Keep the existing control if compatible; a
paired rebuild is required only for a concrete mismatch or attribution issue.
Do not replace any bundle currently under test.

## Source anchors and primary references

Line numbers describe the pinned source above, not future modified files.

- [Previous/current vertex evaluation and native velocity](../../../servers/rendering/renderer_rd/shaders/forward_clustered/scene_forward_clustered.glsl#L804): previous context at 804-817, current at 847-861; output formula at 3106-3113.
- [E5 attachment validation](../../../servers/rendering/renderer_rd/storage_rd/mesh_storage.cpp#L1654): global motion ban at 1686-1690; [public declaration](../../../servers/rendering/rendering_server.h#L262).
- [Forward+ runtime guard](../../../servers/rendering/renderer_rd/forward_clustered/render_forward_clustered.cpp#L344): motion classification at 1090, motion list at 1178, per-view demand at 1831, PRE_OPAQUE at 2197, separate upscaling motion at 2271 and POST_OPAQUE at 2294; surface motion flag at 4231.
- [Shader animation classification](../../../servers/rendering/renderer_rd/forward_clustered/scene_shader_forward_clustered.cpp#L257) currently depends on TIME; [builtin declarations](../../../servers/rendering/shader_types.cpp#L93).
- [Frame publication order](../../../servers/rendering/rendering_server_default.cpp#L443) emits pre-draw before queueing the draw; [camera history](../../../servers/rendering/renderer_scene_cull.cpp#L3699) is committed for each viewport render.
- [Readonly SSBO resource tracking](../../../servers/rendering/rendering_device.cpp#L4712), [persistent graph access state](../../../servers/rendering/rendering_device_graph.h#L213) and [copy dependencies](../../../servers/rendering/rendering_device_graph.cpp#L1866).
- [Velocity target format](../../../servers/rendering/renderer_rd/storage_rd/render_scene_buffers_rd.cpp#L845); existing [compositor contract](../../../doc/classes/CompositorEffect.xml), [velocity getter](../../../doc/classes/RenderSceneBuffersRD.xml) and [scene UBO getter](../../../doc/classes/RenderSceneData.xml).
- [Godot rendering architecture: temporal antialiasing](https://docs.godotengine.org/en/stable/engine_details/architecture/internal_rendering_architecture.html#temporal-antialiasing) explains the role of custom deformation history. [AMD FSR2 motion-vector requirements](https://gpuopen.com/manuals/fidelityfx_sdk/techniques/super-resolution-temporal/) provide an external sign/jitter cross-check, not an FSR version upgrade or qualification of this fork.

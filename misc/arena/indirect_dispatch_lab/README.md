# Indirect compute validation and next GPU work

Status: isolated source patch and CPU contract verification, 2026-10-24.
Audit base: `361310fc6127bbc9dfbe981492b5af44ad7c52a6`.
Existing editor/SDK packages and the E10 hardening stand are immutable controls.
This patch supplies no replacement editor; full-engine compilation and GPU
runtime qualification are separate gates.

## Implemented metadata fix

`compute_list_dispatch_indirect` already lets a GPU-produced three-word command
choose compute workgroup counts without CPU readback. This can drive bounded
active-unit queues, culling stages or pose-cache jobs. It is an existing API,
not a new swarm scheduler.

The old range check evaluated `p_offset + 12` as uint32 and omitted alignment.
For example, offset `UINT32_MAX - 3` wraps its command end to 8, so a buffer of
8 bytes passes that check. An offset of 1 also passes when enough bytes remain.
With otherwise valid call state, such metadata could reach backend recording.
These are source-derived cases; no invalid GPU command was executed to reproduce
them.

The new pure `RenderingDeviceIndirect::validate_dispatch_range` requires a
four-byte offset and a complete twelve-byte command, promoting the addition to
uint64 before evaluating it. RenderingDevice calls it before descriptor
preparation, binding state changes, graph commands or dispatch-count mutation.
The public void method, uint32 argument, RID ownership and valid call semantics
remain unchanged. Expected invalid metadata retains an early engine diagnostic.
There is no new managed method or SDK contract.

The [Vulkan dispatch specification](https://docs.vulkan.org/refpages/latest/refpages/source/vkCmdDispatchIndirect.html)
requires four-byte offset alignment and a complete command within the buffer
(VUID 02710 and 00407). These checks inspect CPU-visible metadata only. The caller
still generates workgroup counts within the device limits and its own bounded
work capacity; the helper does not inspect or clamp GPU payloads.

## CPU verification

Four new cases in `tests/servers/rendering/test_indirect_draw.cpp` cover exact
command ends, prefixes and short buffers, alignment, uint32 overflow, synthetic
uint64 command ends and a small exhaustive range sweep. The sweep uses an
independent subtraction oracle guarded against underflow.

The exact test source and actual indirect helper were compiled with MSVC
19.42 in a standalone harness using vendored doctest 2.4.12. The thin
`tests/test_macros.h` shim supplies doctest, `initializer_list` and an empty
force-link marker; it substitutes no implementation or test assertions.
All 30 cases / 12,717 assertions passed, including the existing draw/count-range
tests. The four new cases contain 12,543 assertions. Removing either alignment
or integer promotion from a copied helper compiled successfully and made the
new tests fail. These controls distinguish a regression from a compile failure.

The normal engine test filter `*[IndirectDraw]*` includes the new cases.
Standalone success qualifies the pure validator, not the complete RD integration,
driver execution or diagnostic/recovery behavior. A later exact-source engine
build should run that filter and a small direct/indirect numerical compute probe
after the renderer A/B slot is released. No FPS benefit is claimed for this fix.

## Next correctness work

E12b now implements these follow-ups in a separate candidate. See
[the checked API contract](E12B_CONTRACT.md) and the
[six-case numerical stand](../checked_compute_lab/README.md).
Native commit `7d59f5064a6cd40a1cf2aaa578b8e90d29d1cbe8` built successfully
with its exact generated .NET SDK; 106 CPU cases / 15,631 assertions passed.
GPU recovery, driver behavior and performance remain unqualified until the
parallel A/B slot is released. The earlier E12a evidence below remains specific
to its source patch, rather than a claim about this new candidate.

Two independent findings remain outside this patch:

| Finding at the audit base | Safe first consumer rule | Required follow-up |
| --- | --- | --- |
| Argument buffer also appears in compute shader storage bindings; compute graph tracks only one usage and detects a conflicting second usage only in development builds | Use a distinct argument buffer and a consumer uniform set that does not bind it; producer and consumer use separate lists/stages | Release preflight and alias/recovery tests before advertising a generally checked indirect-compute path |
| Dynamic-persistent storage can be created for indirect dispatch, but Vulkan passes raw offset while persistent CPU upload advances by frame slot | Use ordinary nonpersistent argument buffers | Reject unsupported persistent arguments or define and qualify frame-offset semantics |

`compute_list_add_barrier` creates a new graph compute list but restores
bindings. It does not remove the argument buffer from a previous producer set:
the consumer must bind its appropriate replacement set. `buffer_copy` and
`buffer_clear` already exist and require closed lists. These findings do not
require another copy/reset API, and this patch does not qualify those broader
usage combinations.

Source seams at the audit base: `rendering_device.cpp`,
`compute_list_dispatch_indirect`, `compute_list_add_barrier` and
`storage_buffer_create`; `rendering_device_graph.cpp`,
`add_compute_list_usage`; Vulkan driver's persistent mapped upload and
`command_compute_dispatch_indirect`.

## Scheduling already available

A synchronous `RenderingServer.frame_pre_draw` handler can call
`call_on_render_thread` before `_draw` is queued. The render-thread producer
therefore runs before scene update, particle/collider work, probes and viewports.
End every RD list before returning and use tracked dependencies. Do not use a
deferred/async signal handler or treat deprecated blanket barriers as ordering.
The pinned implementation is in `rendering_server_default.cpp`, `draw` and
`_draw`, with thread dispatch in `rendering_server_default.h`.

This is once per RenderingServer draw, not proof that a particular viewport
rendered. Viewport selection/skip happens later in `renderer_viewport.cpp`.
E8 render-history epochs must advance only for the matching acknowledged viewport
recording. A global callback does not supply finalized per-view/XR state.

An exact per-view early hook remains a conditional capability for a consumer that
needs that state. The existing global compute route is sufficient for the
single-view, caller-published state contract; adding a hook solely because
PRE_OPAQUE follows depth would duplicate it.

## Occlusion research boundary

Existing POST_OPAQUE compositor access, RenderSceneBuffersRD depth getters and
caller-owned RD textures can build a previous-depth hierarchy without a native
getter patch. Capture/copy/reduce within the callback and retain the matching
camera/projection, size and viewport epoch. Holding the native texture RID does
not preserve immutable depth. Godot's built-in CPU occlusion buffer tests native
owner bounds, not individual GPU stable IDs inside one crowd owner.

For Godot reverse-Z, a conservative hierarchy stores the farthest depth using
min reduction, including clear/uncovered pixels. The object's nearest possible
bound depth must be strictly behind the hierarchy over its entire projected
rectangle, with conservative bias. Near-plane intersections, nonfinite or
ambiguous projections and uncertain animation bounds keep the unit visible.
Use neither a center-only sample nor average/bilinear depth as proof.

The native MSAA depth resolve averages samples; the GI resolve selects a
representative sample. Neither guarantees the required farthest depth at a gap.
First qualification therefore disables MSAA; an extension must reduce all raw
samples conservatively. RenderSceneData projection getters already apply depth
correction and TAA jitter, so consumers must avoid applying either twice.

Previous whole-scene depth cannot certify present visibility under arbitrary
camera, occluder or swarm motion. A wall/door or unit moving away can reveal
geometry even without a camera cut. Reprojection and visibility hysteresis alone
do not prove conservative rejection. A safe initial proof restricts camera and
opaque occluders to unchanged state and uses occluder-only depth, not depth that
also contains moving crowd. Unknown history/configuration keeps candidates
visible. Camera visibility must not suppress shadow casters or gameplay work.

PRE_OPAQUE runs after the depth prepass, and ordinary prepassed opaque color uses
depth equality. Inserting previously absent crowd only after that callback is
not the existing opaque path. Rewriting the shared E8 tuple between depth and
color also violates its frozen cross-pass contract.

A larger native feature would instead define:

```text
opted opaque occluders depth
-> current-frame conservative hierarchy
-> per-view GPU producer callback
-> remaining and crowd depth
-> ordinary color and motion
```

That needs explicit pass selection, ordering and history/shadow rules. A generic
before-depth callback alone cannot supply current occluder depth. Caller-owned
proxy raster or a separate occluder view is an existing alternative with extra
representation/work. Compare complete pipelines on enclosed and open scenes,
with zero false-negative visibility, equal geometry/grouping and full producer,
pyramid, frame and memory accounting before selecting a native implementation.

Versioned references: [early scheduling](https://github.com/vladyslav-zaichyk/godot/blob/361310fc6127bbc9dfbe981492b5af44ad7c52a6/servers/rendering/rendering_server_default.cpp),
[indirect compute](https://github.com/vladyslav-zaichyk/godot/blob/361310fc6127bbc9dfbe981492b5af44ad7c52a6/servers/rendering/rendering_device.cpp),
[Forward+ passes](https://github.com/vladyslav-zaichyk/godot/blob/361310fc6127bbc9dfbe981492b5af44ad7c52a6/servers/rendering/renderer_rd/forward_clustered/render_forward_clustered.cpp),
[depth resolve](https://github.com/vladyslav-zaichyk/godot/blob/361310fc6127bbc9dfbe981492b5af44ad7c52a6/servers/rendering/renderer_rd/shaders/effects/resolve.glsl),
[camera projection](https://github.com/vladyslav-zaichyk/godot/blob/361310fc6127bbc9dfbe981492b5af44ad7c52a6/servers/rendering/renderer_rd/storage_rd/render_scene_data_rd.cpp).

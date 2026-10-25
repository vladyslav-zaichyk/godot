# E13c: native TAA, GPU occlusion and E8 history

Status: contract and numerical plan only, 2026-10-25. No native implementation,
new editor/SDK, GLSL compilation or GPU execution accompanies this document.
Audit source: `a5e1a2135cd673f00508c6b5b745d34d83f67d81`.
The existing E13b editor remains native
`3c3646629656d76133bf494edd937f2181a7e98b`, exact generated SDK
`4.7.2-arena-dev-g3c.36466296`. It still rejects E13 with TAA.

Implementation/build work follows the integration owner's acceptance of the
E13b perspective/bounds gate. The eventual E13c editor needs fresh exact-build
E13b regressions, an adapted existing E8 gate, and this combined gate. Existing
source, receipt or orthographic success cannot qualify the combination.

## Goal, ownership and first scope

Use native TAA with E13 visibility selection and existing E8 shader history.
Engine owns native view matrices/jitter, depth/color/velocity attachments,
recording order and per-render command selection. Caller owns stable identities,
current/previous root/deformation samples, validity, generations, GPU banks,
animation resources and conservative culling. No engine transform/history bank
or second stable-ID system is introduced.

First numerical fixture: Windows x64 Vulkan Forward+, single precision, one
history viewport and one view, centered perspective, native resolution, opaque
unshaded immutable indexed tilted quad with fixed vertex correspondence and
E8 procedural deformation. One E5/E9 LOOP owner/command, 16 stable slots, native
draw capacity 32, at most two active test identities chosen from 0, 2 and 7.
The complete fallback covers all 16 slots, with active/group checks before pose
loads. The real VAT body/animation shader remains a later integration gate.

Require clear/color background and `!load_color`. Exclude KEEP/CANVAS/sky,
MSAA, temporal upscaling/FSR2, forced motion vectors, the separate upscaling
motion list, screen-space/GI effects, shadows, transparency, XR, secondary views,
off-center frusta and generic compositor effects. Retain every other E13b/E8
material, geometry, alias, capacity and lifetime restriction. The admitted
split's opaque auto-instancing cost remains part of later full-frame A/B.

## Admission and proposed diagnostic opt-in

The later patch deliberately changes both configure-time and render-time TAA
admission. Removing only `!using_taa` is insufficient. For a TAA render, require
all registered consumers to use the E8 attachment flag and an E8-capable
effective material for both candidate and complete fallback. Preserve non-history
E5's existing global-motion protection. Never silently enable history on an
existing view. Validate before replacing metadata; failed operations retain the
previous complete registration/configuration.

The first TAA path permits only this viewport's native TAA demand:
`force_motion_vectors=false`, global viewport motion count exactly one,
global compositor motion count zero, no upscaling and no separate motion list.
Other motion-demanding views remain outside this qualification. Existing
TAA-off E13b admission stays available with no observer.

The existing E8 capture effect requests motion vectors and uses a compositor;
E13b rejects both. Freeze an explicit, narrowly identified observer exception
instead of copying that effect unchanged. Proposed optional fourth argument:

```cpp
Error viewport_configure_gpu_occlusion_prepass(
        RID viewport, uint32_t occluder_layer_mask, Callable producer,
        RID diagnostic_post_opaque_effect = RID());
```

This signature is a design target, not an available method overload. Empty RID
keeps three-argument source calls and the existing no-compositor policy. It does
not promise binary compatibility with old precompiled bindings; use the new
exact editor/SDK. A nonempty RID requires native TAA and
exactly one enabled effect occurrence across ALL callback stages in the actual
compositor. It must match the registered RID, have a valid POST_OPAQUE callback,
and have all five resolved-access/needs flags false. Duplicate RIDs count twice.
Validate a live effect RID at configure time; verify actual compositor membership,
flags and callback before admission, after the producer and before capture.
An unsupported initial graph retains valid fallback; graph mutation during the
producer rejects the frame. Observer changes increment configuration generation.

The caller retains the effect/compositor. The viewport borrows effect identity;
clear/unregister does not free caller resources. The observer may immediately
copy the current/previous UBO and enqueue depth/color/velocity/input sampling into
fixed caller ledgers. It must not mutate native attachments, scene, current or
previous poses, ID/command banks or history configuration. It owns its capture
compute list; it may begin/end that list only after the engine's producer list
has closed. The producer continues to borrow the engine-owned list and may not
begin/end another one. Native structural checks do not prove arbitrary callback
or shader code read-only; this is a trusted-caller contract, not a sandbox.
Descriptor caches may retain identity-only native RID keys, never borrowed
RenderData/buffer references or later native accesses through saved borrowed
texture RIDs. Reacquire and validate current native identities inside each callback.

## Required order and recording evidence

```text
publish coherent current/previous E8 state and complete fallback
-> current viewport epoch; default selection FALLBACK
-> clear native depth once; draw selected ordinary occluders
-> synchronous E13 compute and exact epoch/configuration acknowledgment
-> freeze candidate/fallback selection
-> LOAD same depth; draw remaining depth
-> clear normal color and velocity attachments; one opaque color+velocity pass
-> registered POST_OPAQUE numerical observer
-> normal native TAA processing, then normal remaining frame recording
-> clean post-draw observation and rendered-history ledger commit
```

Native TAA writes color and velocity in the SAME opaque pass. Do not add a
motion prepass or repurpose the FSR2 motion list. Preserve the existing MRT
clear/load behavior; do not clear velocity after opaque. Background probes must
catch stale velocity after geometry disappears. Depth, color and both E8 vertex
evaluations use the same CURRENT selected ID mapping and frozen snapshot.

Proposed outcome keys, reset false at each actual viewport epoch:
`opaque_recorded`, `taa_process_recorded`, `taa_history_bootstrap`,
`frame_recording_completed`. Set opaque only after its draw list closes; set
TAA process after that call returns; bootstrap records missing history at that
call; completed is set only at the successful end of scene recording. Preserve
the existing selection/mode/epoch/configuration keys. These flags certify CPU
recording decisions, not GPU execution, fences, presentation or image quality.

## Two acknowledgments and previous-state identity

```text
CURRENT selected draw slot -> CURRENT stable(slot, generation)
                          -> current/previous state of THAT identity
```

Never index a previous compact ID list. Candidate firstInstance selects the
private ID region and is already included in INSTANCE_ID. Fallback IDs `[0,16)`
are immutable `i -> i`; candidate IDs occupy `[16,32)`, with four guard words.
Both commands must remain legal in native capacity 32. The old E8 stand's
firstInstance=3 and eight-slot ID buffer are not this fallback ABI.

An E13 acknowledgment selects candidate commands. A rendered-history commit
requires the matching observer epoch, clean enclosing post-draw, completed
recording and the frozen publication tuple. A valid fallback render after a
missing/stale/error acknowledgment STILL commits normally. Successful candidate
acknowledgment without a clean completed render does not commit.

Keep preparation serial, E8 sample IDs and actual native viewport epochs as
separate fields. A skipped preparation advances neither actual render history
nor native viewport epoch. Do not promote speculative/unrendered state. For this
fixture update all 16 history slots per successful render, including hidden
units. Also exercise explicit reset-on-reentry. Retire/reset domains before
generation/epoch/continuity/schema tokens can wrap into old valid history.

Spawn, generation reuse, teleport or invalid previous state evaluates the current
deformed owner-local vertex in both contexts. Native previous camera/owner
matrices still apply: a reset under a moving camera can have camera-only velocity.
Invalid CURRENT state is a rejected publication/batch, never missing history.

## Coordinated temporal recovery

FRAME_REJECTED is different from a successfully rendered fallback. Current
scene-cull code advances previous camera data after render_scene returns even
when E13 returned early, while native TAA may never have run. E8 reseeding alone
cannot reconcile those histories. Do not continue this fixture after an abort,
viewport skip, camera cut or resize without the following declared reset.

Pause this viewport. Outside all render callbacks/producer scopes, execute actual
render-thread `viewport_set_use_taa(false)` then `viewport_set_use_taa(true)`
calls, and await that render-thread action before resuming. These two calls must
execute individually, not be coalesced into an unchanged scene property. They
reconfigure render buffers and remove native TAA history/temporary/previous-
velocity textures. Rebuild bounded cached descriptors by fresh texture identity,
reseed both E8 banks under a new domain, then publish one complete current tuple.
The resumed successful render must record TAA bootstrap before normal accumulation
continues. This is a qualified fixture recovery route, not a new public semantic
reset API or automatic production recovery policy.

This does NOT reset camera matrices, native owner history or global jitter phase.
Use actual captured matrices for reset-frame expectations. Native camera cuts
and E8 domain resets do not themselves clear TAA accumulation. The low-level
`RenderSceneBuffersRD.clear_context("taa")` also exists, but this first fixture
does not call it from the observer or retain RenderData/buffer references for a
later reset. General camera-cut/recovery integration needs its own acceptance.
Hold the camera fixed across each rejected control and its restoration; this
fixture does not claim to roll back the native camera history.

## Independent perspective oracle

Capture actual native current/previous UBOs immediately at POST_OPAQUE, before
TAA. Decode the pinned float32 schema only after matching source/layout hashes:
2848 bytes per scene, 5696 for both; projection offset0, view offset176, time2816,
jitter2824/2828, previous fields +2848. These offsets are not stable API.
The current E13 projection must match the native corrected jittered projection.
Do not de-jitter the culling projection or apply depth/Y correction twice.
Jitter phase follows the native rasterizer frame, not E13 or preparation serials.

For a current triangle with screen barycentrics lambda, current clip vectors C
and corresponding previous clip vectors P, compute `a_i = lambda_i / C_i.w`.
Interpolate EACH clip vector with `sum(a_i * Q_i) / sum(a_i)`, then divide by its
interpolated w. Raw UV velocity is:

```text
0.5 * [(previous_ndc.xy - previous_jitter)
     - (current_ndc.xy  - current_jitter)]
```

Averaging already-divided previous NDC with current screen barycentrics is an
orthographic shortcut. The tilted/depth-changing cases must make it fail.
`reference_math.json` freezes an analytic counterexample with correct velocity
`(2/75, 0)` and a separate jitter-only zero case. CPU reference checks are not
shader compilation or GPU qualification.

Use actual immutable source triangle/deformation correspondence; independently
verify pixel coverage and current depth. Sample four distinct interior pixels
per visible tested identity, at least two pixels from every triangle edge.
Current and previous tested clip-w must be >=0.5; small-motion components must
be <=1/8 UV. Component error <=2^-12 UV; depth error <=1e-5. Designated nonzero
components lie in [2^-8,2^-3]; a zero-output implementation must fail. Reject
NaN/Inf. Near clipping/singular previous-w remain separate selection cases and
cannot silently enter this small-motion oracle. Do not widen thresholds later.

## Fixed matrix, work and storage

`gate_plan.json` lists every scheduling record. Eight groups total128:
12 calibration,8 selection,6 hidden/reentry,10 lifecycle,8 cadence,
12 perspective/jitter,8 temporal reset/guards,64 fixed-bank stress.
T042 skips the viewport. T060/T062 reject invalid fallback before the producer;
T063 attempts a forbidden nested compute-list begin, which must return INVALID_ID
quietly before recording any culler work. The valid fallback must be restored
between independent negative controls. Build the false-flag view with motion
demand off before enabling TAA; a failed false rebind that retains true is not
the negative control. Expected127 actual view records,125 producer invocations,
124 complete cullers/observer captures/clean history commits/TAA process records,
and3 FRAME_REJECTED. TAA process counts are NOT resolve-dispatch counts: bootstrap
skips resolve. The 64-record stress tail has no resets or per-frame allocation.

One fixed128x64 Hi-Z backing supports actual extents128x64 and127x63. Explicitly
write clear0 padding without reading outside native depth. Eight mip levels use
10923 float words plus eight guards =43724 bytes. Each complete producer records
copy +7reductions +selection +finalizer:10 dispatches,9 barriers; totals1240/1116.
Observer records one bounded raw-grid dispatch per capture:124 more. Freeze the
later GLSL stage mapping before executing this numerical plan.
Resource preparation may flush CPU queues while the viewport is disabled; no
unlisted actual viewport warmups are permitted. If an implementation needs them,
version the matrix and counts before execution rather than hide rendered frames.

Per-record ledger strides: raw grid131072 bytes (fixed128x64x16), UBO5696,
proof44236 (512 publication/ID/command/state-witness bytes +43724 Hi-Z). At128
records the three ledgers total23168512 bytes. All allocation happens before
recording. At most three terminal BufferGetData calls read these ledgers after
the run, never per-frame or to select GPU work. No image/screenshot files.
Bound cached capture descriptor pairs to16; overflow fails the fixture.

The 512-byte proof prefix reserves ID words at0..143, fallback at144..207,
candidate at208..271, stats at272..335, header at336..399, three16-byte state
witnesses at400..447, and16 actual pre-TAA color-R float-bit samples at448..511.
The unshaded fixture emits linear R=(stable_id+1)/32, background0, occluder31/32,
with fog and other color modifiers disabled. Compare the same at-most16 raw
velocity/depth pixels within2^-12, and preserve guards in unused witness words.
Freeze these pixel maps and shader outputs before GPU execution. Publication
snapshots alone do not prove the fragment's identity. Store proof records as
uint32 words: the44236-byte stride is not a16-byte-aligned uvec4 record stride.

Caller input banks total2960 bytes: two64-byte pose records per slot,16-byte
lifecycle and16-byte active/group/generation state per slot,64-byte header,
36uint ID bank,64-byte fallback/candidate/stats buffers each. Add43724 Hi-Z bytes.
Ledger and input arithmetic is explicit in the JSON plan. These are requested
caller bytes, excluding immutable mesh/shaders/descriptors/native TAA images;
they do not measure total native memory or physical VRAM. No per-unit nodes,
materials, dynamic queues or per-frame growing banks are introduced.

Unregister/clear, remove the observer, detach bindings, then retire/free owned
resources through tracked RD lifetime rules. Capture dependencies and terminal
readback do not turn callback return or post-draw into a general GPU fence.
Require zero remaining caller resource/requested-byte ledger; native sustained
allocation/leak and physical VRAM acceptance remain separate.

## Acceptance and source seams

Completed rows require their declared native candidate/fallback selection and
draw-ID region, including accepted candidate commands containing GPU-overflow
fallback. Require conservative visibility except T020's intentional empty-command
control, matching depth/color/velocity identity, raw numerical limits, correct
history commits, TAA recording/bootstrap metadata and no diagnostics. Negative rows require the
declared quiet rejection and no commit/capture; retained valid fallback is tested
separately. The receipt must match exact editor/SDK/source/layout/input hashes,
all128 planned rows, native frame/preparation mapping and resource shutdown.
The future implementation freezes per-row active/sample expectations and payload
hashes before GPU execution; this document alone is not an executable GPU suite.
The JSON also lists admission-negative native checks for observer lifetime,
callback stages/flags, duplicates, graph mutation and unrelated motion demand;
these are separate from the128 GPU scheduling records.

Qualify only this procedural perspective/TAA/history contract. Real VAT/LOD
correspondence, resolved TAA image quality, eight LOD owners, release drivers,
FPS, full-frame cost, sustained native lifetime and physical VRAM need separate
evidence. E8 adds deformation/history work; no speedup is assumed. Resolved-image
quality belongs to the author's playtest; raw vectors cannot prove no trails.

Pinned seams: RendererViewport configure/TAA setters; Forward+ admission,
opaque clear/MRT and POST_OPAQUE/TAA sites; RenderSceneDataRD correction/UBO;
RendererSceneCull camera commit/jitter; TAA::process allocation; compositor
storage enabled-effects enumeration; E8 history contract and capture/oracle.
Git blob SHA256 values in `gate_plan.json` identify the inspected source.

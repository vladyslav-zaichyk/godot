# E13e: camera occlusion with directional shadows

Status: proposed native contract and prepared source stand, 2026-10-25. This
stage changes no native rendering code, public API or editor build. The control
is native `3365639cceb0767a8f44f908d8823edf60e14424`, exact generated SDK
`4.7.2-arena-dev-g3365639cce`. That editor still rejects the combination with
`unsupported_view` and renders the complete fallback. A reference-boundary pass
cannot qualify simultaneous shadow rendering and camera candidate selection.

## Purpose and first boundary

Select fewer crowd instances for the main camera while retaining all applicable
shadow casters. A unit hidden behind a wall or outside the camera can cast a
shadow onto visible ground. Camera occlusion never establishes light occlusion.
This is a compatibility change; it does not reduce full shadow caster work or
promise an FPS improvement.

First proposed native scope: Windows x64, Vulkan Forward+, one centered
perspective view at native resolution, opaque E8 shader-driven MultiMesh,
native TAA and the existing explicitly registered E13c numerical observer.
Permit at most one visible shadow-enabled light, an ordinary directional light
in Orthogonal mode with one shadow split. Keep unshadowed lights' existing
behavior. The controlled stand uses exactly one light, zero angular distance,
hard shadow filtering and zero pancake size. Orthogonal describes the light's
shadow map, not the main camera. See the official
[DirectionalLight3D reference](https://docs.godotengine.org/en/4.7/classes/class_directionallight3d.html).

Keep all other E13b/E13c/E13d admission, material, geometry, alias, lifetime,
history and capacity restrictions. Do not widen the compositor exception.
Exclude cascades, spot/omni/cube shadows, multiple shadow lights, XR, additional
motion-demanding views, transparency, temporal upscaling and generic screen/GI
effects. The first stand does not qualify real VAT, eight LOD owners, production
lighting, resolved TAA appearance or TAA-off positive admission.

## Ownership and two lists

The engine owns view/light matrices, native shadow atlas and attachments,
shadow scheduling, pass order and camera-local command selection. The caller
owns stable identities, active/generation metadata, current/previous poses,
animation inputs, ID banks, command buffers and conservative owner bounds.
No new identity, transform, animation or temporal bank is introduced.

The persistent complete fallback command and its complete identity prefix are
read-only throughout early depth, producer, shadow and camera passes. Every
active body remains reachable by the light pass, including bodies outside the
camera. The producer writes only its separate candidate command and candidate
ID region. Camera depth/color/velocity use the selected camera mapping; shadows
always use the persistent complete fallback mapping. An immutable command RID
alone is insufficient if the IDs it references were overwritten.

Current poses, lifecycle, generation and deformation inputs remain frozen from
publication until the whole view completes. A shadow pass evaluates CURRENT
pose. E8 previous state belongs only to the current identity's camera motion
evaluation; never index a previous compacted ID list. Commit history after a
clean completed render, including a successfully rendered fallback. Keep E13
acknowledgment, recording outcome and completed-render history commit distinct.

Inactive/dead/invalid lanes collapse all their vertices to one identical finite
world point, producing zero-area primitives in every view. Translating a full
mesh behind the main camera is not a shadow-safe inactive representation.
Reusing a stable slot resets previous identity state through generation rules.
Keep owner bounds conservative over every active current vertex, including
off-camera casters and the animation envelope; camera bounds alone are invalid.

The same effective custom shader and deformation must execute in camera and
shadow passes. Form the current deformed owner-local vertex independently of
the view, then use that pass's native model/view/projection matrices. First scope
excludes main-camera billboards, view-dependent pose, divergent TIME input,
POSITION writes and replacement of MODELVIEW_MATRIX/PROJECTION_MATRIX. Native
checks do not prove shader semantics. This remains a trusted-caller contract.

## Required native follow-up

Preserve the existing order:

```text
freeze current/previous state and complete fallback
-> selected ordinary occluder early depth
-> E13 producer; validate epoch/configuration acknowledgment
-> remaining camera depth with selected commands
-> native directional shadow recording with COMPLETE fallback commands/IDs
-> camera opaque color + E8 velocity with selected commands
-> trusted numerical POST_OPAQUE observer
-> native TAA and remaining recording
-> completed-render observation; commit rendered E8 history
```

At the audited base, `_render_list_template()` applies the command override only
to a render list carrying `gpu_occlusion_selection`. Native shadow render-list
parameters carry none. `_pre_opaque_render()` records shadows after the producer
and remaining depth. Preserve these boundaries; add no scene render, frame,
readback or fence. Keep E13d ordinary opaque batching and light-list batching
independent.

The current blanket shadow rejection must not simply be deleted. Before
admission, inspect the raw scheduled shadow work and the complete visible light
state; derived shadow lists are populated later. For the narrow accepted case,
require exactly one raw work item, matching the admitted directional light,
split/pass zero and Orthogonal mode. Even an empty directional caster list has
one scheduled item in this path. Zero work does not establish a cached
directional success. Unsupported, missing or mismatched work selects fallback.

Snapshot/revalidate the admitted light instance/base identity, shadow mode and
enablement, transform, relevant finite parameters, masks, sky mode and reverse
cull, as well as the visible shadow-light set and raw work tuple. Recheck around
producer execution and before shadow recording. `light_get_version()` alone is
insufficient: transform and some sky/color setters do not change that version.
Arbitrary callback scene mutations remain forbidden; structural checks do not
turn trusted callbacks into a sandbox.

Validation has O(visible lights) work, O(1) retained shadow snapshot and one raw
shadow item in the admitted case. The visible CPU light list is not bounded by
the GPU clustered-light packing limit. Reject on a second shadow light or more
than one work item; never silently truncate the visible-light scan. Preserve
existing epoch, callback, command-capacity and alias checks. Failed admission or
acknowledgment retains complete fallback; never use a stale candidate. A fatal
recording failure aborts the view and follows existing temporal recovery rules.
No new public API is required for the proposed first patch.

Audit source: `b3a5052febb4b338526bad096fcf05a8d423b88a`:

- `render_forward_clustered.cpp`: camera override around 821; early/remaining
  depth around 2528; `_pre_opaque_render()` call around 2655; directional shadow
  recording around 1849; shadow-list parameters around 3411; rejection around
  2442; shader-driven custom shadow material around 4742.
- `renderer_scene_cull.cpp`: caster query around 3248; directional enqueue around
  3488; positional atlas caching around 3615. Use full source paths under
  `servers/rendering/renderer_rd/forward_clustered/` and
  `servers/rendering/renderer_scene_cull.cpp`, respectively.
- `light_storage.cpp` under `servers/rendering/renderer_rd/storage_rd/`:
  transform setter around 596 and directional sky mode setter around 410.

## Small numerical stand

`gate_plan.json` freezes geometry, states, thresholds and capacity before any
GPU run. One owner, 16 logical stable slots, native draw capacity 32, procedural
boxes and a lit planar receiver, maximum 128x128. Complete IDs occupy [0,16),
candidate IDs [16,32), followed by four guard words. Full command uses
firstInstance 0 and instanceCount 16; candidate uses firstInstance 16 and its
bounded count. No growable GPU work queue or per-body resources.

The wall contributes early depth but casts no shadow. The receiver also casts
none. One camera-visible body, ID 0, is a routing witness; ID 2 is camera-hidden
by the wall; ID 7 is outside the camera with a visible ground shadow. The custom
consumer is unshaded for identity sampling and still casts native shadows. The
ground has a lit shadow-sensitive material with an independent color identity.
Inactive slots use zero-area collapse.

Ten frozen states have three arms each: crowd casting OFF counterfactual, E13
OFF with shadows reference, and E13 requested with shadows. All 30 measured
views use native TAA. One explicit I000 view initializes history before observer
registration. Camera/light/current state stay fixed per triplet, while native
jitter and actual-render history advance normally. No unrecorded per-arm TAA
reset. Decode actual current/previous UBOs for each row; do not demand identical
object velocities across sequential held-pose arms.

The producer is a controlled ID selector, not a Hi-Z algorithm. It samples actual
early depth and publishes a predetermined candidate. S02 deliberately publishes
zero candidates while ID 0 remains active: accepted selection must remove its
foreground pixels while hidden/offscreen caster shadows remain. This routing
diagnostic is intentionally nonconservative and excluded from conservative
visibility claims. S08 also publishes zero but withholds the acknowledgment;
the complete fallback must keep ID 0 visible. The frozen matrix is:

| State | Change from control geometry |
| --- | --- |
| S00 | Wall-hidden caster only, plus visible routing body |
| S01 | Off-camera caster only, plus visible routing body |
| S02 | Both casters, accepted zero-camera-candidate diagnostic |
| S03 | Camera movement |
| S04 | Directional light turn |
| S05 | ID 2 death / inactive zero-area geometry |
| S06 | ID 2 reused under generation 2 |
| S07 | Viewport resized to 96x96 |
| S08 | Zero candidate with missing acknowledgment |
| S09 | Recovery and visible body root movement |

These are fixed-box root poses, not VAT or general animated mesh deformation.
Resize uses actual native history/bootstrap behavior; it adds no hidden reset.

The independent CPU oracle chooses common world receiver witnesses from frozen
geometry and all three captured projections, with edge/interior guards before
reading lighting. Ray/box tests establish caster attribution and camera
visibility independently. Each shadow-enabled arm must show a preregistered
shadow delta against its caster-OFF counterfactual, while a lit control and
receiver identity/depth remain valid. Two dark images alone prove nothing.
Frozen thresholds include shadow delta at least 0.20, lit counterfactual at
least 0.15, retained delta at least 90%, requested/reference color error at most
0.03, depth tolerance 1e-5 and motion tolerance 2^-12. Reject a run without at
least eight nonzero expected body-motion and eight receiver camera-motion
probes; an all-zero motion ledger cannot qualify the gate.
Check actual early depth, color/depth/velocity, native UBOs and copied command/ID/
pose/lifecycle inputs. Verify guards, coherent publication, reset-on-generation,
rendered history, zero background motion and numeric command-routing witnesses.

Four fixed terminal reads total 17,957,760 requested ledger bytes; no per-frame
CPU readback or image files. Bound attachment descriptor identities to four;
overflow, incomplete rows, diagnostics or cleanup failures reject the run.
Borrowed render-data/scene-buffer wrappers remain callback-scoped. Caller RD
objects, BufferRD wrappers and owned nodes are released after disabling the
view, unregistering, draining work and dropping material references. This is a
work/capacity limit, not a frame-time or physical-VRAM guarantee.

## Gates and acceptance

Compile-only is the default. It proves C# source/API compatibility with the exact
editor's SDK and leaves GPU status pending. CPU synthetic/adversarial checks
prove oracle discrimination only. Neither compiles GLSL through the driver nor
qualifies real shadow rendering. The existing native336 reference must report
fallback/`unsupported_view`; it cannot pass the combined-required gate.

After the integrator releases the GPU slot: run the reference-boundary gate on
the preserved control; implement the narrow native admission patch in isolation;
build it separately; run combined-required and existing E13b/E8/E13c/E13d gates
on that exact editor/SDK. Extend native negative coverage for multiple lights,
cascades, positional shadows, missing/mismatched raw work and forbidden producer
mutation, including fallback restoration. Those future native negatives are not
claimed by this source-only stand. Resolved TAA appearance, real animation,
multiple LOD owners and shadow quality each require later integration evidence.

Only then compare full frames with identical lighting/casters: E13 OFF, E13 ON
retaining all camera IDs, and E13 ON with conservative camera selection. Preserve
scene, grouping, build settings and shadow quality. Measure complete frame,
GPU preparation and memory separately. No performance acceptance follows from
these 16-slot correctness checks.

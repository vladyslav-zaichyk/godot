# E13d: preserve ordinary opaque instancing

Status: native implementation and separate combined editor preparation,
2026-10-25. BufferRD's explicit tutorial-tag repair is carried in the same
editor; there is no documentation-only editor. GPU qualification is deferred
while the integration owner experiments with Hi-Z. Existing editor/SDK bundles
remain immutable controls.

## Problem and invariant

The first E13 implementation changes every opaque repeat suffix to one when
the current-frame depth split is admitted. That disables ordinary automatic
instancing in both depth and color. Restoring the original suffix without
phase-aware traversal would let one instanced draw include surfaces belonging
to the other depth phase.

Preserve sorted elements, canonical RenderElementInfo, uploaded InstanceData,
and original native base indices. Color retains the original repeat groups.
Early and remaining depth use a separate suffix table, split only at an
original group boundary or an early/remaining membership change. A render
subrange bounds the effective repeat to its own end. All instanced counts,
rejection skips, phase skips and successful iteration use that same effective
depth suffix. Feature-off and unrelated passes retain their existing behavior.

The table is built once in O(number of opaque surfaces), not per crowd unit.
It uses one reused renderer-owned CPU allocation: four bytes per active opaque
surface plus LocalVector capacity slack. The native renderer owns its lifetime.
There are no new GPU banks, public methods, Hi-Z algorithms, consumer limits,
material permissions, shadow admissions or temporal modes.

## Diagnostics and accounting

On an admitted split, auto_instancing_disabled is false. Additional outcome
fields are original_opaque_repeat_group_count, early_depth_repeat_group_count
and remaining_depth_repeat_group_count. These are CPU-planned group counts,
not proof that a driver submitted or completed those draws. Rejected producer
acknowledgment still restores these witnesses for the admitted split.

Viewport RenderInfo retains stock per-list accounting from instance-data
construction. It is not promoted to a count of physical RD calls across both
depth phases and all later passes. GPU/driver instrumentation is needed for
an actual submission-count performance claim.

## Verification

CPU contracts enumerate compatibility partitions, phase masks, interior starts,
subrange ends and nonzero base offsets using an independent exact-coverage
oracle. Reusing the scratch allocation and the native repeat limit are covered.
The exact native helper and test source must compile; a Python mirror alone
does not qualify the implementation.

The separate ordinary-mesh numerical stand uses one supported TAA viewport,
six distinct transformed owners sharing geometry/material and no registered
crowd consumer. It records early depth in the producer, then pre-TAA identity,
depth, velocity and actual current/previous scene UBOs in the trusted observer.
This catches phase leakage that a final-color-only comparison would miss.
Mixed cases do not assume stable sorting of equal keys. All-early and
all-remaining cases must preserve a single original color group.

On the new editor, integration also repeats the existing orthographic,
perspective, E8 and combined E13c real-VAT regressions. The ordinary-only stand
does not qualify E8 histories, shader-driven consumer composition or GPU timing.
Correctness readback and qualification capture are excluded from timing runs.

## Performance experiment and limits

Use fixed repeated environment geometry and unchanged crowd grouping, camera,
materials and build flags. Compare E13 OFF, prior E13 ON/pass-all and E13d
ON/pass-all, then independently consider real culling. Report render-thread
CPU, full-frame tails and inclusive GPU preparation-through-completion.
Homogeneous groups should batch; alternating phases can retain many depth runs.
Neither planned groups nor increased culling alone is an FPS result.

The recent integration A/B admitted only eight early surfaces. This patch does
not promise to remove its measured overhead or speed up the eight distinct
MultiMesh LOD owners, which are not ordinary repeat-group candidates.

## Editor documentation scope

BufferRD uses explicit opening/closing tutorials tags to accommodate the
existing DocTools parser. Check a clean authored-resource import using the
new combined editor. The separately reproduced documentation-dump singleton
failure/access violation is not claimed fixed by this XML correction.

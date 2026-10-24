# GPU draw-count: source audit

Research date: 2026-10-24. Local source:
`bf946e0472a510694f355340f0df30a43e3106fa`; E9 binary source:
`91592ae7841f587b02a0309f2f7b82bed430892f`.
This is source/API research; no build or GPU invocation occurred.

## Existing upstream request

[Godot proposal #13652](https://github.com/godotengine/godot-proposals/issues/13652)
requests a GPU counter-buffer RD draw API and support query. It was opened
2025-11-17 and was open with zero comments and no linked implementation when
checked. Its GPU-generated-curve workload demonstrates demand beyond crowd
rendering. Exact issue/PR/commit searches found no ready implementation; this
is a search result, not proof that no unpublished implementation exists.

Reviewed official master
[`c8256a8ecec068340753869e423dd478c84ef167`](https://github.com/godotengine/godot/commit/c8256a8ecec068340753869e423dd478c84ef167),
dated 2026-10-23, still has driver wrappers but no count path in RD or its
graph. Ordinary RD indirect drawing
[#97247](https://github.com/godotengine/godot/pull/97247) and native MultiMesh
indirect drawing [#99455](https://github.com/godotengine/godot/pull/99455) are
distinct earlier features. No upstream backport was identified for this gap.

## Local source seams

Line numbers describe the pinned research revision.

| Location | Finding / required change |
| --- | --- |
| `drivers/vulkan/rendering_device_driver_vulkan.cpp:5749,5762` | Indexed/nonindexed count wrappers already call Vulkan; no new draw primitive is needed. |
| Same file `:565-588,904,920,1332-1493` | Count extension/core feature is not enabled. Optional KHR registration, entrypoint availability and truthful query are required. |
| `drivers/d3d12/rendering_device_driver_d3d12.cpp:4903,4929` | Existing ExecuteIndirect counter argument; packed signature stride differs from Vulkan. Preserve it; no D3D12 qualification in this proposal. |
| `drivers/metal/metal3_objects.cpp:1295,1316` | Count methods are unimplemented, so no support may be advertised. |
| `servers/rendering/rendering_device_driver.h:699,701` | Count primitive interface exists. Capability/limit plumbing is missing. |
| `servers/rendering/rendering_device.h:1560` | Public indirect draw takes CPU-known count; add a separate checked count-buffer API. |
| `servers/rendering/rendering_device.cpp:6169-6285` | Reuse validation/binding ordering, but add counter validation and track/check both inputs. Old count-one stride semantics differ. |
| `servers/rendering/rendering_device_graph.h:621-635` | Add two count instruction records; current records contain only fixed count. |
| Graph cpp `:975-982,1568-1576,2227-2247` | Add execution, diagnostic decoding and append operations. |
| Graph cpp `:2300-2314` | Same-usage tracker deduplication exists; mixed read usages are not combined. Explicit alias preflight is a correctness prerequisite. |
| `servers/rendering/renderer_rd/forward_clustered/render_forward_clustered.cpp:649-658` | E9 MULTI_DRAW already submits once per compatible owner. Native counted routing does not further reduce that CPU call count. |
| `servers/rendering/shared_draw_command_span.h` | Reuse bounded numeric design, not its ordinary count-one stride exception or ordinary-MDI capability definition. |

The feature is a medium cross-layer change: existing driver operations remove
one part of the work, but capability enablement, render-graph synchronization,
numeric validation, native borrowed state and a matching managed consumer all
remain. A driver-only wrapper exposure is not a safe patch. Timing estimates
before a prototype would be speculative.

The principal design uncertainty is complete read-alias handling. A local
new-API check cannot catch a later ordinary draw using the same bank as shader
input. The first stage must resolve bidirectional rejection or combined-read
graph support. Independent review also required explicit uint32 width checks
before arithmetic: maximum one does not otherwise expose an oversized stride.

## Actual consumer observation

The parallel renderer's time-bound R14 policy currently describes eight LOD
owners, 32 spatial segments per owner and 256 sparse commands. Its shared
histogram/prefix/scatter/finalize producer also supplies one aggregate command
per LOD for a `collapsed` control. The prefix records global active groups,
not the per-owner counts required here. Scatter reads the original command
slots. These are read-only observations of another agent's workspace, not a
frozen source/performance qualification.

A count buffer keeps eight owners/eight native submissions in that example.
It can reduce command traversal but adds compaction and publication. Therefore
R14's aggregate control must remain in an adoption comparison. A genuinely
sparse packed-geometry workload with many independent ranges is a stronger
candidate than interchangeable segments of one geometry. Different materials,
pipelines or native lighting owners remain separate.

## Recommendation and boundaries

Complete the [contract](README.md), then consider E10a and E10b as separate
patches. Preserve every existing candidate as an immutable control. First
prototype a legal count path and numerical consumer; performance adoption is
conditional on active/max occupancy and end-to-end comparison with the best
applicable producer. No shared simulation, combat, networking or full renderer
rewrite is needed for this API investigation.

Two other engine candidates remain conditional: original E6 external geometry
attachment, and previous geometry for compute-mutated meshes. E6a already
allows compute to write native-owned streams. The
[E8 contract](../instance_indirection_lab/GPU_HISTORY_CONTRACT.md) explains
why retaining previous mutated geometry is a separate design. Neither is a
prerequisite for count-buffer drawing.

Retained local research evidence belongs under
`D:/Projects/godot-arena-dev/e10-indirect-count`. Workers created no files,
worktrees, helpers or GPU processes. The author and renderer workspaces and
all six previous bundle/archive identities were read only.

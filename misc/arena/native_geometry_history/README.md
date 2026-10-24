# Compute animation history options

Status: research and proposed contract, 2026-10-24. Source audit:
`43904a9c33f45378dbdb62e0158fa82fb30505a4`, Arena Godot 4.7.2.
No native implementation, replacement build or GPU qualification accompanies
this contract. The existing E10b editor and E10 hardening stand remain separate.

Use BufferRD and E8 for a shared animation cache with independent unit histories.
Consider a new native previous-geometry binding only for a consumer that writes
native mesh vertices through E6a and needs native vertex-fetch motion vectors.
The proposed binding in [CONTRACT.md](CONTRACT.md) supplies one old shape per
surface. It does not allocate an animation cache or replace per-unit history.

## What the existing APIs can express

| Consumer | Existing route | Native change needed |
| --- | --- | --- |
| Shared local-space pose pages with different poses per unit | E5 resolves current draw slot to stable identity; BufferRD supplies pose pages; E8 selects that identity's current or previous page | No new API to express it; correctness and benefit still need consumer qualification |
| Compute input from a mesh's original positions or skin data | E6a getters expose engine-owned base streams created with storage usage | No new API; getters do not expose per-instance skinned outputs |
| A retained GPU copy of a native stream | RD `buffer_copy` from the native vertex RID to a suitably sized storage buffer | No CPU readback or new copy API |
| Current native geometry with a shader override for previous geometry | E6a current stream plus copied BufferRD history and E8 previous evaluation | Source-feasible without a native patch, but outside E8's current immutable-base contract and not yet qualified |
| Previous native vertex attributes for compute-overwritten base geometry | Explicit borrowed previous stream, cache invalidation and dynamic classification | Proposed native patch in CONTRACT.md |

BufferRD currently validates `storage_buffer_owner`, whereas E6a returns a
`vertex_buffer_owner` RID even when that vertex buffer has storage usage.
Wrapping the E6a RID directly is therefore not the existing route. RD copy
accepts both buffer owners; a separate storage snapshot can be wrapped.
This adds a copy and storage unless compute already produces the pose cache.

For the recommended immutable-base route, both vertex evaluations first resolve
the **current** visible slot to the same stable `(slot, generation)` identity.
That identity selects its own current or previous pose reference and root state.
Page keys include mesh and asset revision, topology/LOD, animation sample and
blend parameters. Different behavior changes which key a unit chooses; it does
not require a separate native mesh per unit.

Two units can share current page A while their previous pages are B and C.
One native surface binding can supply A/B or A/C, but cannot select both previous
shapes. Use per-ID previous-page indirection, or explicitly split groups by
the current/previous page pair. A double buffer of the entire pose cache alone
does not fix changing page IDs.

## Consumer history and memory

Retain the last acknowledged rendered sample, including interpolation, rather
than the previous simulation tick. Validate page generation and the asset, LOD,
schema and sample key separately from each per-ID snapshot's expected history
domain, continuity token and render epoch. An immutable page can be reused
across epochs and by both current and previous snapshots without changing its
identity.
Current-reference failure suppresses the affected work with a bounded diagnostic;
missing previous history evaluates the current complete pose in both contexts.
The [E8 history contract](../instance_indirection_lab/GPU_HISTORY_CONTRACT.md)
defines native camera/owner transforms, continuity resets and scheduling.

Page lifetime includes current references, previous references and submitted
in-flight readers. Capacity is fixed. On exhaustion, reuse an already valid
coarser/static representation with an explicit history reset, omit permitted
cosmetic work, or reject the run. Never overwrite a pinned page. A fallback's
cost and count belong in the result; an overflowing cache is not a successful
comparison. Gameplay state and stable identity remain authoritative outside it.

Geometry storage is proportional to the live unique pages, not automatically
to the unit count:

```text
sum over live pages p of align16(vertex_count[p] * bytes_per_cached_vertex[p])
+ page metadata + per-ID current/previous references + per-ID root snapshots
```

For an illustrative 256 pages of 1024 vertices, a float4 position cache uses
4 MiB per bank, or 8 MiB for two disjoint banks before metadata and retirement.
Position/normal/tangent float4 records use 24 MiB for those two banks. This
example describes a consumer layout, not Godot's native vertex packing.
Do not omit per-ID state, retired pages or in-flight capacity from accounting.
Different current/previous pages may overlap and share immutable data.

A cache may save repeated deformation across units and passes when many units
reuse poses. Unique poses or cheap VAT sampling may make its extra compute,
copy, fetch and history bandwidth slower. Compare the best existing animation
LOD/VAT route before treating the cache as an optimization.

## Native source findings

The pinned fork contains these relevant seams:

- `mesh_storage.h`, `MeshInstance::Surface`: two native deformation outputs
  with current/previous indices and `last_change` for skeleton/blend shapes.
- `mesh_storage.cpp`, `_mesh_surface_generate_version_for_input_mask`: base
  surfaces repeat their current buffer for previous inputs; MeshInstance
  deformation selects its separate previous output. E6a getters return the base.
- `rendering_device.cpp`, `buffer_copy`, `storage_buffer_is_valid`,
  `vertex_buffer_create`, `vertex_array_create`: copies accept native vertex
  owners, BufferRD's validator accepts storage owners, and vertex arrays require
  vertex owners. A new vertex binding needs a matching checked validator.
- `render_forward_clustered.cpp`, `_fill_render_list`: dynamic classification
  recognizes owner movement, native deformation, particles, native MultiMesh
  motion and E8. Raw compute writes into a base buffer do not set that state.
- `scene_forward_clustered.glsl`: previous position/normal/tangent inputs feed
  previous vertex evaluation. Compressed current and previous positions use the
  same decode basis. UV/custom/index history is not a separate native stream.

Versioned source: [mesh storage](https://github.com/vladyslav-zaichyk/godot/blob/43904a9c33f45378dbdb62e0158fa82fb30505a4/servers/rendering/renderer_rd/storage_rd/mesh_storage.cpp),
[RD buffers](https://github.com/vladyslav-zaichyk/godot/blob/43904a9c33f45378dbdb62e0158fa82fb30505a4/servers/rendering/rendering_device.cpp),
[Forward+ routing](https://github.com/vladyslav-zaichyk/godot/blob/43904a9c33f45378dbdb62e0158fa82fb30505a4/servers/rendering/renderer_rd/forward_clustered/render_forward_clustered.cpp).

## Related upstream work

Checked 2026-10-24. These sources provide foundations and related requests;
the inspected APIs do not expose the proposed previous native stream binding.

| Primary source | Checked state | Relevance |
| --- | --- | --- |
| [PR 80618](https://github.com/godotengine/godot/pull/80618) | Merged 2023-08-17 | Internal double-buffered skeletal/blend-shape geometry history |
| [PR 118973](https://github.com/godotengine/godot/pull/118973) | Merged 2026-06-19 | E6a base mesh RD getters and storage opt-in |
| [PR 82733](https://github.com/godotengine/godot/pull/82733) and [proposal 7982](https://github.com/godotengine/godot-proposals/issues/7982) | Open | Shader previous-evaluation context; our E8 addresses the corresponding shader-driven case |
| [PR 117836](https://github.com/godotengine/godot/pull/117836) | Open | Caller-owned MeshRD proposal; inspected API has no previous-stream attachment |

The official [CompositorEffect reference](https://docs.godotengine.org/en/latest/classes/class_compositoreffect.html)
exposes motion-vector demand/readback access and places PRE_OPAQUE after the
depth prepass. Geometry producers must run before the earliest geometry consumer;
that callback alone cannot supply coherent compute animation for the whole frame.

## Adoption order

First identify a concrete native consumer: stable topology, one previous shape
per surface, and a reason to keep native vertex fetch instead of BufferRD pose
selection. Compare the existing shader route and GPU-copy workaround on equal
geometry, grouping, build settings and material work. Implement the narrow native
contract only when that consumer needs it. Qualification then proceeds through
checked API tests, a separate small numerical stand and an immutable candidate
build; GPU execution waits for the parallel renderer A/B slot.

The immediate outcome is a defined choice between these routes. No FPS,
physical VRAM, million-unit capacity or temporal-image-quality gain is implied
by exposing the binding.

# E10b MultiMesh GPU count contract

Status: proposed implementation contract, based on native source
`82e80df4074789810a169959f6fe0ce8874ffc76`. E10a supplies the counted RD draw;
E10b will connect it to a shader-driven MultiMesh. This stage changes
documentation only. The E10a candidate remains an immutable control while
its renderer owner tests it separately.

## Scope and public API

The first candidate targets Windows x64, Vulkan Forward+, 3D, one main-view
timeline and opaque indexed geometry. Each owner retains one immutable triangle
surface, effective material, bindings, transform, lighting state and conservative
finite AABB. Preserve E5 restrictions on native LODs, shadow meshes, blend shapes,
interpolation, colors/custom data and indirect first-instance support. Different
materials or geometry owners remain separate spans. Compatible geometry ranges
may occupy one indexed surface, as in E9.

```cpp
Error multimesh_attach_shared_draw_command_span_count(
    RID multimesh, int64_t draw_slot_capacity, RID commands,
    int64_t command_byte_offset, RID count_buffer,
    int64_t count_byte_offset, int64_t maximum_count,
    int64_t command_byte_stride,
    bool use_shader_motion_vectors = false);

RID multimesh_get_shared_draw_count_buffer_rd_rid(RID multimesh) const;
int multimesh_get_shared_draw_count_byte_offset(RID multimesh) const;
```

Append `MULTIMESH_INDIRECT_SUBMISSION_MULTI_DRAW_COUNT = 2`, preserving LOOP=0
and MULTI_DRAW=1. Only the new method creates counted views; the old span method
continues rejecting this mode. Existing public signatures and enums retain their
values. New numeric inputs bind as signed64, including managed `long` arguments.
RenderingServer keeps its existing synchronous render-thread marshaling.

Three quantities have different meanings:

| Quantity | Meaning |
| --- | --- |
| Draw-slot capacity | Highest addressable instance slot plus one; returned by the existing instance-count query. |
| Maximum command count | CPU-known span capacity; returned by `multimesh_get_shared_draw_command_count` in counted mode. |
| Published count | One GPU-written uint32 selecting a dense command prefix; never returned through CPU metadata queries. |

The two new queries return invalid RID/0 for invalid, ordinary, detached or
non-counted views. Query results neither retain storage nor validate GPU contents.
Runtime count zero leaves the view attached with positive capacities.

## Attachment validation and state changes

Check every condition before clearing uniform sets, changing fields or notifying
dependencies. Numeric attachment limits are capacity `1..INT32_MAX`, offsets
`0..INT32_MAX`, stride `20..INT32_MAX` and maximum `1..1024`. Offsets and stride
align to four; stride remains mandatory at maximum one. Retain original signed64
values until these independent bounds pass. Then calculate with uint64:

```cpp
command_end = offset + (maximum - 1) * stride + 20;
counter_end = count_offset + 4;
```

Both ends must fit the actual buffers and RD uint32 byte-size domain. Validate
the full maximum interval even when the future GPU count is zero. Commands and
counter must be live global-RD storage RIDs with INDIRECT usage and must not use
dynamic persistent storage. A shared RID is legal only if its four counter bytes
are outside the entire command interval, including stride padding.

Require E10a's enabled count capability and count-specific physical limit, also
at maximum one: `maximum_count <= enabled_count_limit`. Preserve E5's independent
first-instance capability guard.
Ordinary MULTI_DRAW limits are not a substitute. Expected invalid metadata or
banks return `ERR_INVALID_PARAMETER`; unsupported backend/capability/history
returns `ERR_UNAVAILABLE`; conflicting existing allocation/interpolation follows
E9's `ERR_ALREADY_IN_USE`. Expected attachment rejection is quiet. No precedence
is promised when several preconditions fail together.

Introduce a list-independent, unbound C++ RD validator shared by attachment and
the E10a draw entry point. It checks capability, bank metadata, persistence,
usage and ranges without reading bytes or recording commands. Keep the existing
`storage_buffer_is_valid` behavior unchanged.

After validation, commit the complete view before one dependency notification.
Failed rebinds preserve all old RIDs, capacity, offsets, maximum, stride, mode,
history flag and caches. Every successful old attachment clears count RID/offset
and retains its old count/stride/mode semantics. Detach clears all counted
metadata and leaves an empty ordinary MultiMesh, preserving mesh and custom AABB.
Ordinary allocation resets the fields after the existing detach requirement;
attempting CPU mutation while attached remains forbidden. Destruction detaches.
Clearing view metadata never resets or frees the GPU counter.

## GPU production and storage lifetime

The producer publishes a dense prefix of complete indexed commands, then its
count in `0..maximum`. It owns legal index/vertex ranges, stable-ID coverage and
overflow-safe `firstInstance + instanceCount <= draw_slot_capacity`. Capacity
covers addressed slots rather than a sum of instance counts. Keep INSTANCE_ID's
existing firstInstance-inclusive semantics, with no second base offset.

Metadata validation cannot prove command contents. Use bounded reservations
before writes and a separate overflow diagnostic. On producer overflow, publish
count zero and reject the run; never silently drop authoritative units. Keep
reservation/diagnostic counters separate from the published count. Legal GPU
clamping is not a producer overflow policy: the raw count itself also has a
physical limit. See the [Vulkan counted draw rules](https://docs.vulkan.org/refpages/latest/refpages/source/vkCmdDrawIndexedIndirectCount.html).

Order reset, production/scatter, compaction, final publication and consumption
through tracked queue dependencies. A single invocation cannot globally reset
an append counter while other workgroups append in the same dispatch. Publish
one coherent snapshot before the earliest permitted pass and preserve it through
the last use for that view. R14 integration needs per-owner counters with
INDIRECT usage and compaction after scatter, or a separate immutable sparse
source: current scatter reads the original command slots.

The view borrows both banks. E10b allocates no counter, identity transform bank,
native history or CPU count readback. Caller retains buffers, mesh and BufferRD
dependencies until every borrower is detached and queued consumption retires
under the global RD lifetime rules. A callback or fixed frame delay alone is not
a GPU fence. Detaching/destroying one borrower cannot release another's storage.
Requested buffer bytes, caller accounting and physical VRAM are separate metrics.

## Checked renderer bindings and failure behavior

Replace Forward+'s LOOP/else dispatch with explicit LOOP, MULTI_DRAW and
MULTI_DRAW_COUNT branches. Counted mode submits indexed E10a RD draws using the
stored maximum/stride and counter RID/offset. Count zero is still a recorded draw
with resource dependencies. Any counted submission Error skips that submission
and produces a bounded diagnostic; there is no fixed-count or LOOP fallback.

E10a rejects counted banks as shader, vertex or index inputs anywhere in the
same draw list, including other owners, later draws, padding and subpasses.
Commands and counter may share a RID with each other; identities, poses and
geometry must use other RIDs. Attachment cannot prove future material bindings.

**Checked native bindings are an implementation prerequisite.** Existing RD
public void binds can reject an alias while Forward+ still updates its cached
binding and later renders with stale descriptors. Add internal checked binding
operations for uniform sets, vertex and index arrays, sharing validation and
binding with the public entry points. Preserve public signatures; a separate
preflight followed by an unchecked void call is insufficient.

Expected alias rejection must return a quiet Error from these checked bindings
and the counted draw's pending-shader checks before existing ERR_FAIL diagnostics.
Forward+ owns the bounded diagnostic for that failure. Preserve unexpected
pipeline-state and render-thread diagnostics; do not globally suppress logging.

Forward+ must check global scene/pass/default-transform bindings and all later
surface bindings, including ordinary meshes and particles after a counted owner.
Only publish a cache entry after successful binding. On a surface failure, skip
its draw and invalidate caches as needed, including material, transforms,
vertex/index, `prev_shader` and pipeline hash; shader/hash are currently updated
before geometry binding. The next valid owner must rebind correctly. Preserve
repeat-run advancement when skipping ordinary instances. A global binding failure
aborts the current batch. Keep diagnostics bounded through existing owner/batch
state, with no unbounded queue or per-frame spam. Unexpected skips fail acceptance;
intentional rejection probes require exact expected diagnostics and a subsequent
valid-owner raster oracle.

## Pass routing and temporal history

Allow counted owners in the main single-view opaque color and its depth variants.
Use an explicit internal render-purpose allowance, disabled by default, rather
than inferring main view from pass mode: reflection probes reuse color/depth and
particle heightfields reuse shadow mode. Secondary probes, shadow/collider passes,
transparency, XR and capture/bake/SDF routes skip counted owners with a bounded
diagnostic in this first scope. Ordinary and existing non-counted views retain
their routing. The first stand disables those consumers.

Derive the allowance from explicit main-render context and single-view state;
forward it at the main depth/opaque calls and admitted E8 motion call. Alpha and
all secondary render-list constructors retain the disabled default.

The history flag stays independent and defaults false. Preserve E8's existing
attachment/material guards, per-view motion demand, dynamic classification and
main-view motion-list routing. Main-view motion color-list variants are allowed
only under the E8 opt-in; additional pass combinations stay outside qualification.
With the flag true, current slots still resolve
to the same stable identity for current and previous evaluation after compaction
or reordering. E8/count combinations require separate temporal gates before
qualification; counted mode never silently enables history or native geometry
history. Mesh geometry stays immutable throughout attachment.

## Verification and acceptance

| Gate | Required evidence |
| --- | --- |
| CPU and bindings | Signed limits independently; max 0/1/1024/1025; strides 20/32 including max1; exact final command/counter bytes; prefix/suffix/overlap/padding; independent capabilities; old API rejects new enum; signed64/default/const query bindings. |
| Native numerical stand after A/B | Two native owners; max1/4; separate/shared banks; first/middle/last source groups; K to 0 to 1; reorder; sparse nonzero firstInstance; packed geometry ranges; legal nonempty excluded tails; exact IDs, five command words, counter bytes and raster. |
| Live state and ownership after A/B | Failed rebind preserves complete view; every old attachment resets count metadata; wrong-device/usage and dynamic persistent rejection; detach/destruction and two shared borrowers. CPU layout tests alone cannot qualify these live operations. |
| Renderer failure after A/B | Alias before/after counted use, including count0, another ordinary owner/particle, subpasses and repeated render-list calls; same-pipeline subsequent valid owner; pipeline/cache recovery; excluded routes and material replacements. No stale or unintended raster is allowed. |
| Separate qualification | E8/reordering/motion demand, additional passes, sustained resource plateau, physical-memory measurements and performance. No E10a/E9 result is inherited. |

The stand defaults to exact-SDK compilation only. GPU execution waits for the
other agent's A/B slot and requires the direct E10a count gate as a prerequisite.
Prepare a new immutable editor/SDK/stand package for implementation; preserve
all seven existing candidates and their manifests/archives.

First compare fixed-dense E9 with counted-dense using identical production,
publication, zero tail, owners, IDs, geometry and passes. Separately compare
end-to-end against the best applicable sparse E9 producer and R14 collapsed
control, including compaction/publication cost. Isolate omitted tail clearing as
another experiment. Sweep maximum 32/256/1024 and empty/one/1-in-16/quarter/full
occupancy. Measure full-frame distributions/tails, total GPU/preparation time and
requested/physical memory. Counted submission keeps the same native owner/call
count as E9 MULTI_DRAW. Accept repeatable end-to-end speedup or substantial memory
savings without frame, correctness or lifetime regression.

## Implementation seams at the pinned source

| Source | Required change |
| --- | --- |
| `rendering_server{,_default,_enums}` and `storage/mesh_storage` | New attach, two queries, appended enum, unavailable defaults and marshaling. Nine arguments need explicit forwarding: `server_wrap_mt_common.h:663` ends at FUNC8R. |
| `rendering_device.cpp:1448,6231` | Shared list-independent bank validator; old storage validation lacks persistence rejection. |
| `renderer_rd/storage_rd/mesh_storage.h:255` and cpp `:1600,1659,1724,1769,1808` | Borrowed count fields, validate then commit/notify, every reset/rebind path. |
| `renderer_rd/forward_clustered/render_forward_clustered.cpp:314,562,582,599,606,649` | Checked binding/cache recovery and explicit counted submission. |
| Same renderer cpp `:2195,2278,2304,2475,2899,2994,3046,3172` and header `:222` | Main-purpose allowance, depth/color/motion routing and explicit secondary exclusions. |
| `shared_draw_command_span.h` and existing native/C# labs | Reuse E9/E10a numeric rules while preserving old API behavior; add native counted and recovery gates. |

Paths in the last table are relative to `servers/rendering/`, except
`servers/server_wrap_mt_common.h`. Line numbers describe the pinned native base;
implementation will have its own source, SDK and qualification identities.

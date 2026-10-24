# E10 research: GPU-sourced indirect draw count

Status: **proposed contract only**. No native API, editor, SDK or GPU result is
delivered here. Research source is `bf946e0472a510694f355340f0df30a43e3106fa`,
including the E9 native implementation `91592ae7841f587b02a0309f2f7b82bed430892f`.
The renderer owner continues testing its immutable E9 bundle independently.
See [source audit and decision](AUDIT.md).

## Decision

Investigate a counted submission path for genuinely sparse compatible command
spans. E9 already records one RD submission per owner in MULTI_DRAW mode. This
extension can reduce GPU command records consumed; it does not merge materials,
remove native owners, reduce geometry work, or guarantee a shorter CPU frame.
Command compaction/publication adds work. An aggregate command is preferable
when the existing geometry/ID arrangement permits it cheaply.

Recommend two independently reviewable implementation stages:

1. **E10a: RD capability and count submission.** Enable the optional Vulkan
   device extension, expose truthful feature/limit queries, add render-graph
   instructions/dependencies and checked public RD calls. Prove a small RD
   consumer before connecting native MultiMesh.
2. **E10b: native MultiMesh consumer.** Add borrowed counter storage to the E9
   view, transactional attachment/reset/query behavior, Forward+ routing and
   a procedural source consumer. Production integration remains separate.

Neither stage is implemented in this research revision; no new build is
produced. Adoption requires applicable R14/future-workload measurements.

## Proposed API and scope

Working names; all numeric inputs below are signed 64-bit until checked:

```cpp
Error RenderingDevice::draw_list_draw_indirect_count(
    DrawListID list, bool use_indices, RID commands, int64_t command_offset,
    RID count_buffer, int64_t count_offset, int64_t maximum_count,
    int64_t stride);

Error RenderingServer::multimesh_attach_shared_draw_command_span_count(
    RID multimesh, int64_t draw_slot_capacity, RID commands,
    int64_t command_offset, RID count_buffer, int64_t count_offset,
    int64_t maximum_count, int64_t stride,
    bool use_shader_motion_vectors = false);
```

Preserve the old RD uint32 signature, E9 API and existing enum values. Append
a distinct counted submission enum value; old span attachment must reject it
because that API supplies no counter. Existing command-count queries return
the configured maximum in counted mode, never a GPU readback. Add count RID
and byte-offset queries. Every successful old attachment/reset clears counted
metadata; failed replacement preserves the complete previous view.

E10a covers indexed and nonindexed RD calls on Vulkan. E10b initially targets
Windows x64, Vulkan/Forward+, 3D, E5 draw-only ownership, one compatible immutable
indexed surface and opaque state. Retain E9 topology, finite bounds and shader
restrictions. E8 remains an independent opt-in whose combinations need their
own gates; shadows and other passes do not inherit qualification.

## Device capability

The existing Vulkan driver wrappers are insufficient: this revision does not
request the count extension or enable the corresponding core feature.
For the first patch, request optional `VK_KHR_draw_indirect_count` and use its
KHR entrypoints only after extension enablement and function-pointer checks.
Unsupported hardware must still initialize normally and report unavailable.

Append a public support query and count-specific maximum query. Keep these
separate from ordinary `multiDrawIndirect` and from `drawIndirectFirstInstance`.
The count operation has its own capability contract; preserve E5's separate
first-instance requirement. D3D12/Metal report unsupported in this first scope.
A core-only Vulkan 1.2 route is a later option: inserting aggregated Vulkan12
features beside existing promoted feature structures is invalid. See the
[Vulkan guide](https://docs.vulkan.org/guide/latest/extensions/VK_KHR_draw_indirect_count.html)
and [device-create rules](https://docs.vulkan.org/refpages/latest/refpages/source/VkDeviceCreateInfo.html).

## Storage, validation and synchronization

Both inputs must be live same-device storage-buffer RIDs with INDIRECT usage.
Use the existing storage-buffer indirect creation flag. A counter is one
uint32; offsets align to four. Validate its complete four-byte range and the
entire maximum command span with wide arithmetic before descriptor, tracker,
graph, counter or attachment mutation.

Reject negative values and independently bound RD command offset, counter
offset, stride and maximum to uint32 before multiplication or narrowing.
In particular, an oversized stride must fail even when maximum is one.
RD maximum is positive and respects the enabled count limit. MultiMesh
also bounds maximum to 1024, with its existing int32 field/offset limits.
Indexed stride is at least 20 bytes; nonindexed stride is at least 16; both
align to four **even when maximum is one**. Do not blindly reuse E9a's
ordinary count-one/ignored-stride rule. For command size S validate
`offset + (maximum - 1) * stride + S <= buffer_size`, including when the
GPU's runtime count is zero.

Execution uses `min(raw_count, maximum)`. The raw value must also satisfy the
physical device count limit; clamping does not legalize arbitrary uint32
overflow. Our producer contract is stronger: publish `0..maximum`, set a
bounded overflow diagnostic and fail closed on overflow. Never use an illegal
raw UINT_MAX as GPU poison. These requirements follow the
[indexed count command](https://docs.vulkan.org/refpages/latest/refpages/source/vkCmdDrawIndexedIndirectCount.html).

The same RID may supply commands and counter if the counter's four-byte range
does not overlap the full maximum command interval. A prefix counter before
the command bank is a useful layout. Deduplicate the same tracker; register
both distinct buffers as INDIRECT_BUFFER_READ, include DRAW_INDIRECT stage
and service transfer-worker readiness for each. Runtime count zero still
records the draw and its dependencies. No CPU count readback or implicit LOOP
fallback is permitted.

The current graph cannot combine arbitrary shader/vertex/index reads with
indirect reads of one tracker inside a draw list. V1 must preflight and reject
these aliases before mutation, covering already-recorded uses, pending bound
resources and subsequent bindings/draws in the same list. Checking only the
new entrypoint is insufficient: counted draw followed by an ordinary shader
draw of the same bank must also be handled. This needs bidirectional release
preflight/reservation on affected recording paths, or combined-read graph
support as a prerequisite. Resolve and test that choice before implementing
the counted path. Do not rely on DEV-only conflict messages.

Caller owns both buffers. Borrowers detach and pending GPU consumption retires
before caller free/reuse. One borrower cannot free another's storage. Use an
explicit reset -> producer -> publication -> consumption dependency chain.
Resetting a counter in one invocation while other workgroups increment it in
the same dispatch is not a valid global reset barrier.

## Producer and numerical gates

Compact complete command records into a dense prefix after ID scatter, or
keep an immutable sparse command source for scatter. The current R14 scatter
reads original command offsets; early in-place compaction would corrupt it.
Publish counts per owner, not only the existing global active-group diagnostic.
Keep legal geometry/instance ranges in every potentially consumed command.

Required gates include counts 0/1/maximum, maximum one, strides 20/32, four-byte
counter offsets, separate/shared banks, active first/middle/last groups,
K->0->1 transitions and changing order. Prove exact IDs, all five indexed
command words, geometry selection, firstInstance and numerical raster output.
For count zero, a legal nonempty tail must remain invisible. Check full-range
rejection, wrong usage/device, signed overflow, overlap, unavailable support,
alias preflight, transactional replacement and old-API reset. Do not issue
invalid Vulkan commands to test validation. Legal max+1 raw-count clamping is
only a separate RD probe when within the physical limit; producer overflow
must still reject its run. Exercise shared consumers, retirement and a
sustained resource plateau separately from bounded caller accounting.

## Performance experiment

Use one exact editor/SDK, actual toolchain, geometry, owners, state, admitted
IDs and pass set. Compare **fixed-dense E9** with **counted-dense** using the
same producer, count publication and zero tail. That isolates count
consumption. Separately compare end to end against the best existing sparse
E9 producer and applicable R14 collapsed control. Evaluate omitted tail
clearing as another change, not a hidden advantage in the first pair.

Sweep 32/256/1024 commands per owner and 0/one/1-in-16/quarter/full active
occupancy without changing visible geometry between paired arms. Include
packed incompatible geometry ranges that genuinely cannot collapse into one
command, while keeping common state. Record full-frame tails, inclusive GPU
time, complete producer time, requested buffers and suitable physical-memory
evidence. Report observed active/maximum commands per owner; predicted command
counts are not hardware draw instrumentation. Accept only repeatable benefit
without frame, correctness or lifetime regression. Source research establishes
none of those performance results.

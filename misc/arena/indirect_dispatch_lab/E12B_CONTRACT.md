# Checked indirect compute contract

Status: implementation contract, 2026-10-24. Audit base:
`5ad4056427c48a024979bc9dd7fdf5f0daeb5bf4`.
E12b prevents mixed indirect/shader use of one argument bank in a compute segment
and rejects unsupported persistent arguments. It preserves existing editor
packages and adds no simulation, allocator, readback or automatic scheduler.

## API and failure behavior

Add Error-returning counterparts to the existing void operations:

```cpp
Error compute_list_bind_uniform_set_checked(ComputeListID list, RID set, uint32_t index);
Error compute_list_dispatch_checked(ComputeListID list, uint32_t x, uint32_t y, uint32_t z);
Error compute_list_dispatch_indirect_checked(ComputeListID list, RID arguments, int64_t offset);
```

Expected invalid list, RID, index, pipeline, descriptor, argument metadata and
alias combinations return `ERR_INVALID_PARAMETER` without logging. Calls must
use the correct RD render thread; thread violations retain native diagnostics.
Legacy void methods use the same validation and report rejection through their
existing diagnostic style. A matching regenerated .NET SDK exposes the new
methods; old method signatures remain unchanged.

Reject invalid/inactive lists, out-of-range set indices, missing/freed pipeline,
missing or incompatible required descriptor sets and missing required push
constants before dispatch. Direct group counts retain positive, device-bounded
requirements. Indirect group counts are GPU payload: the caller bounds them;
the engine neither reads nor clamps them.

Argument metadata is checked as signed64 before narrowing: offset must fit
uint32, be nonnegative and four-byte aligned, and the complete twelve-byte
command must fit. The RID must belong to the receiving RD's storage owner,
have STORAGE and INDIRECT usage, and be nonpersistent. The initial fixture uses
global RD; foreign-device RIDs fail ownership validation. Dynamic-persistent
arguments are rejected until per-frame indirect offsets are defined and tested.

## Segment ownership and aliases

The unit of reservation is one graph compute list, ending at list end or barrier.
After a successful indirect dispatch its whole argument buffer is reserved for
INDIRECT reads in that segment. Repeated indirect reads are allowed, including
different command offsets. Shader use of that buffer is rejected even when
readonly, disjoint in bytes or paired with zero-work GPU arguments.

Before indirect submission, check both already-recorded graph usages and every
currently required shader set, whether marked bound or pending. Rebinding a set
does not erase an earlier recorded shader use. Before binding a shader set or
dispatching direct/indirect work, check against all existing argument reservations.
A pipeline change preserves reservations and can activate a dormant descriptor;
the next dispatch must revalidate it.

Real runtime aliases cover readonly/readwrite storage bindings. A synthetic
UNIFORM_BUFFER_READ tracker can exercise graph policy, but a storage-owner
argument RID cannot be bound through the current uniform-buffer owner API.
Independent legal UBOs remain usable in mixed descriptor sets.

All fallible checks precede reservation insertion, descriptor preparation or
binding, uniform shared/clear updates, bound flags, graph instructions/usages
and dispatch-count changes. Rejection leaves previous valid pending state usable.
A failed submission does not reserve its candidate bank. This transactional
promise covers API preflight, not allocation/device loss or arbitrary shader
resource lifetime violations.

## Barriers and lifetimes

Reservations live on ComputeList outside its cached State. End clears them.
A valid barrier ends the current graph list and starts another, restoring only
pipeline, live staged descriptor and supplied push-constant state. It permits a new role for the old
bank but does not unbind producer descriptors: replace those bindings before an
indirect consumer. Invalid/inactive barrier calls reject before ending a list
or clearing reservations.

Pure graph compatibility queries inspect frame, list index and usage without
resetting a tracker. Shader producers and indirect consumers use distinct graph
segments so RD tracks their dependencies. The patch does not redesign graph
mixed usages unrelated to indirect arguments or introduce a cross-queue fence.

All RIDs remain caller-owned. Keep buffers, shaders, sets and pipelines alive
through their recorded uses; close lists before freeing resources. No resource
is adopted or freed on submission/rejection. Buffer copy/clear retain existing
list and lifetime rules.

## Qualification

CPU tests cover signed metadata, persistent/usage flags, graph compatibility
without tracker mutation, previous versus next segments, repeated indirect
reads, pipeline/binding type signatures and public API metadata. Pure-helper
and graph tests do not prove full RD descriptor recovery or GPU barriers.

Prepare a small exact-SDK compute stand with independent output/sentinel and
Error oracles: metadata rejection/recovery, pending and recorded shader-first
aliases, arguments-first bind rejection, dormant set activation, repeated and
zero-work indirect commands, end/barrier reuse and restored producer bindings.
Require the complete case matrix and exact diagnostic budget before aggregate
success. Avoid illegal GPU workgroup counts as test poison.

Compile the native implementation and matching managed API separately from old
packages. GPU execution waits for the renderer A/B slot. An editor built with
`dev_build=no` still has DEBUG_ENABLED; full release-path proof requires both
DEBUG_ENABLED and DEV_ENABLED absent. Source placement outside those macros and
standalone tests are bounded evidence, not a release-driver runtime pass.
No FPS or physical-VRAM improvement is promised by these correctness guards.

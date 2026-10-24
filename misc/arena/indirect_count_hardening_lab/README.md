# E10 hardening stand

Status: separate source stand targeting the immutable E10b editor/SDK identified
by `candidate.json`. GPU execution waits for release of the parallel renderer
A/B slot. This task changes no native engine code and does not rebuild, copy into
or modify that editor. Compilation and synthetic validators are separate from
runtime qualification.

The direct and native projects each run one named case per fresh Godot process.
This keeps per-owner warning budgets and process-global state reproducible.
Their independent `oracle.json` files pin operation order, numerical RGBA samples,
expected diagnostic messages/counts and final caller resource counters. Missing
samples, unexpected warnings, extra diagnostics or leaked caller resources fail
the case. The runner also checks native/API/SDK/fixture/source identities and
complete runtime/shutdown logs. Expected negative probes have exact, local
diagnostic budgets; there is no global error suppression.

## Scope

The direct matrix covers command/counter shader aliases before and after counted
submission, pending versus already-recorded shader use, count-zero reservations,
transactional rejection followed by valid drawing, reservation release at list
end, repeated lists, retention across two legal subpasses, persistent-bank
rejection and foreign-device RIDs. Vertex/index alias fixtures are outside this
first matrix: the public creation APIs do not expose the combined storage-owner
and INDIRECT usage required to construct those counted banks safely.

The native matrix covers self-alias counted rejection, checked material-binding
rejection after a leading valid counted owner, subsequent valid-owner raster,
material replacement, repeated K-to-zero-to-one frames, shared borrower
detach/reattach/destruction and a bounded logical-buffer plateau. Draw ordering
and matching pipeline keys are derived from pinned native sort rules; actual
pipeline RID execution is not instrumented. See each oracle's explicit
`not_qualified` list for remaining boundaries.

The plateau case warms its pipelines, then samples RD logical buffer usage over
bounded repeated allocation/retirement cycles. Raw samples and a fixed range
and trend bound are validated independently. This is not physical VRAM, a
general GPU-fence API, a proof of unlimited lifetime or an FPS measurement.
Fixed frame pacing alone does not prove retirement; the probes use numerical
readback completion before freeing caller storage.

Pixel acceptance uses every independent oracle sample. Full-image hashes are
retained as evidence, without claiming that sampled receipts reconstruct the
entire image. No screenshots, image assets or game project copies are produced.

## Compile without launching Godot

Keep this complete source directory separate from the frozen editor. The editor,
GodotSharp directory and NuGetFeed must remain together. Use one fresh output
directory outside both source and editor:

```powershell
python -B run_contract.py --candidate <E10b-candidate-directory> --output <fresh-compile-directory> --compile-only
```

Compilation is the default. Both C# projects use the exact SDK from
`candidate.json`; source, NuGet, build, user data and TEMP stay inside the fresh
output directory. The receipt states `gpu_executed: false` and `passed: null`.
The runner never launches Godot during compilation or verification.

## Deferred GPU sequence

Only after the parallel A/B slot is released, complete the bundled E10a direct
stand and E10b native stand on this exact editor/SDK. An old editor's receipts or
an unavailable-capability guard cannot qualify positive hardening. Preserve
both run directories, logs and generated fixture assemblies. Then run:

```powershell
python -B run_contract.py --candidate <E10b-candidate-directory> --output <fresh-hardening-directory> --run-gpu --e10a-output <completed-E10a-run> --e10b-output <completed-E10b-run>
```

Use `--suite direct|native` and optionally `--case <name>` to isolate a failure.
An isolated subset never claims that all cases passed. Every case opens its own
process; the default suite is correctness work, not a time estimate or benchmark.
Unexpected diagnostics and shutdown errors fail even after a numeric pass.

Synthetic Python tests launch no engine:

```powershell
python -B -m unittest discover -s misc/arena/indirect_count_hardening_lab -p "test_*.py"
```

## Provenance and ownership

Procedural RD and native scene scaffolding, exact-SDK compilation and prerequisite
identity checks adapt `indirect_count_lab/fixture` and `counted_multimesh_lab`
from source `109202199e8b21a35fb0dd9fee4895ebaee7d309`, whose native build is
`00e1b43953e4b0bb136fe397782c9e1d030aa836`. The new negative sequences, native
recovery/lifetime cases and case-specific independent oracles belong to this lab.
No donor game source/assets are reused. Callers own every allocated RD bank and
retire borrowers and shader references before storage; foreign RIDs are freed
only on their creating device/thread.

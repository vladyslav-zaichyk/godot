# Checked compute numerical stand

This bounded public C# consumer targets native commit
`7d59f5064a6cd40a1cf2aaa578b8e90d29d1cbe8` and its matching generated SDK
`4.7.2-arena-dev-g7d59f5064a`. Source authoring does not qualify runtime behavior.
The default runner only restores and compiles; it never starts Godot, including
for `--version`. GPU execution must wait for the parallel renderer A/B slot.

```powershell
python -B run_contract.py --engine D:/path/to/editor.exe `
  --sdk-feed D:/path/to/NuGetFeed --sdk-version 4.7.2-arena-dev-g7d59f5064a `
  --output D:/path/to/fresh-checked-compute-output
```

The runner requires the exact four generated Godot packages and sibling
`GodotSharp/Api/{Debug,Release}` assemblies. It selects installed .NET SDK
10.0.203 with a private `global.json`; override `--dotnet-sdk-version` only when
the coordinator records the actual matching toolchain. Source, editor, API and
package hashes are checked again after compilation and execution. Private
NuGet, user data, TEMP, intermediate output and logs live under the fresh output.
MSBuild runs with two workers and shared compiler/build servers disabled.

After A/B is explicitly released, add **both** `--run-gpu --gpu-slot-released`.
Every case gets a fresh process. `--case NAME` runs a subset and can never mark
the complete matrix qualified. The operator, rather than this script, determines
GPU slot availability; it never closes another editor or process.

| Case | Contract |
| --- | --- |
| metadata_recovery | Signed/range/alignment/list/RID/usage/persistent/foreign-owner rejection; failed candidates do not reserve a bank; same-state recovery |
| pending_and_recorded_aliases | Both readonly and readwrite SSBO aliases, before and after recorded shader use, with direct recovery |
| arguments_first_recovery | Checked bind rejects RO/RW aliases and retains the previous valid descriptors for direct/indirect work |
| dormant_pipeline_activation | Inactive set staged before reservation becomes active after a pipeline change; replacement recovers |
| barriers_end_repeat_zero | GPU producer, repeated offsets, first zero-work reservation, end/barrier role reuse and restored producer bindings |
| push_constant_history | Shader change remains missing-PC after a barrier; supplied PC restores; switching to a zero-PC shader stays quiet |

The producer writes legal `{2,1,1}` commands at byte offsets 16 and 32 and
`{0,1,1}` at 48 in a 64-byte bank. No argument readback occurs before dispatch.
The consumer has local size four and writes eight uint4 records. Full readbacks
check seed/ordinal/tag sums, per-record invocation counts and both guard
records. Every overlapping output write is atomic and order independent;
unexpected invocations beyond the eight admitted records atomically change a
fixed suffix guard rather than writing out of bounds.
The oracle lists each contributing dispatch rather than assuming dispatch WAW
ordering inside a segment. Python recomputes the complete 40-word oracle and its byte hash instead
of trusting a runtime `passed` flag. Thirteen probes and the ordered Error
ledger are mandatory. Checked failures must produce zero native callbacks and
zero runtime/shutdown diagnostics. All lists close and caller resources drain
before the terminal receipt; readback is a stand-specific drain, not a new fence.

`python -B -m unittest test_receipt.py` exercises synthetic receipt adversaries
only. It does not qualify native dispatch or GPU barriers. Runtime receipts
record loaded API/fixture hashes with PE module identity checks, engine commit
and executable hash, driver, GPU, complete input hashes and final resource counts.
The runner retains owned output for the coordinator's provenance-aware cleanup.

Explicit gaps: no wrong-list legacy-barrier diagnostic probe, legacy-void
diagnostic budget, real UBO alias, dormant high-slot preservation across a smaller
pipeline, freed-resource abuse, texture alias, cross-queue proof or full release
driver runtime. No FPS, physical VRAM, animation, native MultiMesh or scheduling
claim is made. The immutable editor packages remain separate references.

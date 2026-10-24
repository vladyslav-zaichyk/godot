# E10b bounded native counted MultiMesh stand

Status: source consumer; compilation and GPU qualification are separate gates.
This stand is not a frame-time or physical-memory benchmark. GPU execution must
wait for the other renderer agent's A/B slot.

The native API/ownership contract is
[E10B_CONTRACT.md](../indirect_count_lab/E10B_CONTRACT.md). The source uses the
new nine-argument counted attachment, counter RID/offset queries and submission
mode 2. It requires the exact candidate editor, GodotSharp APIs and SDK feed.

## What the stand proves if its deferred run passes

Two native owners share immutable packed indexed geometry and a bounded visible-ID
bank; both retain their own material and conservative bounds. Every owner has 16
draw slots. Two quad ranges have different side-pixel coverage, so numerical
probes distinguish geometry selection as well as identity. The single orthographic
viewport is 80 by 32 pixels. Shadows, temporal effects and history are disabled.

The matrix is separate/shared-prefix counter storage, stride 20/32 and maximum
1/min(4, device limit). The full 1/4 matrix requires a limit of at least four.
Each configuration compares fixed E9 MULTI_DRAW and counted mode with identical
producer data through full, zero, sparse first/last, one and reversed phases.
The producer uses three separate compute lists: reset, bounded command/ID writes,
then per-owner count publication. Counters are not read by the CPU for submission.

Counted-only zero/one probes retain legal nonempty commands beyond the published
prefix. These are separate from the matched zero-tail comparison. A further legal
nonempty record outside the configured maximum is present in every bank. Guards
and padding use poison bytes; the GPU draw counter never uses illegal poison.
Each phase checks the entire command/ID/counter banks, twenty numeric center/side
pixels and whole-raster equality. The Python oracle independently reconstructs
the expected bank hashes, identity sets, pixels, phase matrix and operation counts.

Live attachment probes require quiet rejection with the entire prior view intact:
signed metadata/range errors, invalid RIDs, missing INDIRECT usage, overlapping
shared intervals and dynamic persistent banks. The latter use native flag value
`1 << 2`, intentionally not exposed by name in ClassDB at the pinned source.
Old span and single-command attachments must clear counter metadata. An extra
borrower is detached, reattached and destroyed; the original owners and their
full raster must survive. Caller storage is read back for completion and freed
after borrowers and shader references are retired. Bounded accounting is not a
sustained lifetime plateau or a general GPU fence contract.

The paired fixed control additionally requires ordinary MDI and native indirect
first-instance support. Lack of either is an explicit failed prerequisite, with
no substitute submission mode. A device without count support exercises only its
unavailable-attachment guard and cannot qualify positive native drawing.

## Run and evidence

Compilation is the default and launches only dotnet:

```powershell
python -B run_contract.py --engine <candidate/editor.exe> --sdk-feed <candidate/NuGetFeed> --sdk-version <exact-version> --engine-hash <full-native-sha> --output <fresh-output> --compile-only
```

After the A/B slot is released, first run the bundled direct E10a stand with the
**same candidate editor and SDK**. Then pass that successfully completed run directory:

```powershell
python -B run_contract.py --engine <candidate/editor.exe> --sdk-feed <candidate/NuGetFeed> --sdk-version <exact-version> --engine-hash <full-native-sha> --output <fresh-native-output> --run-gpu --e10a-output <completed-direct-e10a-output>
```

The runner verifies direct-gate identities and receipt hashes before native
execution. A supported device requires positive direct count drawing; an
unavailable direct gate permits only the native unavailable-attachment subset.
All generated project files, caches, user data, logs and receipts live
inside the one fresh output directory. Runtime/shutdown diagnostics fail the run;
there is no global log suppression. No screenshots or image files are written.
Readbacks serialize this correctness subset and exclude performance conclusions.
The record explicitly defers renderer alias/subpass/cache recovery, excluded-pass
probes, E8/history, wrong-device RID and transform-backed/ordinary-allocation reset
probes, sustained memory/lifetime and all production integration.

Synthetic validator tests never launch Godot:

```powershell
python -B -m unittest discover -s misc/arena/counted_multimesh_lab -p test_receipt.py
```

## Provenance

Procedural scene/BufferRD ownership and shader lookup adapt the E9 source consumer
from native source `82e80df4074789810a169959f6fe0ce8874ffc76`:
`multicommand_lab/project/MulticommandContract.cs`, `Consumer.gdshader`,
`ContractErrorLogger.cs`, project and scene files. The compile-default runner
adapts `indirect_count_lab/fixture/run_contract.py` from that source. New GPU
production, native counted checks and receipt oracle are specific to this lab.
Only these small source files are reused; no game checkout or assets are copied.

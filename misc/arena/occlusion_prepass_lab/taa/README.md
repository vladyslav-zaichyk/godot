# E13c native TAA/E8 numerical stand

This is a bounded correctness fixture, not a performance or image-quality test.
The exact new editor and its four generated SDK packages must come from one
successful native build manifest. Existing E13b editors do not implement this API.

`run_contract.py` compiles only by default. It neither builds the engine nor opens
Godot unless both `--run-gpu` and `--gpu-slot-released` are supplied after the
parallel renderer A/B finishes. Use a fresh output outside source and editor
directories. Example, with the actual new source SHA and normalized SDK version:

```powershell
python -B run_contract.py --engine D:/candidate/Godot.exe --sdk-feed D:/candidate/feed --native-receipt D:/candidate/native-build-manifest.json --engine-hash FULL_NEW_NATIVE_SHA --sdk-version EXACT_NEW_SDK --output D:/Projects/e13c-compile-01
```

The output preserves exact input/support hashes, compiled consumer, build logs and
native build identity. Its private project, caches and temporary directories are
removed by default. `--keep-work` explicitly retains that single disposable work
directory for investigation. A compile-only receipt has GPU acceptance unset.
Before compiling, the runner sweeps all 16 synthetic Halton starting phases over
the frozen scene inputs. `cpu-scenario-preflight.json` explicitly records this
CPU parameter check without claiming native UBO capture or GPU execution.

The versioned matrix declares one finite E8 initialization render `I000` before
occlusion registration, followed by all 128 prepared records. It permits no hidden
warmups, retry, per-frame GPU readback or growing GPU bank. At completion only
three fixed binary ledgers are read. A completed fallback also commits E8 history;
a skipped or rejected preparation cannot commit it.

`validate_receipt.py` independently decodes the actual current/previous scene UBO,
reconstructs source mesh/deformation/identity from frozen pose words, audits
ordinary-wall Hi-Z and current command/ID selection, checks pre-TAA depth and color
identity, and computes perspective-correct raw velocities. Deterministic witness
choice uses producer matrix words after comparing them with the captured native
UBO; it never selects witnesses from sampled GPU colors or velocities. Quantize
squared distance to the projected quad centroid with round-to-even at 1/1024,
then sort by distance, y, x and triangle index. Four distinct pixels per visible
identity must be at least two pixels from triangle edges; eight background probes
catch stale velocity. The fixed error budgets are never widened after a failure.
At least one actual T045/T046/T049/T050 witness must separate the homogeneous
oracle from the orthographic shortcut by twice the velocity error budget.
Each declared invalid-history identity in T026–T036 must also separate the correct
reset result from bypassing its previous-state proof by twice that budget. This
prevents identical poses from making a broken lifecycle check appear correct.

Any runtime/shutdown diagnostic, including bare `Op... is not supported yet`
re-spirv output, fails the run. Identity/hash drift, incomplete rows, wrong native
epochs, uncommitted history promotion, corrupted guards, false-negative IDs,
zero-output positive controls and remaining caller resources fail acceptance.

Run the independent CPU adversarial checks with:

```powershell
python -B -m unittest -v test_receipt.py
```

These checks do not execute GPU work. Successful numerical evidence qualifies
only one procedural owner, 16 stable slots and one centered-perspective native
TAA view. The new binary still needs fresh inherited orthographic/perspective
and corrected E8 regressions. Real VAT, eight LOD owners, resolved TAA quality,
native memory plateau, physical VRAM and FPS remain separate integration gates.

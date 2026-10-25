# E13d ordinary opaque batching gate

Prepared numerical stand, GPU pending. It isolates ordinary mesh auto-instancing;
it does not replace the existing E13b/E13c crowd regressions or qualify E8, real
VAT, performance, physical VRAM, final TAA appearance or production use.

Six ordinary owners share one mesh and material, with distinct affine transforms
and per-instance identity uniforms. One initial TAA render seeds native transform
history. Then 24 measured TAA records exercise all-early, all-remaining, mixed
blocks and both alternating eligibility assignments, plus object/camera motion.
Equal sort keys have no stable-order promise: mixed phase counts are checked
without assuming creation order. The native original compatible group must be
one; homogeneous phases must retain one group.

The producer samples actual early depth before the remaining depth pass. The
trusted POST_OPAQUE observer requests none of the five compositor flags and
samples actual color identity, depth and velocity, plus both native scene UBOs.
The independent oracle selects four interior pixels per owner using captured
matrices/jitter; it checks phase isolation and current/previous transform motion.
The sibling E13c math helper is imported and hash-pinned by each run. Both source
layout `../taa` and sealed layout `../occlusion_taa_lab` are supported.

Limits: one view, 128x64, six ordinary owners, zero registered MultiMesh consumers,
24 producer dispatches and 24 observer dispatches, three terminal reads,
4,071,936 requested ledger bytes, at most four native attachment descriptor
identities. Capacity/diagnostic failure rejects the run. No images or per-frame
readbacks. Borrowed native wrappers stay callback-scoped. Private runner work is
removed after retaining the assembly, receipts and selected numerical evidence.

Compile only is the default; use the new editor, exact SDK and its successful
`build_windows.py` manifest:

```powershell
python run_contract.py --engine <editor.exe> --sdk-feed <feed> --native-receipt <manifest.json> --engine-hash <40-character-SHA> --sdk-version <exact-version> --output <fresh-D-directory>
```

Only after the integrator releases the GPU, append `--run-gpu
--gpu-slot-released`. A compiled receipt has `passed=null` and cannot qualify
GPU rendering. Numerical runtime success must be followed by the existing E13b,
E8/VAT and E13c regressions on this exact new binary. A later A/B should compare
old E13, new E13 and no E13 with visibility forced to retain every crowd member,
using repeated ordinary occluders. This gate measures no FPS.

`python test_receipt.py` checks synthetic CPU ledgers and deliberate corruption;
it never starts Godot, writes screenshots or asserts a real GPU pass.

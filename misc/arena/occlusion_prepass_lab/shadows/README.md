# E13e directional shadow contract stand

Source-only preparation. Native control3365639 still rejects E13 with shadow
lights. No shadow admission patch, new editor or GPU result is delivered here.
Read [CONTRACT.md](CONTRACT.md) for the proposed engine boundary and ownership.

The small stand uses one shader-driven owner, 16 stable slots and a lit receiver.
Ten triplets compare caster OFF, normal full shadows, and requested E13. A
wall-hidden caster and an offscreen caster must retain their visible shadows.
Two zero-candidate cases distinguish actual camera routing from complete
fallback. The deterministic selector isolates pass compatibility; it does not
implement or benchmark Hi-Z.

All measured rows use native TAA with the existing trusted numerical observer.
There are 30 measured views plus one explicit initialization view, four bounded
terminal reads and no screenshots. The oracle uses actual native matrices and
preregistered geometric receiver witnesses, validates a caster-off shadow delta,
and checks command routing, early depth and E8 history. This is not production,
real VAT, eight-LOD, resolved TAA appearance, FPS or physical VRAM acceptance.

Run CPU-only synthetic/adversarial checks without Godot:

```powershell
python test_receipt.py
```

Compile against the preserved control's exact SDK; compile-only is the default:

```powershell
python run_contract.py --engine <native3365639-editor.exe> --sdk-feed <its-NuGetFeed> --native-receipt <its-native-build-manifest.json> --engine-hash 3365639cceb0767a8f44f908d8823edf60e14424 --sdk-version 4.7.2-arena-dev-g3365639cce --output <fresh-D-directory>
```

The runner verifies editor/SDK/native receipt identity, compiles an isolated
source workspace, preserves selected evidence and removes disposable work.
Compile-only does not start Godot and cannot pass a GPU gate.

After the integrator's A/B ends, append `--run-gpu --gpu-slot-released` for
`reference-boundary`, which expects complete fallback on the preserved control.
Use `--gate combined-required` only with a future exact shadow-capable editor,
matching SDK/native receipt and their explicit identities. This mode requires
real candidate routing and shadow preservation; fallback is a failure.
Do not launch either mode during the integrator's GPU slot.

The next engine task is narrow directional-shadow admission and revalidation,
followed by a separate build and exact-binary regressions. Cascades, positional
shadows and multiple lights remain outside that first patch. Keep the existing
integration editor and all immutable reference bundles unchanged.

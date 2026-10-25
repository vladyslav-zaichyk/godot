# E13e directional shadow contract stand

E13e native implementation and separate editor/SDK preparation. The delivery
manifest pins the new source/editor/SDK; GPU qualification is pending after
integration A/B. Preserved native control3365639 still rejects E13 with shadow
lights. Read [CONTRACT.md](CONTRACT.md) for the narrow engine boundary and ownership.

Native TAA/E8 now admits one Orthogonal directional split with matching raw
shadow work. Camera commands can be selected; native shadows retain complete
fallback commands and IDs. Snapshot checks reject changed light/raw-work/atlas
state around the producer and before shadow recording. Unsupported initial
views retain fallback; mutation after the split aborts the view. No public API
or Hi-Z algorithm is added. Existing shadow-free paths stay available.

The small stand uses one shader-driven owner, 16 stable slots and a lit receiver.
Ten triplets compare caster OFF, normal full shadows, and requested E13. A
wall-hidden caster and an offscreen caster must retain their visible shadows.
Two zero-candidate cases distinguish actual camera routing from complete
fallback. Expected-lit probes inside a dead caster's former shadow footprint
reject equally stale shadows in both arms. The deterministic selector isolates pass compatibility; it does not
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

Compile-only is the default. For the separate new editor, select the combined
gate and use its exact native hash, SDK/feed and successful native build receipt:

```powershell
python run_contract.py --gate combined-required --engine <new-editor.exe> --sdk-feed <its-NuGetFeed> --native-receipt <its-native-build-manifest.json> --engine-hash <full-new-native-SHA> --sdk-version <exact-new-SDK-version> --output <fresh-D-directory>
```

The preserved control can still be compiled in reference-boundary mode:

```powershell
python run_contract.py --engine <native3365639-editor.exe> --sdk-feed <its-NuGetFeed> --native-receipt <its-native-build-manifest.json> --engine-hash 3365639cceb0767a8f44f908d8823edf60e14424 --sdk-version 4.7.2-arena-dev-g3365639cce --output <fresh-D-directory>
```

The runner verifies editor/SDK/native receipt identity, compiles an isolated
source workspace, preserves selected evidence and removes disposable work.
Compile-only does not start Godot and cannot pass a GPU gate.

After the integrator's A/B ends, append `--run-gpu --gpu-slot-released` for
`reference-boundary`, which expects complete fallback on the preserved control.
Use `--gate combined-required` only with the exact separate shadow-capable editor,
matching SDK/native receipt and their explicit identities. This mode requires
real candidate routing and shadow preservation; fallback is a failure.
Do not launch either mode during the integrator's GPU slot.

Exact-binary combined and regression GPU checks follow after slot release.
Cascades, positional shadows and multiple lights remain outside this patch. Keep the existing
integration editor and all immutable reference bundles unchanged.

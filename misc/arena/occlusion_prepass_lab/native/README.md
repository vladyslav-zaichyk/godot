# Native E13a ordering and publication stand

Status: authored source; new native editor/SDK compilation and GPU execution are
pending. This directory does not change or qualify the separate `../fixture/`
proxy reference. Never substitute the older E12b editor or infer a native pass
from a successful managed compile or synthetic receipt test.

The stand uses one actual 64x32 orthographic SubViewport, Vulkan Forward+, no
MSAA/TAA/GI/shadows and public Bilinear scaling at scale1 (the viewport normalizes
this to native scaling OFF). Two selected white ordinary wall owners leave a
gap. Two unselected white markers share their exact mesh/material, interleaved
in owner creation order. Their depth must be absent during the callback and
their color must remain after resumed depth/color. Sixteen immutable stable
slots use one E5 shader-driven indexed MultiMesh; its native draw capacity is32.
The ID bank has immutable fallback `[0,16)` and private candidate `[16,32)`;
both commands stay inside that capacity. Persistent attachment always uses the
legal16-slot uncull command. The stand owns no game assets.

The typed callback validates actual viewport/epoch/configuration/view/extent,
camera transform and corrected projection. It uses ONLY the supplied already
open compute list: native depth copy, six reverse-Z min reductions, a bounded
16-slot scan, then separate command finalization with eight supported barriers.
Cached uniform-set construction is allowed. It never begins/ends another list,
uploads, reads back, draws, frees, submits, changes scene state or retains usable
context/depth/list handles. UniformSetCacheRD owns depth descriptor caching;
freeing its caller-owned shader/pyramid dependencies invalidates those sets.

Fourteen one-shot view renders cover uncull controls, successful culling,
reported producer error, stale/missing acknowledgments, successful zero output,
GPU overflow to full fallback, recovery, feature off/clear, open/current-clear
depth, wall return and a four-pixel camera shift. Eleven callbacks record99
dispatches and88 barriers total. Error/stale/missing probes deliberately publish
zero candidates, so unchanged normal pixels prove native fallback consumption.
The successful zero probe must remove visible crowd pixels. This distinguishes
actual render-local selection from merely correct producer bytes/outcome flags.

Only AFTER the callback and native resumed render, terminal GPU reads capture
the full depth/min hierarchy, ID bank, all five indexed command words and epoch
stamps; a viewport Image supplies22 numerical pixel probes per view and hashes.
No image files or visual screenshots are written. The independent Python oracle
checks actual selected wall depth, full-resolution conservative visibility,
zero false negatives, at least one blocked ID removed, immutable fallback,
guards/uniqueness/capacity, GPU-overflow fallback, pixel colors and equal normal
rasters against feature-off controls. Owner/material/depth state must resume
correctly. Outcome `selected/mode/early_surface_count` and automatic-instancing
disable metadata are checked; unstable reason wording is recorded, not guessed.

Run CPU validation without an editor or GPU:

```powershell
python -B misc/arena/occlusion_prepass_lab/native/test_receipt.py
```

The runner requires a successful `tools/arena/build_windows.py` manifest from
the NEW build, exact EXE/source SHA and its four generated SDK packages. Inject
the final native commit only after the coordinator creates that build:

```powershell
$env:ArenaNativeSourceCommit = '<new full 40-character native commit>'
$env:ArenaGodotSdkVersion = '<matching new generated SDK version>'
python -B misc/arena/occlusion_prepass_lab/native/run_contract.py `
  --engine '<new bundle>/godot.windows.editor.x86_64.arena_dev.mono.exe' `
  --sdk-feed '<new build>/nuget-source' `
  --native-receipt '<new build>/build-<sha>/manifest.json' `
  --output 'D:/ArenaTaskScratch/e13a-native-compile'
```

Default/`--compile-only` runs dotnet restore/build only; it never invokes Godot,
even for version discovery. The compile receipt says GPU pending and no native
render qualification. Runtime additionally verifies the executable's own source
SHA and SHA256, SDK metadata, loaded fixture/API assemblies and MVIDs, source and
support inventories, diagnostic budget and full shutdown. No global NuGet
settings or shared package caches change. Matching Debug runtime API and Release
compile API are identified separately. The new SDK must expose the agreed
`RenderGpuOcclusionContext` and viewport methods; the old SDK should fail compile.

Only after renderer A/B has finished, an explicitly authorized run adds BOTH
`--run-gpu --gpu-slot-released` and uses another fresh output. Missing the release
flag fails before creating output. No code automatically reserves/releases the
GPU or treats elapsed time as authorization.

Every run owns one fresh output directory. Logs, JSON receipts, the native build
manifest and compiled stand DLL remain as designated evidence. The private
`output/work` project, caches, Temp/AppData and intermediates are removed after
stopping owned helpers. `--keep-work` explicitly retains that path for diagnosis;
uncertain links prevent cleanup and produce an exact `cleanup_blocker` in
`run.json`. The runner never removes source, the editor, SDK feed or build logs.

This can qualify only the fixed native orthographic16-slot rendering contract.
It does not qualify general animated bounds, perspective/near clipping,
multi-owner candidate regions, motion, shadows, secondary views, sustained
lifetime, native/physical VRAM, frame time, release drivers or adoption. Caller
requested byte/resource ledgers are not total native allocation measurements.

# Caller-owned RD occlusion reference

Source-only E13a reference stand. **No native E13 API exists in this task.**
This project uses immutable E12b editor commit
`7d59f5064a6cd40a1cf2aaa578b8e90d29d1cbe8`, SDK
`4.7.2-arena-dev-g7d59f5064a` and existing public RD APIs. It does not obtain the
native main viewport depth or run between native occluder and crowd passes.

The small independent proxy renders two opaque quads representing a static wall
with an eight-pixel gap into caller-owned sampled D32 depth, 64 by 32 pixels.
Reverse-Z uses clear 0, GREATER and wall depth 0.6. A compute copy publishes base
depth into one SSBO atlas; six separate min-reduction segments finish a seven
level pyramid. A final one-invocation producer deterministically compacts at most
16 projected rectangles/stable IDs and writes an ordinary indexed indirect
command at byte 16. It intentionally does not draw a native crowd with that bank.
Serial compaction is a bounded oracle, not a production scan or performance model.

Each reduction ends before its consumer; no shader dispatch order or implicit
intra-list barrier is assumed. The producer queries every overlapped aligned
block, inflates rectangles by one pixel and culls only when the reverse-Z minimum
is strictly closer than the object's closest depth plus epsilon. Invalid bounds,
near/uncertain flags and mismatched depth/query epochs fail open. Full-resolution
CPU geometry is the visibility oracle; it never creates a CPU min-reduction tree
or copies a GPU-visible decision into the expected result. Every raster/pyramid
sample is also compared with analytic full-resolution wall coverage. Guards,
stable ID order, complete command fields and byte hashes are checked independently.

| Case | Purpose |
| --- | --- |
| wall_grid | Behind/front/gap/edge/near/uncertain/offscreen and equal-depth rectangles; conservative false positives recorded |
| open_scene | Clear depth cannot occlude any ID |
| camera_shift | Explicit orthographic camera shift updates both proxy geometry and projected candidates |
| camera_cut | Mismatched epoch fails open; a fresh matching epoch resumes proxy culling |
| stale_lists | Reuse the same resources: old wall followed by current-frame clear must restore all IDs |
| lifetime | Four allocate/use/drain/free cycles with exact caller resource ledgers |
| capacity_rejected | Insufficient ID capacity publishes count 0 plus overflow, raw `passed=false`, status `capacity_rejected`, exit 3 |

Capacity rejection is an expected **negative contract**. The runner may mark that
guard verified and the complete matrix's contracts passed, while this case has
`proxy_case_culling_qualified=false`. It never treats count 0 as a valid truncated
crowd or claims native fallback. Only positive reference cases qualify proxy
culling. All outcomes keep `native_qualified=false` and `native_api_present=false`.

Compilation is the default; it never launches the editor, including for version
discovery. Use installed .NET SDK 10.0.203, with the generated four-package feed
and sibling `GodotSharp/Api/{Debug,Release}` assemblies from the immutable editor:

```powershell
python -B run_contract.py --engine D:/path/to/editor.exe `
  --sdk-feed D:/path/to/NuGetFeed --sdk-version 4.7.2-arena-dev-g7d59f5064a `
  --output D:/path/to/fresh-occlusion-reference-output
```

Only after the parallel renderer A/B releases the GPU, add both
`--run-gpu --gpu-slot-released`. Each named case receives a fresh process. Use
`--case NAME` for a subset; `complete_matrix` remains false. `--mode native`
compiles but returns explicit UNAVAILABLE (exit 4) **without launching Godot**.
The C# entry point independently rejects manual native mode as unavailable.
No missing hook is simulated as a native pass. Private NuGet, user data, TEMP,
intermediate output and logs stay inside one fresh output directory. The runner
records actual engine/SDK/API/fixture/input hashes, exact exit codes and a zero
runtime/shutdown diagnostic budget. No readback occurs before the GPU producer;
terminal readbacks drain only this bounded oracle. No images are written.

`python -B -m unittest test_receipt.py` runs synthetic geometry and receipt
adversaries only. It proves neither D32 rasterization nor GPU barriers. Source
structure and private compile runner reuse the small checked-compute lab's
public-API fixture pattern, not its runtime receipts or qualification.

Remaining gaps are explicit: general perspective/AABB projection, native
occluder eligibility and material replacement, native pass scheduling, transparency,
shadows, MSAA, XR, motion, multiple viewports, indirect crowd submission, native
overflow fallback, cross-queue behavior and a full release-driver runtime.
Caller ledger cycles are not physical-VRAM or driver-cache leak proof. There is
no FPS claim. The coordinator retains output provenance and performs cleanup;
the runner never closes another editor or deletes shared caches.

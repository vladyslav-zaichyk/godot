# Arena engine fork

This is an experimental Godot-only fork for GPU rendering APIs. Game source,
licensed assets and renderer performance fixtures are maintained separately.
Godot's existing license, copyright and third-party notices continue to apply.

## Frozen development baseline: 2026-10-25

Engine R&D is paused at native `26f1b2b845817c7dfd3e7861cd04386a5f198df3`.
The fork branch `arena/4.7.2-baseline` and immutable consumer tag
`arena-4.7.2-baseline-2026-10-25` preserve the cumulative patch series plus
documentation and a read-only bundle verifier. Their snapshot commit differs
from the native binary commit; no native rebuild accompanies this freeze.

Use the [frozen Windows .NET release](https://github.com/vladyslav-zaichyk/godot/releases/tag/arena-4.7.2-baseline-2026-10-25)
and [baseline quickstart and qualification record](misc/arena/BASELINE_2026-10-25.md).
The exact SDK is `4.7.2-arena-dev-g26f1b2b.845`. Eight bounded integration
stages passed on this editor; production admission, physical VRAM and FPS gains
remain unqualified. Existing game SDK/defaults and feature branches are unchanged.
Resume engine work for a measured game bottleneck or a reproducible correctness
failure; unfinished qualification does not block game development.

## Branch and version contract

- `upstream`: `https://github.com/godotengine/godot.git`, fetch only.
- `origin`: `https://github.com/vladyslav-zaichyk/godot.git`.
- Default integration branch: `arena/4.7.2`, based on official `4.7.2-stable`,
  commit `ed1daf0bf001b61586d9930840f2f1394092c079`.
- `feature/shared-multimesh` preserves the original shared-buffer patch series.
- New API work uses `feature/<name>` and a PR targeting `arena/4.7.2`. A feature
  that depends on an unmerged patch can temporarily target that feature branch;
  keep each patch's diff and consumer build separately identifiable.
- Consumer builds use a named, immutable `arena-4.7.2-*` tag, full source SHA,
  unique .NET package version and SHA-256 asset manifest. Never force-push a
  published integration branch or move a published consumer tag.

The initial shared-MultiMesh implementation is experimental: real GPU API and
lifetime checks passed; the measured packed variant reduced requested crowd
buffers, but strict frame-tail non-regression was inconclusive. It is not a
production or gameplay admission. BufferRD development has separate checks.

## Upgrade procedure

1. Fetch and verify the desired official release tag from `upstream`.
2. Create a new `upgrade/<version>` candidate from that exact upstream tag.
3. Reapply the ordered feature commits, resolve conflicts, and inspect
   `git range-diff` against the previous patch series. Rebase only unpublished
   candidates; keep existing consumer history available.
4. Update the build helper's `PIN` to the new verified upstream base and review
   its managed SDK/toolchain pins. Retain a compatible immutable control and
   build the new feature candidate using matching options. Rebuild a control
   when its provenance/profile is incompatible or an upstream upgrade requires
   it; a paired rebuild is not mandatory for every patch. Regenerate native/.NET
   bindings and use a new SDK/package identity.
5. Run compiler/API, real-backend correctness and lifetime checks. The renderer
   owner separately runs matched performance/quality qualification.
6. Publish the successful candidate under a new version branch and consumer tag.
   A build pass, GPU contract pass and performance pass are distinct statuses.

## Another Windows machine

Clone this fork and select a published consumer tag, or `arena/4.7.2` for ongoing
development. Install Python 3.13, Visual Studio 2022 C++ tools v143 with Windows
SDK 10.0.22621.0, and .NET SDK 10.0.203 (plus .NET 8 runtime). From the checkout:

```powershell
python tools/arena/build_windows.py --jobs 4
```

The helper creates a private environment, builds an editor and matching managed
API/SDK packages, and records source/toolchain/artifact identity under `.arena/`.
It changes no user-wide NuGet sources. This is an editor build, not game export
templates. A consumer must use the editor and the matching `GodotSharp` directory
and SDK packages together; replacing only the EXE is unsupported.

Portable bundles include the editor, console wrapper, complete `GodotSharp`,
local SDK feed, notices, manifest and validation receipts. Store durable builds
as versioned Release assets; CI artifacts are temporary candidates. Verify a
bundle's SHA-256 manifest before use. Other OS/architecture/backend combinations
need separate builds and qualification.

For the frozen baseline, download the release's standalone
`verify_frozen_baseline.py` and run it against the extracted bundle directory.
The original ZIP does not contain a verifier; its included guide is historical.
The source version is `tools/arena/verify_frozen_baseline.py`. The older
`tools/arena/package_windows.py` packages the initial BufferRD v1 contract only;
do not use it to recreate or requalify this cumulative baseline.
Native and consumer source SHAs are recorded separately: later fixture fixes
must not relabel an already-built native executable.

For a C# consumer, put a `NuGet.Config` beside its `.csproj` before its first
restore. Point the relative feed path at this bundle's `NuGetFeed` directory:

```xml
<configuration>
  <packageSources>
    <clear />
    <add key="ArenaEngine" value="../ArenaEngine/NuGetFeed" />
    <add key="nuget.org" value="https://api.nuget.org/v3/index.json" />
  </packageSources>
  <packageSourceMapping>
    <clear />
    <packageSource key="ArenaEngine">
      <package pattern="Godot.*" />
      <package pattern="GodotSharp" />
      <package pattern="GodotSharpEditor" />
    </packageSource>
    <packageSource key="nuget.org"><package pattern="*" /></packageSource>
  </packageSourceMapping>
</configuration>
```

Use the exact `sdk_version` from the bundle manifest in the project's
`Godot.NET.Sdk` reference. The native editor, managed assemblies and generated
packages must all come from that same bundle. No global NuGet changes are needed.

The `Arena Windows editor` workflow is manual only. Upstream workflows remain
disabled in this fork. Hosted CI does not prove a Vulkan GPU contract, and the
author's desktop is not a public-fork self-hosted runner.

## BufferRD v1 development contract

The intended v1 is Vulkan/Forward+ material-only readonly access to existing
global RenderingDevice storage buffers. Each std430 record is one 16-byte
`uvec4`; float words use `uintBitsToFloat`. `buffer_load` is bounds-checked and
returns zero for an invalid record index; `buffer_length` returns record count.

```glsl
uniform readonly_buffer agents;

void vertex() {
    vec4 state = uintBitsToFloat(buffer_load(agents, uint(INSTANCE_ID)));
    VERTEX += state.xyz;
}
```

`BufferRD` is a borrowed, immutable wrapper after successful attachment.
Compute may update its contents. To replace storage, bind a new wrapper to the
material on the render thread; buffer assignments validate the live RID before
changing the material cache or native binding. Serialize mutations of a material.
Detach all consumers and wait for their rendering work before freeing
the caller-owned RID. The wrapper never frees that RID. Attachments require the
render thread and a global-RD storage allocation of at least 16 bytes, divisible
by 16. This contract remains development scope; consume only a matching tagged
build and its API/GPU receipts.

The initial candidate passed four compiler cases and a procedural RX 6800
Vulkan/Forward+ contract: 40 lifecycle cycles, 562 numeric viewport audits,
eight rejected attachments, four rejected material assignments, and stable
logical RD bytes/native objects after collection and drainage. See the bundle
receipts for exact native/consumer SHAs. This does not qualify physical VRAM,
renderer FPS, other GPUs or gameplay. The initial candidate's runtime-array
length made the optional re-spirv CPU optimizer fall back to original SPIR-V.
Commit `4c138f2` added optimizer support and is included in the frozen baseline;
the fallback description applies only to that historical candidate.

References: [Godot .NET builds](https://docs.godotengine.org/en/stable/engine_details/development/compiling/compiling_with_dotnet.html),
[upstream buffer proposal](https://github.com/godotengine/godot-proposals/issues/6989),
[draft implementation discussion](https://github.com/godotengine/godot/pull/109951).

## E6a native Mesh RD buffers

This backports [upstream PR #118973](https://github.com/godotengine/godot/pull/118973),
source `7cbebab1d05fc8837655b87d10bbde4f9cd5a1af`. RenderingServer exposes
`mesh_surface_get_vertex_buffer_rd_rid`, `mesh_surface_get_attribute_buffer_rd_rid`,
`mesh_surface_get_skin_buffer_rd_rid`, and `mesh_surface_get_index_buffer_rd_rid`.
Create the surface with `Mesh.ARRAY_FLAG_USE_STORAGE_BUFFER` to opt its buffers
into storage usage. In C#, the flag is `Mesh.ArrayFormat.FlagUseStorageBuffer`.
The skin stream already has storage usage when skinning data exists.

The mesh owns every returned RID. Borrow it on the rendering thread with the
global RenderingDevice; never call `free_rid` on it. Keep the owner alive, retire
dependent work before changing its surfaces, and refetch after recreation.
Absent streams and non-RD renderers return invalid RIDs. These getters expose
base surface data, not a per-instance skinned/blend-shape output.

Use queried native stream offsets, strides, compression and allocation sizes.
Positions, attributes, skin data and indices have distinct packed formats; this
is not the BufferRD `uvec4[]` ABI. Index elements are 16-bit through 65536 vertices
and 32-bit above that boundary. Compute writes preserve the fixed format/counts
and valid index range. Schedule writes before consuming draws and set conservative
custom bounds; bounds and cached CPU geometry are not updated by compute.

`misc/arena/mesh_rd_lab` is the procedural contract consumer. Its separate
Windows .NET candidate must retain its exact native/fixture SHAs and SDK version.
Compiler/CPU/managed checks and real Vulkan/lifetime checks have separate status.
This API does not attach arbitrary external buffers, implement a shared animation
cache, or establish an FPS or physical-VRAM improvement. Existing E5 packages are
immutable controls; local GPU runs wait for the renderer agent's test window.

## E13c native TAA with GPU occlusion

E13c extends the current-frame E13 prepass to native TAA when every registered
candidate and complete fallback uses E8 shader-driven motion history and a
compatible effective material. It preserves the existing selected occluder
depth -> synchronous producer -> remaining depth LOAD -> opaque color/velocity
order. Native TAA uses that same opaque pass; no extra motion pass or engine
history bank is introduced.

`viewport_configure_gpu_occlusion_prepass` has an optional fourth borrowed RID,
`diagnostic_post_opaque_effect`. A nonempty RID identifies exactly one enabled
POST_OPAQUE effect with a valid callback and all five needs/resolved flags false.
It is a trusted numerical observer, not a general compositor opt-in or sandbox.
Keep its effect/compositor alive; producer and observer have distinct list
ownership. Empty RID retains the existing no-compositor policy. Use the exact
matching new SDK; three-argument source compatibility does not imply old binary
binding compatibility.

First scope remains Windows x64 Vulkan Forward+, one history viewport/view,
native scaling, clear/color background, no MSAA/upscaling/forced motion or other
motion-demanding views. All previous material/alias/lifetime restrictions apply.
CPU recording outcome flags do not certify GPU completion or commit caller
history. A clean completed fallback render commits E8 history even if candidate
acknowledgment failed; a skipped/rejected recording does not.

See `misc/arena/occlusion_prepass_lab/taa/CONTRACT.md`, its fixed numerical plan,
and the procedural E8 tilted-quad consumer. Bundles ship this as
`occlusion_taa_lab/`, alongside the corrected standalone `gpu_history_lab/` and
the existing TAA-off `occlusion_prepass_lab/`. New-binary E13b/E8 regressions and
the combined GPU gate remain mandatory. Real VAT integration, eight LOD owners,
resolved TAA image quality, sustained native lifetime, physical VRAM and FPS have
separate acceptance; source/compile receipts alone do not qualify them.

## E13d ordinary opaque instancing

E13d preserves the stock ordinary opaque/color repeat groups when E13 splits
depth. Only early and remaining depth use a separate phase-aware suffix table;
sorted elements, native InstanceData indices and color metadata stay unchanged.
The reused CPU table requests four bytes per active opaque surface plus capacity
slack. No public method, Hi-Z algorithm, shadow or temporal admission is added.
Outcome repeat-group counts describe planned groups, not physical GPU calls.

See `misc/arena/occlusion_prepass_lab/BATCHING_CONTRACT.md` and the bounded
ordinary-mesh stand in `misc/arena/occlusion_prepass_lab/batching`. The stand
captures early depth and pre-TAA identity/depth/velocity for six shared-mesh
owners across 24 records; it does not qualify E8 or crowd histories. New-binary
E13b/E8/E13c integration regressions remain required. The older native oracle
now expects `auto_instancing_disabled=false`; consume each oracle with its
matching candidate, rather than relabeling old evidence.

The same separate editor carries BufferRD's explicit tutorial-tag correction.
There is no documentation-only build. Clean editor resource import and GPU
execution have separate gates; the XML correction does not claim to fix the
independently observed CLI documentation-dump singleton/access violation.

## E13e directional shadows with camera occlusion

E13e admits one ordinary Orthogonal directional shadow split with native TAA/E8
and matching raw shadow work. It scans the complete visible CPU light list and
snapshots finite light/current-shadow metadata, masks, identities and directional
atlas size/texture. Rechecks around the producer and before native shadow setup
reject post-admission mutation. Unsupported initial views retain fallback.
Shadow-free TAA-on/off admission and E13d batching retain their existing behavior.

Camera depth/color/velocity can use selected candidates; native shadow lists
always use persistent complete fallback commands AND immutable complete IDs.
Freeze current poses/lifecycle through all passes, keep conservative off-camera
caster bounds and collapse inactive bodies to zero-area geometry in every view.
The effective custom shader must deform identically before applying each pass's
native view matrices. This trusted contract adds no public API, transform bank,
shadow-culling algorithm, frame or fence. Quality/atlas precision stay frozen;
structural checks are not an arbitrary callback/shader sandbox.

See `misc/arena/occlusion_prepass_lab/shadows/CONTRACT.md` and the 16-slot stand,
packaged as `occlusion_shadow_lab/`. Its 30 measured TAA rows plus I000 cover
hidden/offscreen casters, actual zero-candidate routing, ACK fallback and caster
death/reuse. A caster-OFF counterfactual proves real shadow observability; four
expected-lit departed-shadow probes reject identical stale reference/candidate
shadows. Geometry, thresholds and GPU capacity remain frozen.

Use the separate exact editor/SDK/receipt with `--gate combined-required`.
Old native3365639 remains a reference-boundary fallback control. Compiler/CPU
evidence cannot qualify native GPU rendering, VAT, eight LOD owners, resolved
TAA quality, physical VRAM or FPS. Integration `936c4eac` subsequently passed all
eight basic stages on exact native `26f1b2b`: orthographic, perspective, E8,
real-VAT CPU preflight, real-VAT temporal control, combined TAA/occlusion, E13d
and E13e. E13d passed 24 records; E13e passed 30 measured views plus I000.
This is bounded acceptance with one owner, 16 stable slots and one Orthogonal
directional light; real VAT plus shadows is a separate, unqualified combination.
Runtime negative/recovery and sustained lifetime gates remain deferred. The
latest timing attempt retained 23 windows / 342,220 frames without a complete
clean A/B cohort, so no FPS improvement is accepted. Existing editor bundles
remain immutable controls. Cascades, positional and multiple shadow lights are
excluded. See the frozen baseline record for fixture and integration caveats.

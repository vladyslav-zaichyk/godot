# Arena engine fork

This is an experimental Godot-only fork for GPU rendering APIs. Game source,
licensed assets and renderer performance fixtures are maintained separately.
Godot's existing license, copyright and third-party notices continue to apply.

## Branch and version contract

- `upstream`: `https://github.com/godotengine/godot.git`, fetch only.
- `origin`: `https://github.com/vladyslav-zaichyk/godot.git`.
- Default integration branch: `arena/4.7.2`, based on official `4.7.2-stable`,
  commit `ed1daf0bf001b61586d9930840f2f1394092c079`.
- `feature/shared-multimesh` preserves the original shared-buffer patch series.
- New API work uses `feature/<name>` and a PR targeting `arena/4.7.2`.
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
   its managed SDK/toolchain pins. Build an unmodified upstream control and the feature candidate using matching
   options. Regenerate native/.NET bindings and use a new SDK/package identity.
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

Run `python verify_bundle.py .` inside an extracted bundle before use. To assemble
one from hosted CI artifacts, use `tools/arena/package_windows.py` with the editor
artifact, native receipt artifact, committed fixture, full native SHA, successful
GPU evidence directory and a fresh output path. `--help` lists the arguments.
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
by 16. This contract is development scope, not a claim that v1 is already built
or verified; consume only a matching tagged build and its API/GPU receipts.

The initial candidate passed four compiler cases and a procedural RX 6800
Vulkan/Forward+ contract: 40 lifecycle cycles, 562 numeric viewport audits,
eight rejected attachments, four rejected material assignments, and stable
logical RD bytes/native objects after collection and drainage. See the bundle
receipts for exact native/consumer SHAs. This does not qualify physical VRAM,
renderer FPS, other GPUs or gameplay. Runtime-array length currently makes
Godot's optional re-spirv CPU optimizer fall back to the original SPIR-V;
Vulkan execution passed, while its performance effect remains unmeasured.

References: [Godot .NET builds](https://docs.godotengine.org/en/stable/engine_details/development/compiling/compiling_with_dotnet.html),
[upstream buffer proposal](https://github.com/godotengine/godot-proposals/issues/6989),
[draft implementation discussion](https://github.com/godotengine/godot/pull/109951).

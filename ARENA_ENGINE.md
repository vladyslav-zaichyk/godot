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
4. Build an unmodified upstream control and the feature candidate using matching
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
material. Detach all consumers and wait for their rendering work before freeing
the caller-owned RID. The wrapper never frees that RID. Attachments require the
render thread and a global-RD storage allocation of at least 16 bytes, divisible
by 16. This contract is development scope, not a claim that v1 is already built
or verified; consume only a matching tagged build and its API/GPU receipts.

References: [Godot .NET builds](https://docs.godotengine.org/en/stable/engine_details/development/compiling/compiling_with_dotnet.html),
[upstream buffer proposal](https://github.com/godotengine/godot-proposals/issues/6989),
[draft implementation discussion](https://github.com/godotengine/godot/pull/109951).

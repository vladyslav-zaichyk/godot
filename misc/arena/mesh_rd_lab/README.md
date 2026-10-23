# Native mesh RD access contract (E6a)

This procedural fixture checks the four borrowed native mesh-stream getters and
`Mesh.ArrayFormat.FlagUseStorageBuffer` on a matching custom .NET editor. It is an
API correctness experiment, not an animation cache or performance benchmark.
The fixture uses an ordinary `MeshInstance3D`, Vulkan and Forward+; no Arena
source, assets, BufferRD wrapper or shader-driven MultiMesh is required.

## Fixed checks

- A position-only indexed quad exposes vertex/index RIDs and invalid
  attribute/skin RIDs. Repeated getters preserve identity. The fixture queries
  the format, vertex offset/stride and index stride before compute accesses it.
- An unflagged mesh still exposes native RIDs, but binding its vertex buffer as
  storage is rejected with one precisely expected native diagnostic.
- A separate flagged quad with packed color and four bone weights exposes all
  four streams. Each stream binds directly through `RDUniform` and its untimed
  readback matches a CPU byte oracle for the queried fixed packed layout. This probe is not skinned
  or rendered; it checks stream access and packing only.
- The displayed quad renders before writable storage binding, so the native
  vertex/index arrays already exist. Three compute phases update the **same**
  native position and index RIDs. Positions move left/center/right; the center
  phase also replaces the second triangle with a duplicate of the first.
  Exact native bytes and twelve numerical viewport pixels check GPU producer
  output and ordinary raster consumption. No image files are saved.
- Every consumer-created probe uniform set is released before the probe mesh
  is disposed. All four former borrowed RIDs then fail a storage binding with
  one precise diagnostic each. The consumer never frees a borrowed native RID.

Expected totals are three dispatches, ten buffer readbacks, three viewport
readbacks, twelve pixel samples, five positive storage uniform sets and five
expected native errors. The callback ledger and scoped stdout error/location
pairs must agree. Any extra error, warning or shutdown leak rejects the run.

The companion CPU `[MeshRD]` tests check that storage opt-in changes no packed
payload/offset/stride, including compressed and eight-weight formats, and
preserves the exact 65536/65537-vertex index-width boundary. Runtime here uses
queried uncompressed float3 positions and uint16 indices. This does not extend
the BufferRD uvec4 ABI to native vertex buffers.

## Run after the matching build is available

Invoke explicitly when the GPU is free:

```powershell
python -B run_contract.py --engine <bundle/editor.exe> --sdk-feed <bundle/feed> --output <fresh-evidence-directory> --engine-hash <full-native-commit> --sdk-version <exact-sdk-version>
```

`ArenaGodotSdkVersion` may supply the SDK version instead. Paths are explicit
and source-relative; all restore/build/runtime output, private NuGet settings,
logs and temporary directories stay under the fresh output directory. The
runner stages only the eight project inputs, verifies source/package/API/runtime
assembly identities, and writes `run.json`, `contract.json`, `shutdown.json` and
`acceptance.json`. It does not build Godot or alter global NuGet settings.

An authored fixture is not a runtime PASS. Acceptance requires a matching native
editor/SDK and a successful numerical GPU run. No performance, physical VRAM,
steady lifetime plateau, shadow/depth/TBN, temporal-history, variable-topology,
GPU index-range validation, gameplay or co-op qualification is claimed.

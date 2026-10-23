# re-spirv runtime-array length contract

CPU-only regression suite for the optional re-spirv specialization optimizer.
It compiles only `optimize.cpp` and Godot's vendored `re-spirv.cpp`, using the same
public parser/optimizer API as the Vulkan backend. It never builds, launches or
modifies a Godot editor, SDK bundle, game project or GPU benchmark.

## Run

Use Python 3.13 and a C++17 compiler. On Windows run in an x64 VS2022 developer
prompt; Linux uses `c++` by default. Supply Khronos `spirv-as`, `spirv-val` and
`spirv-dis` through an explicit tool directory. No tools are downloaded or
installed by the runner; tool versions and SHA-256 identities are recorded.

```powershell
python -B tools/arena/re_spirv_array_length/run.py --tools D:/Tools/SPIRV-Tools/bin --output D:/Scratch/respv-contract-01
```

The output directory must be fresh and outside this engine checkout. All
objects, binaries, temporary files, modules, logs and receipts stay there.
`--optimizer-source` can select a pristine reference or deliberately faulty
source file for negative controls. Keep those sources outside the checkout too.

## Proof

Four authored compute modules exercise ordinary code, two live length queries,
an unused query, and a guarded dynamic load. One live-only module uses 16-byte
`uvec4` records matching BufferRD. The helper-function fixture assigns local
numeric ID1 before querying member literal1, exposing accidental literal remaps.

The suite runs 24 combinations of function inlining, dead-code elimination and
boolean specialization. Khronos validation checks each original and optimized
module against Vulkan1.1/SPIR-V1.3. Structural checks follow each live query's
pointer/type/member chain and require dead queries to disappear when applicable.
An independent bounded scalar interpreter follows actual branches and rejects
executed OOB loads. It checks analytic output for varying descriptor lengths,
empty buffers, boundary indices and UINT_MAX: 2880 before/after interpreter
evaluations. Checks remain enabled under optimized Python.

`receipt.json` identifies the exact optimizer, header and contract source bytes,
compiler commands, independent tools, modules and per-case results. A checkout
HEAD alone does not identify dirty sources. CPU validation does not establish
GPU correctness, pipeline creation cost, FPS or compatibility with every shader.
The baseline editor/SDK release remains unchanged; validate a separate native
candidate on a real GPU before integration or publishing a replacement bundle.

## Vendored patch

`thirdparty/re-spirv/patches/0001-support-op-array-length.patch` applies at the
Godot repository root. It only allows the opcode and tracks word3 (Structure)
as an ID; word4 (ArrayMember) stays a literal. The result remains runtime data.
After updating the upstream library, check application against the freshly
extracted upstream source and rerun this suite. Drop the patch only when both
dispatchers are correctly implemented upstream.

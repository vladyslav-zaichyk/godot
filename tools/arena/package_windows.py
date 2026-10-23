"""Assemble only verified engine artifacts and the separately pinned consumer."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('artifact', 'receipts', 'fixture', 'output', 'gpu-evidence'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--source-commit', required=True)
    args = parser.parse_args()
    artifact, receipts, fixture, output = [p.resolve() for p in (args.artifact, args.receipts, args.fixture, args.output)]
    if output.exists() or not re.fullmatch('[0-9a-f]{40}', args.source_commit):
        raise SystemExit('Use a new output directory and a full native source SHA.')
    records = [(p, json.loads(p.read_bytes())) for p in receipts.rglob('manifest.json')]
    records = [(p, value) for p, value in records if value.get('source_commit') == args.source_commit]
    if len(records) != 1:
        raise SystemExit('A unique matching native build receipt is required.')
    receipt_path, record = records[0]
    if any(step['exit_code'] for step in record['steps']):
        raise SystemExit('Build receipt contains a failed step.')
    log = (receipt_path.parent / 'native-contracts.log').read_text(encoding='utf-8', errors='replace')
    count = re.search(r'test cases:\s*(\d+)\s*\|\s*(\d+) passed\s*\|\s*0 failed', log)
    if not count or int(count[1]) != 33 or count[1] != count[2]:
        raise SystemExit('Expected the pinned 29 math and four BufferRD compiler cases to pass.')
    binaries = list((artifact / 'bin').glob('*.arena_dev.mono.exe'))
    if len(binaries) != 1 or sha(binaries[0]) != record['binary_sha256']:
        raise SystemExit('Native editor identity does not match the build receipt.')
    binary = binaries[0]
    wrapper = binary.with_name(binary.stem + '.console.exe')
    api = artifact / 'bin/GodotSharp'
    feed = artifact / '.arena/nuget-source'
    if not wrapper.is_file() or not (api / 'Api/Debug/GodotSharp.dll').is_file():
        raise SystemExit('Editor wrapper or complete managed API is missing.')
    for name, expected in record['sdk_packages'].items():
        if not (feed / name).is_file() or sha(feed / name) != expected:
            raise SystemExit('SDK feed differs from native build receipt: ' + name)
    sdk_files = list(feed.glob('Godot.NET.Sdk.*.nupkg'))
    if len(sdk_files) != 1:
        raise SystemExit('Expected one generated SDK identity.')
    with zipfile.ZipFile(sdk_files[0]) as archive:
        nuspec = next(name for name in archive.namelist() if name.endswith('.nuspec'))
        metadata = ET.fromstring(archive.read(nuspec))
        version = next(node.text for node in metadata.iter() if node.tag.split('}')[-1] == 'version')
    repo = fixture.parents[2]
    relative_fixture = fixture.relative_to(repo).as_posix()
    dirty = subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain', '--untracked-files=all',
                                     '--', relative_fixture, 'ARENA_ENGINE.md'], text=True)
    if dirty.strip():
        raise SystemExit('Commit the exact fixture and handoff documentation before packaging.')
    fixture_commit = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    fixture_files = subprocess.check_output(['git', '-C', str(repo), 'ls-files', '--', relative_fixture], text=True).splitlines()
    if not fixture_files:
        raise SystemExit('The fixture must have committed source files.')
    gpu = args.gpu_evidence.resolve()
    acceptance = json.loads((gpu / 'acceptance.json').read_bytes())
    raw = json.loads((gpu / 'contract.json').read_bytes())
    run = json.loads((gpu / 'run.json').read_bytes())
    if (acceptance.get('passed') is not True or acceptance.get('performance_qualified') is not False
            or acceptance.get('contract_sha256') != sha(gpu / 'contract.json')
            or acceptance.get('shutdown_receipt_sha256') != sha(gpu / 'shutdown.json')
            or run['engine_sha256'] != sha(binary)
            or run['managed_api_sha256'] != sha(api / 'Api/Debug/GodotSharp.dll')):
        raise SystemExit('The successful GPU receipt must identify this exact engine/API bundle.')
    spec = importlib.util.spec_from_file_location('buffer_rd_contract', fixture / 'run_contract.py')
    contract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contract)
    contract.validate(raw, args.source_commit, version)
    audited_shutdown = contract.inspect_shutdown((gpu / 'runtime.log').read_text(encoding='utf-8', errors='replace'),
                                                 raw['expected_native_error_callbacks'])
    if not audited_shutdown['passed'] or json.loads((gpu / 'runtime-exit.json').read_bytes())['exit_code'] != 0:
        raise SystemExit('The entire Vulkan runtime/shutdown must pass.')
    inputs = {name: sha(fixture / 'project' / name) for name in contract.INPUTS}
    if inputs != acceptance['source_sha256'] or inputs != run['source_sha256']:
        raise SystemExit('The packaged fixture inputs differ from the tested inputs.')
    assembly = Path(run['generated_project_output']) / 'mono/temp/bin/Debug/BufferRdLab.dll'
    if (sha(assembly) != run['managed_fixture_assembly_sha256']
            or raw['managed_fixture_assembly_sha256'] != sha(assembly)
            or raw['managed_api_assembly_sha256'] != sha(api / 'Api/Debug/GodotSharp.dll')):
        raise SystemExit('The tested managed assemblies are not the identified build artifacts.')
    output.mkdir(parents=True)
    for path in (binary, wrapper):
        shutil.copy2(path, output / path.name)
    shutil.copytree(api, output / 'GodotSharp')
    shutil.copytree(feed, output / 'NuGetFeed')
    for name in ('LICENSE.txt', 'COPYRIGHT.txt', 'AUTHORS.md'):
        shutil.copy2(artifact / name, output / name)
    shutil.copy2(repo / 'ARENA_ENGINE.md', output / 'ARENA_ENGINE.md')
    shutil.copy2(repo / 'tools/arena/verify_bundle.py', output / 'verify_bundle.py')
    (output / '_sc_').write_bytes(b'')
    shutil.copytree(receipt_path.parent, output / 'evidence/native-build')
    for name in fixture_files:
        source = repo / name
        target = output / 'buffer_rd_lab' / source.relative_to(fixture)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    proof = output / 'evidence/vulkan-contract'
    proof.mkdir()
    for path in gpu.iterdir():
        if path.is_file() and path.suffix in ('.json', '.log'):
            shutil.copy2(path, proof / path.name)
    shutil.copy2(assembly, proof / assembly.name)
    hashes = {p.relative_to(output).as_posix(): sha(p) for p in output.rglob('*') if p.is_file()}
    manifest = {'native_source_commit': args.source_commit, 'fixture_source_commit': fixture_commit,
                'handoff_documentation_commit': fixture_commit,
                'sdk_version': version, 'upstream_base': record['upstream_base'],
                'build_record': 'evidence/native-build/manifest.json', 'artifacts_sha256': hashes,
                'capabilities': {'buffer_rd': 1, 'records': 'std430 uvec4, 16 bytes',
                                 'renderer': 'Forward+', 'backend': 'Vulkan', 'architecture': 'Windows x64'},
                'qualification': {'native_compiler_cases': 4, 'native_math_cases': 29,
                                  'real_gpu': 'numeric correctness and bounded lifetime PASS on ' + raw['gpu_name'],
                                  'performance': 'not qualified'},
                'gpu_receipt': 'evidence/vulkan-contract/acceptance.json',
                'optimizer_note': 'OpArrayLength bypasses the optional Godot re-spirv CPU optimizer; Vulkan uses the original SPIR-V. Performance impact not qualified.',
                'fixture_scope': 'Separately pinned procedural consumer; native executable source identity is authoritative.'}
    (output / 'manifest.json').write_bytes((json.dumps(manifest, indent=2) + '\n').encode())
    print(json.dumps({'candidate': str(output), 'sdk_version': version, 'artifacts': len(hashes)}))


if __name__ == '__main__':
    main()

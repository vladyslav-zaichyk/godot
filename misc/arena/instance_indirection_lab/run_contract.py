"""Build/run the small procedural instance-indirection contract on an explicit bundle.

Never builds the engine, changes global NuGet settings, writes images or measures FPS.
All generated files live in the fresh --output directory. GPU execution is explicit;
do not invoke this while another renderer experiment owns the GPU.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr
import shutil

HERE = Path(__file__).resolve().parent
PROJECT = HERE / 'project'
INPUTS = ('InstanceIndirectionLab.csproj', 'project.godot', 'Main.tscn',
          'InstanceIndirectionContract.cs', 'FixtureErrorLogger.cs', 'Producer.glslinc', 'Consumer.gdshader')
MODES = ('eight_identity', 'shared_identity', 'shader_driven')
PACKAGES = ('Godot.NET.Sdk', 'GodotSharp', 'GodotSharpEditor', 'Godot.SourceGenerators')
DIAGNOSTIC = re.compile(r'\b(?:ERROR:|SCRIPT ERROR:|WARNING:|FATAL(?: ERROR)?[: ]|Unhandled exception)|'
                        r'ObjectDB instances leaked|Orphan StringName|RID allocations', re.I)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, value):
    path.write_bytes((json.dumps(value, indent=2) + '\n').encode())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def source_hashes(root):
    return {name: sha(root / name) for name in INPUTS}


def validate(raw, source, sdk):
    expected = dict(contract='shader-driven-instance-contract-v1', passed=True, engine_hash=source,
                    sdk_version=sdk, driver='vulkan', renderer='forward_plus', initial_view_attaches=24,
                    same_resource_rebinds=3, compute_dispatches=9, viewport_image_readbacks=9,
                    numeric_pixel_samples=144, ownership_probes=18, final_owned_rd_resource_count=0,
                    final_owned_requested_buffer_bytes=0, final_material_count=0, image_files_written=0,
                    gpu_buffer_readbacks=0, performance_window_count=0, performance_qualified=False)
    for key, value in expected.items():
        require(raw.get(key) == value, 'Contract field differs: ' + key)
    require(not raw.get('failure') and raw.get('native_diagnostics') == [], 'Runtime diagnostics/failure.')
    rows = raw['mode_audits']
    require([row['mode'] for row in rows] == list(MODES), 'Incomplete mode order.')
    for row, pools in zip(rows, (8, 1, 0)):
        require((row['views'], row['draw_slot_capacity'], row['stable_agents']) == (8, 8, 2), 'Different fixture scale.')
        require(row['caller_transform_buffer_count'] == pools and row['caller_transform_input_bytes'] == pools * 384
                and row['exposed_transform_rid_count'] == pools, 'Caller transform inputs/exposed RID count differ.')
        require((row['ids_bytes'], row['pose_bytes'], row['command_bytes']) == (256, 128, 160), 'Fixed buffers differ.')
        require(row['caller_owned_requested_buffer_bytes'] == 544 + pools * 384, 'Caller-owned byte accounting differs.')
    attaches = raw['attach_audits']
    require(len(attaches) == 24, 'Incomplete actual attach ledger.')
    for mode, pools in zip(MODES, (8, 1, 0)):
        rows = [row for row in attaches if row['mode'] == mode]
        require(Counter(row['view'] for row in rows) == Counter(range(8)), 'Missing/duplicate view.')
        require(len({row['multimesh'] for row in rows}) == 8 and all(row['multimesh'] for row in rows), 'Different views not attached.')
        require(len({row['command_rid'] for row in rows}) == 1 and rows[0]['command_rid'], 'Command pool not shared.')
        transforms = {row['transform_rid'] for row in rows if row['transform_rid']}
        require(len(transforms) == pools, 'Identity pool aliasing differs.')
        for row in rows:
            require(row['capacity'] == 8 and row['first_instance'] == 3 and row['shader_driven'] == (mode == 'shader_driven'),
                    'Attach mode/span differs.')
            require(bool(row['transform_rid']) == (pools != 0), 'Unexpected transform RID.')
    rebinds = raw['rebind_audits']
    require(Counter(row['mode'] for row in rebinds) == Counter(MODES), 'Incomplete rebind cases.')
    require(all(row['invalid_capacity_rejected'] and row['previous_binding_preserved'] and row['valid_rebind_passed']
                for row in rebinds), 'Rebind preservation failed.')
    for row in rebinds:
        cases = ['zero_capacity'] + (['unaligned_command_offset', 'short_command_range'] if row['mode'] == 'shader_driven' else [])
        require(Counter(row['negative_cases']) == Counter(cases), 'Incomplete quiet attach-parameter rejections.')
    pixels = raw['pixel_audits']
    keys = Counter((row['mode'], row['phase'], row['view'], row['kind']) for row in pixels)
    expected_keys = Counter((mode, phase, view, kind) for mode in MODES for phase in range(3)
                            for view in range(8) for kind in ('agent', 'vacated'))
    require(keys == expected_keys, 'Incomplete numerical raster cases.')
    images = {}
    for row in pixels:
        phase, view = row['phase'], row['view']
        agent = (view & 1) ^ (phase != 0)
        local_x = (-0.5 if agent == 0 else 0.5) * (-1 if phase == 2 else 1)
        color = (4 if agent == 0 else 3) if phase == 2 else (1 if agent == 0 else 2)
        if row['kind'] == 'vacated':
            local_x, color = -local_x, 0
        x = math.floor(((-3 + (view % 4) * 2 + local_x) / 8 + 0.5) * 128)
        y = 16 if view < 4 else 48
        rgb = [255 if color & (1 << c) else 0 for c in range(3)]
        require(row['agent'] == agent and (row['x'], row['y']) == (x, y) and row['expected_rgb'] == rgb,
                'Numerical oracle coordinates/expectation differ.')
        require(len(row['actual_rgb']) == 3 and all(abs(a - b) <= 1 for a, b in zip(row['actual_rgb'], rgb)), 'Numeric pixel differs.')
        key = row['mode'], phase
        require(re.fullmatch('[0-9a-f]{64}', row['image_sha256']) is not None, 'Invalid raster identity.')
        require(images.setdefault(key, row['image_sha256']) == row['image_sha256'], 'Conflicting raster hashes.')
    for phase in range(3):
        require(len({images[mode, phase] for mode in MODES}) == 1, 'Three-mode raster differs.')
    require(len({images[MODES[0], phase] for phase in range(3)}) == 3, 'Reorder/update did not change the raster.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', required=True, type=Path)
    parser.add_argument('--sdk-feed', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--engine-hash', required=True)
    parser.add_argument('--sdk-version', default=os.environ.get('ArenaGodotSdkVersion'))
    parser.add_argument('--dotnet', default='dotnet')
    parser.add_argument('--timeout', type=int, default=300)
    args = parser.parse_args()
    engine, feed, output = args.engine.resolve(), args.sdk_feed.resolve(), args.output.resolve()
    if not engine.is_file() or not feed.is_dir() or not args.sdk_version or args.timeout < 1:
        parser.error('Existing matching editor/feed, exact SDK version and positive timeout are required.')
    if not re.fullmatch('[0-9a-f]{40}', args.engine_hash) or output.exists() or output == HERE or HERE in output.parents:
        parser.error('Use a full lowercase native SHA and a fresh output directory outside this fixture.')
    api = engine.parent / 'GodotSharp/Api/Debug/GodotSharp.dll'
    if not api.is_file():
        parser.error('The matching complete GodotSharp tree must accompany the editor.')
    output.mkdir(parents=True)
    work, project = output / 'work', output / 'work/project'
    project.mkdir(parents=True)
    before = source_hashes(PROJECT)
    for name in INPUTS:
        shutil.copy2(PROJECT / name, project / name)
    env = os.environ.copy()
    for key in ('MSBuildSDKsPath', 'MSBUILD_EXE_PATH', 'DOTNET_MSBUILD_SDK_RESOLVER_SDKS_DIR',
                'DOTNET_MSBUILD_SDK_RESOLVER_SDKS_VER', 'NUGET_FALLBACK_PACKAGES', 'NUGET_RESTORE_MSBUILD_ARGS'):
        env.pop(key, None)
    for key, folder in {'TEMP': 'tmp', 'TMP': 'tmp', 'APPDATA': 'appdata', 'LOCALAPPDATA': 'localappdata',
                        'DOTNET_CLI_HOME': 'dotnet-home', 'NUGET_PACKAGES': 'nuget-packages',
                        'NUGET_HTTP_CACHE_PATH': 'nuget-http', 'NUGET_PLUGINS_CACHE_PATH': 'nuget-plugins',
                        'NUGET_SCRATCH': 'nuget-scratch'}.items():
        path = work / folder
        path.mkdir()
        env[key] = str(path)
    config = project / 'NuGet.Config'
    config.write_bytes(('<?xml version="1.0" encoding="utf-8"?><configuration><packageSources><clear />'
        '<add key="Local" value=' + quoteattr(feed.as_posix()) + ' />'
        '<add key="nuget.org" value="https://api.nuget.org/v3/index.json" /></packageSources>'
        '<packageSourceMapping><clear /><packageSource key="Local"><package pattern="Godot.*" />'
        '<package pattern="GodotSharp" /><package pattern="GodotSharpEditor" /></packageSource>'
        '<packageSource key="nuget.org"><package pattern="*" /></packageSource></packageSourceMapping>'
        '</configuration>\n').encode())
    env.update(ArenaGodotSdkVersion=args.sdk_version, RestoreConfigFile=str(config),
               DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1',
               DOTNET_CLI_USE_MSBUILD_SERVER='0', MSBUILDDISABLENODEREUSE='1', UseSharedCompilation='false',
               DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE='1', DOTNET_GENERATE_ASPNET_CERTIFICATE='false')
    run = dict(engine=str(engine), engine_sha256=sha(engine), engine_source_hash=args.engine_hash,
               managed_api_sha256=sha(api), sdk_version=args.sdk_version, sdk_feed=str(feed), source_sha256=before,
               runner_sha256=sha(Path(__file__)), project=str(project), commands=[], scope='Numeric GPU correctness only; no performance qualification.')
    dump(output / 'run.json', run)

    def execute(label, command):
        log = output / (label + '.log')
        step = dict(label=label, command=list(map(str, command)), exit_code=None)
        run['commands'].append(step)
        try:
            with log.open('wb') as stream:
                process = subprocess.run(step['command'], cwd=project, env=env, stdin=subprocess.DEVNULL,
                    stdout=stream, stderr=subprocess.STDOUT, timeout=args.timeout,
                    creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name == 'nt' else 0)
            step['exit_code'] = process.returncode
            require(process.returncode == 0, label + ' failed; inspect ' + str(log))
        finally:
            if log.is_file():
                step['log_sha256'] = sha(log)
            dump(output / 'run.json', run)
            dump(output / (label + '-exit.json'), step)

    try:
        packages = {}
        import zipfile
        for path in feed.glob('*.nupkg'):
            with zipfile.ZipFile(path) as archive:
                nuspec = ET.fromstring(archive.read(next(n for n in archive.namelist() if n.endswith('.nuspec'))))
                metadata = {node.tag.split('}')[-1]: node.text for node in nuspec.iter()}
                name = metadata.get('id')
                if name in PACKAGES and metadata.get('version') == args.sdk_version:
                    require(name not in packages, 'Ambiguous generated package: ' + name)
                    packages[name] = dict(path=str(path), sha256=sha(path))
        require(set(packages) == set(PACKAGES), 'Exact generated SDK/API packages are required.')
        run['sdk_packages'] = packages
        execute('dotnet-version', [args.dotnet, '--version'])
        obj = (work / 'obj').as_posix() + '/'
        properties = ['-p:ArenaGodotSdkVersion=' + args.sdk_version, '-p:BaseIntermediateOutputPath=' + obj,
                      '-p:MSBuildProjectExtensionsPath=' + obj, '-p:IntermediateOutputPath=' + obj + 'Debug/',
                      '-p:UseSharedCompilation=false', '-p:ImportDirectoryBuildProps=false', '-p:ImportDirectoryBuildTargets=false']
        csproj = project / 'InstanceIndirectionLab.csproj'
        execute('restore', [args.dotnet, 'restore', csproj, '--configfile', config, '--disable-parallel', *properties])
        execute('build', [args.dotnet, 'build', csproj, '--configuration', 'Debug', '--no-restore', '--no-incremental',
                          '--disable-build-servers', '-maxcpucount:2', *properties])
        assets = json.loads((Path(obj) / 'project.assets.json').read_bytes())
        for name in PACKAGES[1:]:
            require([v for v in assets['libraries'] if v.startswith(name + '/')] == [name + '/' + args.sdk_version], 'Resolved API version differs: ' + name)
        for name, package in packages.items():
            cached = work / 'nuget-packages' / name.lower() / args.sdk_version.lower() / (name.lower() + '.' + args.sdk_version.lower() + '.nupkg')
            require(cached.is_file() and sha(cached) == package['sha256'], 'Restored package differs from feed: ' + name)
        assembly = project / '.godot/mono/temp/bin/Debug/InstanceIndirectionLab.dll'
        run['managed_fixture_assembly_sha256'] = sha(assembly)
        require(source_hashes(PROJECT) == before == source_hashes(project), 'Fixture source changed while building.')
        record = output / 'contract.json'
        execute('runtime', [engine, '--path', project, '--rendering-method', 'forward_plus', '--rendering-driver', 'vulkan',
                           '--', '--record=' + str(record), '--expected-engine-hash=' + args.engine_hash,
                           '--expected-sdk-version=' + args.sdk_version])
        diagnostics = [dict(line=i, text=line) for i, line in enumerate((output / 'runtime.log').read_text(encoding='utf-8', errors='replace').splitlines(), 1) if DIAGNOSTIC.search(line)]
        shutdown = dict(passed=not diagnostics, diagnostics=diagnostics, runtime_log_sha256=sha(output / 'runtime.log'))
        dump(output / 'shutdown.json', shutdown)
        require(shutdown['passed'], 'Runtime/shutdown diagnostics detected.')
        raw = json.loads(record.read_bytes())
        validate(raw, args.engine_hash, args.sdk_version)
        require(raw['source_sha256'] == before, 'Runtime source identity differs.')
        require(raw['managed_fixture_assembly_sha256'] == run['managed_fixture_assembly_sha256'] == sha(assembly), 'Runtime fixture assembly differs.')
        require(raw['managed_api_assembly_sha256'] == run['managed_api_sha256'] == sha(api), 'Runtime managed API differs.')
        require(sha(engine) == run['engine_sha256'] and source_hashes(PROJECT) == before == source_hashes(project), 'Engine or source changed.')
        require(all(sha(Path(p['path'])) == p['sha256'] for p in packages.values()), 'Feed packages changed.')
        dump(output / 'acceptance.json', dict(passed=True, performance_qualified=False, contract_sha256=sha(record),
             shutdown_sha256=sha(output / 'shutdown.json'), source_sha256=before, modes=list(MODES), caller_transform_input_bytes=[3072, 384, 0]))
        print('Instance indirection numeric contract PASS: ' + str(output / 'acceptance.json'))
        return 0
    except Exception as error:
        dump(output / 'acceptance.json', dict(passed=False, performance_qualified=False, failure=str(error)))
        print('Instance indirection contract FAIL: ' + str(error))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

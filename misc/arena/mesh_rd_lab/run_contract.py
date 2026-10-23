"""Build/run the small procedural native mesh-stream contract on an explicit bundle.

Never builds the engine, changes global NuGet settings, writes images or measures FPS.
All generated files live in the fresh --output directory. GPU execution is explicit;
do not invoke this while another renderer experiment owns the GPU.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr
import shutil

HERE = Path(__file__).resolve().parent
PROJECT = HERE / 'project'
INPUTS = ('MeshRdLab.csproj', 'project.godot', 'Main.tscn', 'MeshRdContract.cs',
          'MeshRdErrorLogger.cs', 'Producer.glslinc', 'Probe.glslinc', 'Consumer.gdshader')
STREAMS = ('vertex', 'attribute', 'skin', 'index')
GUARDS = {'unflagged_vertex': 'Vertex buffer supplied (binding: 0) was not created with storage flag.',
          **{'freed_' + stream: 'Storage buffer supplied (binding: 0) is invalid.' for stream in STREAMS}}
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
    import struct
    expected = dict(contract='mesh-rd-numeric-contract-v1', passed=True, engine_hash=source,
                    sdk_version=sdk, driver='vulkan', renderer='forward_plus', compute_dispatches=3,
                    positive_storage_bindings=5, gpu_buffer_readbacks=10, viewport_image_readbacks=3,
                    numeric_pixel_samples=12, final_owned_rd_resource_count=0,
                    engine_owned_borrowed_buffers_freed_by_consumer=0, image_files_written=0,
                    performance_window_count=0, performance_qualified=False)
    for key, value in expected.items():
        require(raw.get(key) == value, 'Contract field differs: ' + key)
    require(not raw.get('failure') and raw.get('unexpected_native_diagnostics') == [], 'Runtime diagnostics/failure.')
    callbacks = raw['expected_native_error_callbacks']
    require(Counter(row['test'] for row in callbacks) == Counter(GUARDS.keys()), 'Incomplete expected native error ledger.')
    for row in callbacks:
        file = row['file'].replace(chr(92), '/')
        require(row['rationale'] == GUARDS[row['test']] and row['error_type'] == 0 and row['code'] and
                row['function'] in ('uniform_set_create', 'RenderingDevice::uniform_set_create') and
                (file == 'servers/rendering/rendering_device.cpp' or file.endswith('/servers/rendering/rendering_device.cpp')) and
                type(row['line']) is int and row['line'] > 0, 'Unexpected native diagnostic location/rationale.')
    streams = raw['stream_audits']
    require(len(streams) == 6, 'Incomplete stream presence/packed byte audits.')
    main = next(row for row in streams if row['test'] == 'main_presence')
    unflagged = next(row for row in streams if row['test'] == 'unflagged_presence')
    for row, storage in ((main, True), (unflagged, False)):
        ids = row['rids']
        require(row['storage_flag'] is storage and len(ids) == 4 and ids[0] and ids[3] and not ids[1] and not ids[2]
                and ids[0] != ids[3], 'Absent/present native stream RID contract differs.')
    require((main['vertex_count'], main['vertex_stride'], main['vertex_offset'], main['index_stride']) == (4, 12, 0, 2), 'Native layout differs.')
    require(main['format'] & (1 << 30), 'Storage opt-in bit missing.')
    populated = [row for row in streams if row['test'] == 'populated_stream']
    require(Counter(row['stream'] for row in populated) == Counter(STREAMS), 'Four getter coverage differs.')
    require(len({row['rid'] for row in populated}) == 4 and all(row['rid'] for row in populated), 'Populated stream identities differ.')
    probe_payloads = dict(
        vertex=struct.pack('<12f', -0.7, -0.7, 0, 0.7, -0.7, 0, 0.7, 0.7, 0, -0.7, 0.7, 0),
        attribute=bytes((255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 255, 255, 255, 255)),
        skin=struct.pack('<8H', 0, 0, 0, 0, 65535, 0, 0, 0) * 4,
        index=struct.pack('<6H', 0, 1, 2, 0, 2, 3))
    for row in populated:
        require(row['storage_flag'] is True and row['bytes'] == dict(vertex=48, attribute=16, skin=64, index=12)[row['stream']]
                and row['sha256'] == hashlib.sha256(probe_payloads[row['stream']]).hexdigest(), 'Packed native stream payload differs.')
    phases = raw['phase_audits']
    require([row['phase'] for row in phases] == [0, 1, 2], 'Missing repeated native updates.')
    f32 = lambda value: struct.unpack('<f', struct.pack('<f', value))[0]
    for row in phases:
        phase, center = row['phase'], -2 + 2 * row['phase']
        lo, hi, edge = f32(center - f32(0.7)), f32(center + f32(0.7)), f32(0.7)
        vertices = struct.pack('<12f', lo, -edge, 0, hi, -edge, 0, hi, edge, 0, lo, edge, 0)
        indices = struct.pack('<6H', 0, 1, 2, 0, 1 if phase == 1 else 2, 2 if phase == 1 else 3)
        require(row['vertex_rid'] == main['rids'][0] and row['index_rid'] == main['rids'][3]
                and (row['vertex_bytes'], row['index_bytes']) == (48, 12), 'Compute replaced native resource identities.')
        require(row['vertices_sha256'] == hashlib.sha256(vertices).hexdigest() and
                row['indices_sha256'] == hashlib.sha256(indices).hexdigest(), 'Independent compute byte oracle differs.')
    pixels = raw['pixel_audits']
    expected_keys = Counter((phase, kind) for phase in range(3) for kind in
                            ('lower_right', 'upper_left', *('other_' + str(other) for other in range(3) if other != phase)))
    require(Counter((row['phase'], row['kind']) for row in pixels) == expected_keys, 'Incomplete numerical raster checks.')
    images = {}
    for row in pixels:
        phase, kind = row['phase'], row['kind']
        if kind == 'lower_right':
            xy, white = (37 + 32 * phase, 37), True
        elif kind == 'upper_left':
            xy, white = (26 + 32 * phase, 26), phase != 1
        else:
            xy, white = (32 + 32 * int(kind[6:]), 32), False
        rgb = [255 if white else 0] * 3
        require((row['x'], row['y']) == xy and row['expected_rgb'] == rgb and len(row['actual_rgb']) == 3 and
                all(abs(a-b) <= 1 for a,b in zip(row['actual_rgb'], rgb)), 'Independent raster oracle differs.')
        require(re.fullmatch('[0-9a-f]{64}', row['image_sha256']) is not None and
                images.setdefault(phase, row['image_sha256']) == row['image_sha256'], 'Conflicting raster identity.')
    require(len(set(images.values())) == 3, 'Repeated GPU updates did not change raster.')
    retirements = raw['retirement_audits']
    require(Counter(row['stream'] for row in retirements) == Counter(STREAMS), 'Incomplete engine-owned lifetime checks.')
    for row in retirements:
        original = next(item for item in populated if item['stream'] == row['stream'])
        require(row['borrowed_rid'] == original['rid'] and row['mesh_disposed'] is True and
                row['consumer_uniform_set_released_before_mesh'] is True and row['stale_storage_bind_rejected'] is True,
                'Borrowed lifetime contract differs.')


def inspect_shutdown(console, callbacks):
    by_name = {row['test']: row for row in callbacks}
    failures, expected = [], []
    active, seen = None, set()
    error_count = location_count = 0
    for number, line in enumerate(console.splitlines(), 1):
        if line.startswith('MESH_RD_EXPECTED_GUARD_BEGIN='):
            name = line.split('=', 1)[1]
            if active is not None or name not in by_name or name in seen:
                failures.append(dict(line=number, text='Invalid expected-error begin: ' + line))
            active, error_count, location_count = name, 0, 0
            continue
        if line.startswith('MESH_RD_EXPECTED_GUARD_END='):
            name = line.split('=', 1)[1]
            if name != active or error_count != 1 or location_count != 1:
                failures.append(dict(line=number, text='Expected exactly one native error/location pair: ' + name))
            seen.add(name)
            active = None
            continue
        callback = by_name.get(active, {})
        if callback and line.strip() == f"at: {callback['function']} ({callback['file']}:{callback['line']})":
            location_count += 1
        if DIAGNOSTIC.search(line):
            if callback and callback.get('rationale') == GUARDS.get(active) and line == 'ERROR: ' + callback['rationale'] and error_count == 0:
                error_count += 1
                expected.append(dict(line=number, test=active, text=line))
            else:
                failures.append(dict(line=number, text=line))
    if active is not None or seen != set(GUARDS) or len(callbacks) != 5 or len(expected) != 5:
        failures.append(dict(line=0, text='Incomplete bounded native diagnostic scopes.'))
    return dict(passed=not failures, expected_native_error_count=len(expected), expected_diagnostics=expected, diagnostics=failures)


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
        csproj = project / 'MeshRdLab.csproj'
        execute('restore', [args.dotnet, 'restore', csproj, '--configfile', config, '--disable-parallel', *properties])
        execute('build', [args.dotnet, 'build', csproj, '--configuration', 'Debug', '--no-restore', '--no-incremental',
                          '--disable-build-servers', '-maxcpucount:2', *properties])
        assets = json.loads((Path(obj) / 'project.assets.json').read_bytes())
        for name in PACKAGES[1:]:
            require([v for v in assets['libraries'] if v.startswith(name + '/')] == [name + '/' + args.sdk_version], 'Resolved API version differs: ' + name)
        for name, package in packages.items():
            cached = work / 'nuget-packages' / name.lower() / args.sdk_version.lower() / (name.lower() + '.' + args.sdk_version.lower() + '.nupkg')
            require(cached.is_file() and sha(cached) == package['sha256'], 'Restored package differs from feed: ' + name)
        assembly = project / '.godot/mono/temp/bin/Debug/MeshRdLab.dll'
        run['managed_fixture_assembly_sha256'] = sha(assembly)
        require(source_hashes(PROJECT) == before == source_hashes(project), 'Fixture source changed while building.')
        record = output / 'contract.json'
        execute('runtime', [engine, '--path', project, '--rendering-method', 'forward_plus', '--rendering-driver', 'vulkan',
                           '--', '--record=' + str(record), '--expected-engine-hash=' + args.engine_hash,
                           '--expected-sdk-version=' + args.sdk_version])
        raw = json.loads(record.read_bytes())
        shutdown = inspect_shutdown((output / 'runtime.log').read_text(encoding='utf-8', errors='replace'), raw.get('expected_native_error_callbacks', []))
        shutdown['runtime_log_sha256'] = sha(output / 'runtime.log')
        dump(output / 'shutdown.json', shutdown)
        require(shutdown['passed'], 'Runtime/shutdown diagnostics detected.')
        validate(raw, args.engine_hash, args.sdk_version)
        require(raw['source_sha256'] == before, 'Runtime source identity differs.')
        require(raw['managed_fixture_assembly_sha256'] == run['managed_fixture_assembly_sha256'] == sha(assembly), 'Runtime fixture assembly differs.')
        require(raw['managed_api_assembly_sha256'] == run['managed_api_sha256'] == sha(api), 'Runtime managed API differs.')
        require(sha(engine) == run['engine_sha256'] and source_hashes(PROJECT) == before == source_hashes(project), 'Engine or source changed.')
        require(all(sha(Path(p['path'])) == p['sha256'] for p in packages.values()), 'Feed packages changed.')
        dump(output / 'acceptance.json', dict(passed=True, performance_qualified=False, contract_sha256=sha(record),
             shutdown_sha256=sha(output / 'shutdown.json'), source_sha256=before, phases=3, retired_engine_owned_streams=list(STREAMS), expected_native_errors=5))
        print('Mesh RD numeric contract PASS: ' + str(output / 'acceptance.json'))
        return 0
    except Exception as error:
        dump(output / 'acceptance.json', dict(passed=False, performance_qualified=False, failure=str(error)))
        print('Mesh RD contract FAIL: ' + str(error))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

"""Compile the small E9 consumer against an exact matching bundle.

Compilation is the default and does not launch Godot. --run-gpu explicitly opens
Vulkan/Forward+ after the parallel A/B owner releases the GPU. This is a numerical
subset, not a performance benchmark. Generated project, private NuGet caches,
user data, TEMP and logs stay inside one fresh --output directory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr
import zipfile

HERE = Path(__file__).resolve().parent
PROJECT = HERE / 'project'
INPUTS = ('MulticommandLab.csproj', 'project.godot', 'Main.tscn', 'MulticommandContract.cs',
          'ContractErrorLogger.cs', 'Consumer.gdshader', 'Producer.glslinc')
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


def validate(raw, args):
    for key, value in dict(contract='multicommand-numeric-contract-v1', passed=True,
            engine_hash=args.engine_hash, sdk_version=args.sdk_version, driver='vulkan',
            renderer='forward_plus', layout=args.layout, archetypes=args.archetypes, lods=args.lods,
            segments=args.segments, units=args.units, final_owned_rd_resource_count=0,
            final_owned_requested_buffer_bytes=0, image_files_written=0,
            performance_window_count=0, performance_qualified=False).items():
        require(raw.get(key) == value, 'Contract field differs: ' + key)
    require(not raw.get('failure') and raw.get('native_diagnostics') == [], 'Runtime diagnostics/failure.')
    segments_set = set((1, args.segments))
    expected_keys = {(control, segments, stride) for control in ('C0', 'C1', 'C2')
                     for segments in segments_set for stride in (20, 32)}
    count = len(expected_keys)
    key = lambda row: (row['control'], row['segments'], row['stride'])
    cases = {key(row): row for row in raw['cases']}
    rebinds = {key(row): row for row in raw['rebinds']}
    require(len(raw['cases']) == count and set(cases) == expected_keys
            and len(raw['rebinds']) == count and set(rebinds) == expected_keys
            and len(raw['phases']) == count * 4, 'Incomplete/duplicate case, phase or rebind coverage.')
    require(raw['compute_dispatches'] == count * 4 and raw['gpu_buffer_readbacks'] == count * 8
            and raw['caller_ownership_probes'] == count * 3, 'Incomplete producer/readback/ownership ledger.')
    require(all(p['final_command_checked'] for p in raw['phases']), 'Missing final command validation.')
    reject_keys = {'negative_capacity', 'capacity_above_int32', 'negative_offset', 'offset_above_int32',
                   'unaligned_offset', 'zero_count', 'negative_count', 'count_above_bound', 'short_stride',
                   'unaligned_stride', 'stride_above_int32', 'last_command_out_of_buffer',
                   'offset_plus_command_out_of_buffer', 'invalid_mode'}
    require(all(r['transactional'] and r['count_one_multidraw'] and r['old_api_resets_count_stride']
            and r['old_api_resets_mode'] and r['history_false_and_no_transform_bank_preserved']
            and len(r['rejected']) == len(reject_keys) and set(r['rejected']) == reject_keys
            for r in raw['rebinds']), 'Incomplete transactional/numeric guards.')
    pixel_samples = 0
    for (control, segments, stride), row in cases.items():
        packed = args.layout == 'packed' and control != 'C0'
        owners = args.archetypes if packed else args.archetypes * args.lods
        commands = owners * (args.lods * segments if packed else 1 if control == 'C0' else segments)
        calls = owners if control in ('C0', 'C2') else commands
        require(row['layout'] == args.layout and row['native_owner_count'] == owners
                and row['allocated_command_slots'] == commands
                and row['expected_rd_calls_per_color_pass'] == calls and row['command_prefix_bytes'] == 16,
                'Owner/command/predicted submission ledger differs.')
        probes = {0, args.units * (segments - 1), args.archetypes * args.lods * segments * args.units - 1}
        pixel_samples += len(probes) * 4
    require(raw['numeric_pixel_samples'] == pixel_samples, 'Incomplete numerical pixel sampling.')
    phases = {(r['control'], r['segments'], r['stride'], r['phase']): r for r in raw['phases']}
    expected_phases = {(*entry, phase) for entry in expected_keys for phase in range(4)}
    require(set(phases) == expected_phases, 'Missing/duplicate phase identity.')
    for segments in segments_set:
        for stride in (20, 32):
            for phase in range(4):
                admitted_segments = sum(phase != 3 and (phase != 1 or s % 2 == 0 or s == segments - 1)
                                        for s in range(segments))
                expected = args.archetypes * args.lods * admitted_segments * args.units
                a, b = phases['C1', segments, stride, phase], phases['C2', segments, stride, phase]
                for control in ('C0', 'C1', 'C2'):
                    row = phases[control, segments, stride, phase]
                    nonempty = args.archetypes * args.lods * ((1 if admitted_segments else 0)
                                                           if control == 'C0' else admitted_segments)
                    require(row['admitted_identities'] == expected and row['nonempty_command_slots'] == nonempty,
                            'Producer admission/nonempty-command coverage differs.')
                for key in ('ids_sha256', 'command_sha256', 'numeric_raster_sha256'):
                    require(a[key] == b[key], 'C1/C2 producer/raster identity differs: ' + key)
    require(raw.get('not_qualified'), 'Report must retain unsupported-gate disclosures.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', required=True, type=Path)
    parser.add_argument('--sdk-feed', required=True, type=Path)
    parser.add_argument('--sdk-version', default=os.environ.get('ArenaGodotSdkVersion'))
    parser.add_argument('--engine-hash', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--dotnet', default='dotnet')
    parser.add_argument('--timeout', type=int, default=300)
    parser.add_argument('--layout', choices=('separate', 'packed'), default='separate')
    parser.add_argument('--archetypes', type=int, default=32)
    parser.add_argument('--lods', type=int, default=4)
    parser.add_argument('--segments', type=int, default=16)
    parser.add_argument('--units', type=int, default=4)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--run-gpu', action='store_true', help='Explicit GPU launch; wait for the parallel A/B owner.')
    mode.add_argument('--compile-only', action='store_true', help='Default: build the managed consumer without launching Godot.')
    args = parser.parse_args()
    engine, feed, output = args.engine.resolve(), args.sdk_feed.resolve(), args.output.resolve()
    checkout = next((p for p in HERE.parents if (p / '.git').exists()
                     and (p / 'SConstruct').is_file() and (p / 'version.py').is_file()), HERE)
    protected = (checkout, engine.parent, feed)
    if output.exists() or any(output == root or root in output.parents for root in protected):
        parser.error('Use one fresh output directory outside the Godot source, editor and SDK feed.')
    if not engine.is_file() or not feed.is_dir() or not args.sdk_version or args.timeout < 1 or not (1 <= args.archetypes <= 32 and 1 <= args.lods <= 4 and 1 <= args.segments <= 1024 and 1 <= args.units <= 256):
        parser.error('Matching editor/feed/SDK, positive timeout and bounded scene arguments are required.')
    if args.archetypes * args.lods * args.segments * args.units > 1_000_000 or (args.layout == 'packed' and args.lods * args.segments > 1024):
        parser.error('Population <=1,000,000 and <=1024 commands per native owner are required.')
    if not re.fullmatch('[0-9a-f]{40}', args.engine_hash):
        parser.error('Use the exact full lowercase native source SHA.')
    api = engine.parent / 'GodotSharp/Api/Debug/GodotSharp.dll'
    release_api = engine.parent / 'GodotSharp/Api/Release/GodotSharp.dll'
    api_xml = api.with_suffix('.xml')
    if not api.is_file() or not release_api.is_file() or not api_xml.is_file() or 'MultimeshAttachSharedDrawCommandSpan' not in api_xml.read_text(encoding='utf-8'):
        parser.error('Complete matching E9 native/managed bundle required; earlier SDKs are incompatible.')
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
        path.mkdir(exist_ok=True)
        env[key] = str(path)
    config = project / 'NuGet.Config'
    config.write_bytes(('<?xml version="1.0" encoding="utf-8"?><configuration><packageSources><clear />'
        '<add key="Local" value=' + quoteattr(feed.as_posix()) + ' />'
        '<add key="nuget.org" value="https://api.nuget.org/v3/index.json" /></packageSources>'
        '<packageSourceMapping><clear /><packageSource key="Local"><package pattern="Godot.*" />'
        '<package pattern="GodotSharp" /><package pattern="GodotSharpEditor" /></packageSource>'
        '<packageSource key="nuget.org"><package pattern="*" /></packageSource></packageSourceMapping></configuration>\n').encode())
    env.update(ArenaGodotSdkVersion=args.sdk_version, RestoreConfigFile=str(config), DOTNET_CLI_TELEMETRY_OPTOUT='1',
               DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1', DOTNET_CLI_USE_MSBUILD_SERVER='0', MSBUILDDISABLENODEREUSE='1',
               UseSharedCompilation='false', DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE='1', DOTNET_GENERATE_ASPNET_CERTIFICATE='false')
    run = dict(engine=str(engine), engine_sha256=sha(engine), engine_source_hash=args.engine_hash,
               managed_api_sha256=sha(api), managed_release_api_sha256=sha(release_api),
               sdk_version=args.sdk_version, source_sha256=before,
               runner_sha256=sha(Path(__file__)), project=str(project), commands=[], gpu_requested=args.run_gpu,
               fixture_arguments=dict(layout=args.layout, archetypes=args.archetypes, lods=args.lods, segments=args.segments, units=args.units))
    dump(output / 'run.json', run)

    def execute(label, command):
        log = output / (label + '.log')
        step = dict(label=label, command=list(map(str, command)), exit_code=None)
        run['commands'].append(step)
        try:
            with log.open('wb') as stream:
                process = subprocess.Popen(step['command'], cwd=project, env=env, stdin=subprocess.DEVNULL,
                    stdout=stream, stderr=subprocess.STDOUT, start_new_session=os.name != 'nt',
                    creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name == 'nt' else 0)
                step['owned_process_id'] = process.pid
                try:
                    process.wait(timeout=args.timeout)
                except BaseException:
                    # Only this invocation's owned process tree; never a name-based kill.
                    if process.poll() is None:
                        if os.name == 'nt':
                            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
                        else:
                            os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=30)
                    raise
            step['exit_code'] = process.returncode
            require(process.returncode == 0, label + ' failed; inspect ' + str(log))
        finally:
            if log.is_file():
                step['log_sha256'] = sha(log)
            dump(output / 'run.json', run)
            dump(output / (label + '-exit.json'), step)

    try:
        packages = {}
        for path in feed.glob('*.nupkg'):
            with zipfile.ZipFile(path) as archive:
                nuspec = ET.fromstring(archive.read(next(n for n in archive.namelist() if n.endswith('.nuspec'))))
                metadata = {node.tag.split('}')[-1]: node.text for node in nuspec.iter()}
                name = metadata.get('id')
                if name in PACKAGES and metadata.get('version') == args.sdk_version:
                    require(name not in packages, 'Ambiguous generated package: ' + name)
                    packages[name] = dict(path=str(path), sha256=sha(path))
        require(set(packages) == set(PACKAGES), 'Exact generated SDK/API packages required.')
        with zipfile.ZipFile(packages['GodotSharp']['path']) as archive:
            dlls = [n for n in archive.namelist() if n.startswith('lib/') and n.endswith('/GodotSharp.dll')]
            require(len(dlls) == 1 and hashlib.sha256(archive.read(dlls[0])).hexdigest() == run['managed_release_api_sha256'],
                    'SDK GodotSharp payload differs from the native bundle Release API.')
        run['sdk_packages'] = packages
        execute('dotnet-version', [args.dotnet, '--version'])
        obj = (work / 'obj').as_posix() + '/'
        properties = ['-p:ArenaGodotSdkVersion=' + args.sdk_version, '-p:BaseIntermediateOutputPath=' + obj,
                      '-p:MSBuildProjectExtensionsPath=' + obj, '-p:IntermediateOutputPath=' + obj + 'Debug/',
                      '-p:UseSharedCompilation=false', '-p:ImportDirectoryBuildProps=false', '-p:ImportDirectoryBuildTargets=false']
        csproj = project / 'MulticommandLab.csproj'
        execute('restore', [args.dotnet, 'restore', csproj, '--configfile', config, '--disable-parallel', *properties])
        execute('build', [args.dotnet, 'build', csproj, '--configuration', 'Debug', '--no-restore', '--no-incremental',
                          '--disable-build-servers', '-maxcpucount:2', *properties])
        assets = json.loads((Path(obj) / 'project.assets.json').read_bytes())
        for name in PACKAGES[1:]:
            require([v for v in assets['libraries'] if v.startswith(name + '/')] == [name + '/' + args.sdk_version], 'Resolved API version differs: ' + name)
        for name, package in packages.items():
            cached = work / 'nuget-packages' / name.lower() / args.sdk_version.lower() / (name.lower() + '.' + args.sdk_version.lower() + '.nupkg')
            require(cached.is_file() and sha(cached) == package['sha256'], 'Restored package differs: ' + name)
        assembly = project / '.godot/mono/temp/bin/Debug/MulticommandLab.dll'
        built_api = assembly.parent / 'GodotSharp.dll'
        require(built_api.is_file() and sha(built_api) == run['managed_release_api_sha256'],
                'Built fixture reference differs from the exact SDK/Release API payload.')
        run['managed_fixture_reference_api_sha256'] = sha(built_api)
        run['managed_fixture_assembly_sha256'] = sha(assembly)
        require(source_hashes(PROJECT) == before == source_hashes(project), 'Fixture source changed while building.')
        require(sha(engine) == run['engine_sha256'] and sha(api) == run['managed_api_sha256']
                and sha(release_api) == run['managed_release_api_sha256'],
                'Native bundle changed while compiling.')
        require(all(sha(Path(p['path'])) == p['sha256'] for p in packages.values()), 'Feed packages changed.')
        dump(output / 'run.json', run)
        if not args.run_gpu:
            dump(output / 'acceptance.json', dict(compiled=True, gpu_executed=False, passed=None,
                 performance_qualified=False, source_sha256=before, managed_fixture_assembly_sha256=sha(assembly)))
            print('E9 consumer compiled; GPU contract NOT RUN: ' + str(output / 'acceptance.json'))
            return 0
        record = output / 'contract.json'
        execute('runtime', [engine, '--path', project, '--rendering-method', 'forward_plus', '--rendering-driver', 'vulkan',
                           '--', '--record=' + str(record), '--expected-engine-hash=' + args.engine_hash,
                           '--expected-sdk-version=' + args.sdk_version, '--layout=' + args.layout,
                           '--archetypes=' + str(args.archetypes), '--lods=' + str(args.lods),
                           '--segments=' + str(args.segments), '--units=' + str(args.units)])
        diagnostics = [dict(line=i, text=line) for i, line in enumerate((output / 'runtime.log').read_text(encoding='utf-8', errors='replace').splitlines(), 1) if DIAGNOSTIC.search(line)]
        dump(output / 'shutdown.json', dict(passed=not diagnostics, diagnostics=diagnostics, runtime_log_sha256=sha(output / 'runtime.log')))
        require(not diagnostics, 'Runtime/shutdown diagnostics detected.')
        raw = json.loads(record.read_bytes())
        validate(raw, args)
        require(raw['source_sha256'] == before, 'Runtime source identity differs.')
        require(raw['managed_fixture_assembly_sha256'] == run['managed_fixture_assembly_sha256'] == sha(assembly), 'Fixture assembly differs.')
        require(raw['managed_api_assembly_sha256'] == run['managed_api_sha256'] == sha(api), 'Managed API differs.')
        require(sha(engine) == run['engine_sha256'] and source_hashes(PROJECT) == before == source_hashes(project), 'Engine/source changed.')
        require(all(sha(Path(p['path'])) == p['sha256'] for p in packages.values()), 'Feed packages changed.')
        dump(output / 'acceptance.json', dict(passed=True, gpu_executed=True, performance_qualified=False,
             contract_sha256=sha(record), shutdown_sha256=sha(output / 'shutdown.json'), source_sha256=before))
        print('E9 numerical subset PASS; consult contract.not_qualified: ' + str(output / 'acceptance.json'))
        return 0
    except Exception as error:
        dump(output / 'acceptance.json', dict(passed=False, performance_qualified=False, failure=str(error)))
        print('E9 contract FAIL: ' + str(error))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

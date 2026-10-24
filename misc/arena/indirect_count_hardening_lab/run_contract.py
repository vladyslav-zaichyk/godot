"""Compile E10 hardening stands. Only explicit --run-gpu starts Godot after A/B.

Each runtime case has a separate process, strict diagnostic budget and numerical
oracle. No mode builds or modifies the candidate engine. All generated output
and private caches stay under one fresh --output directory.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr
import zipfile

from prerequisites import validate_prerequisites
from validate_receipt import validate, load_json

HERE = Path(__file__).resolve().parent
SUITES = ('direct', 'native')
PACKAGES = ('Godot.NET.Sdk', 'GodotSharp', 'GodotSharpEditor', 'Godot.SourceGenerators')
DIAGNOSTIC = re.compile(r'\b(?:ERROR:|SCRIPT ERROR:|WARNING:|FATAL(?: ERROR)?[: ]|Unhandled exception)|'
                        r'ObjectDB instances leaked|Orphan StringName|RID allocations', re.I)
HEADER = re.compile(r'^\s*(ERROR|WARNING):\s*(.*?)\s*$')


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return load_json(path)


def dump(path, value):
    path.write_bytes((json.dumps(value, indent=2) + '\n').encode())


def ordinary(path):
    path = Path(path).absolute()
    for parent in (path, *path.parents):
        require(not parent.is_symlink() and not parent.is_junction(), 'Linked path is outside the stand contract: ' + str(parent))
    return path.resolve()


def source_files(root):
    result = {}
    for path in root.rglob('*'):
        ordinary(path)
        if path.is_file() and '__pycache__' not in path.parts:
            result[path.relative_to(root).as_posix()] = sha(path)
    return dict(sorted(result.items()))


def verify_candidate(candidate):
    pin = read(HERE / 'candidate.json')
    manifest_path = candidate / 'manifest.json'
    require(sha(manifest_path) == pin['manifest_sha256'], 'Use the frozen E10b candidate manifest named in candidate.json.')
    manifest = read(manifest_path)
    require(manifest.get('native_source_commit') == pin['native_source_commit']
            and manifest.get('fixture_source_commit') == pin['baseline_fixture_source_commit']
            and manifest.get('sdk_version') == pin['sdk_version'], 'Frozen native/fixture/SDK identity differs.')
    for name, digest in manifest['artifacts_sha256'].items():
        pure = PurePosixPath(name)
        require(not pure.is_absolute() and '..' not in pure.parts, 'Candidate manifest path escaped.')
        path = ordinary(candidate / name)
        require(path.is_file() and sha(path) == digest, 'Candidate payload differs: ' + name)
    engine = candidate / 'godot.windows.editor.x86_64.arena_dev.mono.exe'
    api = candidate / 'GodotSharp/Api/Debug/GodotSharp.dll'
    release = candidate / 'GodotSharp/Api/Release/GodotSharp.dll'
    return pin, {'engine_sha256': sha(engine), 'engine_source_hash': pin['native_source_commit'],
                 'managed_api_sha256': sha(api), 'managed_release_api_sha256': sha(release), 'sdk_version': pin['sdk_version']}


def shutdown_diagnostics(log, oracle_case):
    expected = Counter()
    for item in oracle_case['diagnostics']:
        expected[(item['kind'], item['message'])] += item['count']
    seen = Counter()
    unexpected = []
    for number, line in enumerate(log.read_bytes().decode('utf-8', errors='replace').splitlines(), 1):
        if not DIAGNOSTIC.search(line):
            continue
        match = HEADER.fullmatch(line)
        if match and (match[1].lower(), match[2]) in expected:
            seen[(match[1].lower(), match[2])] += 1
        else:
            unexpected.append({'line': number, 'text': line})
    require(not unexpected and seen == expected,
            'Runtime/shutdown diagnostic budget differs: ' + repr({'unexpected': unexpected, 'seen': list(seen.items()), 'expected': list(expected.items())}))
    return {'passed': True, 'runtime_log_sha256': sha(log), 'expected_diagnostics': oracle_case['diagnostics'],
            'observed_diagnostics': [{'kind': kind, 'message': message, 'count': count} for (kind, message), count in seen.items()]}


def validate_oracles(oracles, coverage):
    for suite, oracle in oracles.items():
        require(oracle.get('contract') == 'e10-hardening-' + suite + '-oracle-v1', 'Wrong oracle contract.')
        require(oracle.get('assembly') == ('DirectHardeningLab' if suite == 'direct' else 'NativeHardeningLab'), 'Wrong project assembly.')
        inputs = oracle.get('inputs')
        require(type(inputs) is list and bool(inputs) and len(inputs) == len(set(inputs))
                and 'oracle.json' in inputs and oracle['assembly'] + '.csproj' in inputs, 'Missing project input inventory.')
        cases = oracle.get('cases')
        require(type(cases) is dict and bool(cases) and set(cases) == set(coverage[suite]), 'Incomplete case matrix: ' + suite)
        require(all(type(name) is str and re.fullmatch('[a-z][a-z0-9_]+', name) and type(case) is dict
                    for name, case in cases.items()), 'Invalid runtime case names/schema.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--suite', choices=(*SUITES, 'all'), default='all')
    parser.add_argument('--case', help='One named runtime case. Default: all cases in the selected suites.')
    parser.add_argument('--dotnet', default='dotnet')
    parser.add_argument('--timeout', type=int, default=300, help='Per command/case timeout in seconds.')
    parser.add_argument('--e10a-output', type=Path)
    parser.add_argument('--e10b-output', type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--compile-only', action='store_true', help='Default; dotnet only, no Godot process.')
    mode.add_argument('--run-gpu', action='store_true', help='Explicit launch only after the parallel renderer A/B is released.')
    args = parser.parse_args()
    candidate, output = ordinary(args.candidate), ordinary(args.output)
    suites = SUITES if args.suite == 'all' else (args.suite,)
    checkout = next((p for p in HERE.parents if (p / '.git').exists() and (p / 'SConstruct').is_file()), HERE)
    if output.exists() or any(output == p or p in output.parents for p in (checkout, candidate, HERE)):
        parser.error('Use one fresh output directory outside the source and candidate.')
    if args.timeout < 1:
        parser.error('Positive per-command timeout required.')
    if args.run_gpu and (args.e10a_output is None or ('native' in suites and args.e10b_output is None)):
        parser.error('--run-gpu requires completed same-candidate --e10a-output and, for native, --e10b-output after A/B release.')
    pin, expected = verify_candidate(candidate)
    oracles = {suite: read(HERE / suite / 'oracle.json') for suite in suites}
    validate_oracles(oracles, read(HERE / 'coverage.json'))
    selected = [(suite, name) for suite in suites for name in oracles[suite]['cases'] if args.case is None or name == args.case]
    if not selected or (args.case is not None and len(selected) != 1):
        parser.error('Select exactly one known case, or omit --case for the full suite.')
    before = source_files(HERE)
    output.mkdir(parents=True)
    work = output / 'work'
    work.mkdir()
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
    config = work / 'NuGet.Config'
    config.write_bytes(('<?xml version="1.0" encoding="utf-8"?><configuration><packageSources><clear />'
        '<add key="Local" value=' + quoteattr((candidate / 'NuGetFeed').as_posix()) + ' />'
        '<add key="nuget.org" value="https://api.nuget.org/v3/index.json" /></packageSources>'
        '<packageSourceMapping><clear /><packageSource key="Local"><package pattern="Godot.*" />'
        '<package pattern="GodotSharp" /><package pattern="GodotSharpEditor" /></packageSource>'
        '<packageSource key="nuget.org"><package pattern="*" /></packageSource></packageSourceMapping></configuration>\n').encode())
    env.update(ArenaGodotSdkVersion=pin['sdk_version'], RestoreConfigFile=str(config), DOTNET_CLI_TELEMETRY_OPTOUT='1',
               DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1', DOTNET_CLI_USE_MSBUILD_SERVER='0', MSBUILDDISABLENODEREUSE='1',
               UseSharedCompilation='false', DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE='1', DOTNET_GENERATE_ASPNET_CERTIFICATE='false')
    run = dict(expected, candidate=str(candidate), candidate_manifest_sha256=pin['manifest_sha256'],
               lab_source_sha256=before, gpu_requested=args.run_gpu, selected_cases=selected, commands=[], compiled={}, cases=[])
    dump(output / 'run.json', run)

    def execute(label, command, cwd):
        log = output / (label + '.log')
        step = {'label': label, 'command': list(map(str, command)), 'exit_code': None}
        run['commands'].append(step)
        try:
            with log.open('wb') as stream:
                process = subprocess.Popen(step['command'], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                    stdout=stream, stderr=subprocess.STDOUT, start_new_session=os.name != 'nt',
                    creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name == 'nt' else 0)
                step['owned_process_id'] = process.pid
                try:
                    process.wait(timeout=args.timeout)
                except BaseException:
                    if process.poll() is None:
                        if os.name == 'nt':
                            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], check=False,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
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
        return log

    try:
        packages = {}
        for path in (candidate / 'NuGetFeed').glob('*.nupkg'):
            with zipfile.ZipFile(path) as archive:
                metadata = ET.fromstring(archive.read(next(n for n in archive.namelist() if n.endswith('.nuspec'))))
                values = {n.tag.split('}')[-1]: n.text for n in metadata.iter()}
            name = values.get('id')
            require(name in PACKAGES and values.get('version') == pin['sdk_version'] and name not in packages,
                    'Exact four-package feed required.')
            packages[name] = {'path': str(path), 'sha256': sha(path)}
        require(set(packages) == set(PACKAGES), 'Missing exact generated SDK/API packages.')
        run['sdk_packages'] = packages
        execute('dotnet-version', [args.dotnet, '--version'], work)
        for suite in suites:
            oracle = oracles[suite]
            project = work / suite
            project.mkdir()
            inputs = oracle['inputs']
            require(isinstance(inputs, list) and len(inputs) == len(set(inputs)) and 'oracle.json' in inputs,
                    'Oracle project inventory missing or duplicated.')
            hashes = {}
            for name in inputs:
                require(PurePosixPath(name).name == name and name not in ('.', '..'), 'Project input path escaped.')
                shutil.copy2(HERE / suite / name, project / name)
                hashes[name] = sha(project / name)
            assembly_name = oracle['assembly']
            project_file = project / (assembly_name + '.csproj')
            obj = (work / ('obj-' + suite)).as_posix() + '/'
            props = ['-p:ArenaGodotSdkVersion=' + pin['sdk_version'], '-p:BaseIntermediateOutputPath=' + obj,
                     '-p:MSBuildProjectExtensionsPath=' + obj, '-p:IntermediateOutputPath=' + obj + 'Debug/',
                     '-p:UseSharedCompilation=false', '-p:ImportDirectoryBuildProps=false', '-p:ImportDirectoryBuildTargets=false']
            execute('restore-' + suite, [args.dotnet, 'restore', project_file, '--configfile', config, '--disable-parallel', *props], project)
            execute('build-' + suite, [args.dotnet, 'build', project_file, '--configuration', 'Debug', '--no-restore',
                '--no-incremental', '--disable-build-servers', '-maxcpucount:2', *props], project)
            assets = read(Path(obj) / 'project.assets.json')
            for name in PACKAGES[1:]:
                require([v for v in assets['libraries'] if v.startswith(name + '/')] == [name + '/' + pin['sdk_version']],
                        'Resolved API version differs: ' + name)
            for name, package in packages.items():
                cached = work / 'nuget-packages' / name.lower() / pin['sdk_version'].lower() / (name.lower() + '.' + pin['sdk_version'].lower() + '.nupkg')
                require(cached.is_file() and sha(cached) == package['sha256'], 'Restored package differs: ' + name)
            assembly = project / '.godot/mono/temp/bin/Debug' / (assembly_name + '.dll')
            require(sha(assembly.parent / 'GodotSharp.dll') == expected['managed_release_api_sha256'], 'Compiled API reference differs.')
            run['compiled'][suite] = {'assembly_sha256': sha(assembly), 'source_sha256': hashes, 'project': str(project), 'assembly': str(assembly)}
        require(source_files(HERE) == before, 'Source changed while compiling.')
        verify_candidate(candidate)
        dump(output / 'run.json', run)
        if not args.run_gpu:
            dump(output / 'acceptance.json', {'compiled': True, 'gpu_executed': False, 'passed': None,
                'performance_qualified': False, 'all_cases_qualified': False, 'compiled_suites': list(suites),
                'selected_cases': selected, 'lab_source_sha256': before})
            print(json.dumps({'compiled': list(suites), 'planned_cases': len(selected), 'gpu_executed': False}))
            return 0
        gates = validate_prerequisites(candidate, expected, ordinary(args.e10a_output),
                                       ordinary(args.e10b_output) if 'native' in suites else None)
        run['prerequisites'] = gates
        for suite, name in selected:
            proof = run['compiled'][suite]
            case_output = output / 'cases' / suite / name
            case_output.mkdir(parents=True)
            receipt = case_output / 'contract.json'
            label = 'runtime-' + suite + '-' + name
            log = execute(label, [candidate / 'godot.windows.editor.x86_64.arena_dev.mono.exe', '--path', proof['project'],
                '--rendering-method', 'forward_plus', '--rendering-driver', 'vulkan', '--', '--case=' + name,
                '--record=' + str(receipt), '--expected-engine-hash=' + pin['native_source_commit'],
                '--expected-sdk-version=' + pin['sdk_version']], Path(proof['project']))
            raw = read(receipt)
            validate(raw, oracles[suite], name, {'engine_hash': pin['native_source_commit'], 'sdk_version': pin['sdk_version'],
                'managed_api_sha256': expected['managed_api_sha256'], 'fixture_sha256': proof['assembly_sha256'],
                'source_sha256': proof['source_sha256']})
            shutdown = shutdown_diagnostics(log, oracles[suite]['cases'][name])
            dump(case_output / 'shutdown.json', shutdown)
            run['cases'].append({'suite': suite, 'case_name': name, 'contract_sha256': sha(receipt),
                                  'shutdown_sha256': sha(case_output / 'shutdown.json'), 'passed': True})
            dump(output / 'run.json', run)
        require(source_files(HERE) == before, 'Stand inputs changed during runtime.')
        verify_candidate(candidate)
        for suite, proof in run['compiled'].items():
            require(sha(Path(proof['assembly'])) == proof['assembly_sha256'], 'Compiled fixture changed.')
            require({name: sha(Path(proof['project']) / name) for name in oracles[suite]['inputs']} == proof['source_sha256'], 'Runtime project source changed.')
        for group in gates.values():
            require(all(sha(Path(group['output']) / name) == digest for name, digest in group['receipt_sha256'].items()), 'Prerequisite receipts changed.')
        require([(case['suite'], case['case_name']) for case in run['cases']] == selected, 'Runtime did not complete the selected case matrix.')
        all_cases = args.suite == 'all' and args.case is None
        dump(output / 'acceptance.json', {'compiled': True, 'gpu_executed': True, 'passed': True,
            'all_cases_qualified': all_cases, 'performance_qualified': False, 'case_count': len(run['cases']),
            'lab_source_sha256': before, 'not_qualified': {suite: oracles[suite].get('not_qualified', []) for suite in suites}})
        print(json.dumps({'passed': True, 'cases': len(run['cases']), 'all_cases_qualified': all_cases, 'performance_qualified': False}))
        return 0
    except Exception as error:
        dump(output / 'acceptance.json', {'passed': False, 'gpu_executed': any(s['label'].startswith('runtime-') for s in run['commands']),
            'performance_qualified': False, 'failure': str(error)})
        print('E10 hardening FAIL: ' + str(error))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

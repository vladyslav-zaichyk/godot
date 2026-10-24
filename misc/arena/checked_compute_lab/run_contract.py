"""Compile only by default. --run-gpu --gpu-slot-released explicitly starts six fresh Vulkan processes."""
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

from validate_receipt import CASES, INPUTS, load_json, require, validate, validate_oracle

HERE = Path(__file__).resolve().parent
PROJECT = HERE / 'project'
NATIVE = '7d59f5064a6cd40a1cf2aaa578b8e90d29d1cbe8'
PACKAGES = ('Godot.NET.Sdk', 'GodotSharp', 'GodotSharpEditor', 'Godot.SourceGenerators')
DIAGNOSTIC = re.compile(r'\b(?:ERROR:|SCRIPT ERROR:|WARNING:|FATAL(?: ERROR)?[: ]|Unhandled exception)|'
    r'ObjectDB instances leaked|Orphan StringName|RID allocations|OpArrayLength is not supported yet', re.I)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, value):
    path.write_bytes((json.dumps(value, indent=2) + '\n').encode())


def ordinary(path):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        require(not item.is_symlink() and not getattr(item, 'is_junction', lambda: False)(), 'Linked path rejected: ' + str(item))
        if item.exists():
            require(not (getattr(item.lstat(), 'st_file_attributes', 0) & 0x400), 'Reparse path rejected: ' + str(item))
    return path.resolve()


def source_hashes(project):
    return {name: sha(project / name) for name in INPUTS}


def shutdown(log):
    found = [{'line': number, 'text': line} for number, line in
        enumerate(log.read_bytes().decode('utf-8', errors='replace').splitlines(), 1) if DIAGNOSTIC.search(line)]
    require(not found, 'Unexpected runtime/shutdown diagnostic: ' + repr(found))
    return {'passed': True, 'diagnostics': [], 'runtime_log_sha256': sha(log)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--sdk-feed', type=Path, required=True)
    parser.add_argument('--sdk-version', required=True)
    parser.add_argument('--engine-hash', default=NATIVE)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dotnet', default='dotnet')
    parser.add_argument('--dotnet-sdk-version', default='10.0.203')
    parser.add_argument('--timeout', type=int, default=300)
    parser.add_argument('--case', choices=CASES)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--compile-only', action='store_true', help='Default: dotnet only; never execute the editor.')
    modes.add_argument('--run-gpu', action='store_true')
    parser.add_argument('--gpu-slot-released', action='store_true', help='Operator confirms the parallel renderer A/B has released the GPU.')
    args = parser.parse_args()
    engine, feed, output = map(ordinary, (args.engine, args.sdk_feed, args.output))
    checkout = next((p for p in HERE.parents if (p / '.git').exists()), HERE)
    if output.exists() or any(output == p or p in output.parents or output in p.parents for p in (checkout, engine.parent, feed, HERE)):
        parser.error('Use one fresh output outside the source, editor and SDK feed, and not their ancestor.')
    if not engine.is_file() or not feed.is_dir() or args.engine_hash != NATIVE or args.timeout < 1:
        parser.error('Exact 7d59f50 editor/feed and a positive timeout are required.')
    if not re.fullmatch(r'[0-9A-Za-z.-]+', args.sdk_version) or not re.fullmatch(r'\d+\.\d+\.\d+', args.dotnet_sdk_version):
        parser.error('Invalid SDK version.')
    if args.run_gpu and not args.gpu_slot_released:
        parser.error('--run-gpu also requires --gpu-slot-released after the renderer A/B is finished.')
    oracle = load_json(PROJECT / 'oracle.json'); validate_oracle(oracle)
    selected = [args.case] if args.case else list(CASES)
    before = source_hashes(PROJECT)
    support = {name: sha(HERE / name) for name in ('run_contract.py', 'validate_receipt.py', 'test_receipt.py', 'README.md')}
    api = ordinary(engine.parent / 'GodotSharp/Api/Debug/GodotSharp.dll')
    release = ordinary(engine.parent / 'GodotSharp/Api/Release/GodotSharp.dll')
    identity = dict(engine_source_hash=NATIVE, engine_sha256=sha(engine), sdk_version=args.sdk_version,
                    managed_api_sha256=sha(api), managed_release_api_sha256=sha(release))
    packages = {}
    for path in sorted(feed.glob('*.nupkg')):
        ordinary(path)
        with zipfile.ZipFile(path) as archive:
            metadata = ET.fromstring(archive.read(next(name for name in archive.namelist() if name.endswith('.nuspec'))))
            fields = {node.tag.split('}')[-1]: node.text for node in metadata.iter()}
        if fields.get('id') in PACKAGES:
            name = fields['id']
            require(fields.get('version') == args.sdk_version and name not in packages, 'Feed has mismatched/duplicate SDK package: ' + name)
            packages[name] = {'path': str(path), 'sha256': sha(path)}
    require(set(packages) == set(PACKAGES), 'Exact four generated SDK packages required.')
    output.mkdir(parents=True)
    work = output / 'work'; work.mkdir()
    project = work / 'project'; project.mkdir()
    for name in INPUTS: shutil.copy2(PROJECT / name, project / name)
    dump(work / 'global.json', {'sdk': {'version': args.dotnet_sdk_version, 'rollForward': 'disable', 'allowPrerelease': False}})
    env = os.environ.copy()
    for key in ('MSBuildSDKsPath', 'MSBUILD_EXE_PATH', 'DOTNET_MSBUILD_SDK_RESOLVER_SDKS_DIR',
                'DOTNET_MSBUILD_SDK_RESOLVER_SDKS_VER', 'NUGET_FALLBACK_PACKAGES', 'NUGET_RESTORE_MSBUILD_ARGS'):
        env.pop(key, None)
    for key, name in {'TEMP': 'tmp', 'TMP': 'tmp', 'APPDATA': 'appdata', 'LOCALAPPDATA': 'localappdata',
            'DOTNET_CLI_HOME': 'dotnet-home', 'NUGET_PACKAGES': 'nuget-packages', 'NUGET_HTTP_CACHE_PATH': 'nuget-http',
            'NUGET_PLUGINS_CACHE_PATH': 'nuget-plugins', 'NUGET_SCRATCH': 'nuget-scratch'}.items():
        path = work / name; path.mkdir(exist_ok=True); env[key] = str(path)
    config = work / 'NuGet.Config'
    config.write_bytes(('<?xml version="1.0" encoding="utf-8"?><configuration><packageSources><clear />'
        '<add key="Local" value=' + quoteattr(feed.as_posix()) + ' /><add key="nuget.org" value="https://api.nuget.org/v3/index.json" />'
        '</packageSources><packageSourceMapping><clear /><packageSource key="Local"><package pattern="Godot.*" />'
        '<package pattern="GodotSharp" /><package pattern="GodotSharpEditor" /></packageSource>'
        '<packageSource key="nuget.org"><package pattern="*" /></packageSource></packageSourceMapping></configuration>\n').encode())
    env.update(ArenaGodotSdkVersion=args.sdk_version, RestoreConfigFile=str(config), DOTNET_CLI_TELEMETRY_OPTOUT='1',
        DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1', DOTNET_CLI_USE_MSBUILD_SERVER='0', MSBUILDDISABLENODEREUSE='1',
        UseSharedCompilation='false', DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE='1', DOTNET_GENERATE_ASPNET_CERTIFICATE='false')
    run = dict(identity, contract='checked-compute-run-v1', source_sha256=before, support_sha256=support,
        engine=str(engine), sdk_feed=str(feed), sdk_packages=packages, project=str(project), selected_cases=selected,
        gpu_requested=args.run_gpu, commands=[], cases=[])
    dump(output / 'run.json', run)

    def execute(label, command, cwd):
        log = output / (label + '.log')
        step = dict(label=label, command=list(map(str, command)), exit_code=None)
        run['commands'].append(step)
        try:
            with log.open('wb') as stream:
                process = subprocess.Popen(step['command'], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                    stdout=stream, stderr=subprocess.STDOUT, start_new_session=os.name != 'nt',
                    creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name == 'nt' else 0)
                step['owned_process_id'] = process.pid
                try: process.wait(timeout=args.timeout)
                except BaseException:
                    if process.poll() is None:
                        if os.name == 'nt':
                            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], check=False,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
                        else: os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=30)
                    raise
            step['exit_code'] = process.returncode
            require(process.returncode == 0, label + ' failed; inspect ' + str(log))
        finally:
            if log.is_file(): step['log_sha256'] = sha(log)
            dump(output / (label + '-exit.json'), step); dump(output / 'run.json', run)
        return log

    def unchanged():
        require(source_hashes(PROJECT) == before and source_hashes(project) == before, 'Project source changed.')
        require({name: sha(HERE / name) for name in support} == support, 'Runner/validator support changed.')
        require(sha(engine) == identity['engine_sha256'] and sha(api) == identity['managed_api_sha256']
                and sha(release) == identity['managed_release_api_sha256'], 'Native/API payload changed.')
        require(all(sha(Path(value['path'])) == value['sha256'] for value in packages.values()), 'SDK feed changed.')

    try:
        log = execute('dotnet-version', [args.dotnet, '--version'], project)
        require(log.read_text(encoding='utf-8').strip() == args.dotnet_sdk_version, 'Selected dotnet SDK differs.')
        obj = (work / 'obj').as_posix() + '/'
        props = ['-p:ArenaGodotSdkVersion=' + args.sdk_version, '-p:BaseIntermediateOutputPath=' + obj,
            '-p:MSBuildProjectExtensionsPath=' + obj, '-p:IntermediateOutputPath=' + obj + 'Debug/',
            '-p:UseSharedCompilation=false', '-p:ImportDirectoryBuildProps=false', '-p:ImportDirectoryBuildTargets=false']
        csproj = project / 'CheckedComputeLab.csproj'
        execute('restore', [args.dotnet, 'restore', csproj, '--configfile', config, '--disable-parallel', *props], project)
        execute('build', [args.dotnet, 'build', csproj, '--configuration', 'Debug', '--no-restore', '--no-incremental',
            '--disable-build-servers', '-maxcpucount:2', *props], project)
        assets = load_json(Path(obj) / 'project.assets.json')
        for name in PACKAGES[1:]:
            require([value for value in assets['libraries'] if value.startswith(name + '/')] == [name + '/' + args.sdk_version], 'Resolved package differs: ' + name)
        for name, package in packages.items():
            cached = work / 'nuget-packages' / name.lower() / args.sdk_version.lower() / (name.lower() + '.' + args.sdk_version.lower() + '.nupkg')
            require(cached.is_file() and sha(cached) == package['sha256'], 'Cached SDK/API payload differs: ' + name)
        assembly = project / '.godot/mono/temp/bin/Debug/CheckedComputeLab.dll'
        require(sha(assembly.parent / 'GodotSharp.dll') == identity['managed_release_api_sha256'], 'Compile reference API differs.')
        run.update(assembly=str(assembly), managed_fixture_assembly_sha256=sha(assembly), dotnet_sdk_version=args.dotnet_sdk_version)
        unchanged(); dump(output / 'run.json', run)
        if not args.run_gpu:
            dump(output / 'acceptance.json', dict(identity, compiled=True, gpu_executed=False, passed=None,
                all_cases_qualified=False, performance_qualified=False, source_sha256=before,
                managed_fixture_assembly_sha256=run['managed_fixture_assembly_sha256'], selected_cases=selected))
            print(json.dumps({'compiled': True, 'gpu_executed': False, 'planned_cases': len(selected)})); return 0
        expected = dict(engine_hash=NATIVE, engine_sha256=identity['engine_sha256'], sdk_version=args.sdk_version,
            managed_api_assembly_sha256=identity['managed_api_sha256'], managed_fixture_assembly_sha256=run['managed_fixture_assembly_sha256'], source_sha256=before)
        for case in selected:
            folder = output / 'cases' / case; folder.mkdir(parents=True)
            receipt = folder / 'contract.json'
            log = execute('runtime-' + case, [engine, '--path', project, '--rendering-method', 'forward_plus',
                '--rendering-driver', 'vulkan', '--', '--case=' + case, '--record=' + str(receipt),
                '--expected-engine-hash=' + NATIVE, '--expected-engine-sha256=' + identity['engine_sha256'],
                '--expected-sdk-version=' + args.sdk_version], project)
            validate(load_json(receipt), oracle, case, expected)
            dump(folder / 'shutdown.json', shutdown(log))
            run['cases'].append(dict(case_name=case, passed=True, receipt_sha256=sha(receipt), shutdown_sha256=sha(folder / 'shutdown.json')))
            dump(output / 'run.json', run)
        unchanged(); require(sha(assembly) == run['managed_fixture_assembly_sha256'], 'Fixture assembly changed.')
        require([case['case_name'] for case in run['cases']] == selected, 'Incomplete runtime matrix.')
        dump(output / 'acceptance.json', dict(identity, compiled=True, gpu_executed=True, passed=True,
            all_cases_qualified=args.case is None, performance_qualified=False, release_driver_qualified=False,
            source_sha256=before, managed_fixture_assembly_sha256=run['managed_fixture_assembly_sha256'],
            completed_cases=selected, not_qualified=oracle['not_qualified']))
        print(json.dumps({'passed': True, 'case_count': len(selected), 'all_cases_qualified': args.case is None})); return 0
    except Exception as error:
        dump(output / 'acceptance.json', {'passed': False, 'gpu_executed': any(s['label'].startswith('runtime-') for s in run['commands']),
            'all_cases_qualified': False, 'performance_qualified': False, 'failure': str(error)})
        print('Checked compute FAIL: ' + str(error)); return 2


if __name__ == '__main__': raise SystemExit(main())

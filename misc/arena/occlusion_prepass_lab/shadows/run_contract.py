"""Compile the E13e source-contract stand by default. GPU execution needs two explicit flags."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr
import zipfile

sys.dont_write_bytecode = True
from validate_receipt import DIAGNOSTIC, INPUTS, load_json, require, sha, validate, validate_plan

HERE = Path(__file__).resolve().parent
PROJECT = HERE / 'project'
PACKAGES = ('Godot.NET.Sdk', 'GodotSharp', 'GodotSharpEditor', 'Godot.SourceGenerators')


def dump(path, value):
    path.write_bytes((json.dumps(value, indent=2) + '\n').encode())


def ordinary(path):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        require(not item.is_symlink() and not getattr(item, 'is_junction', lambda: False)()
                and not (item.exists() and getattr(item.lstat(), 'st_file_attributes', 0) & 0x400),
                'Linked/reparse path rejected: ' + str(item))
    return path.resolve()


def source_hashes(project):
    return {name: sha(HERE / name if project == PROJECT and name == 'gate_plan.json' else project / name) for name in INPUTS}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gate', choices=('reference-boundary', 'combined-required'), default='reference-boundary')
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--sdk-feed', type=Path, required=True)
    parser.add_argument('--native-receipt', type=Path, required=True)
    parser.add_argument('--engine-hash', required=True)
    parser.add_argument('--sdk-version', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dotnet', default='dotnet')
    parser.add_argument('--dotnet-sdk-version', default='10.0.203')
    parser.add_argument('--timeout', type=int, default=300)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--compile-only', action='store_true')
    modes.add_argument('--run-gpu', action='store_true')
    parser.add_argument('--gpu-slot-released', action='store_true')
    parser.add_argument('--keep-work', action='store_true', help='Retain work for coordinator process-CWD audit and cleanup.')
    args = parser.parse_args()
    if args.run_gpu and not args.gpu_slot_released:
        parser.error('--run-gpu requires --gpu-slot-released after the integrator releases Hi-Z A/B.')
    if not re.fullmatch('[0-9a-f]{40}', args.engine_hash) or not re.fullmatch('[0-9A-Za-z.-]+', args.sdk_version) or args.timeout < 1:
        parser.error('Full native hash, exact matching SDK and positive timeout required.')
    engine, feed, native_path, output = map(ordinary, (args.engine, args.sdk_feed, args.native_receipt, args.output))
    checkout = next((p for p in HERE.parents if (p / '.git').exists()), HERE)
    require(not output.exists() and all(output != p and output not in p.parents and p not in output.parents
            for p in (checkout, engine.parent, feed, native_path.parent)), 'Use a fresh output outside source/editor/feed/receipts.')
    require(engine.is_file() and feed.is_dir() and native_path.is_file(), 'Complete editor, feed and receipt required.')
    plan = load_json(HERE / 'gate_plan.json'); validate_plan(plan)
    if args.gate == 'reference-boundary':
        require(args.engine_hash == plan['reference']['engine_hash'] and args.sdk_version == plan['reference']['sdk_version']
                and sha(engine) == plan['reference']['engine_sha256'], 'Reference requires the exact sealed E13d editor and SDK.')
    else:
        require(args.engine_hash != plan['reference']['engine_hash'], 'Combined gate requires a future shadow-capable native patch.')
    native = load_json(native_path)
    require(native['source_commit'] == args.engine_hash and native['binary_sha256'] == sha(engine), 'Build/editor identity differs.')
    require(native['effective_dotnet_sdk'] == args.dotnet_sdk_version, 'Build dotnet SDK differs.')
    for name in ('native', 'glue', 'managed', 'native-contracts'):
        steps = [step for step in native['steps'] if step['name'] == name]
        require(len(steps) == 1 and steps[0]['exit_code'] == 0, 'Missing/failed native gate: ' + name)
    require(all(step['exit_code'] == 0 for step in native['steps']), 'Failed native build receipt.')
    api = ordinary(engine.parent / 'GodotSharp/Api/Debug/GodotSharp.dll')
    release = ordinary(engine.parent / 'GodotSharp/Api/Release/GodotSharp.dll')
    require(api.is_file() and release.is_file(), 'Matching editor API tree required.')
    packages = {}
    for path in feed.glob('*.nupkg'):
        ordinary(path)
        with zipfile.ZipFile(path) as archive:
            metadata = ET.fromstring(archive.read(next(name for name in archive.namelist() if name.endswith('.nuspec'))))
            fields = {node.tag.split('}')[-1]: node.text for node in metadata.iter()}
        if fields.get('id') in PACKAGES:
            name = fields['id']
            require(name not in packages and fields['version'] == args.sdk_version
                    and native['sdk_packages'][path.name] == sha(path), 'SDK identity differs: ' + name)
            packages[name] = dict(path=str(path), sha256=sha(path))
    require(set(packages) == set(PACKAGES), 'Exact four SDK/API packages required.')
    before = source_hashes(PROJECT)
    support = {name: sha(HERE / name) for name in ('run_contract.py', 'validate_receipt.py', 'test_receipt.py', 'gate_plan.json', 'README.md', 'CONTRACT.md')}
    identity = dict(gate=args.gate, engine_hash=args.engine_hash, engine_sha256=sha(engine), sdk_version=args.sdk_version,
                    managed_api_assembly_sha256=sha(api), source_sha256=before, plan_sha256=sha(HERE / 'gate_plan.json'))
    output.mkdir(parents=True); work = output / 'work'; project = work / 'project'; project.mkdir(parents=True)
    evidence = output / 'evidence'; evidence.mkdir()
    shutil.copy2(native_path, evidence / 'native-build-manifest.json')
    for name in INPUTS:
        shutil.copy2(HERE / name if name == 'gate_plan.json' else PROJECT / name, project / name)
    dump(work / 'global.json', dict(sdk=dict(version=args.dotnet_sdk_version, rollForward='disable', allowPrerelease=False)))
    env = os.environ.copy()
    for key in ('MSBuildSDKsPath', 'MSBUILD_EXE_PATH', 'DOTNET_MSBUILD_SDK_RESOLVER_SDKS_DIR', 'DOTNET_MSBUILD_SDK_RESOLVER_SDKS_VER',
                'NUGET_FALLBACK_PACKAGES', 'NUGET_RESTORE_MSBUILD_ARGS'):
        env.pop(key, None)
    for key, folder in {'TEMP': 'tmp', 'TMP': 'tmp', 'APPDATA': 'appdata', 'LOCALAPPDATA': 'localappdata',
                        'DOTNET_CLI_HOME': 'dotnet-home', 'NUGET_PACKAGES': 'nuget-packages', 'NUGET_HTTP_CACHE_PATH': 'nuget-http',
                        'NUGET_PLUGINS_CACHE_PATH': 'nuget-plugins', 'NUGET_SCRATCH': 'nuget-scratch'}.items():
        path = work / folder; path.mkdir(exist_ok=True); env[key] = str(path)
    config = work / 'NuGet.Config'
    config.write_bytes(('<?xml version="1.0" encoding="utf-8"?><configuration><packageSources><clear />'
        '<add key="Local" value=' + quoteattr(feed.as_posix()) + ' /><add key="nuget.org" value="https://api.nuget.org/v3/index.json" />'
        '</packageSources><packageSourceMapping><clear /><packageSource key="Local"><package pattern="Godot.*" />'
        '<package pattern="GodotSharp" /><package pattern="GodotSharpEditor" /></packageSource>'
        '<packageSource key="nuget.org"><package pattern="*" /></packageSource></packageSourceMapping></configuration>\n').encode())
    env.update(ArenaGodotSdkVersion=args.sdk_version, RestoreConfigFile=str(config), DOTNET_CLI_TELEMETRY_OPTOUT='1',
               DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1', DOTNET_CLI_USE_MSBUILD_SERVER='0', MSBUILDDISABLENODEREUSE='1',
               UseSharedCompilation='false', DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE='1', DOTNET_GENERATE_ASPNET_CERTIFICATE='false')
    run = dict(contract='arena-e13e-directional-shadow-run-v1', identity=identity, support_sha256=support,
               packages=packages, commands=[], gpu_requested=args.run_gpu, disposable_paths=[str(work)])
    dump(output / 'run.json', run)

    def execute(label, command):
        step = dict(label=label, command=list(map(str, command)), exit_code=None); run['commands'].append(step)
        log = output / (label + '.log')
        with log.open('wb') as stream:
            process = subprocess.Popen(step['command'], cwd=project, env=env, stdin=subprocess.DEVNULL, stdout=stream,
                stderr=subprocess.STDOUT, creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name == 'nt' else 0)
            step['owned_process_id'] = process.pid
            try:
                step['exit_code'] = process.wait(timeout=args.timeout)
            except BaseException:
                if process.poll() is None:
                    if os.name == 'nt':
                        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], check=False, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
                    else:
                        process.kill()
                    process.wait(timeout=30)
                raise
        dump(output / 'run.json', run)
        require(step['exit_code'] == 0, label + ' failed; see ' + str(log))
        return log

    try:
        version = execute('dotnet-version', [args.dotnet, '--version'])
        require(version.read_bytes().decode('utf-8-sig').strip() == args.dotnet_sdk_version, 'Effective dotnet SDK differs.')
        obj = (work / 'obj').as_posix() + '/'
        props = ['-p:ArenaGodotSdkVersion=' + args.sdk_version, '-p:BaseIntermediateOutputPath=' + obj,
                 '-p:MSBuildProjectExtensionsPath=' + obj, '-p:IntermediateOutputPath=' + obj + 'Debug/',
                 '-p:UseSharedCompilation=false', '-p:ImportDirectoryBuildProps=false', '-p:ImportDirectoryBuildTargets=false']
        execute('restore', [args.dotnet, 'restore', project / 'ShadowContractLab.csproj', '--configfile', config, '--disable-parallel', *props])
        execute('build', [args.dotnet, 'build', project / 'ShadowContractLab.csproj', '--configuration', 'Debug', '--no-restore',
                          '--no-incremental', '--disable-build-servers', '-maxcpucount:2', *props])
        assets = load_json(Path(obj) / 'project.assets.json')
        for name in PACKAGES[1:]:
            require([key for key in assets['libraries'] if key.startswith(name + '/')] == [name + '/' + args.sdk_version], 'Resolved API differs.')
        for name, package in packages.items():
            cached = work / 'nuget-packages' / name.lower() / args.sdk_version.lower() / (name.lower() + '.' + args.sdk_version.lower() + '.nupkg')
            require(cached.is_file() and sha(cached) == package['sha256'], 'Restored package bytes differ.')
        assembly = project / '.godot/mono/temp/bin/Debug/ShadowContractLab.dll'
        require(sha(assembly.parent / 'GodotSharp.dll') == sha(release), 'Compile API differs.')
        identity['managed_fixture_assembly_sha256'] = sha(assembly); shutil.copy2(assembly, evidence / assembly.name)
        dump(output / 'expected.json', identity)
        verdict = dict(compiled=True, gpu_executed=False, status='compiled_GPU_pending', shadow_reference_contract_passed=False, combined_shadow_occlusion_contract_passed=False,
                       native_qualified=False, performance_qualified=False, passed=None)
        if args.run_gpu:
            log = execute('runtime', [engine, '--path', project, '--rendering-method', 'forward_plus', '--rendering-driver', 'vulkan', '--',
                '--gate=' + args.gate, '--record=' + str(output / 'contract.json'), '--expected-engine-hash=' + args.engine_hash,
                '--expected-engine-sha256=' + identity['engine_sha256'], '--expected-sdk-version=' + args.sdk_version])
            diagnostics = [line for line in log.read_bytes().decode('utf-8', errors='replace').splitlines() if DIAGNOSTIC.search(line)]
            dump(output / 'shutdown.json', dict(passed=not diagnostics, diagnostics=diagnostics, log_sha256=sha(log)))
            require(not diagnostics, 'Runtime/shutdown diagnostics occurred.')
            verdict = dict(validate(load_json(output / 'contract.json'), plan, identity, output, args.gate), compiled=True, gpu_executed=True, passed=True)
        require(source_hashes(PROJECT) == before == source_hashes(project),
                'Source changed during run.')
        require({name: sha(HERE / name) for name in support} == support and sha(engine) == identity['engine_sha256']
                and sha(api) == identity['managed_api_assembly_sha256'] and sha(assembly) == identity['managed_fixture_assembly_sha256'],
                'Support/editor/API/assembly changed.')
        dump(output / 'acceptance.json', dict(verdict, identity=identity))
        print(json.dumps(verdict)); return 0
    except Exception as error:
        dump(output / 'acceptance.json', dict(passed=False, native_qualified=False, performance_qualified=False, failure=str(error)))
        print('E13e source-contract stand failed: ' + str(error)); return 2
    finally:
        target = ordinary(work)
        require(target == ordinary(output) / 'work', 'Unexpected cleanup target.')
        if args.keep_work:
            run['work_cleanup_pending'] = True
            run['cleanup_reason'] = 'Coordinator must check actual process working directories before bounded removal.'
        else:
            for folder, directories, files in os.walk(target, followlinks=False):
                for name in directories + files:
                    ordinary(Path(folder) / name)
            shutil.rmtree(target); run['cleaned_paths'] = [str(target)]; run['work_cleanup_pending'] = False
        dump(output / 'run.json', run)


if __name__ == '__main__':
    raise SystemExit(main())

"""Compile the separately sealed E13c TAA/E8 stand; GPU execution needs BOTH explicit flags."""
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

from validate_receipt import DIAGNOSTIC, load_json, preflight_scenarios, require, validate

HERE = Path(__file__).resolve().parent
PROJECT = HERE / 'project'
PACKAGES = ('Godot.NET.Sdk', 'GodotSharp', 'GodotSharpEditor', 'Godot.SourceGenerators')
INPUTS = ('OcclusionTaaLab.csproj', 'project.godot', 'Main.tscn', 'Main.cs', 'HistoryCapture.cs',
          'ProbeLogger.cs', 'Consumer.gdshader', 'Legacy.gdshader', 'Wall.gdshader', 'CopyDepth.comp',
          'Reduce.comp', 'Select.comp', 'Finalize.comp', 'Capture.comp', 'scenarios.json', 'gate_plan.json', 'FreezeScenarios.py')
SUPPORT = ('run_contract.py', 'validate_receipt.py', 'test_receipt.py', 'README.md', 'CONTRACT.md', 'gate_plan.json', 'reference_math.json')



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


def source_hashes(root):
    return {name: sha(input_path(root, name)) for name in INPUTS}


def input_path(root, name):
    # One canonical plan; inject it into the disposable runnable project.
    return HERE / name if root == PROJECT and name == 'gate_plan.json' else root / name


def remove_owned_work(work, output):
    target, owner = ordinary(work), ordinary(output)
    require(target == owner / 'work' and target.parent == owner, 'Unexpected disposable work path.')
    if not target.exists():
        return
    for folder, children, files in os.walk(target, followlinks=False):
        for name in children + files:
            ordinary(Path(folder) / name)
    shutil.rmtree(target)
    require(not target.exists(), 'Disposable work was not removed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--sdk-feed', type=Path, required=True)
    parser.add_argument('--native-receipt', type=Path, required=True, help='Successful build_windows.py manifest for this exact editor/feed.')
    parser.add_argument('--engine-hash', default=os.environ.get('ArenaNativeSourceCommit'))
    parser.add_argument('--sdk-version', default=os.environ.get('ArenaGodotSdkVersion'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dotnet', default='dotnet')
    parser.add_argument('--dotnet-sdk-version', default='10.0.203')
    parser.add_argument('--timeout', type=int, default=300)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--compile-only', action='store_true', help='Default: compile managed stand only; never launch the editor.')
    modes.add_argument('--run-gpu', action='store_true')
    parser.add_argument('--gpu-slot-released', action='store_true', help='Explicit operator confirmation that renderer A/B has ended.')
    parser.add_argument('--keep-work', action='store_true', help='Retain private generated project/cache for a specific investigation.')
    args = parser.parse_args()
    if args.run_gpu and not args.gpu_slot_released:
        parser.error('--run-gpu requires --gpu-slot-released after the parallel renderer A/B is finished.')
    if not args.engine_hash or not re.fullmatch('[0-9a-f]{40}', args.engine_hash) or not args.sdk_version or not re.fullmatch('[0-9A-Za-z.-]+', args.sdk_version):
        parser.error('Inject the NEW full native SHA and exact matching SDK version using arguments or ArenaNativeSourceCommit/ArenaGodotSdkVersion.')
    if args.timeout < 1 or not re.fullmatch(r'\d+\.\d+\.\d+', args.dotnet_sdk_version):
        parser.error('Positive timeout and exact dotnet SDK version required.')
    engine, feed, receipt_path, output = map(ordinary, (args.engine, args.sdk_feed, args.native_receipt, args.output))
    # An extracted bundle may live inside an unrelated support Git repository.
    # Protect the engine source checkout only for this source-tree layout.
    checkout = next((p for p in HERE.parents if (p / '.git').exists()
                     and p / 'misc/arena/occlusion_prepass_lab/taa' == HERE), HERE)
    if output.exists() or any(output == p or p in output.parents or output in p.parents for p in (checkout, engine.parent, feed, receipt_path.parent, HERE)):
        parser.error('Use one fresh output outside the source/editor/feed/build receipts and not their ancestor.')
    if not engine.is_file() or not feed.is_dir() or not receipt_path.is_file():
        parser.error('Existing new native editor, SDK feed and build manifest required.')
    plan = load_json(HERE / 'gate_plan.json')
    scenarios = load_json(PROJECT / 'scenarios.json')
    require(plan['contract'] == 'arena-e13c-native-taa-e8-plan-v2', 'Versioned counted-initialization plan required.')
    if (PROJECT / 'gate_plan.json').exists():
        require(sha(PROJECT / 'gate_plan.json') == sha(HERE / 'gate_plan.json'), 'Injected plan differs from canonical plan.')
    before = source_hashes(PROJECT)
    support = {name: sha(HERE / name) for name in SUPPORT}
    api = ordinary(engine.parent / 'GodotSharp/Api/Debug/GodotSharp.dll')
    release = ordinary(engine.parent / 'GodotSharp/Api/Release/GodotSharp.dll')
    require(api.is_file() and release.is_file(), 'The complete matching Debug/Release GodotSharp tree must accompany the editor.')
    native = load_json(receipt_path)
    require(native.get('source_commit') == args.engine_hash and native.get('binary_sha256') == sha(engine), 'Native build receipt does not identify this editor.')
    steps = native.get('steps')
    require(type(steps) is list and steps and all(type(s.get('exit_code')) is int and s['exit_code'] == 0 for s in steps), 'Native build contains failed/incomplete steps.')
    for required_step in ('native', 'glue', 'managed', 'native-contracts'):
        require(sum(s.get('name') == required_step for s in steps) == 1, 'Missing/duplicate native build gate: ' + required_step)
    require(native.get('effective_dotnet_sdk') == args.dotnet_sdk_version, 'Native build used a different dotnet SDK.')
    packages = {}
    for path in sorted(feed.glob('*.nupkg')):
        ordinary(path)
        with zipfile.ZipFile(path) as archive:
            metadata = ET.fromstring(archive.read(next(n for n in archive.namelist() if n.endswith('.nuspec'))))
            fields = {node.tag.split('}')[-1]: node.text for node in metadata.iter()}
        if fields.get('id') in PACKAGES:
            name = fields['id']
            require(fields.get('version') == args.sdk_version and name not in packages, 'Mismatched/ambiguous SDK package: ' + name)
            require(native.get('sdk_packages', {}).get(path.name) == sha(path), 'Generated package differs from native receipt: ' + name)
            packages[name] = dict(path=str(path), sha256=sha(path))
    require(set(packages) == set(PACKAGES), 'Exact four generated SDK/API packages required.')
    identity = dict(engine_source_hash=args.engine_hash, engine_sha256=sha(engine), sdk_version=args.sdk_version,
                    managed_api_sha256=sha(api), managed_release_api_sha256=sha(release), native_receipt_sha256=sha(receipt_path))
    output.mkdir(parents=True)
    evidence = output / 'evidence'; evidence.mkdir()
    shutil.copy2(receipt_path, evidence / 'native-build-manifest.json')
    work = output / 'work'; work.mkdir()
    project = work / 'project'; project.mkdir()
    for name in before:
        (project / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(input_path(PROJECT, name), project / name)
    dump(work / 'global.json', {'sdk': {'version': args.dotnet_sdk_version, 'rollForward': 'disable', 'allowPrerelease': False}})
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
    run = dict(identity, contract='native-taa-occlusion-run-v1', source_sha256=before, support_sha256=support,
               engine=str(engine), sdk_feed=str(feed), sdk_packages=packages, project=str(project), commands=[],
               gpu_requested=args.run_gpu, gpu_slot_released=args.gpu_slot_released, native_receipt=str(receipt_path),
               disposable_paths=[str(work)], retained_work_reason='operator --keep-work' if args.keep_work else None)
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
                    if process.poll() is None:
                        if os.name == 'nt':
                            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], check=False, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
                        else:
                            os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=30)
                    raise
            step['exit_code'] = process.returncode
            require(process.returncode == 0, label + ' failed; inspect ' + str(log))
        finally:
            if log.is_file():
                step['log_sha256'] = sha(log)
            dump(output / (label + '-exit.json'), step); dump(output / 'run.json', run)
        return log

    def unchanged():
        require(source_hashes(PROJECT) == before == source_hashes(project), 'Fixture sources changed.')
        require({name: sha(HERE / name) for name in support} == support, 'Runner/validator support changed.')
        require(sha(engine) == identity['engine_sha256'] and sha(api) == identity['managed_api_sha256']
                and sha(release) == identity['managed_release_api_sha256'] and sha(receipt_path) == identity['native_receipt_sha256'], 'Native/API/receipt payload changed.')
        require(all(sha(Path(value['path'])) == value['sha256'] for value in packages.values()), 'SDK feed changed.')

    try:
        dump(output / 'cpu-scenario-preflight.json', dict(preflight_scenarios(scenarios),
             scenarios_sha256=before['scenarios.json'], plan_sha256=before['gate_plan.json']))
        log = execute('dotnet-version', [args.dotnet, '--version'])
        require(log.read_bytes().decode('utf-8-sig').strip() == args.dotnet_sdk_version, 'Effective dotnet SDK differs.')
        obj = (work / 'obj').as_posix() + '/'
        props = ['-p:ArenaGodotSdkVersion=' + args.sdk_version, '-p:BaseIntermediateOutputPath=' + obj,
                 '-p:MSBuildProjectExtensionsPath=' + obj, '-p:IntermediateOutputPath=' + obj + 'Debug/',
                 '-p:UseSharedCompilation=false', '-p:ImportDirectoryBuildProps=false', '-p:ImportDirectoryBuildTargets=false']
        csproj = project / 'OcclusionTaaLab.csproj'
        execute('restore', [args.dotnet, 'restore', csproj, '--configfile', config, '--disable-parallel', *props])
        execute('build', [args.dotnet, 'build', csproj, '--configuration', 'Debug', '--no-restore', '--no-incremental',
                          '--disable-build-servers', '-maxcpucount:2', *props])
        assets = load_json(Path(obj) / 'project.assets.json')
        for name in PACKAGES[1:]:
            require([v for v in assets['libraries'] if v.startswith(name + '/')] == [name + '/' + args.sdk_version], 'Resolved API differs: ' + name)
        for name, package in packages.items():
            cached = work / 'nuget-packages' / name.lower() / args.sdk_version.lower() / (name.lower() + '.' + args.sdk_version.lower() + '.nupkg')
            require(cached.is_file() and sha(cached) == package['sha256'], 'Restored SDK bytes differ: ' + name)
        assembly = project / '.godot/mono/temp/bin/Debug/OcclusionTaaLab.dll'
        require(sha(assembly.parent / 'GodotSharp.dll') == identity['managed_release_api_sha256'], 'Compile-reference API differs.')
        run.update(managed_fixture_assembly_sha256=sha(assembly), dotnet_sdk_version=args.dotnet_sdk_version)
        shutil.copy2(assembly, evidence / 'OcclusionTaaLab.dll')
        unchanged(); dump(output / 'run.json', run)
        if not args.run_gpu:
            dump(output / 'acceptance.json', dict(identity, compiled=True, gpu_executed=False, passed=None,
                 native_render_contract_passed=False, native_qualified=False, performance_qualified=False,
                 status='compiled_gpu_pending', source_sha256=before, managed_fixture_assembly_sha256=run['managed_fixture_assembly_sha256']))
            print(json.dumps({'compiled': True, 'gpu_executed': False, 'native_render_contract_passed': False, 'planned_preparations': 128, 'counted_initialization_renders': 1})); return 0
        record = output / 'contract.json'
        log = execute('runtime', [engine, '--path', project, '--rendering-method', 'forward_plus', '--rendering-driver', 'vulkan', '--',
                      '--record=' + str(record), '--expected-engine-hash=' + args.engine_hash,
                      '--expected-engine-sha256=' + identity['engine_sha256'], '--expected-sdk-version=' + args.sdk_version])
        diagnostics = [dict(line=i, text=line) for i, line in enumerate(log.read_bytes().decode('utf-8', errors='replace').splitlines(), 1) if DIAGNOSTIC.search(line)]
        dump(output / 'shutdown.json', dict(passed=not diagnostics, diagnostics=diagnostics, runtime_log_sha256=sha(log)))
        require(not diagnostics, 'Unexpected runtime/shutdown diagnostics.')
        expected = dict(engine_hash=args.engine_hash, engine_sha256=identity['engine_sha256'], sdk_version=args.sdk_version,
                        managed_api_assembly_sha256=identity['managed_api_sha256'], managed_fixture_assembly_sha256=run['managed_fixture_assembly_sha256'], source_sha256=before, plan_sha256=sha(HERE / 'gate_plan.json'))
        dump(output / 'expected.json', expected)
        verdict = validate(load_json(record), plan, expected, output, scenarios)
        unchanged(); require(sha(assembly) == run['managed_fixture_assembly_sha256'], 'Managed fixture changed.')
        dump(output / 'acceptance.json', dict(identity, **verdict, compiled=True, gpu_executed=True, passed=True,
             native_qualified=False, source_sha256=before, managed_fixture_assembly_sha256=run['managed_fixture_assembly_sha256'],
             contract_sha256=sha(record), shutdown_sha256=sha(output / 'shutdown.json')))
        print('Controlled native TAA/E8 numerical contract PASS. No performance/general renderer qualification.'); return 0
    except Exception as error:
        dump(output / 'acceptance.json', dict(passed=False, compiled=any(s['label'] == 'build' and s['exit_code'] == 0 for s in run['commands']),
             gpu_executed=any(s['label'] == 'runtime' for s in run['commands']), native_render_contract_passed=False,
             native_qualified=False, performance_qualified=False, failure=str(error)))
        print('Native TAA stand FAIL: ' + str(error)); return 2
    finally:
        if not args.keep_work:
            try:
                remove_owned_work(work, output); run['cleaned_paths'] = [str(work)]
            except Exception as error:
                run['cleanup_blocker'] = str(error)
                print('Private work retained: ' + str(error))
        dump(output / 'run.json', run)


if __name__ == '__main__':
    raise SystemExit(main())

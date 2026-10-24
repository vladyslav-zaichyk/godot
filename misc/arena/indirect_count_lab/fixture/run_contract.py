"""Compile the small E10a consumer against an exact matching bundle.

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

from validate_receipt import validate

HERE = Path(__file__).resolve().parent
PROJECT = HERE / 'project'
INPUTS = ('IndirectCountLab.csproj', 'project.godot', 'Main.tscn', 'IndirectCountContract.cs',
          'ContractErrorLogger.cs', 'DrawVertex.glslinc', 'DrawFragment.glslinc', 'Producer.glslinc')
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', required=True, type=Path)
    parser.add_argument('--sdk-feed', required=True, type=Path)
    parser.add_argument('--sdk-version', default=os.environ.get('ArenaGodotSdkVersion'))
    parser.add_argument('--engine-hash', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--dotnet', default='dotnet')
    parser.add_argument('--timeout', type=int, default=300)
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
    if not engine.is_file() or not feed.is_dir() or not args.sdk_version or args.timeout < 1:
        parser.error('Matching editor/feed/SDK and positive timeout are required.')
    if not re.fullmatch('[0-9a-f]{40}', args.engine_hash):
        parser.error('Use the exact full lowercase native source SHA.')
    api = engine.parent / 'GodotSharp/Api/Debug/GodotSharp.dll'
    release_api = engine.parent / 'GodotSharp/Api/Release/GodotSharp.dll'
    api_xml = api.with_suffix('.xml')
    if not api.is_file() or not release_api.is_file() or not api_xml.is_file() or 'DrawListDrawIndirectCount' not in api_xml.read_text(encoding='utf-8'):
        parser.error('Complete matching E10a native/managed bundle required; earlier SDKs are incompatible.')
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
               runner_sha256=sha(Path(__file__)), validator_sha256=sha(HERE / 'validate_receipt.py'),
               project=str(project), commands=[], gpu_requested=args.run_gpu)
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
        csproj = project / 'IndirectCountLab.csproj'
        execute('restore', [args.dotnet, 'restore', csproj, '--configfile', config, '--disable-parallel', *properties])
        execute('build', [args.dotnet, 'build', csproj, '--configuration', 'Debug', '--no-restore', '--no-incremental',
                          '--disable-build-servers', '-maxcpucount:2', *properties])
        assets = json.loads((Path(obj) / 'project.assets.json').read_bytes())
        for name in PACKAGES[1:]:
            require([v for v in assets['libraries'] if v.startswith(name + '/')] == [name + '/' + args.sdk_version], 'Resolved API version differs: ' + name)
        for name, package in packages.items():
            cached = work / 'nuget-packages' / name.lower() / args.sdk_version.lower() / (name.lower() + '.' + args.sdk_version.lower() + '.nupkg')
            require(cached.is_file() and sha(cached) == package['sha256'], 'Restored package differs: ' + name)
        assembly = project / '.godot/mono/temp/bin/Debug/IndirectCountLab.dll'
        built_api = assembly.parent / 'GodotSharp.dll'
        require(built_api.is_file() and sha(built_api) == run['managed_release_api_sha256'],
                'Built fixture reference differs from the exact SDK/Release API payload.')
        run['managed_fixture_reference_api_sha256'] = sha(built_api)
        run['managed_fixture_assembly_sha256'] = sha(assembly)
        require(source_hashes(PROJECT) == before == source_hashes(project), 'Fixture source changed while building.')
        require(sha(HERE / 'validate_receipt.py') == run['validator_sha256'], 'Receipt validator changed.')
        require(sha(engine) == run['engine_sha256'] and sha(api) == run['managed_api_sha256']
                and sha(release_api) == run['managed_release_api_sha256'],
                'Native bundle changed while compiling.')
        require(all(sha(Path(p['path'])) == p['sha256'] for p in packages.values()), 'Feed packages changed.')
        dump(output / 'run.json', run)
        if not args.run_gpu:
            dump(output / 'acceptance.json', dict(compiled=True, gpu_executed=False, passed=None,
                 performance_qualified=False, source_sha256=before, managed_fixture_assembly_sha256=sha(assembly)))
            print('E10a consumer compiled; GPU contract NOT RUN: ' + str(output / 'acceptance.json'))
            return 0
        record = output / 'contract.json'
        execute('runtime', [engine, '--path', project, '--rendering-method', 'forward_plus', '--rendering-driver', 'vulkan',
                           '--', '--record=' + str(record), '--expected-engine-hash=' + args.engine_hash,
                           '--expected-sdk-version=' + args.sdk_version])
        diagnostics = [dict(line=i, text=line) for i, line in enumerate((output / 'runtime.log').read_text(encoding='utf-8', errors='replace').splitlines(), 1) if DIAGNOSTIC.search(line)]
        dump(output / 'shutdown.json', dict(passed=not diagnostics, diagnostics=diagnostics, runtime_log_sha256=sha(output / 'runtime.log')))
        require(not diagnostics, 'Runtime/shutdown diagnostics detected.')
        raw = json.loads(record.read_bytes())
        validate(raw, args)
        require(raw['source_sha256'] == before, 'Runtime source identity differs.')
        require(raw['managed_fixture_assembly_sha256'] == run['managed_fixture_assembly_sha256'] == sha(assembly), 'Fixture assembly differs.')
        require(raw['managed_api_assembly_sha256'] == run['managed_api_sha256'] == sha(api), 'Managed API differs.')
        require(sha(engine) == run['engine_sha256'] and source_hashes(PROJECT) == before == source_hashes(project)
                and sha(HERE / 'validate_receipt.py') == run['validator_sha256'], 'Engine/source/validator changed.')
        require(all(sha(Path(p['path'])) == p['sha256'] for p in packages.values()), 'Feed packages changed.')
        dump(output / 'acceptance.json', dict(passed=True, gpu_executed=True, performance_qualified=False,
             contract_sha256=sha(record), shutdown_sha256=sha(output / 'shutdown.json'), source_sha256=before))
        status = 'count-draw numerical subset PASS' if raw['supports_draw_indirect_count'] else 'unavailable guard PASS; positive count drawing NOT RUN'
        print('E10a ' + status + '; consult contract.not_qualified: ' + str(output / 'acceptance.json'))
        return 0
    except Exception as error:
        dump(output / 'acceptance.json', dict(passed=False, performance_qualified=False, failure=str(error)))
        print('E10a contract FAIL: ' + str(error))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

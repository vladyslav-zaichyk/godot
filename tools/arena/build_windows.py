"""Build a pinned Windows editor and its matching managed API in a private workspace."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
PIN = 'ed1daf0bf001b61586d9930840f2f1394092c079'


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args], text=True).strip()


def dump(path, value):
    path.write_bytes((json.dumps(value, indent=2) + '\n').encode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, choices=range(1, 7), default=4)
    parser.add_argument('--work', type=Path, default=ROOT / '.arena')
    args = parser.parse_args()
    if os.name != 'nt':
        raise SystemExit('This recipe targets Windows x64; use a platform-specific qualified recipe elsewhere.')
    if git('status', '--porcelain', '--untracked-files=all'):
        raise SystemExit('Commit source changes before producing an identified engine build.')
    git('merge-base', '--is-ancestor', PIN, 'HEAD')
    commit = git('rev-parse', 'HEAD')
    status = 'arena-dev-g' + commit[:10]
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    run = work / ('build-' + commit[:12])
    if run.exists():
        raise SystemExit('This commit already has a build record; retain it and choose a fresh --work for a retry.')
    run.mkdir()
    env = dict(os.environ)
    for key in ('SCONS_CACHE', 'SCONS_CACHE_LIMIT', 'SCONSFLAGS'):
        env.pop(key, None)
    private = {'TEMP': 'tmp', 'TMP': 'tmp', 'APPDATA': 'appdata', 'LOCALAPPDATA': 'localappdata',
               'NUGET_PACKAGES': 'packages', 'NUGET_HTTP_CACHE_PATH': 'nuget-http',
               'NUGET_PLUGINS_CACHE_PATH': 'nuget-plugins', 'NUGET_SCRATCH': 'nuget-scratch',
               'DOTNET_CLI_HOME': 'dotnet-home', 'PYTHONPYCACHEPREFIX': 'pycache'}
    for key, folder in private.items():
        path = work / folder
        path.mkdir(exist_ok=True)
        env[key] = str(path)
    env.update(GODOT_VERSION_STATUS=status, DOTNET_CLI_TELEMETRY_OPTOUT='1',
               DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1', DOTNET_CLI_USE_MSBUILD_SERVER='0',
               MSBUILDDISABLENODEREUSE='1', UseSharedCompilation='false')
    feed = work / 'nuget-source'
    feed.mkdir(exist_ok=True)
    config = work / 'NuGet.Config'
    config.write_bytes(('<?xml version="1.0" encoding="utf-8"?><configuration><packageSources><clear />'
                       f'<add key="ArenaLocal" value="{feed.as_posix()}" />'
                       '<add key="nuget.org" value="https://api.nuget.org/v3/index.json" />'
                       '</packageSources></configuration>\n').encode())
    env['RestoreConfigFile'] = str(config)
    nuget_config = work / 'appdata/NuGet'
    nuget_config.mkdir(parents=True, exist_ok=True)
    (nuget_config / 'NuGet.Config').write_bytes(config.read_bytes())
    record = {'source_commit': commit, 'upstream_base': PIN, 'status': status,
              'work': str(work), 'python': sys.version, 'steps': []}
    sdk = subprocess.check_output(['dotnet', '--version'], cwd=ROOT / 'modules/mono', env=env, text=True).strip()
    if sdk != '10.0.203':
        raise SystemExit(f'Expected .NET SDK 10.0.203; effective SDK is {sdk}.')
    record['effective_dotnet_sdk'] = sdk
    dump(run / 'manifest.json', record)

    def execute(name, command):
        started = time.time()
        with (run / (name + '.log')).open('wb') as log:
            process = subprocess.Popen([str(x) for x in command], cwd=ROOT, env=env,
                                       stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS)
            code = process.wait()
        record['steps'].append({'name': name, 'command': [str(x) for x in command],
                                'seconds': time.time() - started, 'exit_code': code})
        dump(run / 'manifest.json', record)
        print(f'{name}: {"PASS" if code == 0 else "FAIL"}', flush=True)
        if code:
            raise SystemExit(f'Build stopped; inspect {run / (name + ".log")}')

    venv = work / 'venv'
    if not (venv / 'Scripts/python.exe').exists():
        execute('venv', [sys.executable, '-m', 'venv', venv])
    python = venv / 'Scripts/python.exe'
    execute('scons-install', [python, '-m', 'pip', 'install', '--disable-pip-version-check', 'SCons==4.8.1'])
    flags = ['platform=windows', 'target=editor', 'arch=x86_64', 'module_mono_enabled=yes',
             'tests=yes', 'dev_build=no', 'optimize=speed_trace', 'd3d12=no', 'accesskit=no',
             'angle=no', 'debug_symbols=yes', 'lto=none', 'msvc_version=14.3',
             'mssdk_version=10.0.22621.0', 'extra_suffix=arena_dev', f'-j{args.jobs}']
    execute('native', [python, '-m', 'SCons', *flags])
    binary = ROOT / 'bin/godot.windows.editor.x86_64.arena_dev.mono.exe'
    (ROOT / 'bin/_sc_').write_bytes(b'')
    execute('glue', [binary, '--headless', '--generate-mono-glue', ROOT / 'modules/mono/glue'])
    execute('managed', [python, ROOT / 'modules/mono/build_scripts/build_assemblies.py',
                        '--godot-output-dir', ROOT / 'bin', '--godot-platform', 'windows',
                        '--push-nupkgs-local', feed])
    execute('native-contracts', [binary, '--headless', '--test',
                                '--test-case=[AABB]*,[Transform3D]*,[Basis]*,*[BufferRD]*,*[ShaderDriven]*,*[MeshRD]*', '--no-colors'])
    record['sdk_packages'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in feed.glob('*.nupkg')}
    record['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
    record['sdk_versions'] = subprocess.check_output(['dotnet', '--list-sdks'], env=env, text=True).splitlines()
    record['gpu_validation'] = 'not run: execute the separately versioned real-GPU fixture'
    dump(run / 'manifest.json', record)
    print(f'Editor: {binary}\nLocal SDK feed: {feed}\nReceipt: {run / "manifest.json"}')


if __name__ == '__main__':
    main()

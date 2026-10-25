"""Compile the small E8 consumer against an explicit matching bundle.

Compilation is the default. --run-gpu additionally opens a real Vulkan/Forward+
window: wait for the renderer A/B owner to release the GPU first. No engine build,
screenshots, global NuGet changes or performance measurements. All generated
files go to one fresh --output directory outside the source checkout.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr
import zipfile

HERE = Path(__file__).resolve().parent
PROJECT = HERE / 'project'
INPUTS = ('GpuHistoryLab.csproj', 'project.godot', 'Main.tscn', 'GpuHistoryContract.cs',
          'HistoryCapture.cs', 'HistoryErrorLogger.cs', 'Consumer.gdshader',
          'Producer.glslinc', 'Capture.glslinc')
PACKAGES = ('Godot.NET.Sdk', 'GodotSharp', 'GodotSharpEditor', 'Godot.SourceGenerators')
DIAGNOSTIC = re.compile(r'\b(?:ERROR:|SCRIPT ERROR:|WARNING:|FATAL(?: ERROR)?[: ]|Unhandled exception)|'
                        r'ObjectDB instances leaked|Orphan StringName|RID allocations|Op[A-Za-z0-9_]+ is not supported yet', re.I)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, value):
    path.write_bytes((json.dumps(value, indent=2) + '\n').encode())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def source_hashes(root):
    return {name: sha(root / name) for name in INPUTS}


def validate_pixel_coverage(row, current_clips):
    require(type(row['x']) is int and type(row['y']) is int
            and 0 <= row['x'] < 128 and 0 <= row['y'] < 64, 'Sample outside pixel grid.')
    require(row['triangle'] in ([0, 1, 2], [0, 2, 3]), 'Unexpected fixed mesh triangle.')
    require(len(current_clips) == 4 and all(len(q) == 4 and all(math.isfinite(v) for v in q)
            and abs(q[3]) > 1e-8 for q in current_clips), 'Invalid current clip geometry.')
    corners = [((q[0] / q[3] * .5 + .5) * 128, (q[1] / q[3] * .5 + .5) * 64)
               for q in current_clips]
    a, b, c = [corners[i] for i in row['triangle']]
    p = row['x'] + .5, row['y'] + .5
    sub = lambda u, v: (u[0] - v[0], u[1] - v[1])
    cross = lambda u, v: u[0] * v[1] - u[1] * v[0]
    area = cross(sub(b, a), sub(c, a))
    require(abs(area) > 1e-8, 'Degenerate current triangle.')
    first, second = cross(sub(b, p), sub(c, p)) / area, cross(sub(c, p), sub(a, p)) / area
    weights = [first, second, 1 - first - second]
    require(len(row['barycentric']) == 3 and min(weights) > 0
            and all(math.isfinite(v) for v in row['barycentric'])
            and all(abs(a - b) < 1e-5 for a, b in zip(weights, row['barycentric'])),
            'Pixel does not reproduce current-triangle barycentrics.')
    for u, v in ((a, b), (b, c), (c, a)):
        edge = sub(v, u)
        distance = abs(cross(edge, sub(p, u))) / math.hypot(*edge)
        require(distance >= 2 - 1e-5, 'Pixel is too close to a triangle edge.')
    depth = sum(weights[j] * current_clips[i][2] / current_clips[i][3]
                for j, i in enumerate(row['triangle']))
    require(math.isfinite(row['expected_depth']) and math.isfinite(row['actual_depth'])
            and row['actual_depth'] > 0 and abs(depth - row['expected_depth']) <= 1e-5
            and abs(depth - row['actual_depth']) <= 1e-5, 'Independent projected depth differs.')


def validate(raw, source, sdk, capacity):
    expected = dict(contract='gpu-history-numeric-contract-v1', passed=True, engine_hash=source,
                    sdk_version=sdk, driver='vulkan', renderer='forward_plus', capacity=capacity,
                    viewport=[128, 64], first_instance=3, draw_slot_capacity=8, pose_bank_count=2,
                    native_transform_input_bytes=0, aborted_epochs=1, lifecycle_cycles=32,
                    gpu_buffer_readbacks=3, per_frame_gpu_readbacks=0, final_owned_rd_resource_count=0,
                    terminal_velocity_capture_readback_completed=True, image_files_written=0,
                    performance_window_count=0, performance_qualified=False,
                    requested_history_input_bytes=144 * capacity + 84, capture_scene_bytes_per_epoch=5696,
                    cases=95, prepared_epochs=95, committed_epochs=94, compositor_captures=94)
    for key, value in expected.items():
        require(raw.get(key) == value, 'Contract field differs: ' + key)
    require(not raw.get('failure') and raw.get('native_diagnostics') == [], 'Runtime diagnostics/failure.')
    require(raw['prepared_epochs'] == raw['cases'] and raw['committed_epochs'] == raw['cases'] - 1
            and raw['compositor_captures'] == raw['committed_epochs'], 'Epoch/ack count differs.')
    rows = raw['epoch_audits']
    require(len(rows) == raw['cases'] and [r['epoch'] for r in rows] == list(range(1, len(rows) + 1)),
            'Missing/duplicate/out-of-order epoch audit.')
    named = {row['name']: row for row in rows}
    required = {'static_zero', 'root_positive', 'camera_only', 'deformation_positive_no_time',
                'deformation_negative_no_time', 'camera_root_deformation', 'compaction_reorder',
                'generation_reuse', 'teleport_continuity', 'domain_fallback', 'representation_fallback',
                'stale_epoch_fallback', 'valid_id_zero', 'hidden_unit', 'hidden_reentry_reset',
                'skipped_speculative', 'resume_reseed', 'invalid_current_id', 'invalid_count_overflow',
                'invalid_current_generation', 'frozen_sample_first', 'frozen_sample_second',
                'multiple_ticks_root_negative', 'jitter_static', 'jitter_camera', 'fsr2_routing_motion'}
    require(required <= set(named), 'Missing named contract case.')
    require(sum(r['name'].startswith('stress_') and r['name'] != 'stress_warmup' for r in rows) == 64,
            'Expected exactly 64 no-readback stress epochs.')
    skipped = named['skipped_speculative']
    require(not skipped['committed'] and not skipped['post_opaque'], 'Speculative epoch committed.')
    require(named['resume_reseed']['reseeded'], 'Abort recovery did not reseed both banks.')
    for name, error in [('invalid_current_id', 2), ('invalid_count_overflow', 1), ('invalid_current_generation', 4)]:
        require(named[name]['diagnostic'] == error and named[name]['instance_count'] == 0,
                'Invalid current payload did not suppress the full batch.')
    pixels = raw['pixel_audits']
    require(len(pixels) == 724 and all(r['tolerance_uv'] == 2 ** -12 for r in pixels),
            'Incomplete fixed pixel ledger or flexible numeric budget.')
    counts = {}
    sampled_pixels = set()
    nonzero = 0
    references = {}
    base_vertices = ((-.375, -.375, 0), (.375, -.375, 0), (.375, .375, 0), (-.375, .375, 0))
    def project(projection, view, pose, vertex):
        x, y, z = vertex
        x, y = x + pose[2] * y + pose[0], y + pose[1]
        q = [view[i * 4] * x + view[i * 4 + 1] * y + view[i * 4 + 2] * z + view[i * 4 + 3] for i in range(3)] + [1]
        return [sum(projection[col * 4 + row] * q[col] for col in range(4)) for row in range(4)]
    for epoch in rows:
        if not epoch['committed']:
            continue
        for identity in epoch['identities']:
            clips = []
            for prefix in ('current', 'previous'):
                require(len(epoch[prefix + '_projection']) == 16
                        and len(epoch[prefix + '_view_transposed_3x4']) == 12
                        and len(identity[prefix + '_pose']) == 3
                        and len(identity[prefix + '_clip']) == 4
                        and all(len(q) == 4 for q in identity[prefix + '_clip']),
                        'Incomplete captured matrix/pose/clip dimensions.')
                actual = [project(epoch[prefix + '_projection'], epoch[prefix + '_view_transposed_3x4'],
                                  identity[prefix + '_pose'], v) for v in base_vertices]
                require(all(abs(a - b) < 1e-5 for ar, br in zip(actual, identity[prefix + '_clip']) for a, b in zip(ar, br)),
                        'Captured matrices/pose do not reproduce clip oracle.')
                clips.append(actual)
            references[epoch['epoch'], identity['id']] = epoch, clips
    for row in pixels:
        key = row['epoch'], row['id']
        pixel_key = (*key, row['x'], row['y'])
        require(pixel_key not in sampled_pixels, 'Duplicate numerical pixel sample.')
        sampled_pixels.add(pixel_key)
        counts[key] = counts.get(key, 0) + 1
        require(0 <= row['x'] < 128 and 0 <= row['y'] < 64, 'Sample outside viewport.')
        require(len(row['barycentric']) == 3 and min(row['barycentric']) > 0
                and abs(sum(row['barycentric']) - 1) < 1e-5, 'Invalid triangle coverage oracle.')
        require(math.isfinite(row['actual_depth']) and row['actual_depth'] > 0
                and abs(row['actual_depth'] - row['expected_depth']) <= 1e-5, 'Independent depth coverage failed.')
        require(len(row['actual_uv']) == 2 and len(row['expected_uv']) == 2, 'Velocity must have two components.')
        require(all(math.isfinite(v) for k in ('actual_uv', 'expected_uv') for v in row[k]), 'Nonfinite velocity.')
        require(all(abs(a - b) <= 2 ** -12 for a, b in zip(row['actual_uv'], row['expected_uv'])), 'Velocity differs.')
        epoch, clips = references[key]
        validate_pixel_coverage(row, clips[0])
        expected = []
        for component in range(2):
            ndc = [sum(row['barycentric'][i] * cs[row['triangle'][i]][component] / cs[row['triangle'][i]][3]
                       for i in range(3)) for cs in clips]
            expected.append(.5 * ((ndc[1] - epoch['previous_jitter'][component]) - (ndc[0] - epoch['current_jitter'][component])))
        require(all(abs(a - b) < 1e-5 for a, b in zip(expected, row['expected_uv'])), 'Independent projection/barycentric oracle differs.')
        if row['designated_nonzero_x']:
            require(2 ** -8 <= abs(expected[0]) <= 2 ** -3, 'Designated X oracle outside fixed range.')
        if row['name'] == 'camera_only':
            require(2 ** -8 <= abs(expected[1]) <= 2 ** -3, 'Designated Y oracle outside fixed range.')
        if math.hypot(*row['expected_uv']) > 2 ** -8:
            nonzero += 1
            require(math.hypot(*row['actual_uv']) > 2 ** -9, 'Nonzero case accepted zero output.')
    require(nonzero >= 64 and all(n == 4 for n in counts.values()), 'Incomplete four-pixel/nonzero coverage.')
    for row in rows:
        if row['committed'] and not row['diagnostic']:
            require(sum(n for (epoch, _), n in counts.items() if epoch == row['epoch']) == 4 * row['instance_count'],
                    'Missing numerical instance samples.')
    require(len(raw['api_audits']) == 32 and all(r['history_query_true'] and r['no_transform_pool']
            and r['detach_reset'] and r['ordinary_reset'] and r['implicit_free'] for r in raw['api_audits']),
            'Incomplete lifecycle/API proof.')
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
    parser.add_argument('--capacity', type=int, default=16)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--run-gpu', action='store_true', help='Explicit GPU launch; wait for the parallel A/B owner.')
    mode.add_argument('--compile-only', action='store_true', help='Default: build the managed consumer without launching Godot.')
    args = parser.parse_args()
    engine, feed, output = args.engine.resolve(), args.sdk_feed.resolve(), args.output.resolve()
    checkout = next((p for p in HERE.parents if (p / '.git').exists()), HERE)
    if output.exists() or output == checkout or checkout in output.parents:
        parser.error('Use one fresh output directory outside this source checkout.')
    if not engine.is_file() or not feed.is_dir() or not args.sdk_version or args.timeout < 1 or not 8 <= args.capacity <= 64:
        parser.error('Matching editor/feed/SDK, positive timeout and capacity 8..64 are required.')
    if not re.fullmatch('[0-9a-f]{40}', args.engine_hash):
        parser.error('Use the exact full lowercase native source SHA.')
    api = engine.parent / 'GodotSharp/Api/Debug/GodotSharp.dll'
    api_xml = api.with_suffix('.xml')
    if not api.is_file() or not api_xml.is_file() or 'MultimeshUsesShaderMotionVectors' not in api_xml.read_text(encoding='utf-8'):
        parser.error('Complete matching E8 native/managed bundle required; E5/E6a-only SDKs are incompatible.')
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
               managed_api_sha256=sha(api), sdk_version=args.sdk_version, source_sha256=before,
               runner_sha256=sha(Path(__file__)), project=str(project), commands=[], gpu_requested=args.run_gpu)
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
        for path in feed.glob('*.nupkg'):
            with zipfile.ZipFile(path) as archive:
                nuspec = ET.fromstring(archive.read(next(n for n in archive.namelist() if n.endswith('.nuspec'))))
                metadata = {node.tag.split('}')[-1]: node.text for node in nuspec.iter()}
                name = metadata.get('id')
                if name in PACKAGES and metadata.get('version') == args.sdk_version:
                    require(name not in packages, 'Ambiguous generated package: ' + name)
                    packages[name] = dict(path=str(path), sha256=sha(path))
        require(set(packages) == set(PACKAGES), 'Exact generated SDK/API packages required.')
        run['sdk_packages'] = packages
        execute('dotnet-version', [args.dotnet, '--version'])
        obj = (work / 'obj').as_posix() + '/'
        properties = ['-p:ArenaGodotSdkVersion=' + args.sdk_version, '-p:BaseIntermediateOutputPath=' + obj,
                      '-p:MSBuildProjectExtensionsPath=' + obj, '-p:IntermediateOutputPath=' + obj + 'Debug/',
                      '-p:UseSharedCompilation=false', '-p:ImportDirectoryBuildProps=false', '-p:ImportDirectoryBuildTargets=false']
        csproj = project / 'GpuHistoryLab.csproj'
        execute('restore', [args.dotnet, 'restore', csproj, '--configfile', config, '--disable-parallel', *properties])
        execute('build', [args.dotnet, 'build', csproj, '--configuration', 'Debug', '--no-restore', '--no-incremental',
                          '--disable-build-servers', '-maxcpucount:2', *properties])
        assets = json.loads((Path(obj) / 'project.assets.json').read_bytes())
        for name in PACKAGES[1:]:
            require([v for v in assets['libraries'] if v.startswith(name + '/')] == [name + '/' + args.sdk_version], 'Resolved API version differs: ' + name)
        for name, package in packages.items():
            cached = work / 'nuget-packages' / name.lower() / args.sdk_version.lower() / (name.lower() + '.' + args.sdk_version.lower() + '.nupkg')
            require(cached.is_file() and sha(cached) == package['sha256'], 'Restored package differs: ' + name)
        assembly = project / '.godot/mono/temp/bin/Debug/GpuHistoryLab.dll'
        run['managed_fixture_assembly_sha256'] = sha(assembly)
        require(source_hashes(PROJECT) == before == source_hashes(project), 'Fixture source changed while building.')
        dump(output / 'run.json', run)
        if not args.run_gpu:
            dump(output / 'acceptance.json', dict(compiled=True, gpu_executed=False, passed=None,
                 performance_qualified=False, source_sha256=before, managed_fixture_assembly_sha256=sha(assembly)))
            print('E8 consumer compiled; GPU contract NOT RUN: ' + str(output / 'acceptance.json'))
            return 0
        record = output / 'contract.json'
        execute('runtime', [engine, '--path', project, '--rendering-method', 'forward_plus', '--rendering-driver', 'vulkan',
                           '--', '--record=' + str(record), '--expected-engine-hash=' + args.engine_hash,
                           '--expected-sdk-version=' + args.sdk_version, '--capacity=' + str(args.capacity)])
        diagnostics = [dict(line=i, text=line) for i, line in enumerate((output / 'runtime.log').read_text(encoding='utf-8', errors='replace').splitlines(), 1) if DIAGNOSTIC.search(line)]
        dump(output / 'shutdown.json', dict(passed=not diagnostics, diagnostics=diagnostics, runtime_log_sha256=sha(output / 'runtime.log')))
        require(not diagnostics, 'Runtime/shutdown diagnostics detected.')
        raw = json.loads(record.read_bytes())
        validate(raw, args.engine_hash, args.sdk_version, args.capacity)
        require(raw['source_sha256'] == before, 'Runtime source identity differs.')
        require(raw['managed_fixture_assembly_sha256'] == run['managed_fixture_assembly_sha256'] == sha(assembly), 'Fixture assembly differs.')
        require(raw['managed_api_assembly_sha256'] == run['managed_api_sha256'] == sha(api), 'Managed API differs.')
        require(sha(engine) == run['engine_sha256'] and source_hashes(PROJECT) == before == source_hashes(project), 'Engine/source changed.')
        require(all(sha(Path(p['path'])) == p['sha256'] for p in packages.values()), 'Feed packages changed.')
        dump(output / 'acceptance.json', dict(passed=True, gpu_executed=True, performance_qualified=False,
             contract_sha256=sha(record), shutdown_sha256=sha(output / 'shutdown.json'), source_sha256=before))
        print('E8 numerical subset PASS; consult contract.not_qualified: ' + str(output / 'acceptance.json'))
        return 0
    except Exception as error:
        dump(output / 'acceptance.json', dict(passed=False, performance_qualified=False, failure=str(error)))
        print('E8 contract FAIL: ' + str(error))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

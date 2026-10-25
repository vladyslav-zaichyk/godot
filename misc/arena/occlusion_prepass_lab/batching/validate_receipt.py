"""Independent ordinary-transform batching oracle; importing never launches Godot."""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import struct
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
MATH_PATH = next((p for p in (HERE.parent / 'taa/validate_receipt.py',
                             HERE.parent / 'occlusion_taa_lab/validate_receipt.py') if p.is_file()), None)
if MATH_PATH is None:
    raise ImportError('The exact sibling E13c numerical math helper is required.')
_spec = importlib.util.spec_from_file_location('arena_e13d_e13c_math', MATH_PATH)
math_helper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(math_helper)
require, load_json, sha = math_helper.require, math_helper.load_json, math_helper.sha
decode_scene, project = math_helper.decode_scene, math_helper.project
barycentric_pixel, perspective_velocity = math_helper.barycentric_pixel, math_helper.perspective_velocity
DIAGNOSTIC = math_helper.DIAGNOSTIC
GUARD, WIDTH, HEIGHT, COUNT = 0xCDCDCDCD, 128, 64, 24
EARLY_STRIDE, RAW_STRIDE, UBO_STRIDE = 32832, 131136, 5696
INPUTS = ('BatchingLab.csproj', 'project.godot', 'Main.tscn', 'Main.cs', 'HistoryCapture.cs',
          'ProbeLogger.cs', 'Ordinary.gdshader', 'Early.comp', 'Capture.comp', 'gate_plan.json')


def helper_identity():
    return dict(path=str(MATH_PATH), sha256=sha(MATH_PATH))


def transform(root, vertex):
    return [sum(root[r * 4 + c] * (*vertex, 1)[c] for c in range(4)) for r in range(3)]


def clips(scene, roots, vertices):
    return [[project(scene, transform(root, vertex)) for vertex in vertices] for root in roots]


def choose_probes(current, triangles):
    """Choose four distinct robust pixels from actual captured, jittered geometry."""
    result = []
    for owner, projected in enumerate(current):
        screen = [((v[0] / v[3] * .5 + .5) * WIDTH, (v[1] / v[3] * .5 + .5) * HEIGHT) for v in projected]
        xmin, xmax = max(0, math.floor(min(p[0] for p in screen))), min(WIDTH - 1, math.ceil(max(p[0] for p in screen)))
        ymin, ymax = max(0, math.floor(min(p[1] for p in screen))), min(HEIGHT - 1, math.ceil(max(p[1] for p in screen)))
        choices = []
        for y in range(ymin, ymax + 1):
            for x in range(xmin, xmax + 1):
                for triangle in triangles:
                    try:
                        weights, depth = barycentric_pixel(projected, triangle, x, y, (WIDTH, HEIGHT), 2)
                    except ValueError:
                        continue
                    center = [sum(screen[t][c] for t in triangle) / 3 for c in range(2)]
                    distance = (x + .5 - center[0]) ** 2 + (y + .5 - center[1]) ** 2
                    choices.append((distance, y, x, tuple(triangle), weights, depth))
        distinct = {}
        for item in sorted(choices):
            distinct.setdefault((item[2], item[1]), item)
        require(len(distinct) >= 4, 'Four robust interior pixels missing for ordinary owner ' + str(owner))
        result.extend((owner, *value[1:]) for value in list(distinct.values())[:4])
    return result


def validate_plan(plan):
    require(plan['contract'] == 'arena-e13d-opaque-batching-plan-v1' and plan['extent'] == [WIDTH, HEIGHT]
            and plan['ordinary_owners'] == 6 and plan['registered_consumers'] == 0, 'Plan scope differs.')
    require(len(plan['records']) == COUNT and plan['initialization_viewport_records'] == 1, 'Scheduling differs.')
    require(plan['limits'] == dict(rows=COUNT, early_stride=EARLY_STRIDE, raw_stride=RAW_STRIDE,
            ubo_stride=UBO_STRIDE, terminal_readbacks=3, descriptor_identities=4,
            requested_ledger_bytes=COUNT * (EARLY_STRIDE + RAW_STRIDE + UBO_STRIDE)), 'Fixed capacities differ.')
    require(plan['oracle'] == dict(depth_tolerance=1e-5, velocity_tolerance=2 ** -12, identity_tolerance=1e-5,
            interior_edge_pixels=2, probes_per_owner=4, wrong_identity_gap=1 / 32,
            minimum_nonzero_motion=2 ** -9), 'Oracle tolerances differ.')
    require([row['phase_mask'] for row in plan['records']] == [63, 0, 7, 21, 42, 56] * 4, 'Phase matrix differs.')
    previous = plan['initial']['roots']
    for index, row in enumerate(plan['records']):
        require(row['id'] == 'B%03d' % (index + 1) and row['previous_roots'] == previous, 'Ordinary rendered history differs.')
        require(len(row['roots']) == 6 and len({tuple(root) for root in row['roots']}) == 6, 'Distinct owner transforms required.')
        for root in row['roots']:
            require(len(root) == 12 and all(math.isfinite(v) for v in root), 'Invalid affine transform.')
            require(root[0] > 0 and root[5] > 0 and root[10] > 0, 'Mirrored/degenerate transform outside fixture.')
        previous = row['roots']
    return dict(cpu_plan_passed=True, gpu_executed=False, planned_records=COUNT)


def words(blob, offset, count):
    require(offset >= 0 and offset + count * 4 <= len(blob), 'Truncated ledger.')
    return struct.unpack_from('<' + 'I' * count, blob, offset)


def floats(blob, offset, count):
    values = struct.unpack_from('<' + 'f' * count, blob, offset)
    require(all(math.isfinite(v) for v in values), 'Nonfinite captured attachment.')
    return values


def validate(raw, plan, expected, root):
    validate_plan(plan)
    require(raw.get('contract') == 'arena-e13d-opaque-batching-receipt-v1' and raw.get('passed') is True
            and raw.get('gpu_executed') is True and not raw.get('failure'), 'Incomplete runtime receipt.')
    for key, value in expected.items():
        require(raw.get(key) == value, 'Identity mismatch: ' + key)
    require(raw['driver'] == 'vulkan' and raw['renderer'] == 'forward_plus', 'Wrong renderer.')
    require(raw['callbacks'] == raw['observer_captures'] == COUNT and raw['gpu_readbacks'] == 3,
            'Native scheduling/readback totals differ.')
    require(raw['initialization_viewport_records'] == 1 and len(raw['records']) == COUNT,
            'Actual measured/initialization render totals differ.')
    require(raw['ordinary_owners'] == 6 and raw['registered_consumers'] == 0 and raw['caller_owned_final'] == 0
            and raw['diagnostics'] == [] and raw['dropped_diagnostics'] == 0 and raw['images_written'] == 0
            and raw['per_frame_gpu_readbacks'] == 0, 'Lifetime/diagnostic/scope contract differs.')
    require(raw['requested_ledger_bytes'] == plan['limits']['requested_ledger_bytes'], 'Ledger accounting differs.')
    blobs = {}
    for name, stride in (('early', EARLY_STRIDE), ('raw', RAW_STRIDE), ('ubo', UBO_STRIDE)):
        item = raw['payloads'][name]
        require(item['path'] == name + '.bin' and item['bytes'] == stride * COUNT, 'Payload shape/path differs.')
        path = root / item['path']
        require(sha(path) == item['sha256'], 'Payload digest differs.')
        blobs[name] = path.read_bytes()
        require(len(blobs[name]) == stride * COUNT, 'Payload size differs.')
    probes = nonzero = early_hits = early_misses = 0
    largest_velocity_error = largest_depth_error = 0.0
    jitter_phases = set()
    last_epoch = 0
    for index, (record, row) in enumerate(zip(raw['records'], plan['records'])):
        require(record['index'] == index and record['serial'] == index + 1 and record['id'] == row['id'], 'Record order differs.')
        outcome, producer = record['outcome'], record['producer']
        epoch, generation = producer['epoch'], producer['configuration_generation']
        require(epoch > last_epoch and generation > 0 and epoch == outcome['epoch']
                and generation == outcome['configuration_generation'], 'Native epoch/configuration differs.')
        last_epoch = epoch
        require(outcome['selected'] is True and outcome['reason'] == 'producer_acknowledged'
                and outcome['auto_instancing_disabled'] is False, 'Native split/batching not used.')
        require(all(outcome[key] is True for key in ('opaque_recorded', 'taa_process_recorded', 'frame_recording_completed')),
                'Opaque/TAA/completed recording evidence missing.')
        early_count = row['phase_mask'].bit_count()
        require(outcome['early_surface_count'] == early_count and outcome['remaining_surface_count'] == 6 - early_count,
                'Wrong eligibility classification.')
        original = outcome['original_opaque_repeat_group_count']
        a, b = outcome['early_depth_repeat_group_count'], outcome['remaining_depth_repeat_group_count']
        require(original == 1, 'Ordinary fixture did not form exactly one stock compatible run.')
        if early_count in (0, 6):
            require((a, b) == ((1, 0) if early_count == 6 else (0, 1)), 'Homogeneous phase batching lost.')
        else:
            require(1 <= a <= early_count and 1 <= b <= 6 - early_count and 2 <= a + b <= 6,
                    'Mixed phase run accounting invalid.')
        early = index * EARLY_STRIDE
        expected_header = (index + 1, epoch & 0xffffffff, epoch >> 32, generation & 0xffffffff,
                           generation >> 32, WIDTH, HEIGHT) + (GUARD,) * 9
        require(words(blobs['early'], early, 16) == expected_header, 'Producer ledger/guards/epoch differ.')
        image = index * RAW_STRIDE
        require(words(blobs['raw'], image, 16) == (index + 1, WIDTH, HEIGHT) + (GUARD,) * 13,
                'Observer ledger/guards differ.')
        current = decode_scene(blobs['ubo'], index * UBO_STRIDE)
        previous = decode_scene(blobs['ubo'], index * UBO_STRIDE + 2848)
        jitter_phases.add(tuple(current['jitter']))
        current_clips = clips(current, row['roots'], plan['vertices'])
        previous_clips = clips(previous, row['previous_roots'], plan['vertices'])
        matrix = math_helper.floats(producer['world_to_clip_words'])
        for point in ((0, 0, 0), (1, 2, -.1), (-2, -.5, .2)):
            actual = project(current, point)
            other = [sum(matrix[c * 4 + r] * (*point, 1)[c] for c in range(4)) for r in range(4)]
            require(max(abs(x - y) for x, y in zip(actual, other)) < 3e-5, 'Producer/opaque projection mismatch.')
        row_nonzero = 0
        for owner, y, x, triangle, weights, depth in choose_probes(current_clips, plan['triangles']):
            pixel = y * WIDTH + x
            native_early = floats(blobs['early'], early + 64 + pixel * 4, 1)[0]
            velocity_x, velocity_y, native_depth, identity = floats(blobs['raw'], image + 64 + pixel * 16, 4)
            require(abs(identity - (owner + 1) / 32) <= 1e-5, 'Wrong ordinary InstanceData/instance uniform identity.')
            depth_error = abs(native_depth - depth)
            require(depth_error <= 1e-5, 'Opaque native transform/depth differs.')
            if row['phase_mask'] & (1 << owner):
                require(abs(native_early - depth) <= 1e-5, 'Early depth omitted/wrong owner transform.')
                early_hits += 1
            else:
                require(native_early == 0.0, 'Remaining-only owner leaked into producer early depth.')
                early_misses += 1
            velocity = perspective_velocity([current_clips[owner][i] for i in triangle],
                [previous_clips[owner][i] for i in triangle], weights, current['jitter'], previous['jitter'])
            velocity_error = max(abs(v - e) for v, e in zip((velocity_x, velocity_y), velocity))
            require(velocity_error <= 2 ** -12, 'Ordinary native current/previous transform motion differs.')
            if math.hypot(*velocity) >= 2 ** -9:
                row_nonzero += 1
                require(math.hypot(velocity_x, velocity_y) > 2 ** -10, 'Nonzero ordinary motion was lost.')
            largest_velocity_error, largest_depth_error = max(largest_velocity_error, velocity_error), max(largest_depth_error, depth_error)
            probes += 1
        require(row_nonzero >= 8, 'Fixture lacks enough observable nonzero owner motion.')
        nonzero += row_nonzero
        for x, y in ((0, 0), (127, 0), (0, 63), (127, 63)):
            pixel = y * WIDTH + x
            require(floats(blobs['early'], early + 64 + pixel * 4, 1) == (0.0,), 'Early background depth dirty.')
            require(floats(blobs['raw'], image + 64 + pixel * 16, 4) == (0.0, 0.0, 0.0, 0.0),
                    'Opaque background color/depth/velocity dirty.')
    require(len(jitter_phases) >= 4 and probes == COUNT * 6 * 4 and early_hits > 0 and early_misses > 0,
            'Missing jitter/phase/interior coverage.')
    return dict(ordinary_batching_contract_passed=True, native_qualified=False, performance_qualified=False,
                real_vat_qualified=False, e8_qualified=False, measured_records=COUNT, ordinary_probes=probes,
                nonzero_motion_probes=nonzero, early_present_probes=early_hits, early_absent_probes=early_misses,
                max_velocity_error_uv=largest_velocity_error, max_depth_error=largest_depth_error,
                native_jitter_phases=len(jitter_phases), borrowed_math=helper_identity())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('receipt', type=Path)
    parser.add_argument('--expected', type=Path, required=True)
    parser.add_argument('--plan', type=Path, default=HERE / 'gate_plan.json')
    args = parser.parse_args()
    print(json.dumps(validate(load_json(args.receipt), load_json(args.plan), load_json(args.expected), args.receipt.parent), indent=2))

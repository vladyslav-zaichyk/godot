"""Numerical directional-shadow gate; importing never starts Godot or a GPU.

Receiver probes are chosen from frozen geometry and captured native projections,
before looking at lighting. A caster-off counterfactual must demonstrate a real
shadow delta. Unsupported reference fallback cannot qualify the future feature.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import struct

HERE = Path(__file__).resolve().parent
GUARD = 0xCDCDCDCD
COUNT, MAX_WIDTH, MAX_HEIGHT = 30, 128, 128
RAW_STRIDE, EARLY_STRIDE, UBO_STRIDE, PROOF_STRIDE = 524352, 65600, 5696, 2944
INPUTS = ('ShadowContractLab.csproj', 'project.godot', 'Main.tscn', 'Main.cs', 'HistoryCapture.cs',
          'ProbeLogger.cs', 'Consumer.gdshader', 'Receiver.gdshader', 'Wall.gdshader',
          'Publish.comp', 'Capture.comp', 'gate_plan.json')
DIAGNOSTIC = re.compile(r'\b(?:ERROR:|SCRIPT ERROR:|WARNING:|FATAL(?: ERROR)?[: ]|Unhandled exception)|'
    r'ObjectDB instances leaked|Orphan StringName|RID allocations|\bOp\w+\s+is not supported yet', re.I)
PLAN_DIGEST = '4567635688f3e83bfb33aa18e04959674fc9de9c0b362df6724e6761403704a6'


def require(value, message):
    if not value:
        raise ValueError(message)


def load_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(Path(path).read_bytes(), object_pairs_hook=pairs,
        parse_constant=lambda v: (_ for _ in ()).throw(ValueError('Nonfinite JSON: ' + v)))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def plan_digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def finite(values, name):
    require(all(type(v) in (int, float) and math.isfinite(v) for v in values), 'Nonfinite ' + name)


def sub(a, b):
    return [x - y for x, y in zip(a, b)]


def add(a, b):
    return [x + y for x, y in zip(a, b)]


def mul(a, scale):
    return [x * scale for x in a]


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def unit(a):
    size = math.sqrt(dot(a, a)); require(size > 1e-12, 'Degenerate direction.')
    return mul(a, 1 / size)


def matrix(scene):
    view = [scene['view'][r * 4:r * 4 + 4] for r in range(3)] + [[0, 0, 0, 1]]
    p = scene['projection']
    return [[sum(p[k * 4 + r] * view[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def inverse(m):
    rows = [list(row) + [int(c == r) for c in range(4)] for r, row in enumerate(m)]
    for col in range(4):
        pivot = max(range(col, 4), key=lambda r: abs(rows[r][col]))
        require(abs(rows[pivot][col]) > 1e-12, 'Singular captured projection.')
        rows[col], rows[pivot] = rows[pivot], rows[col]
        scale = rows[col][col]; rows[col] = [v / scale for v in rows[col]]
        for row in range(4):
            if row != col:
                scale = rows[row][col]
                rows[row] = [a - scale * b for a, b in zip(rows[row], rows[col])]
    return [row[4:] for row in rows]


def apply(m, point):
    return [dot(row, point) for row in m]


def project(scene, point):
    result = apply(matrix(scene), [*point, 1]); finite(result, 'projected geometry')
    return result


def scene_for(row, plan, jitter=(0., 0.)):
    """Synthetic/preflight projection only; runtime always uses captured UBOs."""
    camera, target = row['camera_position'], row['target']
    back = unit(sub(camera, target)); right = unit(cross([0, 1, 0], back)); up = cross(back, right)
    view = [v for axis in (right, up, back) for v in (*axis, -dot(axis, camera))]
    near, far = plan['geometry']['near'], plan['geometry']['far']
    f = 1 / math.tan(math.radians(plan['geometry']['camera_fov']) / 2)
    width, height = row['internal_extent']
    return dict(view=view, projection=[f * height / width, 0, 0, 0, 0, -f, 0, 0,
        -jitter[0], -jitter[1], near / (far - near), -1, 0, 0, far * near / (far - near), 0], jitter=list(jitter))


def ground_pixel(scene, x, y, extent, inv=None):
    inv = inverse(matrix(scene)) if inv is None else inv
    ndc = [2 * (x + .5) / extent[0] - 1, 2 * (y + .5) / extent[1] - 1]
    points = [apply(inv, [*ndc, z, 1]) for z in (1, 0)]
    points = [[v / p[3] for v in p[:3]] for p in points]
    ray = sub(points[1], points[0]); require(abs(ray[1]) > 1e-10, 'Ground ray parallel.')
    t = -points[0][1] / ray[1]
    require(t > 0, 'Ground receiver behind camera.')
    return add(points[0], mul(ray, t))


def visible_box_probes(rows, scenes, plan, center, size, count):
    """Common semantic face anchors and independently intersected pixel rays."""
    lo, hi = bounds(center, size); selected = []; used = [set() for _ in rows]
    inverses = [inverse(matrix(s)) for s in scenes]
    anchors = [[lo[0] + size[0] * fx, lo[1] + size[1] * fy, hi[2]]
               for fy in (.3, .4, .5, .6, .7, .8) for fx in (.5, .3, .7)]
    for anchor in anchors:
        samples = []
        for row, scene, inv, seen in zip(rows, scenes, inverses, used):
            clip = project(scene, anchor); width, height = row['internal_extent']
            if clip[3] <= .1:
                break
            x, y = [math.floor((clip[a] / clip[3] * .5 + .5) * row['internal_extent'][a]) for a in range(2)]
            if not (3 <= x < width - 3 and 3 <= y < height - 3) or (x, y) in seen:
                break
            ground = ground_pixel(scene, x, y, (width, height), inv)
            direction = sub(ground, row['camera_position']); hit = ray_box(row['camera_position'], direction, (lo, hi))
            if hit is None or hit[0] >= 1:
                break
            world = add(row['camera_position'], mul(direction, hit[0])); distances = [min(abs(world[a] - lo[a]), abs(world[a] - hi[a])) for a in range(3)]
            face = min(range(3), key=lambda a: distances[a])
            if any(distances[a] < plan['oracle']['body_face_margin'] for a in range(3) if a != face):
                break
            depth = project(scene, world); depth = depth[2] / depth[3]
            ground_depth = project(scene, ground); ground_depth = ground_depth[2] / ground_depth[3]
            samples.append(dict(x=x, y=y, world=world, depth=depth, ground_world=ground, ground_depth=ground_depth))
        if len(samples) != len(rows):
            continue
        for sample, seen in zip(samples, used):
            seen.add((sample['x'], sample['y']))
        selected.append(dict(anchor=anchor, samples=samples))
        if len(selected) == count:
            break
    require(len(selected) == count, 'Insufficient common visible box coverage.')
    return selected


def velocity_at(current, previous, world, old_world):
    a, b = project(current, world), project(previous, old_world)
    require(a[3] >= .5 and b[3] >= .5, 'Velocity witness clip-W invalid.')
    return [.5 * ((b[c] / b[3] - previous['jitter'][c]) - (a[c] / a[3] - current['jitter'][c])) for c in range(2)]


def bounds(center, size, inset=0):
    require(all(v > 2 * inset for v in size), 'Box inset collapses geometry.')
    return ([center[i] - size[i] / 2 + inset for i in range(3)],
            [center[i] + size[i] / 2 - inset for i in range(3)])


def ray_box(origin, direction, box):
    lo, hi = box; enter, leave = -math.inf, math.inf
    for axis in range(3):
        if abs(direction[axis]) < 1e-12:
            if not lo[axis] <= origin[axis] <= hi[axis]:
                return None
        else:
            a, b = (lo[axis] - origin[axis]) / direction[axis], (hi[axis] - origin[axis]) / direction[axis]
            enter, leave = max(enter, min(a, b)), min(leave, max(a, b))
    return (max(0., enter), leave) if leave >= max(0., enter) else None


def body_box(row, stable_id, plan, inset=0):
    return bounds(row['pose_centers'][stable_id], plan['geometry']['body_size'], inset)


def corners(box):
    lo, hi = box
    return [[(hi if mask & (1 << axis) else lo)[axis] for axis in range(3)] for mask in range(8)]


def camera_clear(point, row, plan):
    direction = sub(point, row['camera_position']); margin = plan['oracle']['camera_clearance']
    boxes = [bounds(plan['geometry']['wall_center'], plan['geometry']['wall_size'], -margin)]
    boxes += [body_box(row, i, plan, -margin) for i in row['active_ids']]
    return not any((hit := ray_box(row['camera_position'], direction, box)) is not None and hit[0] < 1 - 1e-5 for box in boxes)


def light_hits(point, row, plan, inset=0):
    direction = mul(unit(row['light_ray']), -1)
    return [i for i in row['active_ids'] if ray_box(point, direction, body_box(row, i, plan, inset)) is not None]


def receiver_sample(point, scene, row, plan, inv):
    clip = project(scene, point); width, height = row['internal_extent']
    if clip[3] <= .1 or not 0 < clip[2] / clip[3] < 1:
        return None
    x, y = [math.floor((clip[a] / clip[3] * .5 + .5) * row['internal_extent'][a]) for a in range(2)]
    guard = plan['oracle']['clip_guard_pixels']
    if not guard <= x < width - guard or not guard <= y < height - guard:
        return None
    actual = ground_pixel(scene, x, y, (width, height), inv)
    gx, gz = plan['geometry']['ground_size']; center = plan['geometry']['ground_center']
    if abs(actual[0] - center[0]) >= gx / 2 - .1 or abs(actual[2] - center[2]) >= gz / 2 - .1 or not camera_clear(actual, row, plan):
        return None
    depth = project(scene, actual); depth = depth[2] / depth[3]
    return dict(x=x, y=y, world=actual, depth=depth)


def receiver_probes(rows, scenes, plan):
    """Select common world anchors, never from observed brightness or shadows."""
    row = rows[0]; margin = plan['oracle']['ray_interior_margin']; inverses = [inverse(matrix(s)) for s in scenes]
    groups = {}
    for caster in [i for i in plan['oracle']['shadow_caster_ids'] if i in row['active_ids']] + ['lit']:
        choices = []
        if caster == 'lit':
            anchors = ([x / 5, 0., z / 5] for x in range(-20, 26) for z in range(-15, 26))
            required = plan['oracle']['lit_probes']
        else:
            center = row['pose_centers'][caster]; ray = row['light_ray']
            # Different heights along the center ray give a long robust witness region.
            anchors = ([center[0] - ray[0] * height / ray[1] + dx, 0.,
                        center[2] - ray[2] * height / ray[1] + dz]
                       for height in (.3, .4, .5, .6, .7, .8, .9, 1., 1.1)
                       for dx in (0., -.04, .04, -.08, .08) for dz in (0., -.04, .04))
            required = plan['oracle']['probes_per_shadow_caster']
        used = [set() for _ in rows]
        for anchor in anchors:
            samples = [receiver_sample(anchor, scene, arm, plan, inv) for scene, arm, inv in zip(scenes, rows, inverses)]
            if any(sample is None for sample in samples):
                continue
            if any((sample['x'], sample['y']) in seen for sample, seen in zip(samples, used)):
                continue
            if caster == 'lit':
                if any(light_hits(sample['world'], arm, plan, -margin) for sample, arm in zip(samples, rows)):
                    continue
            elif any(caster not in light_hits(sample['world'], arm, plan, margin)
                     or light_hits(sample['world'], arm, plan, -margin) != [caster] for sample, arm in zip(samples, rows)):
                continue
            for sample, seen in zip(samples, used):
                seen.add((sample['x'], sample['y']))
            choices.append(dict(anchor=anchor, samples=samples))
            if len(choices) == required:
                break
        require(len(choices) == required, 'Insufficient common guarded receiver coverage for ' + str(caster) + ' / ' + row['state_id'])
        groups[caster] = choices
    return groups


def omission_proof(row, scene, plan):
    """A convex box is omitted only behind a convex wall, or one clip plane."""
    wall = bounds(plan['geometry']['wall_center'], plan['geometry']['wall_size'], .02)
    result = {}
    for stable_id in row['active_ids']:
        if stable_id in row['candidate_ids'] or (stable_id == 0 and row['intentional_zero_candidate']):
            continue
        vertices = corners(body_box(row, stable_id, plan))
        if stable_id == 2:
            for vertex in vertices:
                hit = ray_box(row['camera_position'], sub(vertex, row['camera_position']), wall)
                require(hit is not None and hit[1] < .99, 'Behind-wall caster not conservatively hidden.')
            result[stable_id] = 'convex_wall_contains_all_vertex_rays'
        elif stable_id == 7:
            clips = [project(scene, v) for v in vertices]
            require(all(c[3] > .1 and c[0] < -c[3] - .01 for c in clips), 'Off-camera caster intersects camera frustum.')
            result[stable_id] = 'all_vertices_outside_left_clip_plane'
        else:
            require(False, 'Unproved omitted stable ID.')
    return result


def validate_plan(plan):
    require(plan_digest(plan) == PLAN_DIGEST, 'Frozen plan/threshold/geometry differs.')
    require(len(plan['records']) == COUNT and plan['limits']['requested_ledger_bytes'] ==
            COUNT * (RAW_STRIDE + EARLY_STRIDE + UBO_STRIDE + PROOF_STRIDE), 'Fixed capacity differs.')
    shadow_probes = lit_probes = omitted = 0
    for start in range(0, COUNT, 3):
        rows = plan['records'][start:start + 3]
        require([r['mode'] for r in rows] == ['counterfactual', 'reference', 'requested'], 'Paired arm order differs.')
        for key in ('state_id', 'camera_position', 'target', 'light_ray', 'internal_extent', 'active_ids',
                    'candidate_ids', 'pose_centers', 'generations'):
            require(rows[0][key] == rows[1][key] == rows[2][key], 'Paired geometry differs: ' + key)
        scenes = [scene_for(row, plan, jitter) for row, jitter in zip(rows, ((-.4 / 128, .3 / 128), (0., 0.), (.4 / 128, -.3 / 128)))]
        for row, scene in zip(rows, scenes):
            omitted += len(omission_proof(row, scene, plan))
        probes = receiver_probes(rows, scenes, plan)
        visible_box_probes(rows, scenes, plan, row['pose_centers'][0], plan['geometry']['body_size'], plan['oracle']['body_identity_probes'])
        shadow_probes += sum(len(v) for k, v in probes.items() if k != 'lit')
        lit_probes += len(probes['lit'])
    return dict(cpu_plan_passed=True, gpu_executed=False, native_qualified=False, performance_qualified=False,
                planned_records=COUNT, planned_viewport_records=COUNT + 1, semantic_shadow_probes=shadow_probes,
                semantic_lit_probes=lit_probes, finite_caster_omission_proofs=omitted)


def u32(blob, offset, count):
    require(offset >= 0 and offset + count * 4 <= len(blob), 'Truncated word ledger.')
    return struct.unpack_from('<' + 'I' * count, blob, offset)


def f32(blob, offset, count):
    require(offset >= 0 and offset + count * 4 <= len(blob), 'Truncated float ledger.')
    values = struct.unpack_from('<' + 'f' * count, blob, offset); finite(values, 'captured attachment')
    return values


def decode_scene(blob, offset):
    return dict(projection=f32(blob, offset, 16), view=f32(blob, offset + 176, 12), jitter=f32(blob, offset + 2824, 2))


def command(words, count, base):
    require(words == (GUARD,) * 4 + (36, count, 0, 0, base) + (GUARD,) * 7, 'Command/span/guards differ.')


def validate_proof(blob, offset, row, previous, serial, callback):
    ids = u32(blob, offset, 36)
    require(ids[:16] == tuple(range(16)), 'Immutable fallback/shadow identity prefix changed.')
    candidate = tuple(row['candidate_ids']) if callback else ()
    require(ids[16:] == candidate + (GUARD,) * (20 - len(candidate)), 'Candidate/identity guard span differs.')
    command(u32(blob, offset + 144, 16), 16, 0)
    command(u32(blob, offset + 208, 16), len(candidate), 16)
    require(u32(blob, offset + 272, 16) == (serial, serial - 1, 16, 0xe8000001) + (GUARD,) * 12, 'E8 header/history serial differs.')
    for i in range(16):
        gen, prev_gen = row['generations'][i], previous['generations'][i]
        active, prev_active = int(i in row['active_ids']), int(i in previous['active_ids'])
        require(u32(blob, offset + 336 + i * 16, 4) == (gen, prev_gen, active, prev_active), 'Lifecycle generation/state differs.')
        require(u32(blob, offset + 592 + i * 16, 4) == (active, active, i, gen), 'Stable owner state differs.')
        for source, start, epoch in ((row, 848, serial), (previous, 1872, serial - 1)):
            x, y, z = source['pose_centers'][i]
            actual = f32(blob, offset + start + i * 64, 12)
            expected = (1., 0., 0., x, 0., 1., 0., y, 0., 0., 1., z)
            require(max(abs(a - b) for a, b in zip(actual, expected)) < 1e-6, 'Frozen finite pose differs.')
            require(u32(blob, offset + start + i * 64 + 48, 4) == (source['generations'][i], i + 1, 0, epoch), 'Pose metadata differs.')
    require(u32(blob, offset + 2896, 12) == (GUARD,) * 12, 'Proof tail guard overwritten.')


def validate(raw, plan, expected, root, gate_mode='reference-boundary'):
    validate_plan(plan)
    require(gate_mode in ('reference-boundary', 'combined-required'), 'Unknown gate mode.')
    require(raw.get('contract') == 'arena-e13e-directional-shadow-receipt-v1' and raw.get('passed') is True
            and raw.get('gpu_executed') is True and not raw.get('failure'), 'Incomplete GPU receipt.')
    for key, value in expected.items():
        require(raw.get(key) == value, 'Identity mismatch: ' + key)
    require(raw['gate'] == gate_mode and raw['driver'] == 'vulkan' and raw['renderer'] == 'forward_plus', 'Gate/driver differs.')
    require(raw['observer_captures'] == COUNT and raw['initialization_viewport_records'] == 1 and len(raw['records']) == COUNT
            and raw['gpu_readbacks'] == 4 and raw['per_frame_gpu_readbacks'] == 0, 'Measured scheduling/readback totals differ.')
    require(raw['registered_consumers'] == 0 and raw['caller_owned_final'] == 0 and raw['caller_requested_bytes_final'] == 0
            and raw['logical_slots'] == 16 and raw['draw_slots'] == 32 and raw['ordinary_shadow_casters'] == 0
            and raw['diagnostics'] == [] and raw['dropped_diagnostics'] == 0 and raw['images_written'] == 0
            and raw['actual_viewport_records'] == 31 and raw['unlisted_viewport_renders'] == 0 and raw['taa_resets'] == 0,
            'Lifetime/diagnostic/scope contract differs.')
    require(raw['requested_ledger_bytes'] == plan['limits']['requested_ledger_bytes'], 'Ledger accounting differs.')
    blobs = {}
    for name, stride in (('raw', RAW_STRIDE), ('early', EARLY_STRIDE), ('ubo', UBO_STRIDE), ('proof', PROOF_STRIDE)):
        item = raw['payloads'][name]
        require(item['path'] == name + '.bin' and item['bytes'] == COUNT * stride, 'Unsafe/wrong payload shape.')
        path = Path(root) / item['path']; require(sha(path) == item['sha256'], 'Payload digest differs.')
        blobs[name] = path.read_bytes(); require(len(blobs[name]) == COUNT * stride, 'Payload length differs.')
    scenes = []; old_scenes = []; callbacks = selected = rejected = 0; previous = plan['initial']; last_epoch = 0
    for index, (record, row) in enumerate(zip(raw['records'], plan['records'])):
        serial = index + 1; requested = row['mode'] == 'requested'
        callback = requested and gate_mode == 'combined-required'; expected_selected = callback and row['producer_ack'] == 'valid'
        require(record['index'] == index and record['id'] == row['id'] and record['state_id'] == row['state_id']
                and record['mode'] == row['mode'] and record['preparation_serial'] == serial and record['scene'] == row,
                'Frozen record/scene/order differs.')
        require(record['capture_count'] == 1 and record['producer_count'] == int(callback)
                and record['frame_recording_completed'] is True and record['taa_reset_before'] is False,
                'Actual capture/producer/frame scheduling differs.')
        outcome, producer = record['outcome'], record['producer']
        if requested:
            require(type(outcome) is dict and outcome['selected'] is expected_selected, 'Candidate route differs.')
            reason = 'producer_acknowledged' if expected_selected else 'producer_ack_rejected' if callback else 'unsupported_view'
            require(outcome['reason'] == reason, 'Native route reason differs.')
            require(outcome['opaque_recorded'] is True and outcome['taa_process_recorded'] is True
                    and outcome['frame_recording_completed'] is True, 'Native opaque/TAA completion recording missing.')
            require(type(outcome['epoch']) is int and outcome['epoch'] > last_epoch
                    and type(outcome['configuration_generation']) is int and outcome['configuration_generation'] > 0,
                    'Stale native epoch/configuration.')
            last_epoch = outcome['epoch']
            if callback:
                require(type(producer) is dict and producer['epoch'] == outcome['epoch'] and
                    producer['configuration_generation'] == outcome['configuration_generation'] and producer['dispatches'] == 1,
                    'Current producer acknowledgment proof missing.')
            else:
                require(producer is None, 'Unsupported view unexpectedly invoked producer.')
        else:
            require(outcome is None and producer is None, 'E13-off reference unexpectedly used native producer.')
        callbacks += int(callback); selected += int(expected_selected); rejected += int(callback and not expected_selected)
        width, height = row['internal_extent']; base = index * RAW_STRIDE
        require(u32(blobs['raw'], base, 16) == (serial, width, height) + (GUARD,) * 13, 'Observer header/guards differ.')
        for y in range(MAX_HEIGHT):
            if y >= height:
                require(u32(blobs['raw'], base + 64 + y * MAX_WIDTH * 32, MAX_WIDTH * 8) == (GUARD,) * (MAX_WIDTH * 8), 'Raw row guard overwritten.')
            elif width < MAX_WIDTH:
                require(u32(blobs['raw'], base + 64 + (y * MAX_WIDTH + width) * 32, (MAX_WIDTH - width) * 8)
                    == (GUARD,) * ((MAX_WIDTH - width) * 8), 'Raw column guard overwritten.')
        scene = decode_scene(blobs['ubo'], index * UBO_STRIDE); old_scene = decode_scene(blobs['ubo'], index * UBO_STRIDE + 2848)
        ideal = scene_for(row, plan)
        require(max(abs(a - b) for a, b in zip(scene['view'], ideal['view'])) < 3e-5, 'Captured camera differs from frozen camera.')
        require(abs(scene['projection'][0] - ideal['projection'][0]) < 3e-5 and
                abs(scene['projection'][5] - ideal['projection'][5]) < 3e-5, 'Native perspective/extent differs.')
        if index > 0 and not row['resize_before']:
            require(max(abs(a - b) for a, b in zip(old_scene['view'], scenes[-1]['view'])) < 3e-5,
                    'Previous camera does not follow actual completed render.')
        omission_proof(row, scene, plan); scenes.append(scene); old_scenes.append(old_scene)
        if callback:
            actual_matrix = matrix(scene); words = producer['world_to_clip_words']
            require(type(words) is list and len(words) == 16 and all(type(v) is int and 0 <= v <= 0xffffffff for v in words), 'Producer matrix words invalid.')
            captured = struct.unpack('<16f', struct.pack('<16I', *words))
            require(max(abs(captured[c * 4 + r] - actual_matrix[r][c]) for r in range(4) for c in range(4)) < 3e-5,
                    'Producer/opaque native projection differs.')
            epoch, generation = producer['epoch'], producer['configuration_generation']
            require(u32(blobs['early'], index * EARLY_STRIDE, 16) ==
                (serial, width, height, epoch & 0xffffffff, epoch >> 32, generation & 0xffffffff, generation >> 32) + (GUARD,) * 9,
                'Early current-epoch header/guards differ.')
            wall_probes = visible_box_probes([row], [scene], plan, plan['geometry']['wall_center'], plan['geometry']['wall_size'],
                                             plan['oracle']['early_wall_probes'])
            for choice in wall_probes:
                sample = choice['samples'][0]; at = index * EARLY_STRIDE + 64 + (sample['y'] * MAX_WIDTH + sample['x']) * 4
                require(abs(f32(blobs['early'], at, 1)[0] - sample['depth']) <= plan['oracle']['depth_tolerance'],
                        'Producer did not sample the actual current wall prepass.')
        else:
            require(u32(blobs['early'], index * EARLY_STRIDE, EARLY_STRIDE // 4) == (GUARD,) * (EARLY_STRIDE // 4),
                    'Uninvoked producer wrote early-depth ledger.')
        validate_proof(blobs['proof'], index * PROOF_STRIDE, row, previous, serial, callback); previous = row
    require(raw['callbacks'] == callbacks, 'Producer total differs.')
    shadow_probes = lit_probes = body_probes = zero_body_probes = nonzero_motion = receiver_motion = 0; max_error = 0.; min_delta = math.inf
    for start in range(0, COUNT, 3):
        rows = plan['records'][start:start + 3]; groups = receiver_probes(rows, scenes[start:start + 3], plan)
        identities = visible_box_probes(rows, scenes[start:start + 3], plan, rows[0]['pose_centers'][0],
                                       plan['geometry']['body_size'], plan['oracle']['body_identity_probes'])
        for choice in identities:
            for arm, sample in enumerate(choice['samples']):
                row_index = start + arm; row = rows[arm]; scene = scenes[row_index]; old_scene = old_scenes[row_index]
                at = row_index * RAW_STRIDE + 64 + (sample['y'] * MAX_WIDTH + sample['x']) * 32
                r, g, b, depth, vx, vy, zero1, zero2 = f32(blobs['raw'], at, 8)
                intentional_zero = (gate_mode == 'combined-required' and row['mode'] == 'requested'
                                    and row['producer_ack'] == 'valid' and row['intentional_zero_candidate'])
                if intentional_zero:
                    require(abs(b - .25) <= plan['oracle']['identity_tolerance'] and abs(g) <= plan['oracle']['identity_tolerance']
                            and abs(depth - sample['ground_depth']) <= plan['oracle']['depth_tolerance'],
                            'Accepted zero command did not remove the visible main-camera body.')
                    world, old_world = sample['ground_world'], sample['ground_world']; zero_body_probes += 1
                else:
                    require(abs(r - 1 / 32) <= plan['oracle']['identity_tolerance'] and abs(g) <= plan['oracle']['identity_tolerance']
                            and abs(b) <= plan['oracle']['identity_tolerance'] and abs(depth - sample['depth']) <= plan['oracle']['depth_tolerance'],
                            'Visible fallback/candidate body identity/depth differs.')
                    previous_row = plan['initial'] if row_index == 0 else plan['records'][row_index - 1]
                    world = sample['world']; old_world = add(sub(world, row['pose_centers'][0]), previous_row['pose_centers'][0])
                    body_probes += 1
                expected_velocity = velocity_at(scene, old_scene, world, old_world)
                require(max(abs(a - b) for a, b in zip((vx, vy), expected_velocity)) <= plan['oracle']['velocity_tolerance'],
                        'Main-camera E8/receiver motion differs from actual completed history.')
                nonzero_motion += int(max(map(abs, expected_velocity)) > 2 * plan['oracle']['velocity_tolerance'])
        for kind, choices in groups.items():
            for choice in choices:
                lights = []
                for arm, sample in enumerate(choice['samples']):
                    row_index = start + arm; at = row_index * RAW_STRIDE + 64 + (sample['y'] * MAX_WIDTH + sample['x']) * 32
                    r, g, b, depth, vx, vy, zero1, zero2 = f32(blobs['raw'], at, 8)
                    require(abs(g) <= plan['oracle']['rgb_tolerance'] and abs(b - .25) <= plan['oracle']['rgb_tolerance'],
                            'Receiver identity witness differs.')
                    require(abs(depth - sample['depth']) <= plan['oracle']['depth_tolerance'], 'Receiver geometry/depth differs.')
                    require(zero1 == zero2 == 0 and abs(vx) < 1 and abs(vy) < 1, 'Velocity/reserved witness invalid.')
                    expected_velocity = velocity_at(scenes[row_index], old_scenes[row_index], sample['world'], sample['world'])
                    require(max(abs(a - b) for a, b in zip((vx, vy), expected_velocity)) <= plan['oracle']['velocity_tolerance'],
                            'Receiver motion differs from actual completed camera history.')
                    receiver_motion += int(max(map(abs, expected_velocity)) > 2 * plan['oracle']['velocity_tolerance'])
                    lights.append(r)
                counter, reference, requested = lights
                require(counter >= plan['oracle']['minimum_counterfactual_light'], 'Caster-off receiver is not visibly lit.')
                if kind == 'lit':
                    require(min(reference, requested) >= counter * plan['oracle']['minimum_lit_retention'], 'Lit witness unexpectedly shadowed.')
                    lit_probes += 1
                else:
                    delta = counter - reference
                    require(delta >= plan['oracle']['minimum_caster_shadow_delta'], 'Caster-on/off counterfactual has no observable shadow delta.')
                    min_delta = min(min_delta, delta); shadow_probes += 1
                error = abs(reference - requested); max_error = max(max_error, error)
                require(error <= plan['oracle']['maximum_requested_lighting_error'], 'Requested path lost/changed receiver shadow.')
    require(nonzero_motion >= 8 and receiver_motion >= 8, 'Frozen camera/body motions have insufficient nonzero numerical witnesses.')
    return dict(passed=True, gate=gate_mode, shadow_reference_contract_passed=True,
        combined_shadow_occlusion_contract_passed=gate_mode == 'combined-required',
        unsupported_fallback_expected=gate_mode == 'reference-boundary', gpu_executed=True,
        candidate_selected_records=selected, producer_rejected_records=rejected, producer_callbacks=callbacks,
        receiver_shadow_probes=shadow_probes, receiver_lit_probes=lit_probes, main_body_identity_probes=body_probes,
        accepted_zero_body_probes=zero_body_probes, nonzero_motion_probes=nonzero_motion,
        nonzero_receiver_motion_probes=receiver_motion, minimum_shadow_delta=min_delta,
        maximum_requested_lighting_error=max_error, native_qualified=False, performance_qualified=False,
        physical_vram_qualified=False, final_taa_image_quality_qualified=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('receipt', type=Path); parser.add_argument('--expected', type=Path, required=True)
    parser.add_argument('--plan', type=Path, default=HERE / 'gate_plan.json')
    parser.add_argument('--gate', choices=('reference-boundary', 'combined-required'), default='reference-boundary')
    args = parser.parse_args()
    print(json.dumps(validate(load_json(args.receipt), load_json(args.plan), load_json(args.expected), args.receipt.parent, args.gate), indent=2))


if __name__ == '__main__':
    main()

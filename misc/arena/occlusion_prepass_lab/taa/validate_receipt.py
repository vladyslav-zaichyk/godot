"""Independent E13c raw-ledger audit. Importing this module never starts Godot.

The current/previous homogeneous clip interpolants are reconstructed from the
captured native UBO and immutable pose words; receipt-supplied expected velocities
are never an oracle. CPU reference tests cannot qualify a GPU render.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import struct

GUARD = 0xCDCDCDCD
RAW_STRIDE = 128 * 64 * 16
UBO_STRIDE = 5696
PROOF_STRIDE = 44236
SCENE_BYTES = 2848
VELOCITY_TOLERANCE = 2 ** -12
DEPTH_TOLERANCE = 1e-5
INPUTS = ('OcclusionTaaLab.csproj', 'project.godot', 'Main.tscn', 'Main.cs', 'HistoryCapture.cs',
          'ProbeLogger.cs', 'Consumer.gdshader', 'Legacy.gdshader', 'Wall.gdshader', 'CopyDepth.comp',
          'Reduce.comp', 'Select.comp', 'Finalize.comp', 'Capture.comp', 'scenarios.json', 'gate_plan.json', 'FreezeScenarios.py')
DIAGNOSTIC = re.compile(
    r'\b(?:ERROR:|SCRIPT ERROR:|WARNING:|FATAL(?: ERROR)?[: ]|Unhandled exception)|'
    r'ObjectDB instances leaked|Orphan StringName|RID allocations|'
    r'\bOp\w+\s+is not supported yet', re.I)


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
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON: ' + value)))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def integer(value, minimum=0, maximum=0x7fffffffffffffff):
    return type(value) is int and minimum <= value <= maximum


def finite(values, name):
    require(all(type(v) in (int, float) and math.isfinite(v) for v in values), 'Nonfinite ' + name)


def words(value, length, name):
    require(type(value) is list and len(value) == length
            and all(integer(v, 0, 0xffffffff) for v in value), 'Invalid ' + name)


def floats(values):
    return struct.unpack('<' + 'f' * len(values), struct.pack('<' + 'I' * len(values), *values))


def u32(blob, offset, count):
    require(integer(offset) and offset + count * 4 <= len(blob), 'Truncated word ledger.')
    return list(struct.unpack_from('<' + 'I' * count, blob, offset))


def f32(blob, offset, count):
    values = struct.unpack_from('<' + 'f' * count, blob, offset)
    finite(values, 'captured UBO')
    return values


def decode_scene(blob, base):
    require(base + SCENE_BYTES <= len(blob), 'Truncated native UBO.')
    return dict(projection=f32(blob, base, 16), view=f32(blob, base + 176, 12),
                time=f32(blob, base + 2816, 1)[0], jitter=f32(blob, base + 2824, 2))


def project(scene, point):
    """Native projection is column-major; stored view is transposed 3x4."""
    finite(point, 'owner-local vertex')
    q = [sum(scene['view'][row * 4 + col] * (*point, 1)[col] for col in range(4))
         for row in range(3)] + [1]
    clip = [sum(scene['projection'][col * 4 + row] * q[col] for col in range(4)) for row in range(4)]
    finite(clip, 'native clip vertex')
    return clip


def pose_vertices(bank, stable_id, mesh):
    """Finite geometry from one frozen bank, without accepting its history proof."""
    b = stable_id * 16
    affine = floats(bank[b:b + 12]); deformation = floats([bank[b + 14]])[0]
    finite((*affine, deformation), 'pose')
    result = []
    for vertex in mesh:
        q = (vertex[0] + deformation * (vertex[1] + .5), vertex[1], vertex[2], 1)
        result.append([sum(affine[row * 4 + col] * q[col] for col in range(4)) for row in range(3)])
    return result


def history_vertices(scene, stable_id, mesh):
    """Reproduce the E8 identity validity decision, independently of GPU output."""
    current, previous = scene['current_pose_words'], scene['previous_pose_words']
    life, state, header = scene['lifecycle_words'], scene['state_words'], scene['header_words']
    words(current, 256, 'current poses'); words(previous, 256, 'previous poses')
    words(life, 64, 'lifecycle'); words(state, 64, 'state'); words(header, 16, 'header')
    b, l = stable_id * 16, stable_id * 4
    c, p = current[b + 12:b + 16], previous[b + 12:b + 16]
    require(header[2:4] == [16, 0xe8000001] and header[4:6] == scene['id_scan_order']
            and header[6] == len(scene['active_ids']) and header[7] in (0, 1)
            and header[8:] == [GUARD] * 8,
            'Invalid current header/guards.')
    require(state[l:l + 4] == [1, 1, stable_id, c[0]] and c[0] != 0 and c[1] != 0
            and c[3] == header[0] and life[l] != 0 and life[l + 2] != 0,
            'Invalid current identity.')
    history = (p[0] == c[0] and p[1] == c[1] and p[3] == header[1]
               and life[l] == life[l + 1] and life[l + 2] == life[l + 3])

    return pose_vertices(current, stable_id, mesh), pose_vertices(previous if history else current, stable_id, mesh), history


def barycentric_pixel(clips, triangle, x, y, extent, edge_minimum=2):
    """Screen barycentrics and affine raster depth at the actual pixel centre."""
    require(len(triangle) == 3 and len(set(triangle)) == 3, 'Invalid triangle indices.')
    selected = [clips[i] for i in triangle]
    require(all(q[3] >= .5 for q in selected), 'Small-motion clip-W outside qualified range.')
    corners = [((q[0] / q[3] * .5 + .5) * extent[0],
                (q[1] / q[3] * .5 + .5) * extent[1]) for q in selected]
    sub = lambda a, b: (a[0] - b[0], a[1] - b[1])
    cross = lambda a, b: a[0] * b[1] - a[1] * b[0]
    a, b, c = corners
    p = (x + .5, y + .5)
    area = cross(sub(b, a), sub(c, a))
    require(abs(area) > 1e-10, 'Degenerate projected triangle.')
    weights = [cross(sub(b, p), sub(c, p)) / area, cross(sub(c, p), sub(a, p)) / area]
    weights.append(1 - sum(weights))
    require(min(weights) > 0 if edge_minimum > 0 else min(weights) >= -1e-10, 'Pixel outside current triangle.')
    for u, v in ((a, b), (b, c), (c, a)):
        edge = sub(v, u)
        require(abs(cross(edge, sub(p, u))) / math.hypot(*edge) >= edge_minimum - 1e-5,
                'Pixel too close to triangle edge.')
    depth = sum(weights[j] * selected[j][2] / selected[j][3] for j in range(3))
    require(0 < depth <= 1 and math.isfinite(depth), 'Depth outside Vulkan reverse-Z range.')
    return weights, depth


def perspective_velocity(current, previous, weights, current_jitter, previous_jitter):
    """Smooth-interpolate homogeneous C and P with CURRENT clip-W, then divide."""
    require(len(current) == len(previous) == len(weights) == 3, 'Velocity triangle arity differs.')
    finite([v for q in current + previous for v in q], 'clip interpolation')
    finite((*weights, *current_jitter, *previous_jitter), 'interpolation weights/jitter')
    require(all(len(q) == 4 and q[3] >= .5 for q in current + previous), 'Invalid previous/current clip-W.')
    require(min(weights) >= 0 and abs(sum(weights) - 1) <= 1e-5, 'Invalid screen barycentrics.')
    a = [weight / q[3] for weight, q in zip(weights, current)]
    divisor = sum(a)
    require(divisor > 0, 'Invalid perspective interpolation denominator.')
    ndc = []
    for clips in (current, previous):
        q = [sum(a[i] * clips[i][component] for i in range(3)) / divisor for component in range(4)]
        require(q[3] >= .5, 'Interpolated clip-W outside qualification.')
        ndc.append([q[c] / q[3] for c in range(2)])
    velocity = [.5 * ((ndc[1][c] - previous_jitter[c]) - (ndc[0][c] - current_jitter[c])) for c in range(2)]
    finite(velocity, 'expected velocity')
    require(max(map(abs, velocity)) <= .125, 'Motion outside preregistered small-motion bound.')
    return velocity


def compare_velocity(actual, expected, designated=()):
    require(len(actual) == len(expected) == 2, 'Velocity dimensions differ.')
    finite((*actual, *expected), 'velocity')
    require(all(abs(a - b) <= VELOCITY_TOLERANCE for a, b in zip(actual, expected)), 'Raw velocity differs.')
    for component in designated:
        require(component in (0, 1) and 2 ** -8 <= abs(expected[component]) <= .125,
                'Designated nonzero oracle outside preregistered range.')
        require(math.hypot(*actual) > 2 ** -9, 'Zero motion output in designated nonzero case.')


def validate_identity(raw, expected):
    require(raw.get('contract') == 'arena-e13c-native-taa-receipt-v1'
            and raw.get('passed') is True and not raw.get('failure'), 'Runtime failed/incomplete.')
    require(raw.get('gpu_executed') is True, 'CPU-only receipt cannot qualify GPU execution.')
    for key in ('engine_hash', 'engine_sha256', 'sdk_version', 'managed_api_assembly_sha256',
                'managed_fixture_assembly_sha256', 'source_sha256', 'plan_sha256'):
        require(key in expected and raw.get(key) == expected[key], 'Identity differs: ' + key)
    require(set(raw['source_sha256']) == set(INPUTS)
            and all(type(value) is str and re.fullmatch('[0-9a-f]{64}', value)
                    for value in raw['source_sha256'].values()), 'Incomplete source inventory.')
    require(re.fullmatch('[0-9a-f]{40}', raw['engine_hash']) is not None, 'Invalid native source identity.')
    require(raw.get('driver') == 'vulkan' and raw.get('renderer') == 'forward_plus'
            and type(raw.get('gpu_name')) is str and raw['gpu_name'].strip(), 'Wrong backend/GPU.')
    require(raw.get('diagnostics') == [] and raw.get('dropped_diagnostics') == 0, 'Unexpected/dropped diagnostics.')
    for key in ('performance_qualified', 'physical_vram_qualified', 'resolved_taa_image_quality_qualified',
                'real_vat_qualified', 'general_compositor_qualified'):
        require(raw.get(key) is False, 'Unsupported qualification claim: ' + key)


def read_payloads(raw, root, record_count):
    result = {}
    root = Path(root).resolve()
    for name, stride in (('raw', RAW_STRIDE), ('ubo', UBO_STRIDE), ('proof', PROOF_STRIDE)):
        item = raw['payloads'][name]
        require(set(item) == {'path', 'bytes', 'sha256'} and item['path'] == name + '.bin', 'Unexpected ledger path/schema.')
        path = root / item['path']
        require(path.is_file() and not path.is_symlink() and not getattr(path, 'is_junction', lambda: False)(),
                'Missing/linked ledger.')
        blob = path.read_bytes()
        require(item['bytes'] == len(blob) == record_count * stride
                and item['sha256'] == hashlib.sha256(blob).hexdigest(), 'Ledger size/hash differs: ' + name)
        result[name] = blob
    return result


def validate_row_mapping(row, planned, previous_epoch):
    require(row.get('id') == planned['id'] and row.get('preparation_serial') == planned['preparation_serial'],
            'Missing/reordered schedule record.')
    flags = ('viewport_recorded', 'producer_invoked', 'culler_complete', 'observer_capture',
             'history_commit', 'taa_process_recorded', 'frame_recording_completed', 'frame_rejected')
    for key in flags:
        require(type(row.get(key)) is bool and row[key] is planned[key], 'Schedule flag differs: ' + planned['id'] + ':' + key)
    require(type(row.get('capture_count')) is int and row['capture_count'] == int(planned['observer_capture']),
            'Duplicate/missing observer invocation.')
    if not planned['viewport_recorded']:
        require(row.get('outcome') is None and row.get('producer') is None,
                'Skipped preparation acquired a native viewport epoch.')
        return previous_epoch
    outcome = row['outcome']
    require(integer(outcome.get('epoch'), 1) and outcome['epoch'] == previous_epoch + 1,
            'Unlisted viewport render or stale epoch.')
    require(integer(outcome.get('configuration_generation'), 1), 'Missing native configuration generation.')
    require(outcome.get('mode') == ('FRAME_REJECTED' if planned['frame_rejected'] else
            'CANDIDATE_SELECTED' if planned['selected_command'] == 'candidate' else 'FALLBACK_SELECTED'),
            'Native selection/rejection differs.')
    for key in ('opaque_recorded', 'taa_process_recorded', 'frame_recording_completed'):
        target = planned['frame_recording_completed']
        require(type(outcome.get(key)) is bool and outcome[key] is target, 'Native recording outcome differs: ' + key)
    require(type(outcome.get('taa_history_bootstrap')) is bool, 'Missing native bootstrap evidence.')
    if planned['frame_recording_completed']:
        require(outcome['taa_history_bootstrap'] is planned['native_taa_reset_before'], 'Native TAA reset/bootstrap differs.')
    else:
        require(not outcome['taa_history_bootstrap'], 'Rejected scene processed TAA.')
    producer = row.get('producer')
    require((type(producer) is dict) is planned['producer_invoked'], 'Producer invocation evidence differs.')
    if producer:
        require(producer.get('invoked') is True and producer.get('complete') is planned['culler_complete'],
                'Incomplete/undeclared producer work.')
        require(producer['epoch'] == outcome['epoch'] and producer['configuration_generation'] == outcome['configuration_generation'],
                'Producer acknowledgment mismatches actual viewport epoch/configuration.')
        require(producer.get('dispatches') == (10 if planned['culler_complete'] else 0)
                and producer.get('barriers') == (9 if planned['culler_complete'] else 0), 'Producer work bound differs.')
    return outcome['epoch']


def validate_proof(blob, row_index, scene, planned, producer):
    offset = row_index * PROOF_STRIDE
    proof = u32(blob, offset, 128)
    ids, fallback, candidate, stats, header = proof[:36], proof[36:52], proof[52:68], proof[68:84], proof[84:100]
    require(ids[:16] == list(range(16)) and ids[32:] == [GUARD] * 4, 'Fallback identity/guard corruption.')
    require(fallback == [GUARD] * 4 + [6, 16, 0, 0, 0] + [GUARD] * 7, 'Complete fallback command changed.')
    require(header == scene['header_words'], 'GPU header differs from frozen publication.')
    for index, stable_id in enumerate((0, 2, 7)):
        require(proof[100 + index * 4:104 + index * 4] == scene['state_words'][stable_id * 4:stable_id * 4 + 4],
                'GPU active/generation state witness differs.')
    require(candidate[:4] == [GUARD] * 4 and candidate[9:] == [GUARD] * 7,
            'Candidate command guard corrupted.')
    require(candidate[4] == 6 and candidate[6:8] == [0, 0], 'Unexpected indexed command shape.')
    require(candidate[5] <= 16 and candidate[8] in (0, 16), 'Command exceeds declared draw capacity.')
    require(candidate[8] + candidate[5] <= 32, 'Native draw range exceeds capacity.')
    require(stats[:4] == [GUARD] * 4 and stats[12:] == [GUARD] * 4, 'Statistics guard corrupted.')
    require(stats[8] | stats[9] << 32 == producer['epoch']
            and stats[10] | stats[11] << 32 == producer['configuration_generation'], 'GPU publication epoch/configuration differs.')
    if planned['id'] == 'T019':
        require(stats[6] == 1 and candidate[5:9] == [16, 0, 0, 0], 'GPU overflow did not publish complete fallback.')
    elif planned['id'] == 'T020':
        require(candidate[5] == 0 and candidate[8] == 16, 'Zero-command control was not empty.')
    else:
        require(stats[6] == 0 and candidate[5] == stats[4] and candidate[8] == 16, 'Candidate command/statistics disagree.')
    command = candidate if planned['selected_command'] == 'candidate' else fallback
    selected = ids[command[8]:command[8] + command[5]]
    require(len(set(selected)) == len(selected) and all(0 <= x < 16 for x in selected), 'Invalid/duplicate current compact IDs.')
    return proof[112:128], selected


def projected_depth(clips, triangles, x, y, extent):
    """Full-resolution visibility reference, independent of Hi-Z reduction/selector."""
    depths = []
    for triangle in triangles:
        try:
            _, depth = barycentric_pixel(clips, triangle, x, y, extent, edge_minimum=0)
            depths.append(depth)
        except ValueError:
            pass
    return max(depths, default=0.0)


def wall_depths(scene, frozen, current, extent):
    wall = frozen['wall']
    positions = [[vertex[c] * wall['scale'][c] + wall['position'][c] for c in range(3)]
                 for vertex in wall['vertices']]
    clips = [project(current, p) for p in positions]
    if not scene['wall_visible']:
        return [0.0] * 8192
    return [projected_depth(clips, frozen['triangles'], x, y, extent)
            if x < extent[0] and y < extent[1] else 0.0 for y in range(64) for x in range(128)]


def expected_probes(scene, frozen, current, previous, extent, probe_scene=None):
    """Freeze witness choice using geometry only, never sampled GPU color/velocity."""
    walls = wall_depths(scene, frozen, current, extent)
    result, geometric_visible = [], []
    for stable_id in scene['active_ids']:
        now, old, _ = history_vertices(scene, stable_id, frozen['source_mesh_vertices'])
        clips = [project(current, p) for p in now]
        previous_clips = [project(previous, p) for p in old]
        corners = [((q[0] / q[3] * .5 + .5) * extent[0], (q[1] / q[3] * .5 + .5) * extent[1]) for q in clips]
        centroid = [sum(q[c] for q in corners) / 4 for c in range(2)]
        options = []
        any_visible = False
        xlow, xhigh = max(0, math.floor(min(p[0] for p in corners))), min(extent[0], math.ceil(max(p[0] for p in corners)))
        ylow, yhigh = max(0, math.floor(min(p[1] for p in corners))), min(extent[1], math.ceil(max(p[1] for p in corners)))
        for y in range(ylow, yhigh):
            for x in range(xlow, xhigh):
                for tidx, triangle in enumerate(frozen['triangles']):
                    try:
                        bary, depth = barycentric_pixel(clips, triangle, x, y, extent, edge_minimum=0)
                    except ValueError:
                        continue
                    if depth <= walls[y * 128 + x] + DEPTH_TOLERANCE:
                        continue
                    any_visible = True
                    try:
                        barycentric_pixel(clips, triangle, x, y, extent)
                    except ValueError:
                        continue
                    distance = sum(((x + .5, y + .5)[c] - centroid[c]) ** 2 for c in range(2))
                    options.append((round(distance * 1024), y, x, tidx))
        if any_visible:
            geometric_visible.append(stable_id)
            if probe_scene is not None:
                probe_clips = [project(probe_scene, p) for p in now]
                probe_corners = [((q[0] / q[3] * .5 + .5) * extent[0],
                                  (q[1] / q[3] * .5 + .5) * extent[1]) for q in probe_clips]
                centroid = [sum(q[c] for q in probe_corners) / 4 for c in range(2)]
                options = []
                xlow, xhigh = max(0, math.floor(min(p[0] for p in probe_corners))), min(extent[0], math.ceil(max(p[0] for p in probe_corners)))
                ylow, yhigh = max(0, math.floor(min(p[1] for p in probe_corners))), min(extent[1], math.ceil(max(p[1] for p in probe_corners)))
                for y in range(ylow, yhigh):
                    for x in range(xlow, xhigh):
                        for tidx, triangle in enumerate(frozen['triangles']):
                            try:
                                barycentric_pixel(probe_clips, triangle, x, y, extent)
                            except ValueError:
                                continue
                            distance = sum(((x + .5, y + .5)[c] - centroid[c]) ** 2 for c in range(2))
                            options.append((round(distance * 1024), y, x, tidx))
            chosen = set()
            for _, y, x, tidx in sorted(options):
                if (x, y) in chosen:
                    continue
                chosen.add((x, y))
                result.append(dict(x=x, y=y, kind='identity', stable_id=stable_id,
                                   triangle=frozen['triangles'][tidx]))
                if len(chosen) == 4:
                    break
            require(len(chosen) == 4, 'Visible identity lacks four preregistered interior witnesses: ' + scene['id'])
    require(geometric_visible == scene['expected_visible_ids'], 'Frozen visible-ID claim differs from independent raster geometry.')
    if scene['id'] == 'T020':
        result = []
    result.extend(dict(x=x, y=y, kind='background') for x, y in frozen['background_pixels'])
    return result, walls


def validate_pyramid(blob, row_index, wall, extent):
    values = u32(blob, row_index * PROOF_STRIDE + 512, 10931)
    require(values[:4] == values[-4:] == [GUARD] * 4, 'Hi-Z guard corrupted.')
    offset, width, height = 4, 128, 64
    reference = wall
    for level in range(8):
        actual = floats(values[offset:offset + width * height])
        finite(actual, 'Hi-Z depth')
        require(all(abs(a - b) <= DEPTH_TOLERANCE for a, b in zip(actual, reference)),
                'Independent ordinary-occluder Hi-Z differs at level ' + str(level))
        offset += width * height
        if level == 7:
            break
        next_width, next_height = max(1, width // 2), max(1, height // 2)
        reference = [min(reference[(y * 2 + dy) * width + x * 2 + dx]
                         if y * 2 + dy < height and x * 2 + dx < width else 0.0
                         for dy in range(2) for dx in range(2))
                     for y in range(next_height) for x in range(next_width)]
        width, height = next_width, next_height


def conservative_ids(scene, matrix_scene, wall, extent):
    """Independent envelope query against analytic ordinary-wall depths."""
    levels = [(128, 64, wall)]
    for level in range(1, 8):
        width, height, source = levels[-1]
        w, h = max(1, width // 2), max(1, height // 2)
        reduced = [min(source[(y * 2 + dy) * width + x * 2 + dx]
                       if y * 2 + dy < height and x * 2 + dx < width else 0.0
                       for dy in range(2) for dx in range(2)) for y in range(h) for x in range(w)]
        levels.append((w, h, reduced))
    result = []
    order = scene['id_scan_order'] + [i for i in range(16) if i not in scene['id_scan_order']]
    for stable_id in order:
        if stable_id not in scene['active_ids']:
            continue
        b = stable_id * 16
        pose = floats(scene['current_pose_words'][b:b + 12])
        deformation = floats([scene['current_pose_words'][b + 14]])[0]
        clips = []
        for corner in range(8):
            x, y, z = (.65 if corner & 1 else -.65), (.65 if corner & 2 else -.65), (.22 if corner & 4 else -.22)
            local = (x + deformation * (y + .5), y, z, 1)
            point = [sum(pose[r * 4 + c] * local[c] for c in range(4)) for r in range(3)]
            clips.append(project(matrix_scene, point))
        if any(q[3] <= 0 or not 0 <= q[2] / q[3] <= 1 for q in clips):
            result.append(stable_id); continue
        pixels = [[(q[c] / q[3] * .5 + .5) * extent[c] for c in range(2)] for q in clips]
        rect = [math.floor(min(p[c] for p in pixels)) - 1 for c in range(2)]
        rect += [math.ceil(max(p[c] for p in pixels)) + 1 for c in range(2)]
        if rect[0] < 0 or rect[1] < 0 or rect[2] > extent[0] or rect[3] > extent[1] or rect[2] <= rect[0] or rect[3] <= rect[1]:
            result.append(stable_id); continue
        nearest = max(q[2] / q[3] for q in clips)
        size = max(rect[2] - rect[0], rect[3] - rect[1])
        level = min(7, (size - 1).bit_length()); block = 1 << level
        width, _, values = levels[level]
        minimum = min(values[y * width + x]
                      for y in range(rect[1] // block, (rect[3] - 1) // block + 1)
                      for x in range(rect[0] // block, (rect[2] - 1) // block + 1))
        if not minimum > nearest + .00002:
            result.append(stable_id)
    return result


def validate_selection(blob, row_index, scene, planned, matrix_scene, wall, extent):
    proof = u32(blob, row_index * PROOF_STRIDE, 128)
    ids, candidate, stats = proof[:36], proof[52:68], proof[68:84]
    expected = [] if planned['id'] == 'T020' else conservative_ids(scene, matrix_scene, wall, extent)
    overflow = planned['id'] == 'T019'
    written = [] if overflow else expected
    require(stats[4:8] == [len(written), len(expected), int(overflow), int(planned['id'] == 'T020')],
            'Independent current envelope selection/statistics differ.')
    require(ids[16:16 + len(written)] == written and ids[16 + len(written):32] == [GUARD] * (16 - len(written)),
            'Current compact ID order or unwritten ID guards differ.')
    require(candidate[5] == (16 if overflow else len(written)), 'Independent current command count differs.')


def preflight_scenarios(frozen):
    """Synthetic camera/16-Halton parameter sweep, explicitly not native/GPU evidence."""
    fround = lambda value: struct.unpack('<f', struct.pack('<f', value))[0]

    def halton(index, base):
        factor, value = 1., 0.
        while index:
            factor = fround(factor / base)
            value = fround(value + factor * (index % base))
            index //= base
        return fround(value * 2 - 1)

    def camera(row, phase):
        width, height = row['internal_extent']; scale = 1 / math.tan(math.radians(row['camera']['fov']) / 2)
        near, far = .1, 20.  # Frozen procedural fixture Camera3D settings.
        jitter = [fround(halton(phase, 2) / width), fround(halton(phase, 3) / height)]
        projection = [0.] * 16
        projection[0] = scale / (width / height); projection[5] = -scale
        projection[8] = -jitter[0]; projection[9] = -jitter[1]
        projection[10] = near / (far - near); projection[11] = -1; projection[14] = near * far / (far - near)
        x, y, z = row['camera']['position']; c, s = math.cos(row['camera']['yaw']), math.sin(row['camera']['yaw'])
        view = [c, 0, -s, -c * x + s * z, 0, 1, 0, -y, s, 0, c, -s * x - c * z]
        return dict(projection=list(map(fround, projection)), view=list(map(fround, view)), jitter=jitter)

    samples = nonzero = completed = 0
    phase_gaps, reset_gaps = [], {}
    for phase in range(16):
        previous, max_gap = None, 0.
        for scene in frozen['records']:
            if scene['id'] in ('T042', 'T060', 'T062', 'T063'):
                continue
            current = camera(scene, (phase + scene['index']) % 16)
            old_camera = previous or current
            probes, wall = expected_probes(scene, frozen, current, old_camera, scene['internal_extent'])
            if scene['wall_visible']:
                require(conservative_ids(scene, current, wall, scene['internal_extent']) == [2],
                        'Synthetic positive wall control did not remove hidden ID7.')
            for probe in probes:
                if probe['kind'] != 'identity':
                    continue
                stable_id, triangle = probe['stable_id'], probe['triangle']
                now, old, valid = history_vertices(scene, stable_id, frozen['source_mesh_vertices'])
                cc, pc = [project(current, vertex) for vertex in now], [project(old_camera, vertex) for vertex in old]
                bary, _ = barycentric_pixel(cc, triangle, probe['x'], probe['y'], scene['internal_extent'])
                velocity = perspective_velocity([cc[i] for i in triangle], [pc[i] for i in triangle], bary,
                                                current['jitter'], old_camera['jitter'])
                designated = [c for c, key in enumerate(('designated_nonzero_x', 'designated_nonzero_y')) if scene[key]]
                compare_velocity(velocity, velocity, designated)
                samples += 1; nonzero += math.hypot(*velocity) > 2 ** -8
                if stable_id in scene.get('invalid_history_ids', []):
                    require(not valid, 'Synthetic lifecycle control has valid previous proof.')
                    bad = [project(old_camera, vertex) for vertex in pose_vertices(scene['previous_pose_words'], stable_id, frozen['source_mesh_vertices'])]
                    mutant = perspective_velocity([cc[i] for i in triangle], [bad[i] for i in triangle], bary,
                                                   current['jitter'], old_camera['jitter'])
                    gap = max(abs(a - b) for a, b in zip(mutant, velocity))
                    key = (phase, scene['id'], stable_id)
                    reset_gaps[key] = max(reset_gaps.get(key, 0), gap)
                if scene['id'] in ('T045', 'T046', 'T049', 'T050'):
                    naive = [.5 * (sum(bary[j] * pc[i][c] / pc[i][3] for j, i in enumerate(triangle))
                        - old_camera['jitter'][c] - sum(bary[j] * cc[i][c] / cc[i][3] for j, i in enumerate(triangle))
                        + current['jitter'][c]) for c in range(2)]
                    max_gap = max(max_gap, max(abs(a - b) for a, b in zip(naive, velocity)))
            previous = current; completed += 1
        phase_gaps.append(max_gap)
    required = {(phase, f'T{serial:03}', 7) for phase in range(16) for serial in range(26, 37)}
    require(completed == 1984 and required <= set(reset_gaps)
            and min(reset_gaps[key] for key in required) >= 2 * VELOCITY_TOLERANCE,
            'Synthetic lifecycle witnesses cannot distinguish bypassed invalid history.')
    require(min(phase_gaps) >= 2 * VELOCITY_TOLERANCE, 'Synthetic perspective control cannot distinguish orthographic oracle.')
    return dict(scope='CPU_synthetic_camera_parameters_not_native_UBO_or_GPU', gpu_executed=False,
                native_render_contract_passed=False, performance_qualified=False, phase_sequences=16,
                synthetic_completed_row_evaluations=completed, synthetic_identity_probes=samples,
                synthetic_nonzero_probes=nonzero, perspective_naive_gap_min_uv=min(phase_gaps),
                invalid_history_gap_min_uv=min(reset_gaps[key] for key in required))


def validate(raw, plan, expected, payload_root, scenarios):
    validate_identity(raw, expected)
    require(plan['contract'] == 'arena-e13c-native-taa-e8-plan-v2', 'Wrong scheduling plan.')
    require(plan['capture']['scene_ubo_bytes'] == SCENE_BYTES
            and plan['capture']['current_previous_ubo_bytes'] == UBO_STRIDE
            and plan['capture']['ubo_offsets'] == {'projection': 0, 'transposed_view_3x4': 176,
                'time': 2816, 'jitter': [2824, 2828], 'previous_base': 2848}, 'Pinned native UBO schema differs.')
    records = plan['records']
    require(len(records) == 128 and len(raw.get('records', [])) == 128, 'Incomplete 128-record gate.')
    require(scenarios.get('contract') == 'arena-e13c-procedural-inputs-v1'
            and len(scenarios['records']) == 128 and scenarios['triangles'] == [[0, 2, 1], [0, 3, 2]], 'Unfrozen input matrix/mesh.')
    initial = raw.get('initialization_records')
    require(type(initial) is list and len(initial) == 1 and initial[0].get('id') == 'I000'
            and initial[0].get('finite_e8_fallback_rendered') is True
            and initial[0].get('before_registration') is True and initial[0].get('observer_captures') == 0
            and initial[0].get('gpu_readbacks') == 0 and initial[0].get('retry_count') == 0
            and initial[0].get('actual_viewport_records') == 1,
            'Missing/hidden/retried initialization render.')
    for key, value in (('gpu_readbacks', 3), ('per_frame_gpu_readbacks', 0), ('images_written', 0),
                       ('unlisted_viewport_renders', 0), ('caller_owned_final', 0), ('caller_requested_bytes_final', 0),
                       ('capture_descriptor_pairs_peak', None), ('callbacks', 125), ('observer_captures', 124),
                       ('history_commits', 124), ('cpu_depth_reads_before_producer', 0)):
        if value is not None:
            require(type(raw.get(key)) is int and raw[key] == value, 'Work/resource counter differs: ' + key)
    require(integer(raw.get('capture_descriptor_pairs_peak'), 1, 16), 'Unbounded capture descriptor cache.')
    require(raw.get('native_api_present') is True, 'Native API unavailable.')
    payloads = read_payloads(raw, payload_root, 128)
    previous_epoch = 0
    previous_commit = 0
    captures = pixels = nonzero = 0
    perspective_counterexample_gap = 0.0
    invalid_history_gaps = {}
    jitter_values = set()
    for index, (row, planned, scene) in enumerate(zip(raw['records'], records, scenarios['records'])):
        require(scene['id'] == planned['id'], 'Frozen input row differs.')
        previous_epoch = validate_row_mapping(row, planned, previous_epoch)
        require(row.get('previous_rendered_preparation') == previous_commit, 'History promoted unrendered preparation.')
        if not planned['observer_capture']:
            for name, stride in (('raw', RAW_STRIDE), ('ubo', UBO_STRIDE), ('proof', PROOF_STRIDE)):
                region = payloads[name][index * stride:(index + 1) * stride]
                require(region == struct.pack('<I', GUARD) * (stride // 4), 'Skipped/rejected record wrote capture ledger.')
            continue
        captures += 1
        current = decode_scene(payloads['ubo'], index * UBO_STRIDE)
        previous = decode_scene(payloads['ubo'], index * UBO_STRIDE + SCENE_BYTES)
        jitter_values.add(current['jitter'])
        matrix_words = row['producer']['world_to_clip_words']
        words(matrix_words, 16, 'native corrected producer projection')
        matrix = floats(matrix_words)
        for col in range(4):
            point = [1 if c == col else 0 for c in range(4)]
            view_point = [sum(current['view'][r * 4 + c] * point[c] for c in range(4)) for r in range(3)] + [point[3]]
            reference = [sum(current['projection'][c * 4 + r] * view_point[c] for c in range(4)) for r in range(4)]
            require(all(abs(matrix[col * 4 + r] - reference[r]) <= 1e-5 for r in range(4)),
                    'Producer projection differs from actual corrected jittered scene UBO.')
        probe_scene = dict(projection=matrix, view=(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0))
        color_bits, selected = validate_proof(payloads['proof'], index, scene, planned, row['producer'])
        require(row.get('scene') == scene, 'Receipt publication differs from preregistered inputs.')
        probes = row.get('probes')
        require(type(probes) is list and 8 <= len(probes) <= 16, 'Capture probe count outside fixed bound.')
        oracle_probes, wall = expected_probes(scene, scenarios, current, previous, planned['internal_extent'], probe_scene)
        require([{key: p[key] for key in oracle} for p, oracle in zip(probes, oracle_probes)] == oracle_probes
                and len(probes) == len(oracle_probes), 'Numerical witnesses differ from geometry-only deterministic choice.')
        validate_pyramid(payloads['proof'], index, wall, planned['internal_extent'])
        validate_selection(payloads['proof'], index, scene, planned, probe_scene, wall, planned['internal_extent'])
        seen = set(); identity_counts = {}; background_count = 0
        require(len(color_bits) == 16, 'Missing actual pre-TAA color witnesses.')
        for pindex, probe in enumerate(probes):
            x, y = probe['x'], probe['y']
            require(integer(x, 0, planned['internal_extent'][0] - 1)
                    and integer(y, 0, planned['internal_extent'][1] - 1) and (x, y) not in seen,
                    'Duplicate/outside actual native probe pixel.')
            seen.add((x, y))
            sample = u32(payloads['raw'], index * RAW_STRIDE + (y * 128 + x) * 16, 4)
            require(sample[2] == planned['preparation_serial'], 'Raw pixel belongs to stale preparation.')
            velocity = floats(sample[:2]); depth = floats([sample[3]])[0]; red = floats([color_bits[pindex]])[0]
            finite((*velocity, depth, red), 'actual native depth/color/velocity')
            if probe['kind'] == 'background':
                background_count += 1
                require(depth == 0 and abs(red) <= VELOCITY_TOLERANCE
                        and max(map(abs, velocity)) <= VELOCITY_TOLERANCE, 'Stale background depth/color/velocity.')
                continue
            require(probe['kind'] == 'identity' and probe['stable_id'] in (0, 2, 7), 'Unknown numerical probe kind/identity.')
            stable_id = probe['stable_id']
            require(stable_id in selected and stable_id in scene['active_ids'], 'Probe did not use CURRENT selected identity.')
            now, old, history_valid = history_vertices(scene, stable_id, scenarios['source_mesh_vertices'])
            current_clips = [project(current, vertex) for vertex in now]
            previous_clips = [project(previous, vertex) for vertex in old]
            require(probe['triangle'] in scenarios['triangles'], 'Wrong immutable source triangle.')
            bary, expected_depth = barycentric_pixel(current_clips, probe['triangle'], x, y, planned['internal_extent'])
            require(abs(depth - expected_depth) <= DEPTH_TOLERANCE, 'Independent native depth differs.')
            require(abs(red - (stable_id + 1) / 32) <= VELOCITY_TOLERANCE, 'Pre-TAA fragment identity differs.')
            expected_velocity = perspective_velocity([current_clips[i] for i in probe['triangle']],
                [previous_clips[i] for i in probe['triangle']], bary, current['jitter'], previous['jitter'])
            if stable_id in scene.get('invalid_history_ids', []):
                require(not history_valid, 'Declared invalid history is actually accepted by its proof.')
                bypassed_clips = [project(previous, vertex)
                    for vertex in pose_vertices(scene['previous_pose_words'], stable_id, scenarios['source_mesh_vertices'])]
                bypassed_velocity = perspective_velocity([current_clips[i] for i in probe['triangle']],
                    [bypassed_clips[i] for i in probe['triangle']], bary, current['jitter'], previous['jitter'])
                key = (planned['id'], stable_id)
                invalid_history_gaps[key] = max(invalid_history_gaps.get(key, 0),
                    max(abs(a - b) for a, b in zip(bypassed_velocity, expected_velocity)))
            if planned['id'] in ('T045', 'T046', 'T049', 'T050'):
                naive = [.5 * (sum(bary[j] * previous_clips[i][c] / previous_clips[i][3]
                                  for j, i in enumerate(probe['triangle'])) - previous['jitter'][c]
                    - sum(bary[j] * current_clips[i][c] / current_clips[i][3]
                          for j, i in enumerate(probe['triangle'])) + current['jitter'][c]) for c in range(2)]
                perspective_counterexample_gap = max(perspective_counterexample_gap,
                    max(abs(a - b) for a, b in zip(naive, expected_velocity)))
            designated = [c for c, key in enumerate(('designated_nonzero_x', 'designated_nonzero_y')) if scene[key]]
            compare_velocity(velocity, expected_velocity, designated)
            if math.hypot(*expected_velocity) > 2 ** -8:
                nonzero += 1
                require(math.hypot(*velocity) > 2 ** -9, 'Nonzero raw-velocity control accepted zero output.')
            identity_counts[stable_id] = identity_counts.get(stable_id, 0) + 1
            pixels += 1
        require(background_count == 8 and all(value == 4 for value in identity_counts.values()), 'Missing fixed background/four-pixel identity samples.')
        require(color_bits[len(probes):] == [GUARD] * (16 - len(probes)), 'Unused color witness guard corrupted.')
        require(set(identity_counts) == (set() if planned['id'] == 'T020' else set(scene['expected_visible_ids'])), 'Missing visible identity numerical probes.')
        require(planned['id'] == 'T020' or set(scene['expected_visible_ids']) <= set(selected), 'False-negative selected identity.')
        if planned['id'] == 'T020':
            require(not identity_counts, 'Intentional zero command still produced body witnesses.')
        if planned['history_commit']:
            previous_commit = planned['preparation_serial']
    require(captures == 124 and previous_epoch == 127 and len(jitter_values) >= 4,
            'Capture/native epoch/actual native jitter coverage differs.')
    require(nonzero >= 64, 'Missing nonzero controls; zero-motion implementation could pass.')
    require(perspective_counterexample_gap >= 2 * VELOCITY_TOLERANCE,
            'Actual perspective witnesses do not reject the orthographic shortcut robustly.')
    required_resets = {(f'T{serial:03}', 7) for serial in range(26, 37)}
    require(required_resets <= set(invalid_history_gaps)
            and all(invalid_history_gaps[key] >= 2 * VELOCITY_TOLERANCE for key in required_resets),
            'Lifecycle controls do not independently distinguish reset from bypassed invalid history.')
    require(raw.get('compute_dispatches') == 1240 and raw.get('compute_barriers') == 1116
            and raw.get('observer_dispatches') == 124, 'Total GPU work differs.')
    return dict(numerical_contract_passed=True, native_render_contract_passed=True,
                native_qualified=False, performance_qualified=False,
                captures=captures, velocity_probes=pixels, nonzero_probes=nonzero,
                distinct_native_jitters=len(jitter_values), actual_viewport_records=previous_epoch,
                perspective_counterexample_gap_uv=perspective_counterexample_gap,
                invalid_history_gap_min_uv=min(invalid_history_gaps[key] for key in required_resets))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('receipt', type=Path)
    parser.add_argument('--plan', type=Path, default=Path(__file__).with_name('gate_plan.json'))
    parser.add_argument('--scenarios', type=Path, required=True)
    parser.add_argument('--expected', type=Path, required=True)
    args = parser.parse_args()
    result = validate(load_json(args.receipt), load_json(args.plan), load_json(args.expected),
                      args.receipt.parent, load_json(args.scenarios))
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()

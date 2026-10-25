"""Independent analytic native wall oracle. Reading this module never starts Godot."""
import hashlib
import json
import math
import re
import struct

GUARD = 0xcdcdcdcd
OFFSETS = (4, 2052, 2564, 2692, 2724, 2732, 2734)
COLUMNS = (8, 16, 24, 31, 40, 48, 56, 62)
INPUTS = ('OcclusionNativeLab.csproj', 'project.godot', 'Main.tscn', 'Main.cs', 'ProbeLogger.cs',
          'Wall.gdshader', 'Consumer.gdshader', 'CopyDepth.comp', 'Reduce.comp', 'Produce.comp', 'Finalize.comp', 'oracle.json')
MATRIX = (
    ('wall_uncull', False, True, 0, 'normal', 'ok', False),
    ('wall_candidate', True, True, 0, 'normal', 'ok', True),
    ('error_fallback', True, True, 0, 'zero', 'error', False),
    ('stale_fallback', True, True, 0, 'zero', 'stale', False),
    ('missing_fallback', True, True, 0, 'zero', 'missing', False),
    ('zero_candidate', True, True, 0, 'zero', 'ok', True),
    ('overflow_gpu_fallback', True, True, 0, 'overflow', 'ok', True),
    ('recovery', True, True, 0, 'normal', 'ok', True),
    ('open_uncull', False, False, 0, 'normal', 'ok', False),
    ('open_candidate', True, False, 0, 'normal', 'ok', True),
    ('shift_wall', True, True, 4, 'normal', 'ok', True),
    ('shift_current_clear', True, False, 4, 'normal', 'ok', True),
    ('wall_returns', True, True, 0, 'normal', 'ok', True),
    ('feature_cleared', False, True, 0, 'normal', 'ok', False),
)


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
    def bad(value):
        raise ValueError('Nonfinite JSON: ' + value)
    return json.loads(path.read_bytes(), object_pairs_hook=pairs, parse_constant=bad)


def validate_oracle(oracle):
    require(type(oracle) is dict and set(oracle) == {'contract', 'width', 'height', 'logical_slots', 'draw_slot_capacity', 'cases'}, 'Oracle schema differs.')
    require(oracle['contract'] == 'native-occlusion-oracle-v1', 'Wrong oracle.')
    for key, expected in (('width', 64), ('height', 32), ('logical_slots', 16), ('draw_slot_capacity', 32)):
        require(type(oracle[key]) is int and oracle[key] == expected, 'Different native stand scale: ' + key)
    require(type(oracle['cases']) is list and len(oracle['cases']) == len(MATRIX), 'Incomplete ordered matrix.')
    for row, expected in zip(oracle['cases'], MATRIX):
        require(type(row) is dict and tuple(row) == ('name', 'enabled', 'wall', 'shift', 'publication', 'ack', 'selected'), 'Case schema differs.')
        require(tuple(row.values()) == expected and all(type(row[key]) is bool for key in ('enabled', 'wall', 'selected'))
                and type(row['shift']) is int, 'Unqualified native case.')


def depth_at(x, y, probe):
    """Selected ordinary quads only; marker/crowd depth must NOT exist at callback."""
    if not probe['wall'] or not (0 <= x < 64 and 0 <= y < 32):
        return 0.0
    original = x + probe['shift'] + 0.5
    return 0.75 if 4 <= original < 28 or 36 <= original < 60 else 0.0


def slot_depth(slot):
    return 0.8125 if slot in (2, 12) else 0.625


def slot_color(slot):
    return 4 if slot in (2, 12) else (1 if slot % 2 == 0 else 2)


def full_resolution_visible(slot, probe):
    # Quad is centered at (column+.5,row+.5) and has a one-pixel half extent.
    x, y = COLUMNS[slot % 8] - probe['shift'], 10 if slot < 8 else 22
    return any(depth_at(px, py, probe) <= slot_depth(slot) + 0.00002
               for py in range(y - 1, y + 2) for px in range(x - 1, x + 2))


def conservative_visible(slot, probe):
    # Expand one pixel, then query all full-resolution pixels of aligned cells.
    # This independently evaluates geometry; it never reads/reduces GPU mip data.
    x, y = COLUMNS[slot % 8] - probe['shift'], 10 if slot < 8 else 22
    x0, y0, x1, y1 = x - 2, y - 2, x + 3, y + 3
    if x0 < 0 or y0 < 0 or x1 > 64 or y1 > 32:
        return True
    block = 1 << (max(x1 - x0, y1 - y0) - 1).bit_length()
    return any(depth_at(px, py, probe) <= slot_depth(slot) + 0.00002
               for py in range((y0 // block) * block, ((y1 + block - 1) // block) * block)
               for px in range((x0 // block) * block, ((x1 + block - 1) // block) * block))


def selected_ids(probe):
    if probe['publication'] == 'zero':
        return []
    return [slot for slot in range(16) if conservative_visible(slot, probe)]


def rgb(bits):
    return [255 if bits & (1 << channel) else 0 for channel in range(3)]


def expected_samples(probe):
    zero = probe['selected'] and probe['publication'] == 'zero'
    result = []
    for slot in range(16):
        x, y = COLUMNS[slot % 8] - probe['shift'], 10 if slot < 8 else 22
        wall = depth_at(x, y, probe) != 0
        bits = 7 if wall else 0
        if not zero and (not wall or slot_depth(slot) > 0.75):
            bits = slot_color(slot)
        result.append(dict(kind='slot', slot=slot, x=x, y=y, rgb=rgb(bits)))
    for index, y in enumerate((3, 29)):
        result.append(dict(kind='ordinary_marker', slot=index, x=32 - probe['shift'], y=y, rgb=rgb(7)))
    for index, x in enumerate((10, 50)):
        result.append(dict(kind='wall', slot=index, x=x - probe['shift'], y=16, rgb=rgb(7 if probe['wall'] else 0)))
    result.append(dict(kind='gap', slot=0, x=31 - probe['shift'], y=16, rgb=rgb(0)))
    result.append(dict(kind='background', slot=0, x=63, y=0, rgb=rgb(0)))
    return result


def words(value, count, name):
    require(type(value) is list and len(value) == count and all(type(v) is int and 0 <= v <= 0xffffffff for v in value), 'Invalid ' + name)


def digest_words(values):
    return hashlib.sha256(struct.pack('<' + 'I' * len(values), *values)).hexdigest()


def integer(value, low=0, high=0x7fffffffffffffff):
    return type(value) is int and low <= value <= high


def validate(raw, oracle, expected):
    validate_oracle(oracle)
    require(type(raw) is dict and raw.get('contract') == 'native-occlusion-case-v1' and raw.get('passed') is True
            and raw.get('failure') == '', 'Native runtime failed/incomplete.')
    for key in ('engine_hash', 'engine_sha256', 'sdk_version', 'managed_api_assembly_sha256', 'managed_fixture_assembly_sha256', 'source_sha256'):
        require(key in expected and raw.get(key) == expected[key], 'Identity differs: ' + key)
    require(type(raw['engine_hash']) is str and re.fullmatch('[0-9a-f]{40}', raw['engine_hash']), 'Invalid native source SHA.')
    require(type(raw.get('checks')) is int and raw['checks'] >= 300, 'Insufficient native checks.')
    require(raw.get('driver') == 'vulkan' and raw.get('renderer') == 'forward_plus' and type(raw.get('gpu_name')) is str
            and raw['gpu_name'].strip(), 'Wrong native backend/GPU.')
    require(raw.get('diagnostics') == [] and type(raw.get('diagnostics')) is list and raw.get('dropped_diagnostics') == 0
            and type(raw['dropped_diagnostics']) is int, 'Unexpected/dropped diagnostic.')
    for key, value in (('callbacks', 11), ('compute_dispatches', 99), ('compute_barriers', 88), ('viewport_readbacks', 14), ('ownership_probes', 4),
                       ('caller_owned_final', 0), ('caller_requested_bytes_final', 0), ('images_written', 0), ('cpu_depth_reads_before_producer', 0)):
        require(type(raw.get(key)) is int and raw[key] == value, 'Incomplete/incorrect counter: ' + key)
    require(raw.get('native_api_present') is True and raw.get('performance_qualified') is False and raw.get('release_driver_qualified') is False,
            'Unsupported qualification claim.')
    probes = raw.get('probes')
    require(type(probes) is list and len(probes) == len(MATRIX), 'Incomplete numerical native matrix.')
    previous_epoch = previous_generation = 0
    viewport = None
    hashes = {}
    evidence = []
    for observed, probe in zip(probes, oracle['cases']):
        require(type(observed) is dict and observed.get('name') == probe['name'] and observed.get('callback_completed_before_readback') is True
                and observed.get('persistent_fallback_unchanged') is True, 'Wrong render/readback matrix.')
        for key, hash_key, count in (('id_words', 'id_sha256', 36), ('candidate_command_words', 'candidate_command_sha256', 16),
                                     ('fallback_command_words', 'fallback_command_sha256', 16), ('stats_words', 'stats_sha256', 16),
                                     ('pyramid_words', 'pyramid_sha256', 2739)):
            words(observed.get(key), count, key)
            require(observed.get(hash_key) == digest_words(observed[key]), 'GPU evidence byte hash differs: ' + key)
        ids, command, fallback, stats, pyramid = (observed[k] for k in ('id_words', 'candidate_command_words', 'fallback_command_words', 'stats_words', 'pyramid_words'))
        require(ids[:16] == list(range(16)) and ids[32:] == [GUARD] * 4, 'Immutable fallback/ID guard corrupted.')
        require(fallback == [GUARD] * 4 + [6, 16, 0, 0, 0] + [GUARD] * 7, 'Persistent fallback command changed.')
        callback, outcome = observed.get('callback'), observed.get('outcome')
        require(type(outcome) is dict and set(outcome) == {'present', 'epoch', 'configuration_generation', 'selected', 'mode', 'reason', 'early_surfaces', 'auto_instancing_disabled'}
                and type(outcome['present']) is bool and type(outcome['selected']) is bool and outcome['selected'] is probe['selected'], 'Wrong native outcome schema/selection.')
        if not probe['enabled']:
            require(callback is None and command == stats == [GUARD] * 16 and pyramid == [GUARD] * 2739 and ids[16:32] == [GUARD] * 16,
                    'Feature-off executed/reused producer data.')
            if outcome['present']:
                require(outcome['mode'] == 'FALLBACK_SELECTED', 'Cleared feature retained candidate mode.')
            admitted = list(range(16))
        else:
            require(type(callback) is dict and callback.get('probe') == probe['name'] and integer(callback.get('viewport'), 1)
                    and integer(callback.get('epoch'), 1) and integer(callback.get('configuration_generation'), 1), 'Invalid current view identity.')
            viewport = callback['viewport'] if viewport is None else viewport
            require(callback['viewport'] == viewport and callback['epoch'] > previous_epoch
                    and callback['configuration_generation'] >= previous_generation, 'Wrong/stale view/epoch/configuration.')
            previous_epoch, previous_generation = callback['epoch'], callback['configuration_generation']
            require(callback.get('view_index') == 0 and type(callback['view_index']) is int and callback.get('internal_size') == [64, 32]
                    and integer(callback.get('native_depth_rid'), 1) and callback.get('dispatches') == 9 and callback.get('barriers') == 8
                    and callback.get('depth_cpu_read_before_producer') is False, 'Invalid native scope/order evidence.')
            require(outcome['present'] is True and outcome['epoch'] == previous_epoch and outcome['configuration_generation'] == previous_generation
                    and outcome['mode'] == ('CANDIDATE_SELECTED' if probe['selected'] else 'FALLBACK_SELECTED')
                    and type(outcome['reason']) is str and outcome['reason'] and type(outcome['early_surfaces']) is int
                    and outcome['early_surfaces'] == (2 if probe['wall'] else 0) and outcome['auto_instancing_disabled'] is False, 'Native phase/outcome differs from E13d batching.')
            matrix_words = callback.get('world_to_clip_words'); words(matrix_words, 16, 'corrected projection')
            matrix = struct.unpack('<16f', struct.pack('<16I', *matrix_words))
            require(all(math.isfinite(v) for v in matrix), 'Nonfinite native projection.')
            for slot in (0, 2, 7, 12, 15):
                point = ((COLUMNS[slot % 8] + 0.5) / 8 - 4, 2 - ((10 if slot < 8 else 22) + 0.5) / 8,
                         0.5 if slot in (2, 12) else -1, 1)
                clip = [sum(matrix[column * 4 + row] * point[column] for column in range(4)) for row in range(4)]
                require(abs(clip[3] - 1) < 0.00001 and abs((clip[0] / clip[3] * 0.5 + 0.5) * 64 - (COLUMNS[slot % 8] + 0.5 - probe['shift'])) < 0.0001
                        and abs((clip[1] / clip[3] * 0.5 + 0.5) * 32 - ((10 if slot < 8 else 22) + 0.5)) < 0.0001
                        and abs(clip[2] / clip[3] - slot_depth(slot)) < 0.00001, 'Actual corrected camera projection differs from qualified geometry.')
            require(pyramid[:4] + pyramid[2735:] == [GUARD] * 8, 'Hi-Z guard corrupted.')
            for level, offset in enumerate(OFFSETS):
                width, height, block = max(1, 64 >> level), max(1, 32 >> level), 1 << level
                for y in range(height):
                    for x in range(width):
                        want = min(depth_at(px, py, probe) for py in range(y * block, (y + 1) * block) for px in range(x * block, (x + 1) * block))
                        actual = struct.unpack('<f', struct.pack('<I', pyramid[offset + y * width + x]))[0]
                        require(math.isfinite(actual) and abs(actual - want) <= 0.00002, 'Native early depth/min hierarchy differs: ' + probe['name'])
            potential = selected_ids(probe)
            overflow = probe['publication'] == 'overflow' and len(potential) > 0
            written = [] if overflow else potential
            require(ids[16:32] == written + [GUARD] * (16 - len(written)) and len(set(written)) == len(written), 'Candidate IDs/ranges/uniqueness differ.')
            require(stats == [GUARD] * 4 + [len(written), len(potential), int(overflow), int(probe['publication'] == 'zero'),
                    previous_epoch & 0xffffffff, previous_epoch >> 32, previous_generation & 0xffffffff, previous_generation >> 32] + [GUARD] * 4,
                    'GPU publication did not use the current native identity.')
            require(command == [GUARD] * 4 + [6, 16 if overflow else len(written), 0, 0, 0 if overflow else 16] + [GUARD] * 7,
                    'Candidate/fallback indexed command differs.')
            exact = [slot for slot in range(16) if full_resolution_visible(slot, probe)]
            if probe['publication'] == 'normal':
                require(set(exact) <= set(written), 'Native occlusion false negative.')
                if probe['wall']:
                    require(len(written) < 16, 'Always-visible producer cannot qualify native occlusion.')
            admitted = list(range(16)) if overflow or not probe['selected'] else written
        samples = observed.get('samples'); wanted = expected_samples(probe)
        require(type(samples) is list and len(samples) == len(wanted), 'Incomplete numeric color/depth-consumer probes.')
        for actual, want in zip(samples, wanted):
            require(type(actual) is dict and set(actual) == set(want)
                    and all(actual[key] == want[key] and type(actual[key]) is type(want[key]) for key in ('kind', 'slot', 'x', 'y')),
                    'Numerical consumer oracle coordinates differ.')
            require(type(actual['rgb']) is list and len(actual['rgb']) == 3 and all(type(a) is int and 0 <= a <= 255 and abs(a - b) <= 1
                    for a, b in zip(actual['rgb'], want['rgb'])), 'Native resumed depth/color/command consumption differs: ' + probe['name'])
        require(type(observed.get('image_sha256')) is str and re.fullmatch('[0-9a-f]{64}', observed['image_sha256']), 'Invalid numerical raster hash.')
        hashes[probe['name']] = observed['image_sha256']
        evidence.append(dict(name=probe['name'], admitted_ids=admitted, selected=probe['selected'], numeric_samples=len(wanted)))
    for name in ('wall_candidate', 'error_fallback', 'stale_fallback', 'missing_fallback', 'overflow_gpu_fallback', 'recovery', 'wall_returns', 'feature_cleared'):
        require(hashes[name] == hashes['wall_uncull'], 'Native split/fallback changed the uncull wall raster: ' + name)
    require(hashes['open_candidate'] == hashes['open_uncull'] and hashes['zero_candidate'] != hashes['wall_uncull']
            and hashes['open_uncull'] != hashes['wall_uncull'] and hashes['shift_wall'] != hashes['shift_current_clear'], 'Raster controls did not exercise native selection/current depth.')
    return dict(native_render_contract_passed=True, qualified_scope='controlled_native_orthographic64x32_sixteen_slots_only',
                false_negative_ids=[], performance_qualified=False, release_driver_qualified=False, probes=evidence)

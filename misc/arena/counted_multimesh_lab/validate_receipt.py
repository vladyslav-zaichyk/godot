"""Independent E10b bounded producer, pixel and execution-ledger oracle. No Godot launch."""
import hashlib
import re
import struct

INPUTS = {'CountedMultimeshLab.csproj', 'project.godot', 'Main.tscn', 'CountedMultimeshContract.cs',
          'ContractErrorLogger.cs', 'Consumer.gdshader', 'Producer.glslinc'}
GUARDS = {'negative_capacity', 'capacity_above_int32', 'negative_command_offset',
          'command_offset_above_int32', 'unaligned_command_offset', 'negative_count_offset',
          'count_offset_above_int32', 'unaligned_count_offset', 'zero_maximum', 'maximum_above_bound',
          'zero_stride', 'short_stride', 'unaligned_stride', 'stride_above_int32', 'short_command_range',
          'short_count_range', 'commands_without_indirect', 'counter_without_indirect',
          'invalid_counter_rid', 'invalid_command_rid', 'invalid_multimesh_rid',
          'persistent_commands', 'persistent_counter', 'counted_mode_through_old_api'}
LIMITATIONS = (
    'No FPS/full-frame or physical VRAM result; readbacks serialize this correctness subset',
    'No sustained lifetime plateau or general GPU fence contract; bounded caller ledger only',
    'No renderer alias/subpass/cache-recovery/excluded-pass probe',
    'No E8/history, shadows, transparency, XR or secondary-view qualification',
    'No wrong-device RID, transform-backed legacy-reset or ordinary-allocation live probe',
    'Paired fixed-dense control additionally requires ordinary MDI and native indirect first-instance support',
    'No game AI/animation/combat/networking or production R14 integration',
    'Expected native call counts are not GPU instrumentation',
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def exact(value, expected, message):
    require(type(value) is type(expected) and value == expected, message)


def hashed(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def key(row, arm=True):
    for name, kind in [('shared', bool), ('stride', int), ('maximum', int)]:
        require(type(row[name]) is kind, 'Invalid case type: ' + name)
    result = row['shared'], row['stride'], row['maximum']
    if arm:
        require(type(row['counted']) is bool, 'Invalid arm type.')
        result += row['counted'],
    return result


def layout(shared, stride, maximum):
    span = ((maximum + 1) * stride + 15) & ~15
    return span, 32 + 2 * span


def admitted(phase, maximum):
    return 0 if phase in (1, 5) else 1 if phase in (3, 6) else min(2, maximum) if phase == 2 else maximum


def source_at(record, count, phase, maximum):
    if record >= count:
        return record
    if phase == 4:
        return maximum - 1 - record
    if phase in (3, 6) or (phase == 2 and record == 1):
        return maximum - 1
    return record


def bank_hash(words):
    return hashlib.sha256(struct.pack('<' + 'I' * len(words), *words)).hexdigest()


def expected_data(shared, stride, maximum, phase):
    span, command_bytes = layout(shared, stride, maximum)
    commands = [0xcdcdcdcd] * (command_bytes // 4)
    counters = commands if shared else [0xcdcdcdcd] * 8
    ids = [0xffffffff] * 32
    visible = set()
    count = admitted(phase, maximum)
    for owner in range(2):
        counters[1 + owner * 2] = count
        for source in range(maximum + 1):
            ids[owner * 16 + 1 + source * 3] = owner * 5 + source
        for record in range(maximum + 1):
            source = source_at(record, count, phase, maximum)
            nonempty = record < count or record == maximum or phase >= 5
            word = (32 + owner * span + record * stride) // 4
            commands[word:word + 5] = [6, int(nonempty), source % 2 * 6, source % 2 * 4, 1 + source * 3]
            if record < count:
                require(owner * 5 + source not in visible, 'Duplicate independent admitted identity.')
                visible.add(owner * 5 + source)
    return count, sorted(visible), bank_hash(commands), bank_hash(ids), bank_hash(counters)


def validate(raw, args):
    for name, expected in dict(contract='counted-multimesh-numeric-contract-v1', passed=True,
            engine_hash=args.engine_hash, sdk_version=args.sdk_version, driver='vulkan', renderer='forward_plus',
            final_owned_rd_resource_count=0, final_owned_requested_bytes=0,
            image_files_written=0, performance_window_count=0, performance_qualified=False).items():
        exact(raw.get(name), expected, 'Contract field differs: ' + name)
    require(raw['failure'] == '' and raw['native_diagnostics'] == [], 'Failure/native diagnostics.')
    require(isinstance(raw['gpu_name'], str) and raw['gpu_name'], 'Missing adapter identity.')
    require(set(raw['source_sha256']) == INPUTS and all(hashed(value) for value in raw['source_sha256'].values()), 'Missing/malformed source identities.')
    for name in ('managed_fixture_assembly_sha256', 'managed_api_assembly_sha256'):
        require(hashed(raw[name]), 'Malformed assembly identity.')
    supported, limit = raw['supports_draw_indirect_count'], raw['max_draw_indirect_count']
    require(type(supported) is bool and type(limit) is int and 0 <= limit <= 0xffffffff and supported == (limit > 0), 'Capability/limit mismatch.')
    exact(raw['native_count_qualified'], supported, 'Unavailable path claimed positive qualification.')
    maxima = {1, min(4, limit)} if supported else set()
    descriptions = {(shared, stride, maximum) for shared in (False, True) for stride in (20, 32) for maximum in maxima}
    expected_cases = {(*description, counted) for description in descriptions for counted in (False, True)}
    cases = {key(row): row for row in raw['cases']}
    require(len(raw['cases']) == len(expected_cases) and set(cases) == expected_cases, 'Missing/duplicate case ledger.')
    expected_phases = {(*case, phase) for case in expected_cases for phase in range(7 if case[3] else 5)}
    phase_keys, phases = set(), {}
    buffer_reads = dispatches = pixels = 0
    for case_key, row in cases.items():
        shared, stride, maximum, counted = case_key
        span, command_bytes = layout(shared, stride, maximum)
        for name, expected in dict(native_owners=2, draw_slot_capacity=16, command_offset=32,
                span_bytes=span, command_bytes=command_bytes, counter_bytes=0 if shared else 32,
                id_bytes=128, valid_nonempty_tail_record=True, phases=7 if counted else 5,
                fixed_submission='MULTI_DRAW', native_transform_bank=False).items():
            exact(row[name], expected, 'Case field differs: ' + name)
        require(row['counter_offsets'] == [4, 12] and all(type(value) is int for value in row['counter_offsets']), 'Counter offset ledger differs.')
        buffer_reads += 1 if shared else 2  # Explicit bounded retirement readbacks.
    for row in raw['phases']:
        case_key = key(row)
        require(type(row['phase']) is int, 'Phase type differs.')
        phase_key = (*case_key, row['phase'])
        require(phase_key in expected_phases and phase_key not in phase_keys, 'Missing/duplicate phase identity.')
        phase_keys.add(phase_key)
        phases[phase_key] = row
        shared, stride, maximum, counted = case_key
        phase = row['phase']
        count, ids, commands_hash, ids_hash, counter_hash = expected_data(shared, stride, maximum, phase)
        require(row['published_counts'] == [count, count] and all(type(value) is int for value in row['published_counts']), 'GPU counter ledger differs.')
        require(row['admitted_ids'] == ids and all(type(value) is int for value in row['admitted_ids']), 'Admitted identity coverage differs.')
        for name, wanted in [('command_sha256', commands_hash), ('ids_sha256', ids_hash), ('counter_sha256', counter_hash)]:
            exact(row[name], wanted, 'Independent full-bank oracle differs: ' + name)
        require(hashed(row['numeric_raster_sha256']), 'Malformed numeric raster hash.')
        for name in ('full_command_and_tail_verified', 'full_ids_verified', 'counter_verified_after_draw'):
            exact(row[name], True, 'Missing producer verification: ' + name)
        exact(row['paired_fixed_equality'], counted and phase < 5, 'Matched pair claim differs.')
        exact(row['excluded_nonempty_tail_probe'], phase >= 5, 'Tail exclusion claim differs.')
        samples = {(sample['id'], sample['side']): sample for sample in row['samples']}
        require(len(row['samples']) == 20 and set(samples) == {(identity, side) for identity in range(10) for side in range(2)}, 'Missing/duplicate raster probe.')
        for (identity, side), sample in samples.items():
            require(type(identity) is int and type(side) is int, 'Sample identity type differs.')
            exact(sample['x'], identity % 5 * 16 + 8 + side * 4, 'Pixel x differs.')
            exact(sample['y'], 32 - (identity // 5 * 16 + 8), 'Pixel y differs.')
            visible = identity in ids and (side == 0 or identity % 5 % 2 == 0)
            bits = 1 + identity % 6 if visible else 0
            wanted = [255 if bits & (1 << channel) else 0 for channel in range(3)]
            require(sample['expected_rgb'] == wanted and all(type(value) is int for value in sample['expected_rgb']), 'Pixel expectation differs.')
            actual = sample['actual_rgb']
            require(len(actual) == 3 and all(type(value) is int and 0 <= value <= 255 and abs(value - expected) <= 4
                    for value, expected in zip(actual, wanted)), 'Pixel/geometry coverage differs.')
        buffer_reads += 2 if shared else 3
        dispatches += 3
        pixels += 20
    require(phase_keys == expected_phases, 'Incomplete phase coverage.')
    for shared, stride, maximum in descriptions:
        for phase in range(5):
            left, right = phases[(shared, stride, maximum, False, phase)], phases[(shared, stride, maximum, True, phase)]
            for name in ('command_sha256', 'ids_sha256', 'counter_sha256', 'numeric_raster_sha256'):
                exact(left[name], right[name], 'Fixed/count pair differs: ' + name)
        for actual, reference in [(5, 1), (6, 3)]:
            exact(phases[(shared, stride, maximum, True, actual)]['numeric_raster_sha256'],
                  phases[(shared, stride, maximum, True, reference)]['numeric_raster_sha256'], 'Legal nonempty tail changed raster.')
    guards = {key(row, False): row for row in raw['guards']}
    require(len(raw['guards']) == len(descriptions) and set(guards) == descriptions, 'Missing/duplicate live guard ledger.')
    for description, row in guards.items():
        expected = GUARDS | ({'overlapping_shared_counter'} if description[0] else set())
        require(len(row['rejected']) == len(expected) and set(row['rejected']) == expected, 'Missing/duplicate live attach probe.')
        for name in ('transactional', 'old_span_resets', 'old_single_resets', 'restored_counted'):
            exact(row[name], True, 'Live rebind/reset failed: ' + name)
    ownership = {key(row, False): row for row in raw['ownership']}
    require(len(raw['ownership']) == len(descriptions) and set(ownership) == descriptions, 'Missing/duplicate ownership ledger.')
    for description, row in ownership.items():
        for name in ('second_borrower_detach', 'second_borrower_destroy', 'other_view_preserved', 'caller_banks_survive'):
            exact(row[name], True, 'Borrowed lifetime subset failed: ' + name)
        exact(row['survivor_raster_sha256'], phases[(*description, True, 6)]['numeric_raster_sha256'], 'Surviving native owner raster changed.')
    if not supported:
        buffer_reads = 2  # One unsupported valid-bank attach and its separate-bank retirement.
    for name, expected in dict(producer_dispatches=dispatches, gpu_buffer_readbacks=buffer_reads, numeric_pixel_samples=pixels,
            retired_cases=len(expected_cases) if supported else 1, unavailable_rejections=0 if supported else 1).items():
        exact(raw[name], expected, 'Execution ledger differs: ' + name)
    require(type(raw['checks']) is int and raw['checks'] > 0 and type(raw['peak_owned_requested_bytes']) is int and raw['peak_owned_requested_bytes'] > 0, 'Missing bounded caller accounting.')
    require(tuple(raw['not_qualified']) == LIMITATIONS, 'Qualification boundaries changed or missing.')

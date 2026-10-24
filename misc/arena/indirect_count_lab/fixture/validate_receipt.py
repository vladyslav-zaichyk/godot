"""Pure independent ledger/pixel checks for E10a receipts; never launches Godot."""
import re


def require(condition, message):
    if not condition:
        raise ValueError(message)


GUARDS = {'wrong_list', 'negative_command_offset', 'command_offset_above_uint32',
          'negative_count_offset', 'count_offset_above_uint32', 'zero_maximum',
          'maximum_above_uint32', 'zero_stride', 'stride_above_uint32',
          'short_command_range', 'short_counter_range', 'invalid_counter_rid'}


def key(row):
    require(type(row['indexed']) is bool and type(row['shared']) is bool
            and type(row['stride']) is int and type(row['maximum']) is int, 'Invalid case key types.')
    return row['indexed'], row['shared'], row['stride'], row['maximum']


def validate(raw, args):
    for name, expected in dict(contract='indirect-count-numeric-contract-v1', passed=True,
            engine_hash=args.engine_hash, sdk_version=args.sdk_version, driver='vulkan',
            renderer='forward_plus', final_owned_rd_resource_count=0, final_owned_requested_bytes=0,
            image_files_written=0, performance_window_count=0, performance_qualified=False).items():
        require(raw.get(name) == expected and type(raw.get(name)) is type(expected), 'Contract field differs: ' + name)
    require(not raw['failure'] and raw['native_diagnostics'] == [], 'Runtime failure/diagnostics.')
    supported, limit = raw['supports_draw_indirect_count'], raw['max_draw_indirect_count']
    require(type(supported) is bool and type(limit) is int and 0 <= limit <= 0xffffffff
            and supported == (limit > 0), 'Enabled capability/limit disagrees.')
    expected = {(indexed, shared, stride, maximum) for indexed in (False, True)
                for shared in (False, True) for stride in (20, 32)
                for maximum in {1, min(4, limit)}} if supported else {(True, False, 20, 1)}
    cases = {key(row): row for row in raw['cases']}
    require(len(raw['cases']) == len(expected) and set(cases) == expected, 'Missing/duplicate/incorrect case ledger.')
    phases, phase_keys, expected_phases = {}, set(), set()
    buffer_reads = texture_reads = 0
    for case_key, case in cases.items():
        indexed, shared, stride, maximum = case_key
        counts = [maximum, 0, 1] + ([maximum + 1] if maximum < limit else []) if supported else [1]
        command_bytes = (32 + maximum * stride + (20 if indexed else 16) + 15) & ~15
        require(case['command_offset'] == 32 and case['count_offset'] == (4 if shared else 12)
                and case['valid_nonempty_records'] == maximum + 1 and case['phases'] == len(counts)
                and case['caller_requested_command_bytes'] == command_bytes
                and case['caller_requested_counter_bytes'] == (0 if shared else 32), 'Case range/ownership ledger differs.')
        for phase, count in enumerate(counts):
            phase_key = (*case_key, phase)
            expected_phases.add(phase_key)
            phases[phase_key] = count
            buffer_reads += 1 if shared else 2
            texture_reads += 1
    rasters = {}
    for row in raw['phases']:
        require(type(row['phase']) is int, 'Phase index type differs.')
        phase_key = (*key(row), row['phase'])
        require(phase_key in expected_phases and phase_key not in phase_keys, 'Missing/duplicate/incorrect phase identity.')
        phase_keys.add(phase_key)
        count = phases[phase_key]
        effective = min(count, row['maximum']) if supported else 0
        require(type(row['raw_gpu_count']) is int and type(row['executed_draws']) is int
                and row['raw_gpu_count'] == count and row['executed_draws'] == effective
                and row['valid_nonempty_records'] == row['maximum'] + 1
                and row['full_command_and_tail_verified'] is True and row['counter_verified_after_draw'] is True,
                'Actual-count/clamp/tail proof differs.')
        for name in ('command_sha256', 'counter_sha256', 'numeric_texture_sha256'):
            require(isinstance(row[name], str) and re.fullmatch(r'[0-9a-f]{64}', row[name]), 'Malformed numerical hash.')
        raster = row['numeric_texture_sha256']
        require(effective not in rasters or rasters[effective] == raster, 'Equivalent count draws have different full texture bytes.')
        rasters[effective] = raster
        samples = {sample['cell']: sample for sample in row['samples']}
        require(len(row['samples']) == 5 and set(samples) == set(range(5)), 'Missing/duplicate numeric pixel.')
        for cell, sample in samples.items():
            color = cell + 1 if cell < effective else 0
            rgb = [255 if color & (1 << channel) else 0 for channel in range(3)]
            require(type(cell) is int and sample['x'] == cell * 16 + 8 and sample['y'] == 8
                    and sample['expected_rgb'] == rgb and all(type(value) is int for value in sample['expected_rgb'])
                    and len(sample['actual_rgb']) == 3 and all(type(value) is int and 0 <= value <= 255
                    and abs(value - wanted) <= 1 for value, wanted in zip(sample['actual_rgb'], rgb)),
                    'Independent pixel/count oracle differs.')
    require(phase_keys == expected_phases and len(raw['phases']) == texture_reads, 'Incomplete phase coverage.')
    guards = {key(row): row for row in raw['guards']}
    require(len(raw['guards']) == (len(expected) if supported else 0)
            and set(guards) == (expected if supported else set()), 'Missing/duplicate guard case.')
    for case_key, row in guards.items():
        wanted = GUARDS | ({'shared_counter_overlap'} if case_key[1] else set())
        require(len(row['rejected']) == len(wanted) and set(row['rejected']) == wanted
                and row['subsequent_valid_count_draw'] is True, 'Incomplete quiet API guard subset.')
    for name, wanted in dict(command_producer_dispatches=texture_reads, counter_producer_dispatches=texture_reads,
            count_draw_calls=texture_reads if supported else 0, unavailable_rejections=0 if supported else 1,
            gpu_buffer_readbacks=buffer_reads, numeric_texture_readbacks=texture_reads,
            numeric_pixel_samples=texture_reads * 5, retired_cases=len(expected)).items():
        require(type(raw[name]) is int and raw[name] == wanted, 'Execution ledger differs: ' + name)
    require(raw['count_draw_qualified'] is supported, 'Unsupported path claimed positive count drawing.')
    require(raw.get('not_qualified'), 'Missing limitations.')

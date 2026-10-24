"""Independent exact Error ledger and full uint-buffer oracle; no engine launch."""
import hashlib
import json
import re
import struct

CASES = ('metadata_recovery', 'pending_and_recorded_aliases', 'arguments_first_recovery',
         'dormant_pipeline_activation', 'barriers_end_repeat_zero', 'push_constant_history')
INPUTS = ('CheckedComputeLab.csproj', 'project.godot', 'Main.tscn', 'Main.cs', 'ProbeLogger.cs', 'oracle.json')
HEX64 = re.compile('[0-9a-f]{64}')


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
    def constant(value):
        raise ValueError('Non-finite JSON number: ' + value)
    return json.loads(path.read_bytes(), object_pairs_hook=pairs, parse_constant=constant)


def oracle_words(contributions):
    require(type(contributions) is list and 1 <= len(contributions) <= 8, 'Missing/big contribution ledger.')
    words = [0xcdcdcdcd] * 40
    words[4:36] = [0] * 32
    for contribution in contributions:
        require(type(contribution) is list and len(contribution) == 3 and all(type(x) is int and 0 <= x <= 0xffffffff for x in contribution), 'Invalid oracle uint.')
        seed, value, count = contribution
        require(1 <= count <= 8, 'Invalid contribution count.')
        for ordinal in range(8):
            at = 4 + ordinal * 4
            terms = [(seed + ordinal * 17) * count, ordinal * count,
                     (seed ^ 0xa511e9b3 ^ ordinal ^ value) * count, count]
            words[at:at + 4] = [(words[at + field] + term) & 0xffffffff for field, term in enumerate(terms)]
    return words


def validate_oracle(oracle):
    require(type(oracle) is dict and oracle.get('contract') == 'checked-compute-oracle-v1'
            and oracle.get('assembly') == 'CheckedComputeLab', 'Wrong oracle.')
    require(oracle.get('inputs') == list(INPUTS), 'Project input inventory differs.')
    require(type(oracle.get('cases')) is dict and tuple(oracle['cases']) == CASES, 'Complete ordered six-case matrix required.')
    for case in oracle['cases'].values():
        require(type(case) is dict and set(case) == {'calls', 'probes'}, 'Invalid case schema.')
        require(type(case['calls']) is list and bool(case['calls']) and type(case['probes']) is list and bool(case['probes']), 'Empty case.')
        for call in case['calls']:
            require(type(call) is list and len(call) == 2 and type(call[0]) is str and bool(call[0])
                    and type(call[1]) is int and call[1] in (0, 31), 'Invalid Error oracle.')
        names = []
        for probe in case['probes']:
            require(type(probe) is list and len(probe) == 2 and type(probe[0]) is str and bool(probe[0]), 'Invalid probe oracle.')
            oracle_words(probe[1])
            names.append(probe[0])
        require(len(names) == len(set(names)), 'Duplicate probe.')


def validate(raw, oracle, case_name, expected):
    validate_oracle(oracle)
    require(case_name in CASES and type(raw) is dict and raw.get('contract') == 'checked-compute-case-v1'
            and raw.get('case_name') == case_name, 'Wrong runtime case.')
    require(raw.get('passed') is True and raw.get('failure') == '', 'Runtime case failed.')
    require(type(raw.get('checks')) is int and raw['checks'] >= 50, 'Check ledger is missing/truncated.')
    require(raw.get('performance_qualified') is False and raw.get('release_driver_qualified') is False, 'Unsupported qualification claim.')
    require(raw.get('driver') == 'vulkan' and raw.get('renderer') == 'forward_plus'
            and type(raw.get('gpu_name')) is str and bool(raw['gpu_name'].strip()), 'Wrong renderer/device.')
    require(type(expected) is dict, 'Missing independent build identity.')
    for name in ('engine_hash', 'engine_sha256', 'sdk_version', 'managed_api_assembly_sha256', 'managed_fixture_assembly_sha256', 'source_sha256'):
        require(name in expected and raw.get(name) == expected[name], 'Identity differs: ' + name)
    require(type(raw['engine_hash']) is str and re.fullmatch('[0-9a-f]{40}', raw['engine_hash']) is not None, 'Invalid engine commit.')
    require(type(raw['sdk_version']) is str and bool(raw['sdk_version']), 'Invalid SDK.')
    for name in ('engine_sha256', 'managed_api_assembly_sha256', 'managed_fixture_assembly_sha256'):
        require(type(raw[name]) is str and HEX64.fullmatch(raw[name]) is not None, 'Invalid payload hash.')
    source = raw['source_sha256']
    require(type(source) is dict and set(source) == set(INPUTS) and all(type(v) is str and HEX64.fullmatch(v) for v in source.values()), 'Invalid input hashes.')
    require(raw.get('diagnostics') == [] and type(raw.get('diagnostics')) is list
            and type(raw.get('dropped_diagnostics')) is int and raw['dropped_diagnostics'] == 0, 'Unexpected/dropped diagnostic.')
    resources = raw.get('resources')
    keys = {'caller_owned_final', 'caller_requested_bytes_final', 'local_devices_final', 'foreign_buffers_final', 'open_lists_final'}
    require(type(resources) is dict and set(resources) == keys
            and all(type(v) is int and v == 0 for v in resources.values()), 'Resources or open lists remain.')
    wanted = oracle['cases'][case_name]
    calls = raw.get('calls')
    require(type(calls) is list and len(calls) == len(wanted['calls']), 'Call coverage is incomplete.')
    for observed, (operation, error) in zip(calls, wanted['calls']):
        require(type(observed) is dict and set(observed) == {'operation', 'error'} and observed['operation'] == operation
                and type(observed['error']) is int and observed['error'] == error, 'Error/order oracle differs: ' + operation)
    probes = raw.get('probes')
    require(type(probes) is list and len(probes) == len(wanted['probes']), 'Numerical probe coverage is incomplete.')
    for observed, (name, contributions) in zip(probes, wanted['probes']):
        require(type(observed) is dict and set(observed) == {'name', 'words', 'sha256'} and observed['name'] == name, 'Probe order/name differs.')
        words = observed['words']
        require(type(words) is list and len(words) == 40 and all(type(v) is int and 0 <= v <= 0xffffffff for v in words), 'Invalid uint output.')
        require(words == oracle_words(contributions), 'Output, invocation counts or guards differ: ' + name)
        digest = hashlib.sha256(struct.pack('<40I', *words)).hexdigest()
        require(type(observed['sha256']) is str and observed['sha256'] == digest, 'Readback hash differs: ' + name)

"""Synthetic receipt adversaries only; this file never launches Godot/dotnet."""
import copy
import hashlib
from pathlib import Path
import struct
import unittest

from validate_receipt import CASES, INPUTS, load_json, oracle_words, validate, validate_oracle

ORACLE = load_json(Path(__file__).parent / 'project/oracle.json')
EXPECTED = dict(engine_hash='7d59f5064a6cd40a1cf2aaa578b8e90d29d1cbe8', engine_sha256='a' * 64,
    sdk_version='4.7.2-arena-dev-g7d59f5064a', managed_api_assembly_sha256='b' * 64,
    managed_fixture_assembly_sha256='c' * 64, source_sha256={name: 'd' * 64 for name in INPUTS})


def specimen(case):
    raw = dict(EXPECTED, contract='checked-compute-case-v1', case_name=case, passed=True, failure='', checks=100,
        performance_qualified=False, release_driver_qualified=False, driver='vulkan', renderer='forward_plus', gpu_name='synthetic only',
        diagnostics=[], dropped_diagnostics=0, resources={key: 0 for key in ('caller_owned_final', 'caller_requested_bytes_final',
            'local_devices_final', 'foreign_buffers_final', 'open_lists_final')}, calls=[], probes=[])
    raw['calls'] = [dict(operation=op, error=error) for op, error in ORACLE['cases'][case]['calls']]
    for name, contributions in ORACLE['cases'][case]['probes']:
        words = oracle_words(contributions)
        raw['probes'].append(dict(name=name, words=words, sha256=hashlib.sha256(struct.pack('<40I', *words)).hexdigest()))
    return raw


class ReceiptTests(unittest.TestCase):
    def reject(self, mutate, case=CASES[0]):
        raw = copy.deepcopy(specimen(case)); mutate(raw)
        with self.assertRaises(ValueError): validate(raw, ORACLE, case, EXPECTED)

    def test_six_independent_case_shapes(self):
        for case in CASES: validate(specimen(case), ORACLE, case, EXPECTED)

    def test_missing_case_matrix(self):
        bad = copy.deepcopy(ORACLE); bad['cases'].pop(CASES[-1])
        with self.assertRaises(ValueError): validate_oracle(bad)

    def test_nonboolean_pass(self): self.reject(lambda r: r.update(passed=1))
    def test_missing_error_call(self): self.reject(lambda r: r['calls'].pop())
    def test_reordered_errors(self): self.reject(lambda r: r['calls'].reverse())
    def test_wrong_error_code(self): self.reject(lambda r: r['calls'][3].update(error=0))
    def test_boolean_error_code(self): self.reject(lambda r: r['calls'][0].update(error=False))
    def test_extra_error_call(self): self.reject(lambda r: r['calls'].append(r['calls'][0]))
    def test_missing_probe(self): self.reject(lambda r: r['probes'].clear())
    def test_mutated_each_output_field(self):
        for index in range(40):
            with self.subTest(index=index): self.reject(lambda r: r['probes'][0]['words'].__setitem__(index, r['probes'][0]['words'][index] ^ 1))
    def test_duplicate_probe(self): self.reject(lambda r: r['probes'].append(r['probes'][0]))
    def test_hash_only_is_not_numeric_proof(self): self.reject(lambda r: r['probes'][0].pop('words'))
    def test_wrong_readback_hash(self): self.reject(lambda r: r['probes'][0].update(sha256='e' * 64))
    def test_source_identity_missing(self): self.reject(lambda r: r.update(source_sha256={}))
    def test_api_identity(self): self.reject(lambda r: r.update(managed_api_assembly_sha256='e' * 64))
    def test_fixture_identity(self): self.reject(lambda r: r.update(managed_fixture_assembly_sha256='e' * 64))
    def test_unexpected_diagnostic(self): self.reject(lambda r: r['diagnostics'].append({'message': 'unexpected'}))
    def test_dropped_diagnostics(self): self.reject(lambda r: r.update(dropped_diagnostics=1))
    def test_resource_leak(self): self.reject(lambda r: r['resources'].update(caller_owned_final=1))
    def test_boolean_resource(self): self.reject(lambda r: r['resources'].update(caller_owned_final=False))
    def test_false_release_claim(self): self.reject(lambda r: r.update(release_driver_qualified=True))
    def test_missing_gpu(self): self.reject(lambda r: r.update(gpu_name=''))


if __name__ == '__main__': unittest.main()

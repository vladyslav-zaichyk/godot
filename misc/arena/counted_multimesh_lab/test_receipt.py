"""Synthetic adversarial receipt tests. This file never opens Godot or a GPU."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from validate_receipt import GUARDS, INPUTS, LIMITATIONS, admitted, expected_data, layout, validate
from run_contract import E10A_INPUTS, validate_e10a_gate

ARGS = SimpleNamespace(engine_hash='8' * 40, sdk_version='4.7.2-synthetic-test')


def receipt(supported=True, limit=1024):
    raw = dict(contract='counted-multimesh-numeric-contract-v1', passed=True, failure='',
        engine_hash=ARGS.engine_hash, sdk_version=ARGS.sdk_version, driver='vulkan', renderer='forward_plus', gpu_name='SYNTHETIC-NOT-A-GPU-RUN',
        supports_draw_indirect_count=supported, max_draw_indirect_count=limit if supported else 0, native_count_qualified=supported,
        source_sha256={name: '0' * 64 for name in INPUTS}, managed_fixture_assembly_sha256='1' * 64, managed_api_assembly_sha256='2' * 64,
        final_owned_rd_resource_count=0, final_owned_requested_bytes=0, image_files_written=0, performance_window_count=0,
        performance_qualified=False, native_diagnostics=[], checks=1, peak_owned_requested_bytes=1,
        cases=[], phases=[], guards=[], ownership=[], producer_dispatches=0, gpu_buffer_readbacks=0,
        numeric_pixel_samples=0, retired_cases=0 if supported else 1, unavailable_rejections=0 if supported else 1,
        not_qualified=list(LIMITATIONS))
    if not supported:
        raw['gpu_buffer_readbacks'] = 2
        return raw
    for shared in (False, True):
        for stride in (20, 32):
            for maximum in sorted({1, min(4, limit)}):
                span, size = layout(shared, stride, maximum)
                for counted in (False, True):
                    raw['cases'].append(dict(shared=shared, stride=stride, maximum=maximum, counted=counted,
                        native_owners=2, draw_slot_capacity=16, command_offset=32, counter_offsets=[4,12],
                        span_bytes=span, command_bytes=size, counter_bytes=0 if shared else 32, id_bytes=128,
                        valid_nonempty_tail_record=True, phases=7 if counted else 5,
                        fixed_submission='MULTI_DRAW', native_transform_bank=False))
                    raw['retired_cases'] += 1
                    raw['gpu_buffer_readbacks'] += 1 if shared else 2
                    last_hash = ''
                    for phase in range(7 if counted else 5):
                        count, ids, commands, identities, counters = expected_data(shared, stride, maximum, phase)
                        raster = hashlib.sha256(str(ids).encode()).hexdigest()
                        last_hash = raster
                        samples = []
                        for identity in range(10):
                            for side in range(2):
                                visible = identity in ids and (side == 0 or identity % 5 % 2 == 0)
                                bits = 1 + identity % 6 if visible else 0
                                rgb = [255 if bits & (1 << channel) else 0 for channel in range(3)]
                                samples.append(dict(id=identity, side=side, x=identity % 5 * 16 + 8 + side * 4,
                                    y=32 - (identity // 5 * 16 + 8), expected_rgb=rgb, actual_rgb=list(rgb)))
                        raw['phases'].append(dict(shared=shared, stride=stride, maximum=maximum, counted=counted, phase=phase,
                            published_counts=[count,count], admitted_ids=ids, command_sha256=commands, ids_sha256=identities,
                            counter_sha256=counters, numeric_raster_sha256=raster, full_command_and_tail_verified=True,
                            full_ids_verified=True, counter_verified_after_draw=True, paired_fixed_equality=counted and phase < 5,
                            excluded_nonempty_tail_probe=phase >= 5, samples=samples))
                        raw['producer_dispatches'] += 3
                        raw['gpu_buffer_readbacks'] += 2 if shared else 3
                        raw['numeric_pixel_samples'] += 20
                    if counted:
                        raw['guards'].append(dict(shared=shared,stride=stride,maximum=maximum,
                            rejected=sorted(GUARDS | ({'overlapping_shared_counter'} if shared else set())),
                            transactional=True,old_span_resets=True,old_single_resets=True,restored_counted=True))
                        raw['ownership'].append(dict(shared=shared,stride=stride,maximum=maximum,
                            second_borrower_detach=True,second_borrower_destroy=True,other_view_preserved=True,
                            caller_banks_survive=True,survivor_raster_sha256=last_hash))
    return raw


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.raw = receipt()

    def rejects(self, mutation):
        raw = copy.deepcopy(self.raw)
        mutation(raw)
        with self.assertRaises((ValueError, KeyError, TypeError)):
            validate(raw, ARGS)

    def test_supported_reference(self):
        validate(self.raw, ARGS)

    def test_small_physical_limits(self):
        for limit in (1, 2, 3, 4):
            with self.subTest(limit=limit):
                validate(receipt(limit=limit), ARGS)

    def test_unavailable_guard_is_separate(self):
        validate(receipt(supported=False), ARGS)

    def test_boolean_is_not_integer(self):
        self.rejects(lambda r: r.update(final_owned_rd_resource_count=False))

    def test_wrong_native_source(self):
        self.rejects(lambda r: r.update(engine_hash='9' * 40))

    def test_missing_source_identity(self):
        self.rejects(lambda r: r['source_sha256'].pop('Producer.glslinc'))

    def test_missing_case(self):
        self.rejects(lambda r: r['cases'].pop())

    def test_duplicate_case(self):
        self.rejects(lambda r: r['cases'].append(copy.deepcopy(r['cases'][0])))

    def test_capacity_is_not_actual_count(self):
        self.rejects(lambda r: r['cases'][0].update(draw_slot_capacity=1))

    def test_missing_phase(self):
        self.rejects(lambda r: r['phases'].pop())

    def test_duplicate_phase(self):
        self.rejects(lambda r: r['phases'].append(copy.deepcopy(r['phases'][0])))

    def test_counter_cannot_be_poison(self):
        self.rejects(lambda r: r['phases'][0].update(published_counts=[0xffffffff,0xffffffff]))

    def test_missing_identity(self):
        self.rejects(lambda r: r['phases'][0]['admitted_ids'].pop())

    def test_incorrect_full_command_bank(self):
        self.rejects(lambda r: r['phases'][0].update(command_sha256='a' * 64))

    def test_incorrect_id_guard_bank(self):
        self.rejects(lambda r: r['phases'][0].update(ids_sha256='a' * 64))

    def test_incorrect_counter_prefix(self):
        self.rejects(lambda r: r['phases'][0].update(counter_sha256='a' * 64))

    def test_missing_side_probe(self):
        self.rejects(lambda r: r['phases'][0]['samples'].pop())

    def test_wrong_geometry_probe(self):
        self.rejects(lambda r: r['phases'][0]['samples'][1].update(actual_rgb=[0,0,0]))

    def test_bool_pixel_rejected(self):
        self.rejects(lambda r: r['phases'][0]['samples'][0].update(x=True))

    def test_paired_hash_mismatch(self):
        self.rejects(lambda r: next(p for p in r['phases'] if p['counted'] and p['phase'] == 0).update(numeric_raster_sha256='a' * 64))

    def test_zero_count_cannot_show_legal_tail(self):
        self.rejects(lambda r: next(p for p in r['phases'] if p['counted'] and p['phase'] == 5).update(numeric_raster_sha256='a' * 64))

    def test_tail_probe_claim_required(self):
        self.rejects(lambda r: next(p for p in r['phases'] if p['phase'] == 6).update(excluded_nonempty_tail_probe=False))

    def test_missing_persistent_rejection(self):
        self.rejects(lambda r: r['guards'][0]['rejected'].remove('persistent_counter'))

    def test_missing_shared_overlap_rejection(self):
        self.rejects(lambda r: next(g for g in r['guards'] if g['shared'])['rejected'].remove('overlapping_shared_counter'))

    def test_old_api_reset_required(self):
        self.rejects(lambda r: r['guards'][0].update(old_single_resets=False))

    def test_borrower_survivor_raster_required(self):
        self.rejects(lambda r: r['ownership'][0].update(survivor_raster_sha256='a' * 64))

    def test_dispatch_ledger(self):
        self.rejects(lambda r: r.update(producer_dispatches=r['producer_dispatches'] - 1))

    def test_retirement_accounting(self):
        self.rejects(lambda r: r.update(retired_cases=r['retired_cases'] - 1))

    def test_live_resource_leak(self):
        self.rejects(lambda r: r.update(final_owned_requested_bytes=4))

    def test_no_performance_claim(self):
        self.rejects(lambda r: r.update(performance_qualified=True))

    def test_qualification_limitations_mandatory(self):
        self.rejects(lambda r: r['not_qualified'].pop())

    def test_unavailable_cannot_claim_positive(self):
        raw = receipt(supported=False)
        raw['native_count_qualified'] = True
        with self.assertRaises(ValueError):
            validate(raw, ARGS)


class DirectGateTests(unittest.TestCase):
    def setUp(self):
        # Tiny synthetic files inside this lab's owned path, never system Temp or a project clone.
        self.temp = tempfile.TemporaryDirectory(prefix='.gate-validator-', dir=Path(__file__).resolve().parent)
        root = Path(self.temp.name)
        self.gate, self.direct = root / 'gate', root / 'candidate-direct-project'
        self.project = self.gate / 'work/project'
        self.project.mkdir(parents=True)
        self.direct.mkdir()
        for name in E10A_INPUTS:
            data = ('synthetic direct input ' + name).encode()
            (self.direct / name).write_bytes(data)
            (self.project / name).write_bytes(data)
        source = {name: hashlib.sha256((self.direct / name).read_bytes()).hexdigest() for name in E10A_INPUTS}
        self.assembly = self.project / '.godot/mono/temp/bin/Debug/IndirectCountLab.dll'
        self.assembly.parent.mkdir(parents=True)
        self.assembly.write_bytes(b'synthetic assembly, never executed')
        assembly_hash = hashlib.sha256(self.assembly.read_bytes()).hexdigest()
        self.expected = dict(engine_sha256='a' * 64, engine_source_hash='8' * 40,
            managed_api_sha256='b' * 64, managed_release_api_sha256='c' * 64, sdk_version='synthetic-sdk')
        self.run = dict(self.expected, gpu_requested=True, source_sha256=source,
            managed_fixture_assembly_sha256=assembly_hash,
            commands=[dict(label=name,exit_code=0) for name in ('dotnet-version','restore','build','runtime')])
        self.raw = dict(contract='indirect-count-numeric-contract-v1', passed=True,
            supports_draw_indirect_count=True, count_draw_qualified=True,
            engine_hash=self.expected['engine_source_hash'], sdk_version=self.expected['sdk_version'],
            managed_api_assembly_sha256=self.expected['managed_api_sha256'],
            managed_fixture_assembly_sha256=assembly_hash, source_sha256=source)
        self.shutdown = dict(passed=True,diagnostics=[])
        self.acceptance = dict(passed=True,gpu_executed=True,source_sha256=source)
        self.save()

    def tearDown(self):
        # This task creates only the small regular files above; no processes or links use this path.
        self.temp.cleanup()

    def save(self):
        for name, value in [('run.json',self.run),('contract.json',self.raw),('shutdown.json',self.shutdown)]:
            (self.gate / name).write_bytes(json.dumps(value).encode())
        self.acceptance['contract_sha256'] = hashlib.sha256((self.gate/'contract.json').read_bytes()).hexdigest()
        self.acceptance['shutdown_sha256'] = hashlib.sha256((self.gate/'shutdown.json').read_bytes()).hexdigest()
        (self.gate/'acceptance.json').write_bytes(json.dumps(self.acceptance).encode())

    def check(self):
        return validate_e10a_gate(self.gate,self.expected,self.direct)

    def reject(self):
        self.save()
        with self.assertRaises(ValueError):
            self.check()

    def test_complete_gate(self):
        files, hashes = self.check()
        self.assertEqual(set(files),set(hashes))

    def test_unavailable_remains_separate(self):
        self.raw.update(supports_draw_indirect_count=False,count_draw_qualified=False)
        self.save()
        self.check()

    def test_absent_source_maps_cannot_compare_none(self):
        for ledger in (self.run,self.raw,self.acceptance):
            ledger.pop('source_sha256')
        self.reject()

    def test_empty_source_maps_rejected(self):
        for ledger in (self.run,self.raw,self.acceptance):
            ledger['source_sha256'] = {}
        self.reject()

    def test_missing_source_input_rejected(self):
        self.run['source_sha256'] = dict(self.run['source_sha256'])
        self.run['source_sha256'].pop('Producer.glslinc')
        self.reject()

    def test_runtime_fixture_assembly_identity_required(self):
        self.raw.pop('managed_fixture_assembly_sha256')
        self.reject()

    def test_runtime_fixture_assembly_mismatch(self):
        self.raw['managed_fixture_assembly_sha256'] = 'd' * 64
        self.reject()

    def test_actual_fixture_assembly_mismatch(self):
        self.assembly.write_bytes(b'changed synthetic compiled assembly')
        self.reject()

    def test_candidate_source_mismatch(self):
        (self.direct/'Producer.glslinc').write_bytes(b'changed bundled source')
        self.reject()

    def test_generated_source_mismatch(self):
        (self.project/'Producer.glslinc').write_bytes(b'changed generated source')
        self.reject()

    def test_compile_only_cannot_be_gpu_gate(self):
        self.acceptance.update(passed=None,gpu_executed=False)
        self.reject()

    def test_runtime_exit_must_be_integer_zero(self):
        self.run['commands'][-1]['exit_code'] = False
        self.reject()

    def test_candidate_native_identity_mismatch(self):
        self.run['engine_source_hash'] = '9' * 40
        self.reject()

    def test_unavailable_cannot_claim_positive(self):
        self.raw['supports_draw_indirect_count'] = False
        self.reject()


if __name__ == '__main__':
    unittest.main()

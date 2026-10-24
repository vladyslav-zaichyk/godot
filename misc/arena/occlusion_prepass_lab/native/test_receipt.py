"""CPU oracle/receipt rejection tests only. Synthetic data never qualifies native rendering."""
import copy
from pathlib import Path
import struct
import unittest

from validate_receipt import (GUARD, MATRIX, OFFSETS, digest_words, expected_samples, full_resolution_visible,
                              load_json, selected_ids, depth_at, validate, validate_oracle)

HERE = Path(__file__).resolve().parent


def bits(value):
    return struct.unpack('<I', struct.pack('<f', value))[0]


def synthetic():
    oracle = load_json(HERE / 'project/oracle.json')
    expected = dict(engine_hash='a' * 40, engine_sha256='b' * 64, sdk_version='synthetic-only',
                    managed_api_assembly_sha256='c' * 64, managed_fixture_assembly_sha256='d' * 64, source_sha256={})
    raw = dict(expected, contract='native-occlusion-case-v1', passed=True, failure='', checks=300,
               driver='vulkan', renderer='forward_plus', gpu_name='SYNTHETIC: NEVER A GPU PASS', diagnostics=[], dropped_diagnostics=0,
               callbacks=11, compute_dispatches=99, compute_barriers=88, viewport_readbacks=14, ownership_probes=4,
               caller_owned_final=0, caller_requested_bytes_final=0, images_written=0, cpu_depth_reads_before_producer=0,
               native_api_present=True, performance_qualified=False, release_driver_qualified=False, probes=[])
    for index, probe in enumerate(oracle['cases']):
        ids = list(range(16)) + [GUARD] * 20
        candidate = [GUARD] * 16
        stats = [GUARD] * 16
        pyramid = [GUARD] * 2739
        callback = None
        outcome = dict(present=index > 0, epoch=index + 1, configuration_generation=1, selected=probe['selected'],
                       mode='CANDIDATE_SELECTED' if probe['selected'] else 'FALLBACK_SELECTED', reason='synthetic',
                       early_surfaces=2 if probe['wall'] else 0, auto_instancing_disabled=probe['enabled'])
        if probe['enabled']:
            epoch, generation = index + 1, 1 if index < 9 else 2
            outcome['configuration_generation'] = generation
            matrix = [0.25, 0, 0, 0, 0, -0.5, 0, 0, 0, 0, 0.125, 0, -probe['shift'] / 32, 0, 0.75, 1]
            callback = dict(probe=probe['name'], viewport=123, epoch=epoch, configuration_generation=generation,
                            view_index=0, internal_size=[64, 32], native_depth_rid=456,
                            world_to_clip_words=[bits(v) for v in matrix], dispatches=9, barriers=8, depth_cpu_read_before_producer=False)
            for level, offset in enumerate(OFFSETS):
                width, height, block = max(1, 64 >> level), max(1, 32 >> level), 1 << level
                for y in range(height):
                    for x in range(width):
                        pyramid[offset + y * width + x] = bits(min(depth_at(px, py, probe)
                            for py in range(y * block, (y + 1) * block) for px in range(x * block, (x + 1) * block)))
            potential = selected_ids(probe)
            overflow = probe['publication'] == 'overflow' and len(potential) > 0
            written = [] if overflow else potential
            ids[16:16 + len(written)] = written
            stats[4:12] = [len(written), len(potential), int(overflow), int(probe['publication'] == 'zero'), epoch, 0, generation, 0]
            candidate[4:9] = [6, 16 if overflow else len(written), 0, 0, 0 if overflow else 16]
        image = '1' * 64
        if probe['name'] == 'zero_candidate':
            image = '2' * 64
        elif probe['name'] in ('open_uncull', 'open_candidate'):
            image = '3' * 64
        elif probe['name'] == 'shift_wall':
            image = '4' * 64
        elif probe['name'] == 'shift_current_clear':
            image = '5' * 64
        row = dict(name=probe['name'], callback=callback, outcome=outcome, id_words=ids,
                   candidate_command_words=candidate, fallback_command_words=[GUARD] * 4 + [6, 16, 0, 0, 0] + [GUARD] * 7,
                   stats_words=stats, pyramid_words=pyramid, image_sha256=image, samples=expected_samples(probe),
                   callback_completed_before_readback=True, persistent_fallback_unchanged=True)
        for key, hash_key in (('id_words', 'id_sha256'), ('candidate_command_words', 'candidate_command_sha256'),
                              ('fallback_command_words', 'fallback_command_sha256'), ('stats_words', 'stats_sha256'), ('pyramid_words', 'pyramid_sha256')):
            row[hash_key] = digest_words(row[key])
        raw['probes'].append(row)
    return raw, oracle, expected


class NativeReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base, cls.oracle, cls.expected = synthetic()

    def rejected(self, mutation):
        raw = copy.deepcopy(self.base)
        mutation(raw)
        with self.assertRaises(ValueError):
            validate(raw, self.oracle, self.expected)

    def test_complete_synthetic_schema(self):
        self.assertTrue(validate(self.base, self.oracle, self.expected)['native_render_contract_passed'])

    def test_oracle_not_always_visible_and_no_false_negatives(self):
        validate_oracle(self.oracle)
        for probe in self.oracle['cases']:
            if probe['publication'] == 'normal':
                exact = {slot for slot in range(16) if full_resolution_visible(slot, probe)}
                self.assertLessEqual(exact, set(selected_ids(probe)))
                self.assertEqual(len(selected_ids(probe)) < 16, probe['wall'])

    def test_matrix_is_fixed(self):
        self.assertEqual(len(MATRIX), 14)
        self.assertEqual(sum(row[1] for row in MATRIX), 11)
        bad = copy.deepcopy(self.oracle); bad['cases'][1]['selected'] = 1
        with self.assertRaises(ValueError):
            validate_oracle(bad)

    def test_drop_render(self):
        self.rejected(lambda raw: raw['probes'].pop())

    def test_boolean_counter_is_not_integer(self):
        self.rejected(lambda raw: raw.update(ownership_probes=True))

    def test_native_identity_mismatch(self):
        self.rejected(lambda raw: raw.update(engine_hash='e' * 40))

    def test_stale_epoch(self):
        self.rejected(lambda raw: raw['probes'][2]['callback'].update(epoch=2))

    def test_wrong_phase_surface_count(self):
        self.rejected(lambda raw: raw['probes'][1]['outcome'].update(early_surfaces=4))

    def test_error_ack_must_render_fallback(self):
        self.rejected(lambda raw: raw['probes'][2]['outcome'].update(selected=True))

    def test_zero_candidate_must_change_actual_consumption(self):
        self.rejected(lambda raw: raw['probes'][5].update(samples=copy.deepcopy(raw['probes'][0]['samples'])))

    def test_persistent_fallback_corruption(self):
        self.rejected(lambda raw: raw['probes'][1]['fallback_command_words'].__setitem__(5, 0))

    def test_candidate_guard_corruption(self):
        self.rejected(lambda raw: raw['probes'][1]['id_words'].__setitem__(35, 0))

    def test_wrong_native_depth_even_with_matching_hash(self):
        def mutate(raw):
            row = raw['probes'][1]; row['pyramid_words'][4 + 16 * 64 + 31] = bits(0.75)
            row['pyramid_sha256'] = digest_words(row['pyramid_words'])
        self.rejected(mutate)

    def test_overflow_must_publish_entire_fallback(self):
        def mutate(raw):
            row = raw['probes'][6]; row['candidate_command_words'][5] = 0
            row['candidate_command_sha256'] = digest_words(row['candidate_command_words'])
        self.rejected(mutate)

    def test_shutdown_diagnostic_rejected(self):
        self.rejected(lambda raw: raw.update(diagnostics=[dict(message='bad')]))

    def test_no_performance_claim(self):
        self.rejected(lambda raw: raw.update(performance_qualified=True))


if __name__ == '__main__':
    unittest.main()

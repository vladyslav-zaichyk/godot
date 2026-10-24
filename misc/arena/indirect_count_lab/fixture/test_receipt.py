"""Adversarial receipt tests, without Godot, build output or a GPU."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from validate_receipt import GUARDS, validate


def receipt(limit=5):
    args = SimpleNamespace(engine_hash='1' * 40, sdk_version='4.7.2-arena-test')
    supported = limit > 0
    raw = dict(contract='indirect-count-numeric-contract-v1', passed=True, failure='',
        engine_hash=args.engine_hash, sdk_version=args.sdk_version, driver='vulkan', renderer='forward_plus',
        final_owned_rd_resource_count=0, final_owned_requested_bytes=0, image_files_written=0,
        performance_window_count=0, performance_qualified=False, native_diagnostics=[],
        supports_draw_indirect_count=supported, max_draw_indirect_count=limit,
        count_draw_qualified=supported, cases=[], phases=[], guards=[], not_qualified=['Correctness subset only.'])
    cases = [(indexed, shared, stride, maximum) for indexed in (False, True) for shared in (False, True)
             for stride in (20, 32) for maximum in sorted({1, min(4, limit)})] if supported else [(True, False, 20, 1)]
    buffer_reads = 0
    for indexed, shared, stride, maximum in cases:
        base = dict(indexed=indexed, shared=shared, stride=stride, maximum=maximum)
        counts = ([maximum, 0, 1] + ([maximum + 1] if maximum < limit else [])) if supported else [1]
        command_bytes = (32 + maximum * stride + (20 if indexed else 16) + 15) & ~15
        raw['cases'].append(dict(base, command_offset=32, count_offset=4 if shared else 12,
            valid_nonempty_records=maximum + 1, phases=len(counts), caller_requested_command_bytes=command_bytes,
            caller_requested_counter_bytes=0 if shared else 32))
        if supported:
            raw['guards'].append(dict(base, rejected=sorted(GUARDS | ({'shared_counter_overlap'} if shared else set())),
                                      subsequent_valid_count_draw=True))
        for phase, count in enumerate(counts):
            effective = min(count, maximum) if supported else 0
            samples = []
            for cell in range(5):
                color = cell + 1 if cell < effective else 0
                rgb = [255 if color & (1 << c) else 0 for c in range(3)]
                samples.append(dict(cell=cell, x=cell * 16 + 8, y=8, expected_rgb=rgb, actual_rgb=list(rgb)))
            raw['phases'].append(dict(base, phase=phase, raw_gpu_count=count, executed_draws=effective,
                valid_nonempty_records=maximum + 1, full_command_and_tail_verified=True,
                counter_verified_after_draw=True, command_sha256='a' * 64, counter_sha256='b' * 64,
                numeric_texture_sha256=f'{effective:064x}', samples=samples))
            buffer_reads += 1 if shared else 2
    count = len(raw['phases'])
    raw.update(command_producer_dispatches=count, counter_producer_dispatches=count,
        count_draw_calls=count if supported else 0, unavailable_rejections=0 if supported else 1,
        gpu_buffer_readbacks=buffer_reads, numeric_texture_readbacks=count, numeric_pixel_samples=count * 5,
        retired_cases=len(cases))
    return raw, args


class ReceiptContract(unittest.TestCase):
    def reject(self, change):
        raw, args = receipt()
        change(raw)
        with self.assertRaises((ValueError, KeyError, TypeError)):
            validate(raw, args)

    def test_supported_and_unavailable_limits(self):
        for limit in (0, 1, 2, 4, 5, 0xffffffff):
            raw, args = receipt(limit)
            validate(raw, args)

    def test_unavailable_never_qualifies_positive_draw(self):
        raw, args = receipt(0)
        raw['count_draw_qualified'] = True
        with self.assertRaises(ValueError): validate(raw, args)

    def test_missing_or_duplicate_case(self):
        self.reject(lambda r: r['cases'].__setitem__(1, deepcopy(r['cases'][0])))

    def test_duplicate_phase_cannot_replace_one_count_transition(self):
        self.reject(lambda r: r['phases'].__setitem__(1, deepcopy(r['phases'][0])))

    def test_duplicate_guard_cannot_replace_missing_case(self):
        self.reject(lambda r: r['guards'].__setitem__(1, deepcopy(r['guards'][0])))

    def test_missing_or_duplicate_guard(self):
        self.reject(lambda r: r['guards'][0]['rejected'].__setitem__(1, r['guards'][0]['rejected'][0]))

    def test_gpu_counter_is_not_the_cpu_maximum(self):
        def change(raw):
            next(p for p in raw['phases'] if p['raw_gpu_count'] == 0)['raw_gpu_count'] = 4
        self.reject(change)

    def test_counter_above_maximum_must_clamp(self):
        def change(raw):
            row = next(p for p in raw['phases'] if p['raw_gpu_count'] > p['maximum'])
            row['executed_draws'] = row['raw_gpu_count']
        self.reject(change)

    def test_tail_and_post_draw_counter_checks_required(self):
        for name in ('full_command_and_tail_verified', 'counter_verified_after_draw'):
            self.reject(lambda r: r['phases'][0].__setitem__(name, False))

    def test_missing_pixel(self):
        self.reject(lambda r: r['phases'][0]['samples'].pop())

    def test_wrong_tail_color_fails_independent_oracle(self):
        self.reject(lambda r: r['phases'][0]['samples'][-1].update(expected_rgb=[255, 0, 0], actual_rgb=[255, 0, 0]))

    def test_numeric_pixel_count_is_not_flexible(self):
        self.reject(lambda r: r.__setitem__('numeric_pixel_samples', r['numeric_pixel_samples'] - 1))

    def test_wrong_full_texture_equivalence_hash(self):
        self.reject(lambda r: r['phases'][-1].__setitem__('numeric_texture_sha256', 'd' * 64))

    def test_separate_command_and_counter_dispatches_required(self):
        self.reject(lambda r: r.__setitem__('counter_producer_dispatches', 0))

    def test_capability_and_limit_must_agree(self):
        self.reject(lambda r: r.__setitem__('max_draw_indirect_count', 0))

    def test_resource_residue_or_diagnostics_fails(self):
        self.reject(lambda r: r.__setitem__('final_owned_rd_resource_count', 1))
        self.reject(lambda r: r['native_diagnostics'].append('unexpected error'))

    def test_performance_claim_fails_closed(self):
        self.reject(lambda r: r.__setitem__('performance_qualified', True))


if __name__ == '__main__':
    unittest.main()

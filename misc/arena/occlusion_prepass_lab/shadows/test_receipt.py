"""Synthetic corruption tests; no Godot, files, screenshots, or GPU execution."""
import copy
import hashlib
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
import validate_receipt as gate


def put_words(blob, offset, words):
    struct.pack_into('<' + 'I' * len(words), blob, offset, *words)


def put_floats(blob, offset, values):
    struct.pack_into('<' + 'f' * len(values), blob, offset, *values)


def encode_scene(blob, offset, scene):
    put_floats(blob, offset, scene['projection']); put_floats(blob, offset + 176, scene['view'])
    put_floats(blob, offset + 2824, scene['jitter'])


def pixel_offset(index, sample):
    return index * gate.RAW_STRIDE + 64 + (sample['y'] * gate.MAX_WIDTH + sample['x']) * 32


def synthetic(mode):
    plan = gate.load_json(gate.HERE / 'gate_plan.json')
    blobs = {name: bytearray(struct.pack('<I', gate.GUARD) * (stride * gate.COUNT // 4)) for name, stride in
             (('raw', gate.RAW_STRIDE), ('early', gate.EARLY_STRIDE), ('ubo', gate.UBO_STRIDE), ('proof', gate.PROOF_STRIDE))}
    rows = plan['records']; scenes = []; previous_scene = gate.scene_for(plan['initial'], plan)
    records = []; previous_row = plan['initial']
    for index, row in enumerate(rows):
        serial = index + 1; callback = row['mode'] == 'requested' and mode == 'combined-required'
        epoch, generation = 100 + index, serial
        # Float32 serialization and decoding are mandatory even in this synthetic control.
        jitter = (((index % 4) - 1.5) / 256, ((index % 3) - 1) / 256)
        scene = gate.scene_for(row, plan, jitter)
        encode_scene(blobs['ubo'], index * gate.UBO_STRIDE, scene)
        encode_scene(blobs['ubo'], index * gate.UBO_STRIDE + 2848, previous_scene)
        scene = gate.decode_scene(blobs['ubo'], index * gate.UBO_STRIDE)
        scenes.append(scene); previous_scene = scene
        width, height = row['internal_extent']
        put_words(blobs['raw'], index * gate.RAW_STRIDE, [serial, width, height] + [gate.GUARD] * 13)
        producer = None; outcome = None
        if row['mode'] == 'requested':
            selected = callback and row['producer_ack'] == 'valid'
            outcome = dict(epoch=epoch, configuration_generation=generation, selected=selected,
                reason='producer_acknowledged' if selected else 'producer_ack_rejected' if callback else 'unsupported_view',
                opaque_recorded=True, taa_process_recorded=True, frame_recording_completed=True)
        if callback:
            m = gate.matrix(scene); matrix_words = [m[r][c] for c in range(4) for r in range(4)]
            producer = dict(epoch=epoch, configuration_generation=generation, dispatches=1,
                world_to_clip_words=list(struct.unpack('<16I', struct.pack('<16f', *matrix_words))))
            put_words(blobs['early'], index * gate.EARLY_STRIDE, [serial, width, height, epoch, 0, generation, 0] + [gate.GUARD] * 9)
            for probe in gate.visible_box_probes([row], [scene], plan, plan['geometry']['wall_center'], plan['geometry']['wall_size'], 4):
                sample = probe['samples'][0]
                put_floats(blobs['early'], index * gate.EARLY_STRIDE + 64 + (sample['y'] * 128 + sample['x']) * 4, [sample['depth']])
        base = index * gate.PROOF_STRIDE
        candidate = row['candidate_ids'] if callback else []
        put_words(blobs['proof'], base, list(range(16)) + candidate + [gate.GUARD] * (20 - len(candidate)))
        put_words(blobs['proof'], base + 144, [gate.GUARD] * 4 + [36, 16, 0, 0, 0] + [gate.GUARD] * 7)
        put_words(blobs['proof'], base + 208, [gate.GUARD] * 4 + [36, len(candidate), 0, 0, 16] + [gate.GUARD] * 7)
        put_words(blobs['proof'], base + 272, [serial, serial - 1, 16, 0xe8000001] + [gate.GUARD] * 12)
        for stable_id in range(16):
            gen, prev_gen = row['generations'][stable_id], previous_row['generations'][stable_id]
            active, prev_active = int(stable_id in row['active_ids']), int(stable_id in previous_row['active_ids'])
            put_words(blobs['proof'], base + 336 + stable_id * 16, [gen, prev_gen, active, prev_active])
            put_words(blobs['proof'], base + 592 + stable_id * 16, [active, active, stable_id, gen])
            for source, start, epoch_value in ((row, 848, serial), (previous_row, 1872, serial - 1)):
                x, y, z = source['pose_centers'][stable_id]
                put_floats(blobs['proof'], base + start + stable_id * 64, [1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z])
                put_words(blobs['proof'], base + start + stable_id * 64 + 48, [source['generations'][stable_id], stable_id + 1, 0, epoch_value])
        previous_row = row
        records.append(dict(index=index, id=row['id'], state_id=row['state_id'], mode=row['mode'], preparation_serial=serial,
            scene=copy.deepcopy(row), outcome=outcome, producer=producer, capture_count=1, producer_count=int(callback),
            frame_recording_completed=True, taa_reset_before=False))
    all_receiver_probes = []; all_body_probes = []
    for start in range(0, gate.COUNT, 3):
        triplet = rows[start:start + 3]; captured = scenes[start:start + 3]
        probes = gate.receiver_probes(triplet, captured, plan); all_receiver_probes.append(probes)
        for kind, choices in probes.items():
            for choice in choices:
                for arm, sample in enumerate(choice['samples']):
                    light = 1. if arm == 0 or gate.expected_lit_group(kind) else 0.
                    index = start + arm; old = gate.decode_scene(blobs['ubo'], index * gate.UBO_STRIDE + 2848)
                    velocity = gate.velocity_at(scenes[index], old, sample['world'], sample['world'])
                    put_floats(blobs['raw'], pixel_offset(index, sample), [light, 0., .25, sample['depth'], *velocity, 0., 0.])
        bodies = gate.visible_box_probes(triplet, captured, plan, triplet[0]['pose_centers'][0], plan['geometry']['body_size'], 4)
        all_body_probes.append(bodies)
        for choice in bodies:
            for arm, sample in enumerate(choice['samples']):
                index = start + arm; row = rows[index]
                current = captured[arm]; old = gate.decode_scene(blobs['ubo'], index * gate.UBO_STRIDE + 2848)
                intentional_zero = (mode == 'combined-required' and row['mode'] == 'requested'
                                    and row['producer_ack'] == 'valid' and row['intentional_zero_candidate'])
                if intentional_zero:
                    rgb = [1., 0., .25]; depth = sample['ground_depth']; world = old_world = sample['ground_world']
                else:
                    rgb = [1 / 32, 0., 0.]; depth = sample['depth']; world = sample['world']
                    previous_row = plan['initial'] if index == 0 else rows[index - 1]
                    old_world = gate.add(gate.sub(world, row['pose_centers'][0]), previous_row['pose_centers'][0])
                velocity = gate.velocity_at(current, old, world, old_world)
                put_floats(blobs['raw'], pixel_offset(index, sample), [*rgb, depth, *velocity, 0., 0.])
    raw = dict(contract='arena-e13e-directional-shadow-receipt-v1', passed=True, failure='', gpu_executed=True,
        gate=mode, driver='vulkan', renderer='forward_plus', observer_captures=30, initialization_viewport_records=1,
        actual_viewport_records=31, gpu_readbacks=4, per_frame_gpu_readbacks=0, registered_consumers=0,
        caller_owned_final=0, caller_requested_bytes_final=0, logical_slots=16, draw_slots=32, ordinary_shadow_casters=0,
        diagnostics=[], dropped_diagnostics=0, images_written=0, unlisted_viewport_renders=0, taa_resets=0,
        requested_ledger_bytes=plan['limits']['requested_ledger_bytes'], callbacks=10 if mode == 'combined-required' else 0,
        records=records, payloads={})
    return plan, raw, blobs, all_receiver_probes, all_body_probes


class ShadowGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.templates = {mode: synthetic(mode) for mode in ('reference-boundary', 'combined-required')}

    def run_gate(self, mutate=None, mode='combined-required'):
        plan, raw, blobs, receivers, bodies = copy.deepcopy(self.templates[mode])
        if mutate:
            mutate(plan, raw, blobs, receivers, bodies)
        virtual = {Path('/synthetic-shadow') / (name + '.bin'): bytes(data) for name, data in blobs.items()}
        for name, data in blobs.items():
            raw['payloads'][name] = dict(path=name + '.bin', bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        original = Path.read_bytes
        def read(path):
            return virtual[path] if path in virtual else original(path)
        with patch.object(Path, 'read_bytes', read):
            return gate.validate(raw, plan, {}, Path('/synthetic-shadow'), mode)

    def test_synthetic_combined_contract(self):
        verdict = self.run_gate()
        self.assertEqual(verdict['candidate_selected_records'], 9)
        self.assertEqual(verdict['producer_rejected_records'], 1)
        self.assertEqual(verdict['receiver_shadow_probes'], 68)
        self.assertEqual(verdict['receiver_lit_probes'], 44)
        self.assertEqual(verdict['departed_caster_lit_probes'], 4)
        self.assertEqual(verdict['accepted_zero_body_probes'], 4)
        self.assertGreaterEqual(verdict['nonzero_motion_probes'], 8)
        self.assertFalse(verdict['native_qualified'])

    def test_reference_fallback_is_not_combined_qualification(self):
        verdict = self.run_gate(mode='reference-boundary')
        self.assertTrue(verdict['unsupported_fallback_expected'])
        self.assertFalse(verdict['combined_shadow_occlusion_contract_passed'])
        self.assertEqual(verdict['producer_callbacks'], 0)

    def test_cpu_receipt_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Incomplete GPU'):
            self.run_gate(lambda p, r, b, s, v: r.update(gpu_executed=False))

    def test_threshold_mutation_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Frozen plan'):
            self.run_gate(lambda p, r, b, s, v: p['oracle'].update(minimum_caster_shadow_delta=0))

    def test_all_lit_rejected(self):
        def mutate(p, r, b, s, v):
            sample = s[0][2][0]['samples'][1]
            put_floats(b['raw'], pixel_offset(1, sample), [1.])
        with self.assertRaisesRegex(ValueError, 'observable shadow delta'):
            self.run_gate(mutate)

    def test_all_shadow_rejected(self):
        def mutate(p, r, b, s, v):
            sample = s[0]['lit'][0]['samples'][1]
            put_floats(b['raw'], pixel_offset(1, sample), [0.])
        with self.assertRaisesRegex(ValueError, 'Lit witness'):
            self.run_gate(mutate)

    def test_dark_counterfactual_rejected(self):
        def mutate(p, r, b, s, v):
            put_floats(b['raw'], pixel_offset(0, s[0][2][0]['samples'][0]), [0.])
        with self.assertRaisesRegex(ValueError, 'not visibly lit'):
            self.run_gate(mutate)

    def test_dead_caster_shadow_in_both_arms_rejected(self):
        def mutate(p, r, b, s, v):
            # Identical stale shadows in reference AND candidate must not pass
            # merely because their lighting agrees and active=0 was published.
            for arm in (1, 2):
                sample = s[5]['departed_id2'][0]['samples'][arm]
                put_floats(b['raw'], pixel_offset(15 + arm, sample), [0.])
        with self.assertRaisesRegex(ValueError, 'Lit witness'):
            self.run_gate(mutate)

    def test_offcamera_caster_shadow_lost_rejected(self):
        def mutate(p, r, b, s, v):
            put_floats(b['raw'], pixel_offset(5, s[1][7][0]['samples'][2]), [1.])
        with self.assertRaisesRegex(ValueError, 'lost/changed'):
            self.run_gate(mutate)

    def test_accepted_zero_command_actually_consumed(self):
        def mutate(p, r, b, s, v):
            sample = v[2][0]['samples'][2]
            put_floats(b['raw'], pixel_offset(8, sample), [1 / 32, 0., 0., sample['depth']])
        with self.assertRaisesRegex(ValueError, 'zero command did not remove'):
            self.run_gate(mutate)

    def test_missing_ack_retains_body_fallback(self):
        def mutate(p, r, b, s, v):
            sample = v[8][0]['samples'][2]
            put_floats(b['raw'], pixel_offset(26, sample), [1., 0., .25, sample['ground_depth']])
        with self.assertRaisesRegex(ValueError, 'Visible fallback/candidate'):
            self.run_gate(mutate)

    def test_metadata_selected_without_actual_producer_rejected(self):
        def mutate(p, r, b, s, v):
            r['records'][2]['producer'] = None
        with self.assertRaisesRegex(ValueError, 'producer acknowledgment'):
            self.run_gate(mutate)

    def test_stale_early_epoch_rejected(self):
        def mutate(p, r, b, s, v):
            put_words(b['early'], 2 * gate.EARLY_STRIDE + 12, [1])
        with self.assertRaisesRegex(ValueError, 'current-epoch'):
            self.run_gate(mutate)

    def test_fake_early_depth_rejected(self):
        def mutate(p, r, b, s, v):
            row = p['records'][2]; scene = gate.decode_scene(b['ubo'], 2 * gate.UBO_STRIDE)
            sample = gate.visible_box_probes([row], [scene], p, p['geometry']['wall_center'], p['geometry']['wall_size'], 4)[0]['samples'][0]
            put_floats(b['early'], 2 * gate.EARLY_STRIDE + 64 + (sample['y'] * 128 + sample['x']) * 4, [0.])
        with self.assertRaisesRegex(ValueError, 'actual current wall'):
            self.run_gate(mutate)

    def test_fallback_shadow_prefix_corruption_rejected(self):
        with self.assertRaisesRegex(ValueError, 'fallback/shadow identity'):
            self.run_gate(lambda p, r, b, s, v: put_words(b['proof'], 2 * gate.PROOF_STRIDE + 8, [0]))

    def test_candidate_capacity_guard_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Candidate/identity'):
            self.run_gate(lambda p, r, b, s, v: put_words(b['proof'], 2 * gate.PROOF_STRIDE + 128, [0]))

    def test_unchanged_generation_on_reuse_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Lifecycle'):
            self.run_gate(lambda p, r, b, s, v: put_words(b['proof'], 18 * gate.PROOF_STRIDE + 336 + 2 * 16, [1]))

    def test_history_motion_zeroed_rejected(self):
        def mutate(p, r, b, s, v):
            sample = v[3][0]['samples'][0]
            put_floats(b['raw'], pixel_offset(9, sample) + 16, [0., 0.])
        with self.assertRaisesRegex(ValueError, 'motion differs'):
            self.run_gate(mutate)

    def test_receiver_motion_zeroed_rejected(self):
        def mutate(p, r, b, s, v):
            sample = s[3]['lit'][0]['samples'][0]
            put_floats(b['raw'], pixel_offset(9, sample) + 16, [0., 0.])
        with self.assertRaisesRegex(ValueError, 'Receiver motion differs'):
            self.run_gate(mutate)

    def test_receiver_depth_wrong_rejected(self):
        def mutate(p, r, b, s, v):
            put_floats(b['raw'], pixel_offset(0, s[0][2][0]['samples'][0]) + 12, [0.])
        with self.assertRaisesRegex(ValueError, 'Receiver geometry/depth'):
            self.run_gate(mutate)

    def test_resize_padding_overwrite_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Raw row guard'):
            self.run_gate(lambda p, r, b, s, v: put_words(b['raw'], 21 * gate.RAW_STRIDE + 64 + 100 * 128 * 32, [0]))

    def test_unlisted_render_rejected(self):
        with self.assertRaisesRegex(ValueError, 'scope contract'):
            self.run_gate(lambda p, r, b, s, v: r.update(unlisted_viewport_renders=1))

    def test_no_receiver_coverage_is_not_success(self):
        row = copy.deepcopy(self.templates['combined-required'][0]['records'][0]); row['light_ray'] = [0., 1., 0.]
        plan = self.templates['combined-required'][0]
        with self.assertRaisesRegex(ValueError, 'receiver coverage'):
            gate.receiver_probes([row] * 3, [gate.scene_for(row, plan)] * 3, plan)


if __name__ == '__main__':
    unittest.main()

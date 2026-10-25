"""Synthetic ledger corruption tests only; no files, renderer or GPU are used."""
import copy
import hashlib
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
import validate_receipt as gate


def fwords(values):
    return list(struct.unpack('<' + 'I' * len(values), struct.pack('<' + 'f' * len(values), *values)))


def camera(position, serial):
    x, y, z = position
    f, near, far = 3 ** .5, .1, 200
    jitter = ((serial % 4 - 1.5) / 128, (serial % 3 - 1) / 64)
    return dict(projection=[f / 2, 0, 0, 0, 0, -f, 0, 0, -jitter[0], -jitter[1], near / (far - near), -1,
                            0, 0, far * near / (far - near), 0],
                view=[1, 0, 0, -x, 0, 1, 0, -y, 0, 0, 1, -z], time=0, jitter=jitter)


def encoded_scene(scene):
    data = bytearray(2848)
    struct.pack_into('<16f', data, 0, *scene['projection'])
    struct.pack_into('<12f', data, 176, *scene['view'])
    struct.pack_into('<2f', data, 2824, *scene['jitter'])
    return data


def synthetic():
    plan = gate.load_json(gate.HERE / 'gate_plan.json')
    buffers = {name: bytearray(stride * gate.COUNT) for name, stride in
               (('early', gate.EARLY_STRIDE), ('raw', gate.RAW_STRIDE), ('ubo', gate.UBO_STRIDE))}
    records, selected = [], []
    previous_camera = plan['initial']['camera']
    for index, row in enumerate(plan['records']):
        epoch, generation = index + 100, 1
        early, raw, ubo = index * gate.EARLY_STRIDE, index * gate.RAW_STRIDE, index * gate.UBO_STRIDE
        struct.pack_into('<16I', buffers['early'], early, index + 1, epoch, 0, generation, 0, 128, 64, *([gate.GUARD] * 9))
        struct.pack_into('<16I', buffers['raw'], raw, index + 1, 128, 64, *([gate.GUARD] * 13))
        current_scene, previous_scene = camera(row['camera'], index + 1), camera(previous_camera, index)
        buffers['ubo'][ubo:ubo + 2848] = encoded_scene(current_scene)
        buffers['ubo'][ubo + 2848:ubo + 5696] = encoded_scene(previous_scene)
        # The oracle consumes float32 UBOs, just as it does for runtime payloads.
        current_scene, previous_scene = gate.decode_scene(buffers['ubo'], ubo), gate.decode_scene(buffers['ubo'], ubo + 2848)
        current = gate.clips(current_scene, row['roots'], plan['vertices'])
        previous = gate.clips(previous_scene, row['previous_roots'], plan['vertices'])
        probes = gate.choose_probes(current, plan['triangles']); selected.append(probes)
        for owner, y, x, triangle, weights, depth in probes:
            pixel = y * 128 + x
            if row['phase_mask'] & (1 << owner):
                struct.pack_into('<f', buffers['early'], early + 64 + pixel * 4, depth)
            velocity = gate.perspective_velocity([current[owner][i] for i in triangle], [previous[owner][i] for i in triangle],
                                                weights, current_scene['jitter'], previous_scene['jitter'])
            struct.pack_into('<4f', buffers['raw'], raw + 64 + pixel * 16, *velocity, depth, (owner + 1) / 32)
        view4 = [[1, 0, 0, -row['camera'][0]], [0, 1, 0, -row['camera'][1]], [0, 0, 1, -row['camera'][2]], [0, 0, 0, 1]]
        projection = current_scene['projection']
        matrix = [sum(projection[k * 4 + r] * view4[k][c] for k in range(4)) for c in range(4) for r in range(4)]
        mask = row['phase_mask']
        outcome = dict(epoch=epoch, configuration_generation=generation, selected=True, reason='producer_acknowledged',
            auto_instancing_disabled=False, opaque_recorded=True, taa_process_recorded=True, frame_recording_completed=True,
            early_surface_count=mask.bit_count(), remaining_surface_count=6 - mask.bit_count(), original_opaque_repeat_group_count=1,
            early_depth_repeat_group_count=0 if mask == 0 else 1 if mask == 63 else 3,
            remaining_depth_repeat_group_count=0 if mask == 63 else 1 if mask == 0 else 3)
        records.append(dict(index=index, serial=index + 1, id=row['id'], outcome=outcome,
                            producer=dict(epoch=epoch, configuration_generation=generation, world_to_clip_words=fwords(matrix))))
        previous_camera = row['camera']
    raw = dict(contract='arena-e13d-opaque-batching-receipt-v1', passed=True, gpu_executed=True, failure='', driver='vulkan',
        renderer='forward_plus', callbacks=24, observer_captures=24, gpu_readbacks=3, initialization_viewport_records=1,
        ordinary_owners=6, registered_consumers=0, caller_owned_final=0, diagnostics=[], dropped_diagnostics=0, images_written=0,
        per_frame_gpu_readbacks=0, requested_ledger_bytes=4071936, records=records, payloads={})
    return plan, raw, buffers, selected


class LedgerGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frozen = synthetic()

    def run_gate(self, mutation=None):
        plan, raw, buffers, selected = copy.deepcopy(self.frozen)
        if mutation:
            mutation(plan, raw, buffers, selected)
        for name, data in buffers.items():
            raw['payloads'][name] = dict(path=name + '.bin', bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        original_read = Path.read_bytes
        virtual = {Path('/synthetic') / (name + '.bin'): bytes(data) for name, data in buffers.items()}

        def read(path):
            return virtual[path] if path in virtual else original_read(path)

        with patch.object(Path, 'read_bytes', read):
            return gate.validate(raw, plan, {}, Path('/synthetic'))

    def test_synthetic_numeric_contract(self):
        verdict = self.run_gate()
        self.assertEqual(verdict['ordinary_probes'], 576)
        self.assertGreaterEqual(verdict['nonzero_motion_probes'], 192)
        self.assertFalse(verdict['native_qualified'])

    def test_cpu_receipt_cannot_qualify(self):
        with self.assertRaisesRegex(ValueError, 'Incomplete runtime'):
            self.run_gate(lambda plan, raw, blobs, probes: raw.update(gpu_executed=False))

    def test_wrong_instance_identity_rejected(self):
        def mutate(plan, raw, blobs, probes):
            owner, y, x, *_ = probes[0][0]
            struct.pack_into('<f', blobs['raw'], 64 + (y * 128 + x) * 16 + 12, (owner + 2) / 32)
        with self.assertRaisesRegex(ValueError, 'identity'):
            self.run_gate(mutate)

    def test_early_phase_leak_rejected(self):
        def mutate(plan, raw, blobs, probes):
            owner, y, x, *_ = probes[1][0]
            struct.pack_into('<f', blobs['early'], gate.EARLY_STRIDE + 64 + (y * 128 + x) * 4, .5)
        with self.assertRaisesRegex(ValueError, 'leaked'):
            self.run_gate(mutate)

    def test_missing_early_owner_rejected(self):
        def mutate(plan, raw, blobs, probes):
            _, y, x, *_ = probes[0][0]
            struct.pack_into('<f', blobs['early'], 64 + (y * 128 + x) * 4, 0)
        with self.assertRaisesRegex(ValueError, 'Early depth omitted'):
            self.run_gate(mutate)

    def test_zero_motion_rejected(self):
        def mutate(plan, raw, blobs, probes):
            for _, y, x, *_ in probes[0]:
                struct.pack_into('<2f', blobs['raw'], 64 + (y * 128 + x) * 16, 0, 0)
        with self.assertRaisesRegex(ValueError, 'motion differs'):
            self.run_gate(mutate)

    def test_batching_disabled_rejected(self):
        with self.assertRaisesRegex(ValueError, 'split/batching'):
            self.run_gate(lambda plan, raw, blobs, probes: raw['records'][0]['outcome'].update(auto_instancing_disabled=True))

    def test_homogeneous_batching_loss_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Homogeneous'):
            self.run_gate(lambda plan, raw, blobs, probes: raw['records'][0]['outcome'].update(early_depth_repeat_group_count=6))

    def test_guard_corruption_rejected(self):
        def mutate(plan, raw, blobs, probes):
            struct.pack_into('<I', blobs['early'], 60, 0)
        with self.assertRaisesRegex(ValueError, 'guards'):
            self.run_gate(mutate)


if __name__ == '__main__':
    unittest.main()

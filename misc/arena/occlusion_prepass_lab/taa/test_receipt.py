"""CPU-only adversarial tests for the independent numerical audit; no Godot/GPU."""
import copy
from fractions import Fraction
import math
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

import validate_receipt as audit


class PerspectiveOracleTests(unittest.TestCase):
    def setUp(self):
        self.current = [[-.5, -.5, .7, 1], [1, -1, 1.2, 2], [0, 2, 2, 4]]
        self.previous = [[-.38, -.5, .71, 1.25], [1.12, -1, 1.21, 2.25], [.12, 2, 2.01, 4.25]]
        self.weights = [.25, .25, .5]

    def test_perspective_counterexample_matches_exact_rational(self):
        result = audit.perspective_velocity(self.current, self.previous, self.weights, [0, 0], [0, 0])
        self.assertAlmostEqual(result[0], float(Fraction(2, 75)), places=14)
        self.assertAlmostEqual(result[1], 0, places=14)
        # An orthographic oracle would incorrectly accept the following pair.
        naive = [2393 / 76500, 37 / 3060]
        self.assertTrue(all(abs(a - b) > 8 * audit.VELOCITY_TOLERANCE for a, b in zip(result, naive)))
        with self.assertRaisesRegex(ValueError, 'Raw velocity'):
            audit.compare_velocity(naive, result)

    def test_actual_jitter_removed_exactly_once(self):
        jc, jp = [1 / 128, -1 / 64], [-1 / 128, 1 / 64]
        jitter = lambda js: [[q[0] + js[0] * q[3], q[1] + js[1] * q[3], q[2], q[3]] for q in self.current]
        velocity = audit.perspective_velocity(jitter(jc), jitter(jp), self.weights, jc, jp)
        self.assertLess(max(map(abs, velocity)), 1e-14)
        without = audit.perspective_velocity(jitter(jc), jitter(jp), self.weights, [0, 0], [0, 0])
        self.assertGreater(math.hypot(*without), 2 ** -8)

    def test_zero_motion_output_fails_positive_control(self):
        expected = audit.perspective_velocity(self.current, self.previous, self.weights, [0, 0], [0, 0])
        with self.assertRaises(ValueError):
            audit.compare_velocity([0, 0], expected, [0])
        audit.compare_velocity([0, 0], [0, 0])

    def test_nonfinite_and_invalid_previous_w_rejected(self):
        for value in (float('nan'), float('inf'), -.5, 0):
            previous = copy.deepcopy(self.previous); previous[1][3] = value
            with self.assertRaises(ValueError):
                audit.perspective_velocity(self.current, previous, self.weights, [0, 0], [0, 0])

    def test_depth_is_affine_in_screen_space(self):
        clips = [[-1, -1, .5, 1], [2, -2, 1, 2], [0, 4, 1.2, 4]]
        weights, depth = audit.barycentric_pixel(clips, [0, 1, 2], 63, 31, [128, 64])
        self.assertAlmostEqual(depth, sum(w * clips[i][2] / clips[i][3] for i, w in enumerate(weights)))
        with self.assertRaises(ValueError):
            audit.barycentric_pixel(clips, [0, 1, 2], 127, 63, [128, 64])


class ReceiptDamageTests(unittest.TestCase):
    def setUp(self):
        self.expected = dict(engine_hash='a' * 40, engine_sha256='b' * 64, sdk_version='4.7.2-test',
            managed_api_assembly_sha256='c' * 64, managed_fixture_assembly_sha256='d' * 64,
            source_sha256={name: 'e' * 64 for name in audit.INPUTS}, plan_sha256='f' * 64)
        self.receipt = dict(self.expected, contract='arena-e13c-native-taa-receipt-v1', passed=True,
            failure='', gpu_executed=True, driver='vulkan', renderer='forward_plus', gpu_name='unit-test-only',
            diagnostics=[], dropped_diagnostics=0, performance_qualified=False, physical_vram_qualified=False,
            real_vat_qualified=False, resolved_taa_image_quality_qualified=False, general_compositor_qualified=False)

    def test_cpu_receipt_cannot_claim_gpu_success(self):
        audit.validate_identity(self.receipt, self.expected)
        self.receipt['gpu_executed'] = False
        with self.assertRaisesRegex(ValueError, 'CPU-only'):
            audit.validate_identity(self.receipt, self.expected)

    def test_exact_source_payload_hash_mismatch_rejected(self):
        self.receipt['source_sha256'] = dict(self.receipt['source_sha256'], **{'Consumer.gdshader': '0' * 64})
        with self.assertRaisesRegex(ValueError, 'Identity differs'):
            audit.validate_identity(self.receipt, self.expected)

    def test_empty_matching_inventory_cannot_pass(self):
        self.receipt['source_sha256'] = {}; self.expected['source_sha256'] = {}
        with self.assertRaisesRegex(ValueError, 'Incomplete source'):
            audit.validate_identity(self.receipt, self.expected)

    def test_diagnostics_and_unearned_quality_claim_rejected(self):
        self.receipt['diagnostics'] = ['bad GPU command']
        with self.assertRaisesRegex(ValueError, 'diagnostics'):
            audit.validate_identity(self.receipt, self.expected)
        self.receipt['diagnostics'] = []; self.receipt['physical_vram_qualified'] = True
        with self.assertRaisesRegex(ValueError, 'Unsupported qualification'):
            audit.validate_identity(self.receipt, self.expected)

    def test_bare_re_spirv_unsupported_instruction_is_diagnostic(self):
        for text in ('OpArrayLength is not supported yet', 'OpVectorExtractDynamic is not supported yet',
                     'ERROR: bad state', 'RID allocations leaked'):
            self.assertIsNotNone(audit.DIAGNOSTIC.search(text))
        self.assertIsNone(audit.DIAGNOSTIC.search('Compiled fixture, GPU pending.'))

    def test_duplicate_json_key_cannot_shadow_identity(self):
        with patch.object(Path, 'read_bytes', return_value=b'{"passed":false,"passed":true}'):
            with self.assertRaisesRegex(ValueError, 'Duplicate JSON key'):
                audit.load_json(Path('unused.json'))

    def test_ledger_hash_mismatch_cannot_be_hidden_by_json_pass(self):
        raw = {'payloads': {name: {'path': name + '.bin', 'bytes': stride, 'sha256': '0' * 64}
                            for name, stride in (('raw', audit.RAW_STRIDE), ('ubo', audit.UBO_STRIDE), ('proof', audit.PROOF_STRIDE))}}
        with patch.object(Path, 'is_file', return_value=True), patch.object(Path, 'is_symlink', return_value=False), \
                patch.object(Path, 'is_junction', return_value=False, create=True), \
                patch.object(Path, 'read_bytes', return_value=bytes(audit.RAW_STRIDE)):
            with self.assertRaisesRegex(ValueError, 'Ledger size/hash differs'):
                audit.read_payloads(raw, Path.cwd(), 1)


class CommandAndHistoryTests(unittest.TestCase):
    def setUp(self):
        guard = audit.GUARD
        self.scene = dict(header_words=[1, 1, 16, 0xe8000001, 7, 2, 2, 0] + [guard] * 8,
                          state_words=[x for stable_id in range(16) for x in [int(stable_id in (2, 7)), 1, stable_id, 1]])
        ids = list(range(16)) + [2, 7] + [guard] * 18
        fallback = [guard] * 4 + [6, 16, 0, 0, 0] + [guard] * 7
        candidate = [guard] * 4 + [6, 2, 0, 0, 16] + [guard] * 7
        stats = [guard] * 4 + [2, 2, 0, 0, 3, 0, 2, 0] + [guard] * 4
        witnesses = [self.scene['state_words'][i * 4:i * 4 + 4] for i in (0, 2, 7)]
        prefix = ids + fallback + candidate + stats + self.scene['header_words'] + sum(witnesses, []) + [guard] * 16
        self.blob = bytearray(struct.pack('<128I', *prefix) + bytes(audit.PROOF_STRIDE - 512))
        self.planned = dict(id='T002', selected_command='candidate')
        self.producer = dict(epoch=3, configuration_generation=2)

    def validate(self):
        return audit.validate_proof(self.blob, 0, self.scene, self.planned, self.producer)

    def test_current_compact_ids_and_complete_fallback_are_distinct(self):
        self.assertEqual(self.validate()[1], [2, 7])
        self.planned['selected_command'] = 'fallback'
        self.assertEqual(self.validate()[1], list(range(16)))

    def test_corrupted_fallback_or_guard_rejected(self):
        for word in (0, 32, 36 + 5, 52, 68 + 8):
            original = bytes(self.blob)
            struct.pack_into('<I', self.blob, word * 4, 0xffffffff)
            with self.assertRaises(ValueError):
                self.validate()
            self.blob[:] = original

    def test_overflow_cannot_publish_a_truncated_prefix(self):
        self.planned['id'] = 'T019'
        struct.pack_into('<I', self.blob, (68 + 6) * 4, 1)
        with self.assertRaisesRegex(ValueError, 'complete fallback'):
            self.validate()
        struct.pack_into('<I', self.blob, (52 + 5) * 4, 16)
        struct.pack_into('<I', self.blob, (52 + 8) * 4, 0)
        self.assertEqual(self.validate()[1], list(range(16)))

    def test_missing_ack_fallback_still_records_taa_and_history(self):
        planned = dict(id='T018', preparation_serial=18, viewport_recorded=True, producer_invoked=True,
            culler_complete=True, observer_capture=True, history_commit=True, taa_process_recorded=True,
            frame_recording_completed=True, frame_rejected=False, selected_command='fallback', native_taa_reset_before=False)
        row = dict(planned, outcome=dict(epoch=17, configuration_generation=2, mode='FALLBACK_SELECTED',
            opaque_recorded=True, taa_process_recorded=True, taa_history_bootstrap=False, frame_recording_completed=True),
            capture_count=1, producer=dict(invoked=True, complete=True, epoch=17, configuration_generation=2, dispatches=10, barriers=9))
        self.assertEqual(audit.validate_row_mapping(row, planned, 16), 17)
        row['history_commit'] = False
        with self.assertRaisesRegex(ValueError, 'history_commit'):
            audit.validate_row_mapping(row, planned, 16)


def synthetic_ledgers():
    """In-memory CPU simulation to exercise acceptance code, never GPU evidence."""
    plan = audit.load_json(Path(__file__).with_name('gate_plan.json'))
    plan['contract'] = 'arena-e13c-native-taa-e8-plan-v2'
    bits = lambda value: struct.unpack('<I', struct.pack('<f', value))[0]
    f32 = lambda value: struct.unpack('<f', struct.pack('<f', value))[0]
    guard = audit.GUARD
    frozen = dict(contract='arena-e13c-procedural-inputs-v1', records=[],
        source_mesh_vertices=[[f32(v) for v in q] for q in [[-.65, -.65, -.22], [.65, -.65, .22], [.65, .65, .22], [-.65, .65, -.22]]],
        triangles=[[0, 2, 1], [0, 3, 2]], background_pixels=[[126, y] for y in (1, 8, 16, 24, 32, 40, 48, 62)],
        wall=dict(vertices=[[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]], position=[-2.25, 0, 0], scale=[2.75, 4, 1]))
    payloads = {name: bytearray(struct.pack('<I', guard) * (128 * stride // 4))
                for name, stride in (('raw', audit.RAW_STRIDE), ('ubo', audit.UBO_STRIDE), ('proof', audit.PROOF_STRIDE))}
    sample_identity = ReceiptDamageTests(); sample_identity.setUp()
    expected = sample_identity.expected
    raw = dict(sample_identity.receipt, records=[], initialization_records=[dict(id='I000', finite_e8_fallback_rendered=True,
        before_registration=True, observer_captures=0, gpu_readbacks=0, retry_count=0, actual_viewport_records=1)], gpu_readbacks=3,
        per_frame_gpu_readbacks=0, images_written=0, unlisted_viewport_renders=0, caller_owned_final=0,
        caller_requested_bytes_final=0, capture_descriptor_pairs_peak=1, compute_dispatches=1240,
        compute_barriers=1116, observer_dispatches=124, callbacks=125, observer_captures=124,
        history_commits=124, cpu_depth_reads_before_producer=0, native_api_present=True)
    last_pose = None; previous_scene = None; epoch = last_commit = 0
    for index, planned in enumerate(plan['records']):
        serial = index + 1
        poses = []
        for stable_id in range(16):
            x = (1.25 if stable_id == 2 else -1.25) + (.0625 if index & 1 else 0)
            z = -.75 if index & 1 else -1.25
            poses.extend([bits(1), 0, 0, bits(x), 0, bits(1), 0, 0, 0, 0, bits(1), bits(z), 1, 1, 0, serial])
        wall = serial in (21, 22, 24) or serial >= 65 and serial % 2 == 0
        reset = planned['consumer_domain_reseed_before']
        scene = dict(id=planned['id'], active_ids=[7, 2], id_scan_order=[7, 2],
            expected_visible_ids=[2] if wall else [7, 2], wall_visible=wall,
            current_pose_words=poses, previous_pose_words=list(poses if last_pose is None or reset else last_pose),
            lifecycle_words=[1, 1, 1, 1] * 16,
            state_words=[v for stable_id in range(16) for v in (int(stable_id in (2, 7)), 1, stable_id, 1)],
            header_words=[serial, serial if last_pose is None or reset else last_commit, 16, 0xe8000001, 7, 2, 2, index & 1] + [guard] * 8,
            designated_nonzero_x=False, designated_nonzero_y=False,
            invalid_history_ids=[7] if 26 <= serial <= 36 else [])
        if scene['invalid_history_ids']:
            scene['lifecycle_words'][7 * 4 + 1] = 0
        frozen['records'].append(scene)
        row = {k: planned[k] for k in ('id', 'preparation_serial', 'viewport_recorded', 'producer_invoked', 'culler_complete',
            'observer_capture', 'history_commit', 'taa_process_recorded', 'frame_recording_completed', 'frame_rejected')}
        row.update(scene=scene, previous_rendered_preparation=last_commit, outcome=None, producer=None,
                   capture_count=int(planned['observer_capture']))
        raw['records'].append(row)
        if not planned['viewport_recorded']:
            continue
        epoch += 1
        row['outcome'] = dict(epoch=epoch, configuration_generation=2,
            mode='FRAME_REJECTED' if planned['frame_rejected'] else 'CANDIDATE_SELECTED' if planned['selected_command'] == 'candidate' else 'FALLBACK_SELECTED',
            opaque_recorded=planned['frame_recording_completed'], frame_recording_completed=planned['frame_recording_completed'],
            taa_process_recorded=planned['frame_recording_completed'], taa_history_bootstrap=planned['native_taa_reset_before'] and planned['frame_recording_completed'])
        if planned['producer_invoked']:
            row['producer'] = dict(invoked=True, complete=planned['culler_complete'], epoch=epoch, configuration_generation=2, dispatches=10 if planned['culler_complete'] else 0,
                                   barriers=9 if planned['culler_complete'] else 0)
        if not planned['observer_capture']:
            continue
        width, height = planned['internal_extent']; scale = 1 / math.tan(math.pi / 6)
        jitter = ((index % 4 - 1.5) / 256, (1.5 - index % 4) / 256)
        projection = [0.] * 16
        projection[0] = scale / (width / height); projection[5] = -scale
        projection[8] = -jitter[0]; projection[9] = -jitter[1]
        projection[10] = .05 / (1000 - .05); projection[11] = -1; projection[14] = .05 * 1000 / (1000 - .05)
        current = dict(projection=[f32(v) for v in projection], view=[1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., -3.], jitter=jitter)
        previous = previous_scene or current
        for base, captured in ((index * audit.UBO_STRIDE, current), (index * audit.UBO_STRIDE + 2848, previous)):
            struct.pack_into('<16f', payloads['ubo'], base, *captured['projection'])
            struct.pack_into('<12f', payloads['ubo'], base + 176, *captured['view'])
            struct.pack_into('<f', payloads['ubo'], base + 2816, serial / 60)
            struct.pack_into('<2f', payloads['ubo'], base + 2824, *captured['jitter'])
        matrix = []
        for col in range(4):
            v = [1 if c == col else 0 for c in range(4)]
            q = [sum(current['view'][r * 4 + c] * v[c] for c in range(4)) for r in range(3)] + [v[3]]
            matrix.extend(f32(sum(current['projection'][c * 4 + r] * q[c] for c in range(4))) for r in range(4))
        row['producer']['world_to_clip_words'] = [bits(v) for v in matrix]
        matrix_scene = dict(projection=matrix, view=[1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0])
        probes, wall_depth = audit.expected_probes(scene, frozen, current, previous, [width, height], matrix_scene)
        row['probes'] = probes
        admitted = [] if serial == 20 else audit.conservative_ids(scene, matrix_scene, wall_depth, [width, height])
        written = [] if serial == 19 else admitted
        ids = list(range(16)) + written + [guard] * (20 - len(written))
        fallback = [guard] * 4 + [6, 16, 0, 0, 0] + [guard] * 7
        candidate = [guard] * 4 + [6, 16 if serial == 19 else len(written), 0, 0, 0 if serial == 19 else 16] + [guard] * 7
        stats = [guard] * 4 + [len(written), len(admitted), int(serial == 19), int(serial == 20), epoch, 0, 2, 0] + [guard] * 4
        color_bits = [guard] * 16
        for pindex, probe in enumerate(probes):
            x, y = probe['x'], probe['y']
            velocity, depth, red = [0, 0], 0, 0
            if probe['kind'] == 'identity':
                stable_id = probe['stable_id']
                now, old, _ = audit.history_vertices(scene, stable_id, frozen['source_mesh_vertices'])
                cc, pc = [audit.project(current, p) for p in now], [audit.project(previous, p) for p in old]
                bary, depth = audit.barycentric_pixel(cc, probe['triangle'], x, y, [width, height])
                velocity = audit.perspective_velocity([cc[i] for i in probe['triangle']], [pc[i] for i in probe['triangle']],
                    bary, current['jitter'], previous['jitter'])
                red = (stable_id + 1) / 32
            struct.pack_into('<4I', payloads['raw'], index * audit.RAW_STRIDE + (y * 128 + x) * 16,
                             bits(velocity[0]), bits(velocity[1]), serial, bits(depth))
            color_bits[pindex] = bits(red)
        states = sum([scene['state_words'][stable_id * 4:stable_id * 4 + 4] for stable_id in (0, 2, 7)], [])
        prefix = ids + fallback + candidate + stats + scene['header_words'] + states + color_bits
        offset = index * audit.PROOF_STRIDE
        struct.pack_into('<128I', payloads['proof'], offset, *prefix)
        pyramid, depth_width, depth_height = wall_depth, 128, 64
        at = offset + 512 + 16
        for level in range(8):
            struct.pack_into('<' + 'f' * len(pyramid), payloads['proof'], at, *pyramid); at += len(pyramid) * 4
            if level == 7: break
            w, h = max(1, depth_width // 2), max(1, depth_height // 2)
            pyramid = [min(pyramid[(y * 2 + dy) * depth_width + x * 2 + dx]
                           if y * 2 + dy < depth_height and x * 2 + dx < depth_width else 0
                           for dy in range(2) for dx in range(2)) for y in range(h) for x in range(w)]
            depth_width, depth_height = w, h
        previous_scene = current; last_pose = poses; last_commit = serial
    return raw, plan, expected, payloads, frozen


class WholeReceiptCpuSimulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw, cls.plan, cls.expected, cls.payloads, cls.frozen = synthetic_ledgers()

    def test_full_128_record_validator_executes_on_cpu_simulation(self):
        with patch.object(audit, 'read_payloads', return_value=self.payloads):
            result = audit.validate(self.raw, self.plan, self.expected, Path.cwd(), self.frozen)
        self.assertEqual(result['captures'], 124)
        self.assertEqual(result['actual_viewport_records'], 127)
        self.assertFalse(result['performance_qualified'])

    def test_correct_pose_words_cannot_hide_wrong_actual_color(self):
        damaged = {**self.payloads, 'proof': bytearray(self.payloads['proof'])}
        struct.pack_into('<f', damaged['proof'], 448, 0)
        with patch.object(audit, 'read_payloads', return_value=damaged):
            with self.assertRaisesRegex(ValueError, 'fragment identity'):
                audit.validate(self.raw, self.plan, self.expected, Path.cwd(), self.frozen)

    def test_raw_zero_velocity_cannot_pass_matching_metadata(self):
        damaged = {**self.payloads, 'raw': bytearray(self.payloads['raw'])}
        probe = self.raw['records'][1]['probes'][0]
        offset = audit.RAW_STRIDE + (probe['y'] * 128 + probe['x']) * 16
        struct.pack_into('<2f', damaged['raw'], offset, 0, 0)
        with patch.object(audit, 'read_payloads', return_value=damaged):
            with self.assertRaisesRegex(ValueError, 'Raw velocity'):
                audit.validate(self.raw, self.plan, self.expected, Path.cwd(), self.frozen)

    def test_ignoring_invalid_history_metadata_fails_numerically(self):
        index = 25
        row = self.raw['records'][index]; scene = row['scene']
        probe = next(p for p in row['probes'] if p.get('stable_id') == 7)
        current = audit.decode_scene(self.payloads['ubo'], index * audit.UBO_STRIDE)
        previous = audit.decode_scene(self.payloads['ubo'], index * audit.UBO_STRIDE + 2848)
        now, _, valid = audit.history_vertices(scene, 7, self.frozen['source_mesh_vertices'])
        self.assertFalse(valid)
        cc = [audit.project(current, vertex) for vertex in now]
        pc = [audit.project(previous, vertex) for vertex in audit.pose_vertices(scene['previous_pose_words'], 7, self.frozen['source_mesh_vertices'])]
        bary, _ = audit.barycentric_pixel(cc, probe['triangle'], probe['x'], probe['y'], [128, 64])
        mutant = audit.perspective_velocity([cc[i] for i in probe['triangle']], [pc[i] for i in probe['triangle']],
                                          bary, current['jitter'], previous['jitter'])
        damaged = {**self.payloads, 'raw': bytearray(self.payloads['raw'])}
        offset = index * audit.RAW_STRIDE + (probe['y'] * 128 + probe['x']) * 16
        struct.pack_into('<2f', damaged['raw'], offset, *mutant)
        with patch.object(audit, 'read_payloads', return_value=damaged):
            with self.assertRaisesRegex(ValueError, 'Raw velocity'):
                audit.validate(self.raw, self.plan, self.expected, Path.cwd(), self.frozen)


if __name__ == '__main__':
    unittest.main()

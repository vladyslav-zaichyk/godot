"""Freeze procedural inputs before shader compilation or GPU execution."""
import json
import pathlib
import struct

HERE = pathlib.Path(__file__).resolve().parent
GUARD = 0xCDCDCDCD


def bits(value):
    return struct.unpack('<I', struct.pack('<f', value))[0]


def f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


def main():
    plan = json.loads((HERE.parent / 'gate_plan.json').read_bytes())
    x7, x2, z, deformation, cx, cy, yaw = -1.25, 1.25, -1., 0., 0., 0., 0.
    gen, continuity, domain, representation = 1, 1, 1, 1
    committed = None
    rows = []
    for index, record in enumerate(plan['records']):
        n = index + 1
        if n in (3, 9, 12, 39, 40): x7 += .125; x2 -= .125
        if n in (4, 10): x7 -= .25; x2 += .25
        if n in (5, 9, 10): deformation += .25
        if n == 6: deformation -= .5
        if n in (7, 10, 24, 28, 30, 50, 53): cx += .125; cy -= .125
        if n in (8, 25, 26, 54): cx -= .125; cy += .125
        if n == 11: deformation = .375
        if n in (27, 28, 29, 30): gen += 1
        if n in (26, 31): continuity += 1
        if n == 26:
            x7 += .125  # Reset must suppress an observable previous-pose displacement.
            x2 += .125  # Unrelated ID2 must preserve its genuine root-motion history.
        if 27 <= n <= 36: x7 += .125 if n & 1 else -.125
        if n in (45, 49, 50): z += .25
        if n == 46: z -= .5
        if n == 47: yaw = .025
        if n == 48: yaw = -.025
        if n in (55, 56): x7 += .125 if n == 55 else -.125; deformation += .125 if n == 55 else -.125
        if n == 57: cx, cy, yaw = -.25, .125, 0.
        if n >= 65:
            phase = (n - 65) & 1
            x7, x2 = (-1.25, 1.25) if not phase else (-1.125, 1.125)
            deformation = 0. if not phase else .25
            z, cx, cy, yaw = -1., 0., 0., 0.
        reseed = record['consumer_domain_reseed_before']
        if reseed: domain += 1
        active = [0, 2] if n == 15 else [7, 2]
        order = list(reversed(active)) if n == 14 or n >= 65 and n % 2 == 0 else active
        wall = n in (21, 22, 24) or n >= 65 and n % 2 == 0
        continuity_by_slot = [continuity if slot == 7 else 1 for slot in range(16)]
        current = []
        for slot in range(16):
            x = x2 if slot == 2 else x7
            d = -deformation if slot == 2 else deformation
            sx, shear, sy = (.875, .125, 1.125) if n == 11 and slot == 7 else (1., 0., 1.)
            current.extend([bits(sx), bits(shear), 0, bits(x), 0, bits(sy), 0, 0,
                            0, 0, bits(1.), bits(z), gen if slot != 2 else 1,
                            representation, bits(d), n])
        previous = list(current if reseed or committed is None else committed['current_pose_words'])
        previous_sample = n if reseed or committed is None else committed['preparation_serial']
        previous_continuity = continuity_by_slot if reseed or committed is None else committed['continuity_by_slot']
        previous_domain = domain if reseed or committed is None else committed['domain']
        life = [value for slot in range(16) for value in (continuity_by_slot[slot], previous_continuity[slot], domain, previous_domain)]
        if n == 32: previous[7 * 16 + 15] = 0
        if n == 33: life[7 * 4 + 3] = domain + 100
        if n == 34: previous[7 * 16 + 13] = representation + 100
        if n == 35: previous[7 * 16 + 12] = 0
        if n == 36: life[7 * 4 + 1] = 0
        states = [value for slot in range(16) for value in (int(slot in active), 1, slot, gen if slot != 2 else 1)]
        header = [n, previous_sample, 16, 0xE8000001, *order, len(active), int(n % 2 == 0), *([GUARD] * 8)]
        visible = [i for i in active if not wall or i != 7]
        row = dict(id=record['id'], index=index, preparation_serial=n,
                   current_pose_words=current, previous_pose_words=previous,
                   lifecycle_words=life, state_words=states, header_words=header,
                   camera=dict(position=[cx, cy, 3.], yaw=yaw, fov=60.),
                   internal_extent=record['internal_extent'], domain=domain,
                   continuity=continuity, continuity_by_slot=continuity_by_slot, reseed=reseed, active_ids=active,
                   id_scan_order=order, expected_visible_ids=visible,
                   invalid_history_ids=[7] if 26 <= n <= 36 else [],
                   wall_visible=wall, previous_rendered_preparation=0 if committed is None else committed['preparation_serial'],
                   designated_nonzero_x=n in (3, 4, 5, 6, 7, 8),
                   designated_nonzero_y=n in (7, 8))
        rows.append(row)
        if record['history_commit']: committed = row
    result = dict(contract='arena-e13c-procedural-inputs-v1',
                  source_mesh_vertices=[[f32(v) for v in row] for row in [[-.65, -.65, -.22], [.65, -.65, .22], [.65, .65, .22], [-.65, .65, -.22]]],
                  triangles=[[0, 2, 1], [0, 3, 2]],
                  deformation='local.x += deformation * (local.y + 0.5); then three affine root rows',
                  background_pixels=[[126, 1], [126, 8], [126, 16], [126, 24], [126, 32], [126, 40], [126, 48], [126, 62]],
                  wall=dict(vertices=[[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]],
                            position=[-2.25, 0., 0.], scale=[2.75, 4., 1.]),
                  probe_selection='all current triangle interior pixel centers, edge distance>=2; sort round(squared distance to quad centroid*1024),y,x,triangle index; first4 distinct pixels per expected visible identity; then8 background',
                  records=rows)
    (HERE / 'scenarios.json').write_bytes((json.dumps(result, indent=2) + '\n').encode())


if __name__ == '__main__':
    main()

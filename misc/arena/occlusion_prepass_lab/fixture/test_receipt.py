"""CPU geometry/receipt adversaries. No Godot or GPU process."""
import copy
import hashlib
from pathlib import Path
import struct
import unittest
from validate_receipt import CASES,INPUTS,OFFSETS,GUARD,depth_at,load_json,reference_visible,full_resolution_visible,validate,validate_oracle,visibility

ORACLE=load_json(Path(__file__).parent/'project/oracle.json')
EXPECTED=dict(engine_hash='7d59f5064a6cd40a1cf2aaa578b8e90d29d1cbe8',engine_sha256='a'*64,sdk_version='4.7.2-arena-dev-g7d59f5064a',
    managed_api_assembly_sha256='b'*64,managed_fixture_assembly_sha256='c'*64,source_sha256={name:'d'*64 for name in INPUTS})


def specimen(case):
    rejected=case=='capacity_rejected'
    raw=dict(EXPECTED,contract='proxy-occlusion-case-v1',case_name=case,mode='reference',status='capacity_rejected' if rejected else 'reference_complete',passed=not rejected,
        failure='',checks=100,proxy_reference_qualified=not rejected,native_qualified=False,native_api_present=False,performance_qualified=False,release_driver_qualified=False,images_written=0,
        driver='vulkan',renderer='forward_plus',gpu_name='synthetic',diagnostics=[],dropped_diagnostics=0,resources=dict(caller_owned_final=0,caller_requested_bytes_final=0,open_lists_final=0),probes=[],lifetime_cycles=[])
    for probe in ORACLE['cases'][case]:
        exact,proxy=visibility(ORACLE,probe);overflow=len(proxy)>probe['capacity'];written=[] if overflow else proxy
        v=[GUARD]*28;v[4:8]=[len(written),len(proxy),int(overflow),probe['query_epoch']];v[8:8+len(written)]=written
        c=[GUARD]*16;c[4:9]=[6,len(written),0,0,0];h=[GUARD]*2739
        for level,offset in enumerate(OFFSETS):
            width=max(1,64>>level);height=max(1,32>>level);block=1<<level
            for y in range(height):
                for x in range(width):
                    value=min(depth_at(px,py,probe) for py in range(y*block,(y+1)*block) for px in range(x*block,(x+1)*block))
                    h[offset+y*width+x]=struct.unpack('<I',struct.pack('<f',value))[0]
        item=dict(name=probe['name'],visible_words=v,command_words=c,pyramid_words=h,producer_completed_before_readback=True)
        for words,digest in (('visible_words','visible_sha256'),('command_words','command_sha256'),('pyramid_words','pyramid_sha256')):
            item[digest]=hashlib.sha256(struct.pack('<'+'I'*len(item[words]),*item[words])).hexdigest()
        raw['probes'].append(item)
        if case=='lifetime':raw['lifetime_cycles'].append(dict(name=probe['name'],caller_owned_final=0,caller_requested_bytes_final=0))
    return raw


def rehash(raw):
    for probe in raw['probes']:
        for words,digest in (('visible_words','visible_sha256'),('command_words','command_sha256'),('pyramid_words','pyramid_sha256')):
            probe[digest]=hashlib.sha256(struct.pack('<'+'I'*len(probe[words]),*probe[words])).hexdigest()


class OracleTests(unittest.TestCase):
    def test_all_seven_shapes(self):
        for case in CASES:validate(specimen(case),ORACLE,case,EXPECTED)
    def reject(self,mutate,case='wall_grid'):
        raw=copy.deepcopy(specimen(case));mutate(raw);rehash(raw)
        with self.assertRaises(ValueError):validate(raw,ORACLE,case,EXPECTED)
    def test_blocked_gap_near_uncertain(self):
        p=ORACLE['cases']['wall_grid'][0];items={c['id']:c for c in ORACLE['candidates']}
        for ident in (1000,1001,1010,1011):self.assertFalse(reference_visible(items[ident],p))
        for ident in (1002,1003,1004,1007,1008,1009,1013):self.assertTrue(reference_visible(items[ident],p))
    def test_edge_false_positive_is_conservative(self):
        p=ORACLE['cases']['wall_grid'][0];c=ORACLE['candidates'][12]
        self.assertFalse(full_resolution_visible(c,p));self.assertTrue(reference_visible(c,p))
    def test_stale_clear_restores_all(self):
        p=ORACLE['cases']['stale_lists'][1];self.assertEqual(len(visibility(ORACLE,p)[1]),16)
    def test_cut_fails_open(self):self.assertEqual(len(visibility(ORACLE,ORACLE['cases']['camera_cut'][0])[1]),16)
    def test_native_pass_rejected(self):self.reject(lambda r:r.update(native_qualified=True))
    def test_nonboolean_pass(self):self.reject(lambda r:r.update(passed=1))
    def test_missing_probe(self):self.reject(lambda r:r['probes'].clear())
    def test_missing_identity(self):self.reject(lambda r:r.update(source_sha256={}))
    def test_unexpected_diagnostic(self):self.reject(lambda r:r['diagnostics'].append({'message':'bad'}))
    def test_dropped_diagnostic(self):self.reject(lambda r:r.update(dropped_diagnostics=1))
    def test_guard_mutation(self):self.reject(lambda r:r['probes'][0]['visible_words'].__setitem__(0,0))
    def test_missing_visible(self):self.reject(lambda r:r['probes'][0]['visible_words'].__setitem__(8,GUARD))
    def test_wrong_first_instance(self):self.reject(lambda r:r['probes'][0]['command_words'].__setitem__(8,1))
    def test_stale_depth(self):self.reject(lambda r:r['probes'][1]['pyramid_words'].__setitem__(4+10*64+10,struct.unpack('<I',struct.pack('<f',0.6))[0]),'stale_lists')
    def test_wrong_min_node_with_valid_hash(self):self.reject(lambda r:r['probes'][0]['pyramid_words'].__setitem__(2052+5*32+5,0))
    def test_all_visible_cannot_fake_occlusion(self):
        def mutate(r):
            v=r['probes'][0]['visible_words'];v[4:8]=[16,16,0,1];v[8:24]=[c['id'] for c in ORACLE['candidates']];r['probes'][0]['command_words'][5]=16
        self.reject(mutate)
    def test_reordered_stable_ids(self):
        def mutate(r):
            v=r['probes'][0]['visible_words'];v[8],v[9]=v[9],v[8]
        self.reject(mutate)
    def test_overflow_cannot_claim_culling(self):self.reject(lambda r:r.update(proxy_reference_qualified=True),'capacity_rejected')
    def test_overflow_does_not_claim_empty_visibility_errors(self):
        result=validate(specimen('capacity_rejected'),ORACLE,'capacity_rejected',EXPECTED)
        self.assertTrue(result['expected_rejection_verified'])
        self.assertIsNone(result['probes'][0]['false_negative_ids'])
        self.assertIsNone(result['probes'][0]['false_positive_ids'])
    def test_overflow_cannot_draw_truncation(self):self.reject(lambda r:r['probes'][0]['command_words'].__setitem__(5,2),'capacity_rejected')
    def test_resource_leak(self):self.reject(lambda r:r['resources'].update(caller_owned_final=1))
    def test_boolean_resource(self):self.reject(lambda r:r['resources'].update(caller_owned_final=False))
    def test_missing_cycle(self):self.reject(lambda r:r['lifetime_cycles'].pop(),'lifetime')
    def test_oracle_empty_matrix(self):
        bad=copy.deepcopy(ORACLE);bad['cases']={}
        with self.assertRaises(ValueError):validate_oracle(bad)


if __name__=='__main__':unittest.main()

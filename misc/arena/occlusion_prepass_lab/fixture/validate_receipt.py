"""Analytic full-resolution wall oracle, independent of GPU min-reduction code."""
import hashlib
import json
import math
import re
import struct

CASES=('wall_grid','open_scene','camera_shift','camera_cut','stale_lists','lifetime','capacity_rejected')
INPUTS=('OcclusionReferenceLab.csproj','project.godot','Main.tscn','Main.cs','ProbeLogger.cs','Depth.vert','Depth.frag','CopyDepth.comp','Reduce.comp','Produce.comp','oracle.json')
GUARD=0xcdcdcdcd
OFFSETS=(4,2052,2564,2692,2724,2732,2734)


def require(value,message):
    if not value: raise ValueError(message)


def load_json(path):
    def pairs(items):
        result={}
        for key,value in items:
            require(key not in result,'Duplicate JSON key: '+key);result[key]=value
        return result
    def bad(value): raise ValueError('Nonfinite JSON: '+value)
    return json.loads(path.read_bytes(),object_pairs_hook=pairs,parse_constant=bad)


def validate_oracle(oracle):
    require(type(oracle) is dict and oracle.get('contract')=='proxy-occlusion-oracle-v1' and oracle.get('assembly')=='OcclusionReferenceLab','Wrong oracle.')
    require(oracle.get('inputs')==list(INPUTS) and type(oracle.get('cases')) is dict and tuple(oracle['cases'])==CASES,'Incomplete ordered case/source matrix.')
    candidates=oracle.get('candidates');require(type(candidates) is list and len(candidates)==16,'Sixteen bounded candidates required.')
    ids=[]
    for c in candidates:
        require(type(c) is dict and set(c)=={'id','rect','depth','flags'},'Candidate schema differs.')
        require(type(c['id']) is int and 0<=c['id']<=0xffffffff and type(c['flags']) is int and 0<=c['flags']<=3,'Invalid ID/flags.')
        require(type(c['rect']) is list and len(c['rect'])==4 and all(type(v) is int for v in c['rect']),'Invalid orthographic rectangle.')
        require(type(c['depth']) in (int,float) and math.isfinite(c['depth']) and 0<=c['depth']<=1,'Invalid legal depth.')
        ids.append(c['id'])
    require(len(set(ids))==16,'Stable IDs must be unique.')
    for name,probes in oracle['cases'].items():
        require(type(probes) is list and 1<=len(probes)<=4,'Empty/over-budget probe matrix.')
        names=[]
        for p in probes:
            require(type(p) is dict and set(p)=={'name','wall','camera_shift','depth_epoch','query_epoch','capacity'},'Probe input schema differs.')
            require(type(p['name']) is str and bool(p['name']) and type(p['wall']) is bool,'Invalid probe name/wall.')
            require(type(p['camera_shift']) is int and p['camera_shift'] in (0,4),'Unqualified camera projection.')
            require(all(type(p[k]) is int and 0<p[k]<=0xffffffff for k in ('depth_epoch','query_epoch')),'Invalid epochs.')
            require(type(p['capacity']) is int and 0<=p['capacity']<=16,'Invalid capacity.')
            names.append(p['name'])
        require(len(names)==len(set(names)),'Duplicate probe names.')


def depth_at(x,y,probe):
    """Pixel-center coverage of two analytic quads; never builds a CPU pyramid."""
    if not probe['wall'] or not (0<=x<64 and 0<=y<32): return 0.0
    world_x=x+probe['camera_shift']+0.5
    return 0.6 if 4<=world_x<28 or 36<=world_x<60 else 0.0


def projected(candidate,probe):
    x0,y0,x1,y1=candidate['rect'];s=probe['camera_shift']
    return x0-s,y0,x1-s,y1


def forced_open(candidate,probe,rect):
    x0,y0,x1,y1=rect
    return (candidate['flags']!=0 or probe['depth_epoch']!=probe['query_epoch'] or
            x0<0 or y0<0 or x1>64 or y1>32 or x1<=x0 or y1<=y0)


def full_resolution_visible(candidate,probe):
    rect=projected(candidate,probe)
    if forced_open(candidate,probe,rect): return True
    x0,y0,x1,y1=rect
    return any(depth_at(x,y,probe)<=candidate['depth']+0.00002 for y in range(y0,y1) for x in range(x0,x1))


def reference_visible(candidate,probe):
    rect=projected(candidate,probe)
    if forced_open(candidate,probe,rect): return True
    x0,y0,x1,y1=rect;x0-=1;y0-=1;x1+=1;y1+=1
    if x0<0 or y0<0 or x1>64 or y1>32: return True
    # Expand directly to complete aligned blocks and query analytic base pixels.
    # No GPU mip array, recursive min tree or GPU result participates in this oracle.
    block=1<<(max(x1-x0,y1-y0)-1).bit_length()
    ax=(x0//block)*block;ay=(y0//block)*block
    bx=((x1+block-1)//block)*block;by=((y1+block-1)//block)*block
    return any(depth_at(x,y,probe)<=candidate['depth']+0.00002 for y in range(ay,by) for x in range(ax,bx))


def visibility(oracle,probe):
    exact=[c['id'] for c in oracle['candidates'] if full_resolution_visible(c,probe)]
    proxy=[c['id'] for c in oracle['candidates'] if reference_visible(c,probe)]
    require(set(exact)<=set(proxy),'CPU reference generated a false negative.')
    return exact,proxy


def uint_array(value,count,name):
    require(type(value) is list and len(value)==count and all(type(v) is int and 0<=v<=0xffffffff for v in value),'Invalid '+name+' words.')


def validate(raw,oracle,case_name,expected):
    validate_oracle(oracle)
    require(case_name in CASES and type(raw) is dict and raw.get('contract')=='proxy-occlusion-case-v1' and raw.get('case_name')==case_name and raw.get('mode')=='reference','Wrong reference receipt.')
    rejected=case_name=='capacity_rejected'
    require(raw.get('failure')=='' and raw.get('passed') is (not rejected) and raw.get('status')==('capacity_rejected' if rejected else 'reference_complete'),'Unexpected runtime outcome.')
    require(raw.get('proxy_reference_qualified') is (not rejected) and raw.get('native_qualified') is False and raw.get('native_api_present') is False,'Reference/native qualification conflated.')
    require(raw.get('performance_qualified') is False and raw.get('release_driver_qualified') is False and type(raw.get('images_written')) is int and raw['images_written']==0,'Unsupported evidence claim.')
    require(type(raw.get('checks')) is int and raw['checks']>=30,'Truncated checks.')
    require(raw.get('driver')=='vulkan' and raw.get('renderer')=='forward_plus' and type(raw.get('gpu_name')) is str and bool(raw['gpu_name'].strip()),'Wrong renderer/GPU.')
    for key in ('engine_hash','engine_sha256','sdk_version','managed_api_assembly_sha256','managed_fixture_assembly_sha256','source_sha256'):
        require(key in expected and raw.get(key)==expected[key],'Identity differs: '+key)
    require(type(raw['engine_hash']) is str and re.fullmatch('[0-9a-f]{40}',raw['engine_hash']) and type(raw['sdk_version']) is str and bool(raw['sdk_version']),'Invalid source/SDK identity.')
    for key in ('engine_sha256','managed_api_assembly_sha256','managed_fixture_assembly_sha256'):
        require(type(raw[key]) is str and re.fullmatch('[0-9a-f]{64}',raw[key]),'Invalid payload hash.')
    require(type(raw['source_sha256']) is dict and set(raw['source_sha256'])==set(INPUTS) and all(type(v) is str and re.fullmatch('[0-9a-f]{64}',v) for v in raw['source_sha256'].values()),'Invalid source inventory.')
    require(type(raw.get('diagnostics')) is list and raw['diagnostics']==[] and type(raw.get('dropped_diagnostics')) is int and raw['dropped_diagnostics']==0,'Unexpected/dropped diagnostic.')
    require(type(raw.get('resources')) is dict and set(raw['resources'])=={'caller_owned_final','caller_requested_bytes_final','open_lists_final'} and all(type(v) is int and v==0 for v in raw['resources'].values()),'Resources remain.')
    probes=raw.get('probes');wanted=oracle['cases'][case_name]
    require(type(probes) is list and len(probes)==len(wanted),'Incomplete probe matrix.')
    evidence=[]
    for observed,probe in zip(probes,wanted):
        require(type(observed) is dict and set(observed)=={'name','visible_words','command_words','pyramid_words','visible_sha256','command_sha256','pyramid_sha256','producer_completed_before_readback'} and observed['name']==probe['name'] and observed['producer_completed_before_readback'] is True,'Wrong probe/readback order claim.')
        for word_key,hash_key,count in (('visible_words','visible_sha256',28),('command_words','command_sha256',16),('pyramid_words','pyramid_sha256',2739)):
            words=observed[word_key];uint_array(words,count,word_key)
            require(observed[hash_key]==hashlib.sha256(struct.pack('<'+'I'*count,*words)).hexdigest(),'Readback hash differs.')
        v,c,h=observed['visible_words'],observed['command_words'],observed['pyramid_words']
        require(v[:4]+v[24:]==[GUARD]*8 and c[:4]+c[9:]==[GUARD]*11 and h[:4]+h[2735:]==[GUARD]*8,'Guard corruption.')
        for level,offset in enumerate(OFFSETS):
            width=max(1,64>>level);height=max(1,32>>level);block=1<<level
            for y in range(height):
                for x in range(width):
                    # Analytic full-resolution coverage is independent of GPU reductions.
                    want=min(depth_at(px,py,probe) for py in range(y*block,(y+1)*block) for px in range(x*block,(x+1)*block))
                    actual=struct.unpack('<f',struct.pack('<I',h[offset+y*width+x]))[0]
                    require(math.isfinite(actual) and abs(actual-want)<=0.000002,'Raster/min-pyramid differs at level '+str(level))
        exact,proxy=visibility(oracle,probe);overflow=len(proxy)>probe['capacity'];written=[] if overflow else proxy
        require(v[4:8]==[len(written),len(proxy),int(overflow),probe['query_epoch']],'Visibility header/overflow differs.')
        require(v[8:24]==written+[GUARD]*(16-len(written)) and c[4:9]==[6,len(written),0,0,0],'Stable IDs/indirect fields differ.')
        require(overflow==rejected,'Unexpected capacity rejection.')
        require(rejected or set(exact)<=set(written),'False-negative occlusion.')
        evidence.append(dict(probe=probe['name'],full_resolution_visible_ids=exact,proxy_visible_ids=proxy,
            false_negative_ids=None if rejected else sorted(set(exact)-set(written)),
            false_positive_ids=None if rejected else sorted(set(written)-set(exact)),capacity_rejected=overflow))
    cycles=raw.get('lifetime_cycles')
    require(type(cycles) is list and len(cycles)==(4 if case_name=='lifetime' else 0),'Lifecycle coverage differs.')
    for item,probe in zip(cycles,wanted):
        require(type(item) is dict and set(item)=={'name','caller_owned_final','caller_requested_bytes_final'} and item['name']==probe['name'] and all(type(item[k]) is int and item[k]==0 for k in ('caller_owned_final','caller_requested_bytes_final')),'Cycle ledger not drained.')
    return dict(matrix_case_contract_passed=True,expected_rejection_verified=rejected,proxy_case_culling_qualified=not rejected,native_qualified=False,probes=evidence)

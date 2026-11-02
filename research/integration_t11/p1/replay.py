"""Produce deterministic public event traces and source/output hashes."""
import hashlib
import json
from pathlib import Path
from research.integration_t11.p1.controller import Controller, VERSION, event_id, TTL, WET_STUCK, MAX_TIME
from research.integration_t11.p1.fixtures import analytic_request

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'research/integration_t11/p1/results'


def dump(path, data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def run():
    cfg=json.loads((ROOT/'research/integration_t11/p1/config.json').read_text(encoding='utf-8'))
    assert cfg['parameters']['max_evidence_ttl_s']['value']==TTL
    assert cfg['parameters']['wet_stuck_s']['value']==WET_STUCK
    assert cfg['parameters']['max_time_s']['value']==MAX_TIME
    h=Controller('device','boot','batch','synthetic'); seq=0; events=[]; snapshots=[]
    def emit(kind,t,payload):
        nonlocal seq
        seq+=1;domain=dict(h.domain)
        e=dict(version=VERSION,id=event_id(domain,seq),seq=seq,domain=domain,occurred_s=t,received_s=t,kind=kind,payload=payload)
        events.append(dict(event=e,decision_s=t)); snapshots.append(h.apply(e,t))
    for kind,state in [('contact','confirmed'),('heat','clear'),('local','dry')]:
        payload=dict(state=state,source='synthetic_external',expires_s=120)
        if kind=='local':payload['exposure']='exposed'
        emit(kind,0,payload)
    emit('forecast',2,dict(issued_s=1,available_s=2,valid_from_s=40,valid_until_s=90,expires_s=110,risk='risk',source='synthetic_forecast'))
    emit('prediction',20,dict(origin_s=0,expires_s=100,request=analytic_request()))
    emit('local',21,dict(state='wet',source='synthetic_external',expires_s=120,exposure='exposed'))
    emit('local',22,dict(state='dry',source='synthetic_external',expires_s=120,exposure='exposed'))
    emit('tick',40,{})
    emit('tick',100,{})
    OUT.mkdir(parents=True,exist_ok=True)
    dump(OUT/'events.json',dict(label='SYNTHETIC_UNCALIBRATED',seed=None,source='software scenario using M6 synthetic fixture; no observed garment',events=events))
    dump(OUT/'snapshots.json',dict(label='SYNTHETIC_UNCALIBRATED',seed=None,snapshots=snapshots))
    baseline=json.loads((ROOT/'docs/team/baseline-t11-p1/snapshot.json').read_text(encoding='utf-8'))['inputs_sha256']
    paths=['docs/team/baseline-t11-p1/snapshot.json','docs/references/T11-P1-sources.md',
           'research/garment_prediction/prediction_m6/config.json','research/garment_prediction/prediction_m6/integrated.py',
           'research/garment_prediction/coupled_m5/coupled.py','research/garment_prediction/model.py',
           'research/garment_prediction/observation_m2/contract.py',
           'research/integration_t11/p1/controller.py','research/integration_t11/p1/config.json',
           'research/integration_t11/p1/fixtures.py','research/integration_t11/p1/replay.py',
           'research/integration_t11/p1/test_controller.py','docs/T11-EVENT-CONTRACT.md',
           'research/integration_t11/p1/results/events.json','research/integration_t11/p1/results/snapshots.json']
    hashes={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths}
    for p,digest in hashes.items():
        if p in baseline and baseline[p]!=digest: raise ValueError('frozen input changed: '+p)
    dump(OUT/'manifest.json',dict(version=VERSION,label='SYNTHETIC_UNCALIBRATED',seed=None,
        command='python -B -X utf8 -m research.integration_t11.p1.replay',sha256=hashes))
    print('replayed',len(events),'events; conditional sampled time',snapshots[4]['prediction']['value']['result']['upper_sample_s'])


if __name__=='__main__':run()

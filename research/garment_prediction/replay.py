"""Reproduce deterministic two-zone example and integrity manifest from repo root."""
import hashlib, json
from pathlib import Path
from model import Zone,Drive,state,run_two,run
P=Path('research/garment_prediction'); OUT=P/'results'; OUT.mkdir(exist_ok=True)
c=json.loads((P/'config.json').read_text(encoding='utf-8-sig'))
z=[Zone(**c['ordinary']),Zone(**c['slow'])]; d=Drive(**c['drive']); y=[state(v,c['initial_x_kg_kg'],c['initial_temperature_c']) for v in z]
result={'config':c,'two_zone':run_two(z,[d,d],y,c['duration_s'],c['step_s']), 'ordinary_convergence':{str(h):run(z[0],d,y[0],c['duration_s'],h) for h in [20,10,2,1]},'ordinary_midpoint_reference':run(z[0],d,y[0],c['duration_s'],.25,'midpoint')}
p=OUT/'example.json'; p.write_bytes((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
def digest(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
snapshot=json.loads(Path('docs/team/baseline-t10-m1/snapshot.json').read_text(encoding='utf-8-sig'))
inputs=['docs/team/baseline-t10-m1/snapshot.json']+[v['snapshot'] for v in snapshot['files'].values()]+[(P/f).as_posix() for f in ['model.py','test_model.py','config.json','parameter_registry.json','replay.py']]
manifest={'label':'SYNTHETIC_UNCALIBRATED','seed':None,'commands':['python -B -X utf8 research/garment_prediction/test_model.py','python -B -X utf8 research/garment_prediction/replay.py'],'inputs_sha256':{p:digest(p) for p in inputs},'artifacts_sha256':{p.as_posix():digest(p)}}
(OUT/'manifest.json').write_bytes((json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
print('Generated',p,'and manifest')

import json,hashlib
from pathlib import Path
from coupled import Zone,Drive,rho_sat,run_segments
from model import state
P=Path('research/garment_prediction/coupled_m5');O=P/'results'
def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):Path(p).write_bytes((json.dumps(x,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
inputs={}
for path,key in [('docs/team/baseline-t10-m5/snapshot.json','inputs_sha256'),('docs/verification/T10-M1-tests.json','reviewed_sha256'),('docs/verification/T10-M2a-tests.json','reviewed_sha256'),('docs/verification/T10-M2b-tests.json','reviewed_sha256'),('docs/verification/T10-M3-tests.json','reviewed_sha256'),('docs/verification/T10-M4-tests.json','reviewed_sha256')]:
 for name,expected in read(path)[key].items():
  if sha(name)!=expected:raise RuntimeError('accepted input changed '+name)
  inputs[name]=expected
 inputs[path]=sha(path)
for f in ('coupled.py','test_coupled.py','config.json','replay.py'):inputs[(P/f).as_posix()]=sha(P/f)
c=read(P/'config.json');z=[Zone(**c['ordinary']),Zone(**c['slow'])];segments=[(seg['end_s'],Drive(**seg['drive'])) for seg in c['segments']];d=segments[0][1];second=segments[1][1];initial=c['initial_state'];a=state(z[0],initial['x_kg_kg'][0],initial['garment_c'][0]);b=state(z[1],initial['x_kg_kg'][1],initial['garment_c'][1]);q=c['coupling'];y=(a[0],a[1],b[0],b[1],q['volume_m3']*initial['gas_rh']*rho_sat(d.air_c),initial['sensor_rh_state'],0.,0.)
result={'initial_drive':vars(d),'second_drive':vars(second),'state_order':['water0_kg','U0_J','water1_kg','U1_J','gas_water_kg','sensor_RH_fraction','external_water_integral_kg','external_energy_integral_J'],'result':run_segments(z,q,y,segments,c['step_s'],d)}
O.mkdir(exist_ok=True);out=O/'example.json';save(out,result);save(O/'manifest.json',{'label':'SYNTHETIC_UNCALIBRATED','seed':None,'commands':['python -B -X utf8 research/garment_prediction/coupled_m5/test_coupled.py','python -B -X utf8 research/garment_prediction/coupled_m5/replay.py'],'inputs_sha256':dict(sorted(inputs.items())),'artifacts_sha256':{out.as_posix():sha(out)},'limits':'synthetic; platform floating-point differences possible'})
print('Verified inputs',len(inputs),'segment diagnostics',result['result']['segments'])

import hashlib,json,math
from pathlib import Path
from physics import simulate,rho_sat
from consumer import evaluate
P=Path('research/garment_prediction/observation_m3');O=P/'results'
def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):Path(p).write_bytes((json.dumps(x,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
inputs={}
for path,key in [('docs/team/baseline-t10-m3/snapshot.json','inputs_sha256'),('docs/verification/T10-M1-tests.json','reviewed_sha256'),('docs/verification/T10-M2a-tests.json','reviewed_sha256'),('docs/verification/T10-M2b-tests.json','reviewed_sha256')]:
 for name,expected in read(path)[key].items():
  if sha(name)!=expected:raise RuntimeError('changed accepted input '+name)
  inputs[name]=expected
 inputs[path]=sha(path)
for path in ['docs/team/assignments/algorithm-t10-m3.md',*[(P/f).as_posix() for f in ('physics.py','consumer.py','test_model.py','config.json','replay.py')]]:inputs[path]=sha(path)
c=read(P/'config.json');p=c['parameters'];normal=simulate(p,c['x0'],c['rh0'],c['sensor_rh0'],c['duration_s'],c['dt']);alternative=simulate({**p,'xb':p['xb']/2},c['x0']/2,c['rh0'],c['sensor_rh0'],c['duration_s'],c['dt'])
maxdiff=max(abs(a['local_rh']-b['local_rh']) for a,b in zip(normal['samples'],alternative['samples']))
def obs(v,u,t):return {'value':v,'unit':u,'measured_at_s':t,'received_at_s':t,'uncertainty_abs':.2 if u=='degC' else .02,'source':'synthetic_input'}
def packet(sample):
 t=sample['time_s']; unknown={'state':'unknown','source':None,'coverage_start_s':None,'coverage_end_s':None,'issued_at_s':None}
 return {'now_s':t,'installation_id':'synthetic','contact':dict(unknown),'charging_heat':dict(unknown),'samples':{'ambient_temp':obs(sample['ambient_temp_c'],'degC',t),'ambient_rh':obs(sample['ambient_rh'],'fraction',t),'local_temp':obs(sample['local_temp_c'],'degC',t),'local_rh':obs(sample['local_rh'],'fraction',t)}}
packets=[packet(normal['samples'][0]),packet(normal['samples'][-1])];policy=read('research/garment_prediction/observation_m2/config.json')['policy'];gate=evaluate(packets,policy,c['gate_tolerances']['temp_delta_c'],c['gate_tolerances']['rh_delta'],{'status':'unknown','reference_time_s':None,'issued_at_s':None,'initial_state_rh':None,'uncertainty_abs':None,'source':None},c['gate_tolerances']['local_temp_delta_c'],c['gate_tolerances']['temp_offset_c'])
aw=-math.expm1(-c['x0']/p['xb']);ambient=p['ambient_rh']*rho_sat(p['temperature_c']);k=p['area_open_m2']/p['r_open_s_m']+p['area_cover_m2']/p['r_cover_s_m']
sensitivity=[{'surface_c':t,'saturation_kg_m3':rho_sat(t),'initial_evap_kg_s':k*(aw*rho_sat(t)-ambient)} for t in (24.5,25,25.5)]
result={'label':'SYNTHETIC_UNCALIBRATED','seed':None,'collection_authorized':False,'baseline':normal,'alternative':alternative,'comparison':{'max_rh_difference_fraction':maxdiff,'different_initial_water_kg':[p['dry_kg']*c['x0'],p['dry_kg']*c['x0']/2]},'hypothetical_surface_temperature_sensitivity':sensitivity,'latent_energy_proxy_j':(normal['producer_only']['initial_water_kg']-normal['producer_only']['water_kg'])*2500000,'observable_packets_only':packets,'consumer_gate':gate}
O.mkdir(exist_ok=True);artifact=O/'simulation.json';save(artifact,result)
save(O/'manifest.json',{'label':'SYNTHETIC_UNCALIBRATED','seed':None,'commands':['python -B -X utf8 research/garment_prediction/observation_m3/test_model.py','python -B -X utf8 research/garment_prediction/observation_m3/replay.py'],'inputs_sha256':dict(sorted(inputs.items())),'artifacts_sha256':{artifact.as_posix():sha(artifact)},'limits':'same-platform JSON reproducibility only; material and geometry parameters uncalibrated'})
print('verified inputs',len(inputs),'RH difference',maxdiff,'initial heat proxy J',result['latent_energy_proxy_j'],'temperature sensitivity',sensitivity,'gate',gate)

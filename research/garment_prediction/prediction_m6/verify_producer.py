"""Offline producer verification; never passed into the online consumer."""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'coupled_m5'))
from coupled import Zone,Drive,integrate
P=Path('research/garment_prediction/prediction_m6')
def verify():
 f=json.loads((P/'producer_fixture.json').read_text(encoding='utf-8'));c=json.loads((P/'config.json').read_text(encoding='utf-8'));z=[Zone(**x) for x in f['zones']];d=Drive(**f['drive']);y=tuple(f['initial_state'])
 actual=[integrate(z,d,f['coupling'],y,t,f['dt_s'])['state'][5]+f['sensor_bias_fraction'] for t in f['times_s']]
 recorded=[x['samples']['local_rh']['value'] for x in c['request']['observations']]
 if any(abs(a-b)>1e-12 for a,b in zip(actual,recorded)):raise ValueError('producer fixture mismatch')
 return {'generator_only_verified':True,'count':len(actual),'tolerance_rh_fraction':1e-12}
if __name__=='__main__':print(verify())

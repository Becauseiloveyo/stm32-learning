"""Deterministic conditional non-identifiability witness, not sensor simulation."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from model import Zone, Drive, state, run_two
from contract import observe, project_packet, LABEL

def build(config, m1):
    central=Zone(**m1['ordinary']); other=Zone(**m1['slow']); d=Drive(**m1['drive'])
    cs=config['central_condition']; results=[]
    for n,x in enumerate(config['hidden_slow_x_initial_kg_kg']):
        ys=[state(central,cs['central_x_initial_kg_kg'],cs['central_initial_temperature_c']),state(other,x,cs['central_initial_temperature_c'])]
        times=(0, config['duration_s']/2, config['duration_s'])
        states=[run_two([central,other],[d,d],ys,t,config['step_s']) for t in times]
        forward=states[-1]
        # Ideal local states are producer-only; they are NOT sensor readings.
        ideal_central=[{'time_s':t,'water_kg':z[0]['water_kg'], 'x_kg_kg':z[0]['water_kg']/central.dry_kg, 'temperature_c':z[0]['temperature_c']} for t,z in zip(times,states)]
        # Manual packet is an independent interface example, not a physical RH curve.
        envelope={**config['packet'],'hidden_state':forward,'hidden_material_tag':'case-'+str(n),'future_weather':{'unavailable':n}}
        public=project_packet(envelope)
        result=observe(public,config['policy'])
        results.append({'producer_only':{'slow_x_initial_kg_kg':x,'ideal_central_trajectory':ideal_central,'zones':forward},'public_observation':public,'consumer':result})
    return {'label':LABEL,'seed':None,'assumption':config['assumption'],'cases':results}

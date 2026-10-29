import json, unittest, copy, math, sys
from pathlib import Path
from physics import simulate, rho_sat
from consumer import evaluate as evaluate_raw
P=Path('research/garment_prediction/observation_m3');C=json.loads((P/'config.json').read_text(encoding='utf-8-sig'));P2=Path('research/garment_prediction/observation_m2/config.json');C2=json.loads(P2.read_text(encoding='utf-8-sig'))
UNKNOWN={'status':'unknown','reference_time_s':None,'issued_at_s':None,'initial_state_rh':None,'uncertainty_abs':None,'source':None}
KNOWN={'status':'externally_characterized','reference_time_s':580,'issued_at_s':595,'initial_state_rh':.5,'uncertainty_abs':.02,'source':'synthetic_external'}
def evaluate(packets,policy,temp_delta_c,rh_delta,history):
 return evaluate_raw(packets,policy,temp_delta_c,rh_delta,history,C['gate_tolerances']['local_temp_delta_c'],C['gate_tolerances']['temp_offset_c'])
class Tests(unittest.TestCase):
 def test_zero_cover(self):
  p={**C['parameters'],'area_cover_m2':0,'flow_m3_s':0};r=simulate(p,.6,.5,.5,60,.1)
  self.assertAlmostEqual(r['producer_only']['air_density_kg_m3'],rho_sat(25)*.5,places=14)
 def test_closed_total(self):
  p={**C['parameters'],'area_open_m2':0,'flow_m3_s':0,'wall_source_kg_s':0}; r=simulate(p,.6,.5,.5,60,.1)['producer_only']
  self.assertAlmostEqual(r['total_water_change_kg'],0,places=13);self.assertAlmostEqual(r['balance_residual_kg'],0,places=13)
 def test_external_wall_budget(self):
  p={**C['parameters'],'area_open_m2':0,'area_cover_m2':0,'flow_m3_s':0,'wall_source_kg_s':1e-10};r=simulate(p,.6,.5,.5,60,.1)['producer_only']
  self.assertAlmostEqual(r['total_water_change_kg'],6e-9,places=13)
 def test_analytic_gas_relaxation(self):
  p={**C['parameters'],'area_cover_m2':0,'area_open_m2':0,'sensor_bias_rh':0};r=simulate(p,.6,.2,.2,60,.1)['producer_only']
  expected=rho_sat(25)*(.5+(.2-.5)*math.exp(-p['flow_m3_s']/p['volume_m3']*60))
  self.assertAlmostEqual(r['air_density_kg_m3'],expected,places=12)
  measured=simulate(p,.6,.2,.2,60,.1)['samples'][-1]['local_rh']; lam=p['flow_m3_s']/p['volume_m3']; mu=1/p['sensor_tau_s']
  expected_sensor=.5+(.2-.5)*math.exp(-mu*60)+(.2-.5)*mu/(mu-lam)*(math.exp(-lam*60)-math.exp(-mu*60))
  self.assertAlmostEqual(measured,expected_sensor,places=10)
 def test_coupled_equilibrium(self):
  p={**C['parameters'],'sensor_bias_rh':0}; x=-p['xb']*math.log(1-p['ambient_rh']); r=simulate(p,x,p['ambient_rh'],p['ambient_rh'],60,.1)
  self.assertAlmostEqual(r['producer_only']['water_kg'],p['dry_kg']*x,places=13);self.assertAlmostEqual(r['samples'][-1]['local_rh'],p['ambient_rh'],places=12)
 def test_step_reference(self):
  p=C['parameters'];a=simulate(p,.6,.5,.5,60,.2);b=simulate(p,.6,.5,.5,60,.1);ref=simulate(p,.6,.5,.5,60,.01,'midpoint')
  self.assertLess(abs(b['samples'][-1]['local_rh']-ref['samples'][-1]['local_rh']),1e-6)
  self.assertLess(abs(b['producer_only']['water_kg']-ref['producer_only']['water_kg']),1e-8)
  self.assertLess(abs(b['producer_only']['balance_residual_kg']),abs(a['producer_only']['balance_residual_kg']))
 def test_steady_zero_exchange(self):
  p={**C['parameters'],'area_cover_m2':0,'area_open_m2':0,'flow_m3_s':0,'wall_source_kg_s':0,'sensor_bias_rh':0}
  r=simulate(p,.6,.5,.5,60,.1);self.assertAlmostEqual(r['samples'][-1]['local_rh'],.5)
 def test_gate(self):
  self.assertFalse(evaluate([],C2['policy'],.5,.05,UNKNOWN)['applicable']); self.assertEqual(evaluate([],C2['policy'],.5,.05,UNKNOWN)['mode'],'SYNTHETIC_UNCALIBRATED')
  packet=copy.deepcopy(C2['packet']); packet['samples']['local_temp']['value']=25.; later=copy.deepcopy(packet); later['now_s']=601; [v.update(measured_at_s=591) for v in later['samples'].values()]; policy=C2['policy'];self.assertTrue(evaluate([packet,later],policy,.5,.05,KNOWN)['applicable'])
  self.assertIn('sensor_history_unknown',evaluate([packet,later],policy,.5,.05,UNKNOWN)['reasons'])
  insufficient=copy.deepcopy(later); insufficient['contact']['coverage_start_s']=590
  self.assertIn('contact:history_uncovered',evaluate([packet,insufficient],policy,.5,.05,KNOWN)['reasons'])
  insufficient=copy.deepcopy(later); insufficient['charging_heat']['coverage_start_s']=590
  self.assertIn('charging_heat:history_uncovered',evaluate([packet,insufficient],policy,.5,.05,KNOWN)['reasons'])
  uncertain={**KNOWN,'uncertainty_abs':.1};self.assertIn('sensor_initial_uncertain',evaluate([packet,later],policy,.5,.05,uncertain)['reasons'])
  with self.assertRaises(ValueError):evaluate([packet,later],policy,.5,.05,{**KNOWN,'issued_at_s':602})
  after={**KNOWN,'reference_time_s':591};self.assertIn('history_reference_after_sample',evaluate([packet,later],policy,.5,.05,after)['reasons'])
  unknown=copy.deepcopy(packet);unknown['contact']={'state':'unknown','source':None,'coverage_start_s':None,'coverage_end_s':None,'issued_at_s':None};self.assertFalse(evaluate([packet,unknown],policy,.5,.05,KNOWN)['applicable'])
  missing=copy.deepcopy(packet);missing['samples']['local_rh']=None;self.assertFalse(evaluate([packet,missing],policy,.5,.05,KNOWN)['applicable'])
  change=copy.deepcopy(later);change['samples']['ambient_temp']['value']=26;self.assertIn('environment_changed_or_uncertain',evaluate([packet,change],policy,.5,.05,KNOWN)['reasons'])
  with self.assertRaises(ValueError):evaluate([packet],policy,float('nan'),.05,KNOWN)
  self.assertIn('nonmonotonic_time',evaluate([packet,packet],policy,.5,.05,KNOWN)['reasons'])
  polluted=copy.deepcopy(later);polluted['hidden_state']={'x':.6}
  with self.assertRaises(ValueError):evaluate([packet,polluted],policy,.5,.05,KNOWN)
 def test_observable_window_regressions(self):
  packet=copy.deepcopy(C2['packet']);packet['samples']['local_temp']['value']=25
  later=copy.deepcopy(packet);later['now_s']=601
  result=evaluate([packet,later],C2['policy'],.5,.05,KNOWN)
  self.assertFalse(result['applicable']);self.assertIn('local_temp:nonmonotonic_sample_time',result['reasons'])
  for v in later['samples'].values():v['measured_at_s']=591
  self.assertTrue(evaluate([packet,later],C2['policy'],.5,.05,KNOWN)['applicable'])
  for channel in ('ambient_temp','ambient_rh','local_temp','local_rh'):
   repeat=copy.deepcopy(later);repeat['samples'][channel]['measured_at_s']=590
   result=evaluate([packet,repeat],C2['policy'],.5,.05,KNOWN)
   self.assertIn(channel+':nonmonotonic_sample_time',result['reasons']);self.assertFalse(result['applicable'])
  near=copy.deepcopy(later);near['samples']['local_temp']['value']=25.09
  self.assertTrue(evaluate([packet,near],C2['policy'],.5,.05,KNOWN)['applicable'])
  a=copy.deepcopy(packet);b=copy.deepcopy(later);a['samples']['local_temp']['value']=40;b['samples']['local_temp']['value']=40
  result=evaluate([a,b],C2['policy'],.5,.05,KNOWN);self.assertFalse(result['applicable']);self.assertIn('local_ambient_temperature_mismatch_or_uncertain',result['reasons'])
  a['samples']['local_temp']['value']=20
  result=evaluate([a,b],C2['policy'],.5,.05,KNOWN);self.assertFalse(result['applicable']);self.assertIn('local_temperature_changed_or_uncertain',result['reasons'])
  b=copy.deepcopy(later);b['samples']['local_rh']['measured_at_s']=589
  self.assertIn('local_rh:nonmonotonic_sample_time',evaluate([packet,b],C2['policy'],.5,.05,KNOWN)['reasons'])
  b=copy.deepcopy(later);b['samples']['local_temp']['value']=25.11
  self.assertIn('local_temperature_changed_or_uncertain',evaluate([packet,b],C2['policy'],.5,.05,KNOWN)['reasons'])
 def test_nonunique(self):
  a=simulate(C['parameters'],.6,.5,.5,60,.1);b=simulate({**C['parameters'],'xb':.075},.3,.5,.5,60,.1)
  diff=max(abs(x['local_rh']-y['local_rh']) for x,y in zip(a['samples'],b['samples']))
  self.assertLess(diff,.001);self.assertGreater(abs(a['producer_only']['water_kg']-b['producer_only']['water_kg']),.02)
 def test_invalid_domain(self):
  for kwargs in [{'x0':-1},{'rh0':2},{'dt':0}]:
   k=dict(x0=.6,rh0=.5,sensor_rh0=.5,duration_s=60,dt=.1);k.update(kwargs)
   with self.assertRaises(ValueError):simulate(C['parameters'],**k)
  with self.assertRaises(ValueError):simulate({**C['parameters'],'temperature_c':51},.6,.5,.5,60,.1)
if __name__=='__main__':unittest.main(verbosity=2)


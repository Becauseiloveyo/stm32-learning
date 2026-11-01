import copy,json,math,sys,unittest
from pathlib import Path
from integrated import predict,trace,Zone,Drive
from model import state,rho_sat
C=json.loads(Path('research/garment_prediction/prediction_m6/config.json').read_text(encoding='utf-8'))
def base():return copy.deepcopy(C['request'])
def analytic():
 r=base();r['step_s']=10;r['target_x']=.3;r['candidates']=r['candidates'][:1];c=r['candidates'][0]
 for z in c['zones']:z.update(evap_m2=0,proj_m2=0,heat_m2=0,rad_m2=0,drain_per_s=.001)
 c['coupling'].update(area_cover_m2=0,flow_m3_s=0)
 z=[Zone(**x) for x in c['zones']];a=state(z[0],1,25);b=state(z[1],.8,25);y=list(c['variants'][0]['initial_state']);y[:4]=[a[0],a[1],b[0],b[1]];c['variants'][0]['initial_state']=y
 for p in r['observations']:p['samples']['local_rh'].update(value=.5,uncertainty_abs=.001)
 r['history_segments'][0]['drive']['solar_w_m2']=0;c['prior_drive']['solar_w_m2']=0
 r['futures']=r['futures'][:1];r['futures'][0]['horizon_s']=2500;r['futures'][0]['segments']=[{'end_s':2500,'drive':copy.deepcopy(c['prior_drive']),'available_at_s':0}]
 return r
class Tests(unittest.TestCase):
 def test_scenario(self):
  r=predict(base());self.assertEqual([x['status'] for x in r['rows']],['compatible','compatible','incompatible']);self.assertIsNone(r['upper_sample_s']);self.assertFalse(r['collection_authorized'])
 def test_analytic_time_and_full_state(self):
  r=analytic();out=predict(r);v=out['rows'][0]['variants'][0];self.assertEqual(v['status'],'compatible')
  exact=math.log(1/.3)/.001;self.assertEqual(v['future'][0]['first_sample_s'],1210);self.assertLessEqual(1210-exact,10)
  c=r['candidates'][0];z=[Zone(**x) for x in c['zones']];coupling=c['coupling'];d=Drive(**c['prior_drive']);start=tuple(c['variants'][0]['initial_state'])
  _,direct,_,_,_=trace(z,coupling,start,d,[(2500,d)],0,[],10,(-.01,.01))
  end=v['future'][0]['final_state'];self.assertTrue(all(abs(a-b)<1e-8 for a,b in zip(end,direct)))
 def test_contact_unknown_missing(self):
  for count in (0,1):
   r=base();r['observations']=r['observations'][:count];self.assertTrue(all(x['status']=='information_insufficient' for x in predict(r)['rows']))
  r=base();r['observations'][-1]['contact']={'state':'unknown','source':None,'coverage_start_s':None,'coverage_end_s':None,'issued_at_s':None}
  self.assertTrue(all(x['status']=='information_insufficient' for x in predict(r)['rows']))
  r=base();r['observations'][0]['samples']['local_rh']=None;self.assertTrue(all(x['status']=='information_insufficient' for x in predict(r)['rows']))
 def test_repeat_install_and_history(self):
  r=base();r['observations'][1]['samples']['local_rh']['measured_at_s']=10;self.assertTrue(all(x['status']=='information_insufficient' for x in predict(r)['rows']))
  r=base();r['observations'][1]['installation_id']='different';self.assertTrue(all(x['status']=='information_insufficient' for x in predict(r)['rows']))
  r=base();r['observations'][-1]['contact']['coverage_start_s']=10;self.assertTrue(all(x['status']=='information_insufficient' for x in predict(r)['rows']))
 def test_late_and_hidden(self):
  r=base();r['observations'][-1]['now_s']=21
  with self.assertRaises(ValueError):predict(r)
  r=base();r['assumptions']['issued_at_s']=21
  with self.assertRaises(ValueError):predict(r)
  r=base();r['true_future_weather']={}
  with self.assertRaises(ValueError):predict(r)
  r=base();r['candidates'][0]['hidden_truth']=1
  with self.assertRaises(ValueError):predict(r)
 def test_missing_wrong_and_model_failure(self):
  r=base();r['candidates']=r['candidates'][2:];self.assertEqual(predict(r)['set_status'],'coverage_or_model_mismatch')
  r=base();r['candidates'][0]['coupling']['volume_m3']=-1;o=predict(r);self.assertEqual(o['rows'][0]['status'],'calculation_failed');self.assertIsNone(o['upper_sample_s'])
  r=base();r['assumptions']={'status':'unknown','source':None,'issued_at_s':None,'structural_rh':None,'numeric_rh':None,'guard_rh':None,'temperature_error_c':None};self.assertTrue(all(x['status']=='information_insufficient' for x in predict(r)['rows']))
 def test_constant_bias_independent_reference(self):
  r=analytic();r['assumptions'].update(structural_rh=0,numeric_rh=0,guard_rh=0);r['candidates'][0]['bias_interval']=[-.04,.04]
  r['observations'][0]['samples']['local_rh']['value']=.51;r['observations'][1]['samples']['local_rh']['value']=.53
  # Analytic zero-exchange sensor = .5: intervals [.009,.011] and [.029,.031].
  self.assertEqual(predict(r)['rows'][0]['status'],'incompatible')
  r['observations'][1]['samples']['local_rh']['value']=.511
  self.assertEqual(predict(r)['rows'][0]['status'],'compatible')
 def test_dynamic_historical_boundary(self):
  r=base();c=r['candidates'][0];d1=Drive(**c['prior_drive']);d2=Drive(**{**vars(d1),'air_c':27})
  r['history_segments']=[{'end_s':15,'drive':vars(d1),'available_at_s':0},{'end_s':20,'drive':vars(d2),'available_at_s':15}]
  zones=[Zone(**x) for x in c['zones']];out,_,_,_,_=trace(zones,c['coupling'],tuple(c['variants'][0]['initial_state']),d1,[(15,d1),(20,d2)],0,[10,20],1,(-.01,.01))
  r['observations'][1]['samples']['ambient_temp']['value']=27;r['observations'][1]['samples']['local_temp']['value']=27
  for p in r['observations']:
   t=p['samples']['local_rh']['measured_at_s'];p['samples']['local_rh']['value']=out[t][0][5]+.005
  result=predict(r);self.assertEqual(result['rows'][0]['status'],'compatible')
 def test_future_cannot_change_history_filter(self):
  a=base();b=copy.deepcopy(a);b['futures'][0]['segments'][0]['drive']['wind_m_s']=5
  ra,rb=predict(a),predict(b)
  self.assertEqual([x['status'] for x in ra['rows']],[x['status'] for x in rb['rows']])
  self.assertEqual(ra['rows'][0]['variants'][0]['history_end_state'],rb['rows'][0]['variants'][0]['history_end_state'])
 def test_finite_response_variants(self):
  r=base();extra=copy.deepcopy(r['candidates'][0]['variants'][0]);extra['id']='second';extra['initial_state'][5]=.51;r['candidates'][0]['variants'].append(extra)
  out=predict(r);self.assertEqual(len(out['rows'][0]['variants']),2);self.assertNotEqual(out['rows'][0]['variants'][0]['id'],out['rows'][0]['variants'][1]['id'])
 def test_temperatures_and_environment(self):
  r=base();r['observations'][-1]['samples']['local_temp']['value']=30
  self.assertTrue(all(x['status']=='information_insufficient' for x in predict(r)['rows']))
 def test_all_joint_and_rewet(self):
  r=analytic();r['target_x']=1;r['futures'][0]['segments'][0]['drive']['rain_kg_s']=.001
  o=predict(r);v=o['rows'][0]['variants'][0];f=v['future'][0];self.assertEqual(f['first_sample_s'],20);self.assertEqual(f['remaining_sample_s'],0);self.assertEqual(f['status'],'calculation_failed');self.assertTrue(f['later_sample_above_target']);self.assertIsNone(o['upper_sample_s'])
 def test_identifiers_and_budget(self):
  r=base();r['candidates'].append(copy.deepcopy(r['candidates'][0]))
  with self.assertRaises(ValueError):predict(r)
  r=base();r['candidates'][0]['variants'].append(copy.deepcopy(r['candidates'][0]['variants'][0]))
  with self.assertRaises(ValueError):predict(r)
  r=base();r['futures'].append(copy.deepcopy(r['futures'][0]))
  with self.assertRaises(ValueError):predict(r)
  r=base();r['step_s']=1e-12
  with self.assertRaisesRegex(ValueError,'step budget'):predict(r)
 def test_hidden_envelope_isolated(self):
  original=base();a={'public':original,'truth':{'water':0.1},'actual_future':1};b={'public':copy.deepcopy(original),'truth':{'water':99},'actual_future':1000}
  self.assertEqual(predict(a['public']),predict(b['public']))
 def test_analytical_initial_and_future_time(self):
  r=analytic();r['target_x']=1.;out=predict(r);f=out['rows'][0]['variants'][0]['future'][0]
  self.assertEqual(f['first_sample_s'],20);self.assertEqual(f['remaining_sample_s'],0)
 def test_nonoverlap(self):
  r=analytic();c=r['candidates'][0];c['zones'][0]['dry_kg']=.01;c['zones'][1]['dry_kg']=1;c['zones'][1]['drain_per_s']=.0001
  z=[Zone(**x) for x in c['zones']];a=state(z[0],.4,25);b=state(z[1],.5,25);y=c['variants'][0]['initial_state'];y[:4]=[a[0],a[1],b[0],b[1]]
  r['futures'][0]['horizon_s']=8000;d=copy.deepcopy(c['prior_drive']);wet={**d,'rain_kg_s':.00001,'rain_c':25}
  r['futures'][0]['segments']=[{'end_s':400,'drive':d,'available_at_s':0},{'end_s':8000,'drive':wet,'available_at_s':0}]
  f=predict(r)['rows'][0]['variants'][0]['future'][0];self.assertEqual(f['status'],'not_reached')
if __name__=='__main__':unittest.main(verbosity=2)


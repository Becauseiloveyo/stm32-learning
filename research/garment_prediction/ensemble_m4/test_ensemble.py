import unittest,json,copy
from pathlib import Path
from ensemble import compare
C=json.loads(Path('research/garment_prediction/ensemble_m4/config.json').read_text(encoding='utf-8'))
def run(c):return compare(*[c[k] for k in ('packets','policy','history','gate','assumptions','candidates','as_of_s')])
def unknown():return {'status':'unknown','source':None,'issued_at_s':None,'structural_abs_rh':None,'numerical_abs_rh':None,'boundary_guard_rh':None,'dt_s':None}
class Checks(unittest.TestCase):
 def setUp(self):self.c=copy.deepcopy(C)
 def test_example(self):
  r=run(self.c);self.assertEqual([x['status'] for x in r['candidates']],['compatible','compatible','incompatible']);self.assertFalse(r['collection_authorized']);self.assertEqual(r['eta'],'unavailable')
 def test_information_insufficient(self):
  self.c['assumptions']=unknown();self.assertTrue(all(r['status']=='information_insufficient' for r in run(self.c)['candidates']))
  self.c=copy.deepcopy(C);self.c['candidates'][0]['discrete_support_complete']=False;self.assertEqual(run(self.c)['candidates'][0]['status'],'information_insufficient')
  self.c=copy.deepcopy(C);self.c['packets'][1]['samples']['local_rh']=None;self.assertTrue(all(r['status']=='information_insufficient' for r in run(self.c)['candidates']))
  self.c=copy.deepcopy(C);self.c['packets'][1]['samples']['local_rh']['measured_at_s']=590;self.assertFalse(run(self.c)['gate']['applicable'])
  self.c=copy.deepcopy(C);self.c['history']={'status':'unknown','reference_time_s':None,'issued_at_s':None,'initial_state_rh':None,'uncertainty_abs':None,'source':None};self.assertFalse(run(self.c)['gate']['applicable'])
 def test_heat_contact_environment(self):
  self.c['packets'][1]['contact']['coverage_start_s']=590;self.assertFalse(run(self.c)['gate']['applicable'])
  self.c=copy.deepcopy(C);self.c['packets'][1]['charging_heat']['coverage_start_s']=590;self.assertFalse(run(self.c)['gate']['applicable'])
  self.c=copy.deepcopy(C);self.c['packets'][1]['samples']['ambient_temp']['value']=27;self.assertTrue(all(r['status']=='information_insufficient' for r in run(self.c)['candidates']))
 def test_failure_retained(self):
  self.c['candidates'][0]['parameters']['volume_m3']=-1;r=run(self.c);self.assertEqual(r['candidates'][0]['status'],'calculation_failed');self.assertEqual(r['set_status'],'unresolved')
  self.c=copy.deepcopy(C);self.c['candidates'][0]['parameters']['sensor_tau_s']=.00001;r=run(self.c);self.assertEqual(r['candidates'][0]['status'],'calculation_failed')
  self.c=copy.deepcopy(C);self.c['candidates'][0]['initial_variants'][0]['x0']=float('nan');r=run(self.c);self.assertEqual(r['candidates'][0]['status'],'calculation_failed');self.assertEqual(r['set_status'],'unresolved')
  self.c=copy.deepcopy(C);self.c['candidates'][0]['initial_variants'][0]['sensor_rh0']=2;r=run(self.c);self.assertEqual(r['candidates'][0]['status'],'calculation_failed')
 def test_initial_uncertainty_variants(self):
  self.c['candidates'][0]['initial_variants']=[{'x0':.6,'rh0':.5,'sensor_rh0':.49},{'x0':.6,'rh0':.5,'sensor_rh0':.51}]
  r=run(self.c)['candidates'][0];self.assertEqual(r['status'],'compatible');self.assertEqual(r['matching_variants'],2)
 def test_missing_candidate_set(self):
  self.c['candidates']=self.c['candidates'][2:];r=run(self.c);self.assertEqual(r['set_status'],'model_or_candidate_coverage_mismatch');self.assertEqual(r['candidates'][0]['status'],'incompatible')
 def test_independent_analytic_constant_bias(self):
  self.c['candidates']=self.c['candidates'][:1];p=self.c['candidates'][0]['parameters'];p.update(area_open_m2=0,area_cover_m2=0,flow_m3_s=0,wall_source_kg_s=0)
  self.c['candidates'][0]['initial_variants']=[{'x0':.6,'rh0':.5,'sensor_rh0':.5}];self.c['candidates'][0]['bias_interval_rh']=[0,.04]
  self.c['assumptions'].update(structural_abs_rh=0,numerical_abs_rh=0,boundary_guard_rh=.001)
  for packet,val in zip(self.c['packets'],[.51,.53]):packet['samples']['local_rh'].update(value=val,uncertainty_abs=.001)
  # Analytic: zero exchange and initial air=sensor=.5 gives prediction .5 for every t.
  # Required constant bias intervals [.009,.011] and [.029,.031] are disjoint.
  self.assertEqual(run(self.c)['candidates'][0]['status'],'incompatible')
  self.c['packets'][1]['samples']['local_rh']['value']=.511
  self.assertEqual(run(self.c)['candidates'][0]['status'],'compatible')
  self.c['packets'][1]['samples']['local_rh']['value']=.5125
  self.assertEqual(run(self.c)['candidates'][0]['status'],'information_insufficient')
 def test_local_temperature_and_bias_domain(self):
  for packet in self.c['packets']:
   packet['samples']['local_temp'].update(value=24.8,uncertainty_abs=.01)
   packet['samples']['ambient_temp'].update(value=25,uncertainty_abs=.01)
  r=run(self.c);self.assertTrue(r['gate']['applicable']);self.assertTrue(all(x['status']=='information_insufficient' for x in r['candidates']))
  self.c=copy.deepcopy(C);self.c['candidates']=self.c['candidates'][:1];p=self.c['candidates'][0]['parameters'];p.update(area_open_m2=0,area_cover_m2=0,flow_m3_s=0,wall_source_kg_s=0)
  self.c['candidates'][0]['initial_variants']=[{'x0':.6,'rh0':.5,'sensor_rh0':.5}];self.c['candidates'][0]['bias_interval_rh']=[0,.6]
  for packet in self.c['packets']:packet['samples']['local_rh'].update(value=.999,uncertainty_abs=.01)
  r=run(self.c)['candidates'][0];self.assertEqual(r['status'],'compatible');self.assertLessEqual(r['variant_results'][0]['common_bias_high'],.5)
  self.c['candidates'][0]['bias_interval_rh']=[.55,.6]
  self.assertEqual(run(self.c)['candidates'][0]['status'],'calculation_failed')
  self.c['candidates'][0]['bias_interval_rh']=[-.52,-.48]
  for packet in self.c['packets']:packet['samples']['local_rh'].update(value=.01,uncertainty_abs=.01)
  r=run(self.c)['candidates'][0];self.assertEqual(r['status'],'compatible');self.assertGreaterEqual(r['variant_results'][0]['common_bias_low'],-.5)
 def test_as_of_evidence_and_replay(self):
  original=run(self.c); earlier=copy.deepcopy(original)
  later=copy.deepcopy(self.c);later['as_of_s']=605;later['assumptions']['issued_at_s']=605
  self.assertEqual(original,earlier)
  with self.assertRaises(ValueError):
   later['as_of_s']=601;run(later)
  self.assertEqual(original,run(self.c))
 def test_time_and_hidden(self):
  self.c['as_of_s']=600
  with self.assertRaises(ValueError):run(self.c)
  self.c=copy.deepcopy(C);self.c['assumptions']['issued_at_s']=602
  with self.assertRaises(ValueError):run(self.c)
  self.c=copy.deepcopy(C);self.c['candidates'][0]['hidden_water']=.3
  with self.assertRaises(ValueError):run(self.c)
  self.c=copy.deepcopy(C);self.c['packets'][0]['future_actual_weather']=999
  with self.assertRaises(ValueError):run(self.c)
if __name__=='__main__':unittest.main(verbosity=2)

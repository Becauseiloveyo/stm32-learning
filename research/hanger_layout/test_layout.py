import unittest,json,copy
from pathlib import Path
from geometry import box,overlaps,evaluate
from energy import simulate
class Tests(unittest.TestCase):
 def test_box_boundary(self):
  self.assertFalse(overlaps([0,0,0,1,1,1],[1,0,0,2,1,1]));self.assertTrue(overlaps([0,0,0,1,1,1],[.9,0,0,2,1,1]))
  with self.assertRaises(ValueError):box([0,0,0,0,1,1])
 def test_layout_collisions_and_mutations(self):
  c=json.loads(Path('research/hanger_layout/config.json').read_text(encoding='utf-8'));a=c['layouts']['left_bar'];b=c['layouts']['right_high']
  self.assertIn(['main','garment'],evaluate(a)['unintended_intersections']);self.assertIn(['battery','reference_head'],evaluate(b)['unintended_intersections']);self.assertTrue(evaluate(c['layouts']['right_high_adjusted'])['no_unintended_intersections'])
  checks={'hook_block':'hook_clear','neck_block':'neck_clear','neighbor_block':'neighbor_clear','no_central_contact':'central_contact_proxy','vent_block':'reference_vent_clear','rain_shadow':'rain_exposure_clear','plug_block':'port_insertion_clear'}
  for mutation,criterion in checks.items():
   with self.subTest(mutation=mutation):
    x=copy.deepcopy(a);x.update(c['failure_mutations'][mutation]);self.assertFalse(evaluate(x)[criterion])
  x=copy.deepcopy(c['layouts']['right_high_adjusted']);x.update(c['targeted_failure_cases']['neighbor_negative_adjusted']['mutation']);self.assertFalse(evaluate(x)['neighbor_clear'])
  x=copy.deepcopy(a);x['central_probe']=[80,5,-5,90,15,5];self.assertFalse(evaluate(x)['central_contact_proxy'])
  x=copy.deepcopy(a);x['battery']=[-10,160,-5,10,190,5];self.assertFalse(evaluate(x)['hook_clear'])
  x=copy.deepcopy(a);x['battery']=[-10,100,-5,10,130,5];self.assertFalse(evaluate(x)['neck_clear'])
 def test_depletion(self):
  r=simulate(1,1,1,1,0,.1,1,[],[],100);self.assertAlmostEqual(r['final_j'],3590);self.assertAlmostEqual(r['energy_residual_j'],0)
 def test_charge_full_and_unplug(self):
  e=[{'start_s':10,'end_s':20,'input_w':1,'efficiency':.5}]
  r=simulate(1/3600,1,1,0,0,0,1,[],e,30,include_trace=True);self.assertAlmostEqual(next(x['full_at_s'] for x in r['trace'] if x['full_at_s'] is not None),12);self.assertAlmostEqual(r['charge_after_efficiency_j'],5);self.assertAlmostEqual(r['final_j'],1);self.assertAlmostEqual(r['curtailed_j'],4);self.assertAlmostEqual(r['energy_residual_j'],0)
 def test_baseline_unserved(self):
  r=simulate(1/3600,1,1,1,0,1,1,[],[],3);self.assertAlmostEqual(r['baseline_unserved_j'],2);self.assertEqual(r['final_j'],0);self.assertAlmostEqual(r['first_baseline_unserved_s'],1);self.assertAlmostEqual(r['energy_residual_j'],0)
 def test_future_charge_not_borrowed(self):
  a=[{'id':'m','period_s':5,'energy_load_j':1}];e=[{'start_s':6,'end_s':8,'input_w':1,'efficiency':1}]
  r=simulate(1/3600,1,1,0,0,0,1,a,e,10);self.assertEqual(r['skipped']['m'],1);self.assertEqual(r['performed']['m'],1);self.assertAlmostEqual(r['final_j'],0);self.assertAlmostEqual(r['curtailed_j'],1)
 def test_simultaneous_boundaries_and_efficiency(self):
  a=[{'id':'m','period_s':5,'energy_load_j':.4}];e=[{'start_s':0,'end_s':5,'input_w':.4,'efficiency':.5}]
  r=simulate(2/3600,1,1,0,0,0,.5,a,e,5);self.assertAlmostEqual(r['charge_after_efficiency_j'],1);self.assertAlmostEqual(r['actions_used_j'],.8);self.assertAlmostEqual(r['final_j'],.2);self.assertAlmostEqual(r['energy_residual_j'],0)
 def test_event_reminder_no_future_borrow(self):
  event=[{'id':'rain_event_wifi','time_s':3,'energy_load_j':2}]
  charge=[{'start_s':5,'end_s':7,'input_w':1,'efficiency':1}]
  r=simulate(1/3600,1,1,1,0,0,1,[],charge,8,event_actions=event)
  self.assertEqual(r['skipped']['rain_event_wifi'],1);self.assertEqual(r['first_skipped_s']['rain_event_wifi'],3)
 def test_invalid(self):
  with self.assertRaises(ValueError):simulate(1,1,1,1,0,0,1,[],[{'start_s':0,'end_s':3,'input_w':1,'efficiency':1},{'start_s':2,'end_s':4,'input_w':1,'efficiency':1}],5)
if __name__=='__main__':unittest.main(verbosity=2)

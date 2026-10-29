import json,math,unittest,copy
from pathlib import Path
from eta import calculate
P=Path('research/garment_prediction/estimation_m2b'); C=json.loads((P/'config.json').read_text(encoding='utf-8'))
def base():
    z={'dry_kg':.1,'evap_m2':0,'proj_m2':0,'heat_m2':0,'rad_m2':0,'xb':.15,'resistance_s_m':140.,'drain_per_s':.001,'alpha':0.,'emissivity':0.}
    d={'air_c':25,'rh':0,'wind_m_s':0,'solar_w_m2':0,'surround_c':25,'rain_kg_s':0,'rain_c':25}
    return {'members':[{'id':'a','zones':[z,copy.deepcopy(z)],'x0':[.8,.6],'temperature_c':[25,25]}],'scenarios':[{'id':'dry','segments':[{'end_s':3000,'drives':[d,copy.deepcopy(d)]}]}],'target_x':.3,'step_s':10,'computed_at_s':0}
class Checks(unittest.TestCase):
    def test_analytic(self):
        r=base(); r['members'][0]['zones'][1]['drain_per_s']=.0005
        out=calculate(r); case=out['cases'][0]; exact=max(math.log(.8/.3)/.001,math.log(.6/.3)/.0005)
        self.assertLess(case['previous_sample_s'],exact); self.assertGreaterEqual(case['first_joint_sample_s'],exact)
        self.assertLessEqual(case['first_joint_sample_s']-exact,10)
        r['step_s']=1; fine=calculate(r)['cases'][0]['first_joint_sample_s']; self.assertLessEqual(fine-exact,1);self.assertLessEqual(abs(fine-case['first_joint_sample_s']),10)
    def test_rain_drain_equilibrium(self):
        r=base();r['members'][0]['x0']=[.8,.8]
        for d in r['scenarios'][0]['segments'][0]['drives']: d['rain_kg_s']=.00002
        r['scenarios'][0]['segments'][0]['end_s']=10000
        exact=math.log((.8-.2)/(.3-.2))/.001
        out=calculate(r); t=out['cases'][0]['first_joint_sample_s']
        self.assertGreaterEqual(t,exact); self.assertLessEqual(t-exact,10)
        for target in (.2,.1):
            r['target_x']=target; out=calculate(r)
            self.assertEqual(out['cases'][0]['status'],'not_reached_within_horizon'); self.assertIsNone(out['upper_sample_s'])
    def test_initial_and_unknown(self):
        r=base(); r['members'][0]['x0']=[.2,.2]; r['scenarios'][0]['segments'][0]['end_s']=100
        r['members'].append({**copy.deepcopy(r['members'][0]),'id':'wet','x0':[1,1]})
        out=calculate(r); self.assertEqual(out['cases'][0]['first_joint_sample_s'],0);self.assertEqual(out['cases'][1]['status'],'not_reached_within_horizon');self.assertIsNone(out['upper_sample_s']);self.assertEqual(out['unresolved_count'],1)
    def test_rewet_no_false_max(self):
        r=base(); m=r['members'][0]; m['x0']=[.4,.5];m['zones'][1]['drain_per_s']=.0001
        dry=r['scenarios'][0]['segments'][0]['drives']; rain=copy.deepcopy(dry); rain[0]['rain_kg_s']=.0001
        r['scenarios'][0]['segments']=[{'end_s':400,'drives':dry},{'end_s':6000,'drives':rain}]
        out=calculate(r); self.assertEqual(out['cases'][0]['status'],'not_reached_within_horizon'); self.assertIsNone(out['upper_sample_s'])
        t=math.log(.5/.3)/.0001; x1=.4*math.exp(-.001*t)+1*(1-math.exp(-.001*(t-400)))
        self.assertGreater(x1,.3);self.assertLess(math.log(.4/.3)/.001,400)
    def test_rewet_after_reaching(self):
        r=base();r['members'][0]['x0']=[.2,.2]; dry=r['scenarios'][0]['segments'][0]['drives']; rain=copy.deepcopy(dry)
        for d in rain: d['rain_kg_s']=.0001
        r['scenarios'][0]['segments']=[{'end_s':100,'drives':dry},{'end_s':1000,'drives':rain}]
        case=calculate(r)['cases'][0];self.assertEqual(case['first_joint_sample_s'],0);self.assertTrue(case['later_sample_above_target'])
    def test_failure_kept(self):
        r=base();bad=copy.deepcopy(r['scenarios'][0]);bad['id']='bad';bad['segments'][0]['drives'][0]['air_c']=60;r['scenarios'].append(bad)
        o=calculate(r);self.assertEqual(o['cases'][1]['status'],'outside_domain_or_invalid_member');self.assertIsNone(o['upper_sample_s']);self.assertEqual(len(o['cases']),2)
        r=base();r['step_s']=1e6;r['scenarios'][0]['segments'][0]['end_s']=1e6;o=calculate(r);self.assertEqual(o['cases'][0]['status'],'numerical_step_failure');self.assertIsNone(o['upper_sample_s'])
    def test_invalid_and_no_leak(self):
        for key,val in [('step_s',0),('target_x',float('nan')),('computed_at_s',-1),('future_actual_weather',{})]:
            r=base();r[key]=val
            with self.assertRaises(ValueError):calculate(r)
        r=base();r['members'][0]['hidden_truth']={'x':1}
        with self.assertRaises(ValueError):calculate(r)
        r=base();r['scenarios'][0]['segments'][0]['end_s']=0
        with self.assertRaises(ValueError):calculate(r)
    def test_config_ensemble(self):
        o=calculate(C['request']);self.assertEqual(len(o['cases']),12);self.assertEqual(o['mode'],'scenario_only');self.assertEqual(o['observation_mapping'],'unavailable');self.assertFalse(o['collection_authorized'])
if __name__=='__main__':unittest.main(verbosity=2)

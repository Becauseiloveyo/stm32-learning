import json, unittest
from copy import deepcopy
from pathlib import Path
from contract import observe, project_packet
from witness import build
ROOT=Path('research/garment_prediction')
C=json.loads((ROOT/'observation_m2/config.json').read_text(encoding='utf-8-sig'))
M=json.loads((ROOT/'config.json').read_text(encoding='utf-8-sig'))
def ev(state,start=None,end=None,issued=None):
    return {'state':state,'source':None if state=='unknown' else 'operator','coverage_start_s':start,'coverage_end_s':end,'issued_at_s':issued}
class ContractTests(unittest.TestCase):
    def setUp(self): self.packet=deepcopy(C['packet']); self.policy=deepcopy(C['policy'])
    def apply(self): return observe(self.packet,self.policy)
    def test_good_is_local_only(self):
        o=self.apply(); self.assertTrue(o['local_record_allowed']); self.assertEqual(o['whole_garment_state'],'unknown'); self.assertFalse(o['collection_authorized'])
    def test_contact_states(self):
        for st in ('not_contact','unknown'):
            p=deepcopy(self.packet); p['contact']=ev(st) if st=='unknown' else ev(st,580,595,595)
            o=observe(p,self.policy); self.assertFalse(o['local_record_allowed']); self.assertTrue(o['diagnostic_only']); self.assertEqual(o['contact_state_effective'],st)
    def test_stale_contact(self):
        self.packet['contact']=ev('confirmed',0,100,100)
        self.assertEqual(self.apply()['contact_state_effective'],'unknown'); self.assertFalse(self.apply()['local_record_allowed'])
    def test_missing_stale_delay_and_error(self):
        self.packet['samples']['local_rh']=None
        self.assertIn('local_rh:missing',self.apply()['quality_reasons'])
        self.packet=deepcopy(C['packet']); self.packet['samples']['local_temp']['measured_at_s']=400; self.packet['samples']['local_temp']['received_at_s']=401
        self.assertIn('local_temp:stale',self.apply()['quality_reasons'])
        self.packet=deepcopy(C['packet']); self.packet['samples']['local_temp']['measured_at_s']=560; self.packet['samples']['local_temp']['received_at_s']=590
        self.assertIn('local_temp:delayed',self.apply()['quality_reasons'])
        self.packet=deepcopy(C['packet']); self.packet['samples']['local_rh']['uncertainty_abs']=0.06
        self.assertIn('local_rh:uncertain',self.apply()['quality_reasons']); self.assertFalse(self.apply()['local_record_allowed'])
    def test_sample_time_skew(self):
        self.packet['samples']['local_temp']['measured_at_s']=550
        self.packet['samples']['local_temp']['received_at_s']=551
        self.assertIn('samples:time_skew',self.apply()['quality_reasons'])
        self.assertFalse(self.apply()['local_record_allowed'])
    def test_evidence_coverage_before_and_after(self):
        for key,st,reason in [('contact','confirmed','contact:uncovered'),('charging_heat','no_known_interference','charging:uncovered')]:
            for start,end in [(591,595),(595,595),(580,589)]:
                with self.subTest(key=key,start=start,end=end):
                    self.packet=deepcopy(C['packet']); self.packet[key]=ev(st,start,end,595)
                    o=self.apply(); self.assertFalse(o['local_record_allowed']); self.assertIn(reason,o['quality_reasons'])
                    if key=='contact': self.assertEqual(o['contact_state_effective'],'unknown')
    def test_partial_coverage(self):
        self.packet['samples']['local_temp']['measured_at_s']=588
        self.packet['samples']['local_temp']['received_at_s']=589
        for key,st,reason in [('contact','confirmed','contact:uncovered'),('charging_heat','no_known_interference','charging:uncovered')]:
            p=deepcopy(self.packet); p[key]=ev(st,589,595,595)
            out=observe(p,self.policy); self.assertIn(reason,out['quality_reasons']); self.assertFalse(out['local_record_allowed'])
    def test_evidence_exact_boundary_and_retrospective(self):
        for start,end,issued in [(590,590,590),(590,590,595),(580,590,595),(590,595,595)]:
            p=deepcopy(self.packet)
            p['contact']=ev('confirmed',start,end,issued); p['charging_heat']=ev('no_known_interference',start,end,issued)
            self.assertTrue(observe(p,self.policy)['local_record_allowed'])
    def test_invalid_evidence_intervals(self):
        for key,st in [('contact','confirmed'),('charging_heat','no_known_interference')]:
            for start,end,issued in [(595,590,595),(580,596,595),(580,601,601),(580,590,601),(-1,590,595)]:
                with self.subTest(key=key,interval=(start,end,issued)):
                    p=deepcopy(self.packet); p[key]=ev(st,start,end,issued)
                    with self.assertRaises(ValueError): observe(p,self.policy)
            p=deepcopy(self.packet); p[key]=ev('unknown',580,590,595)
            with self.assertRaises(ValueError): observe(p,self.policy)
    def test_not_contact_distinct_from_unknown(self):
        self.packet['contact']=ev('not_contact',580,595,595)
        o=self.apply(); self.assertEqual(o['contact_state_effective'],'not_contact'); self.assertFalse(o['local_record_allowed'])
        self.packet['contact']=ev('not_contact',591,595,595)
        o=self.apply(); self.assertEqual(o['contact_state_effective'],'unknown'); self.assertIn('contact:uncovered',o['quality_reasons'])
        self.packet['samples']['local_temp']['measured_at_s']=588; self.packet['samples']['local_temp']['received_at_s']=589
        self.packet['contact']=ev('not_contact',589,595,595)
        o=self.apply(); self.assertEqual(o['contact_state_effective'],'unknown'); self.assertIn('contact:not_contact_evidenced',o['quality_reasons'])
    def test_heat(self):
        for st in ('known_interference','unknown'):
            self.packet['charging_heat']=ev(st) if st=='unknown' else ev(st,580,595,595)
            self.assertFalse(self.apply()['local_record_allowed'])
        self.packet['charging_heat']=ev('no_known_interference',0,1,1)
        self.assertIn('charging:stale',self.apply()['quality_reasons'])
    def test_invalid(self):
        cases=[('value',float('nan')),('value',1.1),('uncertainty_abs',-1),('unit','percent'),('received_at_s',601),('measured_at_s',593),('source','truth')]
        for k,v in cases:
            with self.subTest(k=k,v=v):
                self.packet=deepcopy(C['packet']); self.packet['samples']['local_rh'][k]=v
                with self.assertRaises(ValueError): self.apply()
        self.packet=deepcopy(C['packet']); self.packet['contact']['source']='hidden_truth'
        with self.assertRaises(ValueError): self.apply()
        self.packet=deepcopy(C['packet']); self.packet['hidden_state']={'x':1}
        with self.assertRaises(ValueError): self.apply()
        self.packet=deepcopy(C['packet']); self.packet['samples']['local_rh']['hidden_state']=1
        with self.assertRaises(ValueError): self.apply()
        self.packet=deepcopy(C['packet']); self.packet['now_s']=True
        with self.assertRaises(ValueError): self.apply()
    def test_hidden_projection(self):
        a={**self.packet,'hidden_state':{'water':.1},'material':'cotton','future_weather':[3]}; b={**self.packet,'hidden_state':{'water':9},'material':'other','future_weather':[900]}
        self.assertEqual(project_packet(a),project_packet(b)); self.assertEqual(observe(project_packet(a),self.policy),observe(project_packet(b),self.policy))
    def test_nonidentifiable_witness(self):
        r=build(C,M); a,b=r['cases']
        self.assertEqual(a['public_observation'],b['public_observation']); self.assertEqual(a['consumer'],b['consumer'])
        self.assertEqual(a['producer_only']['zones'][0],b['producer_only']['zones'][0])
        self.assertEqual(a['producer_only']['ideal_central_trajectory'],b['producer_only']['ideal_central_trajectory'])
        self.assertNotIn('ideal_central_trajectory',a['public_observation'])
        self.assertGreater(abs(a['producer_only']['zones'][1]['water_kg']-b['producer_only']['zones'][1]['water_kg']),.05)
        self.assertEqual(a['consumer']['whole_garment_state'],'unknown'); self.assertFalse(a['consumer']['collection_authorized'])
if __name__ == '__main__': unittest.main(verbosity=2)

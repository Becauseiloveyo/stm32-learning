import copy
import json
import unittest
from pathlib import Path
from research.integration_t11.p1.controller import Controller, VERSION, event_id
from research.integration_t11.p1.fixtures import analytic_request

CONFIG = json.loads(Path('research/garment_prediction/prediction_m6/config.json').read_text(encoding='utf-8'))


class Harness:
    def __init__(self):
        self.c = Controller('device', 'boot', 'batch', 'synthetic')
        self.seq = 0

    def event(self, kind, t, payload, received=None, decision=None):
        self.seq += 1
        domain=copy.deepcopy(self.c.domain)
        e = dict(version=VERSION, id=event_id(domain,self.seq), seq=self.seq, domain=domain, occurred_s=t, received_s=t if received is None else received, kind=kind, payload=payload)
        return e, t if decision is None else decision

    def send(self, kind, t, payload, received=None, decision=None):
        e, now = self.event(kind, t, payload, received, decision)
        return self.c.apply(e, now)

    def setup(self, t=0):
        self.send('contact', t, dict(state='confirmed', source='synthetic_external', expires_s=t+120))
        self.send('heat', t, dict(state='clear', source='synthetic_external', expires_s=t+120))
        self.send('local', t, dict(state='dry', source='synthetic_external', expires_s=t+120, exposure='exposed'))

    def request(self, origin=0, target=5):
        req = copy.deepcopy(CONFIG['request'])
        req['target_x'] = target
        return dict(origin_s=origin, expires_s=origin+100, request=req)

    def predict(self, origin=0, target=5):
        return self.send('prediction', origin+20, self.request(origin, target))


class ControllerTests(unittest.TestCase):
    def test_actual_prediction_and_expiry(self):
        h=Harness(); h.setup(); out=h.predict()
        self.assertEqual(out['prediction']['state'], 'conditional')
        self.assertEqual(out['prediction']['value']['result']['upper_remaining_s'], 0)
        self.assertFalse(out['collection_authorized'])
        self.assertEqual(h.send('tick', 100, {})['prediction']['invalidation_reason'], 'prediction_expired')
        self.assertEqual(h.send('tick', 101, {})['prediction']['state'], 'unavailable')

    def test_m6_unknown_and_mismatch(self):
        h=Harness();h.setup();o=h.predict(target=.3)
        self.assertEqual(o['prediction']['state'], 'unresolved')
        self.assertIsNone(o['prediction']['value']['result']['upper_remaining_s'])
        h=Harness();h.setup();p=h.request();p['request']['candidates']=p['request']['candidates'][2:]
        o=h.send('prediction',20,p);self.assertEqual(o['prediction']['state'],'coverage_or_model_mismatch')

    def test_missing_sample_replaces_usable_prediction(self):
        h=Harness();h.setup();self.assertEqual(h.predict()['prediction']['state'],'conditional')
        p=h.request(origin=1);p['request']['observations'][-1]['samples']['local_rh']=None
        o=h.send('prediction',21,p)
        self.assertEqual(o['prediction']['state'],'unresolved')
        self.assertIsNone(o['prediction']['value']['result']['upper_remaining_s'])
        self.assertFalse(o['collection_authorized'])

    def test_sequence_upper_bound(self):
        c=Controller('device','boot','batch','synthetic');domain=copy.deepcopy(c.domain)
        def event(seq):
            return dict(version=VERSION,id=event_id(domain,seq),seq=seq,domain=domain,occurred_s=0,received_s=0,kind='tick',payload={})
        self.assertEqual(c.apply(event(2**53),0)['sequence'],2**53)
        with self.assertRaises(ValueError):c.apply(event(2**53+1),0)

    def test_nonzero_finite_m6_time(self):
        h=Harness();h.setup();p=h.request();p['request']=analytic_request()
        out=h.send('prediction',20,p)['prediction']['value']['result']
        self.assertEqual(out['set_status'],'conditional')
        self.assertEqual(out['upper_sample_s'],1210)
        self.assertEqual(out['upper_remaining_s'],1190)

    def test_revocation_intent(self):
        h=Harness();h.setup();prior=h.predict();key=prior['intents'][0]['key']
        o=h.send('local',21,dict(state='wet',source='operator',expires_s=100,exposure='exposed'))
        rev=[i for i in o['intents'] if i['kind']=='prediction_revoked'][0]
        self.assertEqual(rev['key'],key);self.assertEqual(rev['invalidation_reason'],'local_wet')

    def test_environment_labels_do_not_override_sensor(self):
        def produce(label, wet):
            h=Harness();h.setup()
            envelope={'truth_label':label,'public':dict(state=wet,source='synthetic_external',expires_s=100,exposure='exposed')}
            return h.send('local',1,envelope['public'])
        self.assertEqual(produce('night_rain','wet'),produce('bright_rain','wet'))
        self.assertEqual(produce('high_rh_no_rain','dry'),produce('shadow','dry'))
        self.assertEqual(produce('high_rh_no_rain','dry')['local_wetting']['state'],'dry')
        self.assertEqual(produce('night_rain','wet')['forecast']['state'],'unavailable')

    def test_invalidation_and_no_automatic_revival(self):
        cases=[('contact',dict(state='missing',source='operator',expires_s=140),dict(state='confirmed',source='operator',expires_s=141)),
               ('heat',dict(state='interference',source='operator',expires_s=140),dict(state='clear',source='operator',expires_s=141)),
               ('local',dict(state='wet',source='operator',expires_s=140,exposure='exposed'),dict(state='dry',source='operator',expires_s=141,exposure='exposed')),
               ('fault',dict(state='active'),dict(state='cleared'))]
        for kind,bad,good in cases:
            with self.subTest(kind=kind):
                h=Harness();h.setup();h.predict();h.send(kind,21,bad);self.assertEqual(h.send(kind,22,good)['prediction']['state'],'unavailable')
                p=h.request(origin=2);p['expires_s']=122
                with self.assertRaises(ValueError):h.send('prediction',22,p)
                self.assertEqual(h.send('tick',23,{})['prediction']['state'],'unavailable')
                h.setup(24);self.assertEqual(h.predict(origin=24)['prediction']['state'],'conditional')

    def test_identity_and_old_domain(self):
        h=Harness();h.setup();h.predict();old=copy.deepcopy(h.c.domain)
        out=h.send('identity',21,dict(boot='boot2',batch='batch2',installation='synthetic2'))
        self.assertEqual(out['prediction']['state'],'unavailable')
        e,t=h.event('tick',22,{});e['domain']=old
        with self.assertRaises(ValueError):h.c.apply(e,t)
        h.setup(22);p=h.request(origin=22)
        with self.assertRaises(ValueError):h.send('prediction',42,p)

    def test_rain_independence_and_forecast(self):
        h=Harness();h.setup()
        f=dict(issued_s=1,available_s=2,valid_from_s=5,valid_until_s=30,expires_s=40,risk='risk',source='synthetic_forecast')
        self.assertEqual(h.send('forecast',2,f)['forecast']['state'],'upcoming_risk')
        o=h.send('local',5,dict(state='wet',source='synthetic_external',expires_s=100,exposure='exposed'))
        self.assertEqual([i['kind'] for i in o['intents']],['local_wet','forecast_risk'])
        self.assertEqual(h.send('tick',30,{})['forecast']['state'],'unavailable')
        self.assertEqual(h.send('tick',100,{})['local_wetting']['state'],'unavailable')
        o=h.send('tick',305,{})
        self.assertTrue(o['local_wetting']['ever_wet']);self.assertIsNone(o['local_wetting']['stuck_wet'])

    def test_continuous_wet_requires_fresh_evidence(self):
        h=Harness();h.setup()
        for t in (1,100,200,300):
            o=h.send('local',t,dict(state='wet',source='operator',expires_s=t+120,exposure='exposed'))
        self.assertFalse(o['local_wetting']['stuck_wet'])
        self.assertTrue(h.send('tick',301,{})['local_wetting']['stuck_wet'])
        o=h.send('tick',420,{})
        self.assertIsNone(o['local_wetting']['stuck_wet']);self.assertTrue(o['local_wetting']['ever_wet'])

    def test_obstruction_fault_and_stale(self):
        for state,exposure in [('dry','obstructed'),('unknown','unknown')]:
            h=Harness();h.setup();h.predict()
            o=h.send('local',21,dict(state=state,source='operator',expires_s=100,exposure=exposure))
            self.assertEqual(o['local_wetting']['state'],'unavailable');self.assertEqual(o['prediction']['state'],'unavailable')
        h=Harness();h.setup();h.predict();self.assertEqual(h.send('tick',120,{})['prediction']['state'],'unavailable')

    def test_replay_and_validation_are_atomic(self):
        h=Harness();h.setup();e,t=h.event('tick',1,{});h.c.apply(e,t);snap=h.c.snapshot()
        repeated=h.c.apply(e,50)
        self.assertEqual(repeated['sequence'],snap['sequence']);self.assertEqual(repeated['now_s'],50)
        altered=copy.deepcopy(e);altered['payload']={'hidden':1}
        with self.assertRaises(ValueError):h.c.apply(altered,50)
        e,t=h.event('tick',51,{});e['seq']=1;e['id']=event_id(e['domain'],1)
        with self.assertRaises(ValueError):h.c.apply(e,t)
        e,t=h.event('tick',0,{})
        with self.assertRaises(ValueError):h.c.apply(e,t)
        self.assertEqual(h.c.snapshot(),repeated)
        for value in (float('nan'),float('inf'),True):
            e,t=h.event('tick',51,{});e['occurred_s']=value
            with self.assertRaises(ValueError):h.c.apply(e,t)
        self.assertEqual(h.c.snapshot(),repeated)

    def test_duplicate_advances_expiry_and_id_bound_to_sequence(self):
        h=Harness();h.setup();p=h.request();e,t=h.event('prediction',20,p);out=h.c.apply(e,t)
        self.assertEqual(out['prediction']['state'],'conditional')
        out=h.c.apply(e,100)
        self.assertEqual(out['prediction']['state'],'unavailable')
        self.assertEqual(out['prediction']['invalidation_reason'],'prediction_expired')
        newer,when=h.event('tick',101,{});newer['id']=e['id']
        with self.assertRaises(ValueError):h.c.apply(newer,when)

    def test_keys_bind_domain_and_event(self):
        a=Harness();a.setup();pa=a.predict()
        b=Harness();b.c=Controller('other','boot','batch','synthetic');b.setup();pb=b.predict()
        self.assertNotEqual(pa['intents'][0]['key'],pb['intents'][0]['key'])
        old_key=pa['intents'][0]['key']
        a.send('identity',20,dict(boot='boot',batch='batch2',installation='synthetic'))
        a.setup(20)
        # Same-time new analysis cannot use observations preceding identity invalidation.
        p=a.request();e,t=a.event('prediction',20,p)
        with self.assertRaises(ValueError):a.c.apply(e,t)
        self.assertEqual([x for x in a.c.snapshot()['intents'] if x['kind']=='prediction_revoked'][0]['key'],old_key)

    def test_same_time_prediction_supersedes_with_distinct_key(self):
        h=Harness();h.setup();before=h.predict();old_key=before['intents'][0]['key']
        after=h.send('prediction',20,h.request())
        current=[x for x in after['intents'] if x['kind']=='prediction_conditional'][0]
        revoked=[x for x in after['intents'] if x['kind']=='prediction_revoked'][0]
        self.assertNotEqual(current['key'],old_key)
        self.assertEqual(revoked['key'],old_key)
        self.assertEqual(revoked['invalidation_reason'],'superseded')

    def test_hidden_and_future_rejected(self):
        h=Harness();h.setup();p=h.request();p['request']['candidates'][0]['variants'][0]['true_water']=3
        e,t=h.event('prediction',20,p);before=h.c.snapshot()
        with self.assertRaises(ValueError):h.c.apply(e,t)
        self.assertEqual(h.c.snapshot(),before)
        p=h.request();p['request']['futures'][0]['segments'][0]['available_at_s']=21
        e,t=h.event('prediction',20,p)
        with self.assertRaises(ValueError):h.c.apply(e,t)
        self.assertEqual(h.c.snapshot(),before)

    def test_truth_and_future_suffix_isolation(self):
        def produce(truth, suffix):
            h=Harness();h.setup();public=h.request();envelope=dict(public=public,truth=truth,actual_future=suffix)
            return h.send('prediction',20,envelope['public'])
        self.assertEqual(produce('dew','night'),produce('splash','sun'))
        def local(label):
            h=Harness();h.setup();envelope=dict(truth=label,public=dict(state='wet',source='synthetic_external',expires_s=100,exposure='exposed'))
            return h.send('local',1,envelope['public'])
        self.assertEqual(local('dew'),local('splash'))


if __name__ == '__main__': unittest.main(verbosity=2)

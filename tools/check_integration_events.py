"""Coordinator checks for T11 P1; synthetic lifecycle, never real drying accuracy."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
P = 'research/integration_t11/p1/'
RECEIPT = 'docs/verification/T11-P1-tests.json'
BASELINE = 'docs/team/baseline-t11-p1/snapshot.json'


def require(value, reason):
    if not value:
        raise AssertionError(reason)


def read(path):
    return json.loads((ROOT / path).read_text(encoding='utf-8-sig'))


def sha(path):
    full = (ROOT / path).resolve()
    require(full.is_relative_to(ROOT), 'path outside checkout')
    return hashlib.sha256(full.read_bytes()).hexdigest()


def analytic_request():
    """Drainage-only independent exponential truth, RH response, noninteger grid."""
    q = deepcopy(read('research/garment_prediction/prediction_m6/config.json')['request'])
    q.update(reference_s=0., as_of_s=5.1, target_x=.2, step_s=.2)
    drive = dict(air_c=25., rh=.5, wind_m_s=0., solar_w_m2=0.,
                 surround_c=25., rain_kg_s=0., rain_c=25.)
    q['history_segments'] = [dict(end_s=5.1, drive=deepcopy(drive), available_at_s=0.)]
    c = deepcopy(q['candidates'][0])
    c.update(id='coordinator_exponential', declared_at_s=0., prior_drive=deepcopy(drive),
             bias_interval=[-.03, .03])
    y = []
    for zone, mass, rate, x in zip(c['zones'], (.3, .1), (.04, .03), (.6, .8)):
        zone.update(dry_kg=mass, evap_m2=0., proj_m2=0., heat_m2=0., rad_m2=0., drain_per_s=rate)
        w = mass*x
        y.extend([w, (mass*1400.+w*4180.)*25.])
    c['coupling'].update(area_cover_m2=0., flow_m3_s=0., sensor_tau_s=4., sensor_bias_rh=0.)
    saturation = 610.94*math.exp(17.625*25./(243.04+25.))/(461.5*(273.15+25.))
    y.extend([c['coupling']['volume_m3']*.5*saturation, .2, 0., 0.])
    c['variants'] = [dict(id='initial', initial_state=y)]
    q['candidates'] = [c]
    q['sensor_history'].update(reference_s=0., issued_at_s=0., initial_rh=.2, uncertainty_abs=0.)
    q['assumptions'].update(issued_at_s=0., structural_rh=0., numeric_rh=1e-8,
                            guard_rh=0., temperature_error_c=0.)
    template = q['observations'][0]
    q['observations'] = []
    for t in (.3, 1.7, 5.1):
        p = deepcopy(template)
        p['now_s'] = t
        for key in ('contact', 'charging_heat'):
            p[key].update(coverage_start_s=0., coverage_end_s=t, issued_at_s=t)
        for key, s in p['samples'].items():
            s.update(measured_at_s=t, received_at_s=t,
                     value=25. if key.endswith('temp') else .5)
        p['samples']['local_rh'].update(value=.5-.3*math.exp(-t/4.)+.012, uncertainty_abs=.001)
        q['observations'].append(p)
    q['futures'] = [dict(id='constant', source='synthetic_scenario', horizon_s=80.,
                         segments=[dict(end_s=80., drive=deepcopy(drive), available_at_s=0.)])]
    return q


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--check-receipt', action='store_true')
    group.add_argument('--write-receipt', action='store_true')
    args = parser.parse_args()
    if args.check_receipt:
        r = read(RECEIPT)
        require(r['passed'] and r['collection_authorized'] is False and r['checks'], 'unpassed receipt')
        require(all(c['passed'] for c in r['checks']), 'failed group')
        for path, h in r['reviewed_sha256'].items():
            require(sha(path) == h, 'stale ' + path)
        print('T11 P1 receipt current and passed')
        return 0

    sys.path.insert(0, str(ROOT))
    from research.integration_t11.p1 import controller as module
    Controller = module.Controller
    frozen = read(BASELINE)['inputs_sha256']
    manifest = read(P + 'results/manifest.json')
    artifacts = {p:h for p,h in manifest['sha256'].items() if p.startswith(P+'results/')}
    inputs = {p:h for p,h in manifest['sha256'].items() if p not in artifacts}
    names = set(frozen) | set(inputs) | set(artifacts)
    names.update([BASELINE, P+'results/manifest.json', 'tools/check_integration_events.py',
                  'docs/T11-EVENT-CONTRACT.md', 'docs/team/reports/integration-t11-p1.md'])
    before = {p: sha(p) for p in sorted(names)}
    checks = []

    class Harness:
        def __init__(self, device='coordinator', batch='batch'):
            self.c = Controller(device, 'boot', batch, 'synthetic')

        def event(self, kind, t, payload):
            seq = self.c.seq + 1
            domain = deepcopy(self.c.domain)
            return dict(version=module.VERSION, id=module.event_id(domain,seq), seq=seq, domain=domain,
                        occurred_s=t, received_s=t, kind=kind, payload=deepcopy(payload))

        def send(self, kind, t, payload):
            e = self.event(kind, t, payload)
            return self.c.apply(e, t)

        def setup(self, t=0.):
            for kind, state in [('contact', 'confirmed'), ('heat', 'clear'), ('local', 'dry')]:
                p = dict(state=state, source='synthetic_external', expires_s=t+100.)
                if kind == 'local':
                    p['exposure'] = 'exposed'
                self.send(kind, t, p)

        def prediction_event(self, origin=0.):
            return self.event('prediction', origin+5.1,
                              dict(origin_s=origin, expires_s=origin+80., request=analytic_request()))

        def predict(self, origin=0.):
            e = self.prediction_event(origin)
            return self.c.apply(e, e['occurred_s'])

    def rejects(h, event, decision=None):
        old = h.c.snapshot()
        try:
            h.c.apply(event, event['received_s'] if decision is None else decision)
        except ValueError:
            require(h.c.snapshot() == old, 'invalid event partly changed state')
        else:
            raise AssertionError('invalid event accepted')

    def integrity():
        for p, h in frozen.items():
            require(sha(p) == h, 'frozen input changed: ' + p)
        for p, h in inputs.items():
            require(sha(p) == h, 'manifest source changed: ' + p)
        for p, h in artifacts.items():
            require(sha(p) == h, 'artifact changed: ' + p)
        cfg = read(P+'config.json')['parameters']
        require(cfg['max_evidence_ttl_s']['value'] == module.TTL, 'TTL registry differs')
        require(cfg['wet_stuck_s']['value'] == module.WET_STUCK, 'wet duration registry differs')
        return dict(frozen=len(frozen), inputs=len(inputs), artifacts=len(artifacts))

    def suite():
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-m', 'unittest',
                                 'research.integration_t11.p1.test_controller', '-v'],
                                cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        require(result.returncode == 0, result.stdout + result.stderr)
        return dict(command='python -B -X utf8 -m unittest research.integration_t11.p1.test_controller -v',
                    output=result.stdout+result.stderr)

    def analytical_adapter():
        expected = math.ceil((max(math.log(3.)/.04, math.log(4.)/.03)-5.1)/.2)*.2
        values = []
        for origin in (0., 1000.):
            h = Harness(); h.setup(origin)
            e = h.prediction_event(origin); old = deepcopy(e)
            r = h.c.apply(e, origin+5.1)
            p = r['prediction']['value']; model = p['result']
            require(e == old, 'consumer mutated upstream request')
            require(r['prediction']['state'] == 'conditional', 'valid analytic path blocked')
            require(abs(model['upper_remaining_s']-expected) < 1e-7, 'wrong relative sample time')
            require(p['analysis_s'] == origin+5.1 and model['as_of_s'] == 5.1, 'clock mapping')
            require(model['future_conditions'] == e['payload']['request']['futures'], 'conditions lost')
            require(r['collection_authorized'] is False and model['collection_authorized'] is False, 'authorization')
            values.append(model['upper_remaining_s'])
        return dict(analytic_continuous_remaining_s=max(math.log(3.)/.04, math.log(4.)/.03)-5.1,
                    expected_grid_remaining_s=expected, adapter_remaining_s=values,
                    scope='drainage-only synthetic equations, not garment duration')

    def lifecycle():
        cases = [
            ('contact', dict(state='missing', source='operator', expires_s=100.)),
            ('heat', dict(state='interference', source='operator', expires_s=100.)),
            ('local', dict(state='wet', source='operator', expires_s=100., exposure='exposed')),
            ('fault', dict(state='active'))]
        for kind, p in cases:
            h = Harness(); h.setup(); h.predict()
            r = h.send(kind, 6., p)
            require(r['prediction']['state'] == 'unavailable', 'old prediction survived ' + kind)
            if kind == 'fault':
                h.send('fault', 7., dict(state='cleared'))
            h.setup(7.)
            require(h.c.snapshot()['prediction']['state'] == 'unavailable', 'recovered old prediction')
            bad = h.prediction_event(2.)
            rejects(h, bad)
            require(h.predict(7.)['prediction']['state'] == 'conditional', 'new evidence cannot recover')
        for changed in ('boot', 'batch', 'installation'):
            h = Harness(); h.setup(); h.predict()
            old_domain = deepcopy(h.c.domain)
            p = {k: v for k, v in h.c.domain.items() if k != 'device'}
            p[changed] += '_new'
            r = h.send('identity', 6., p)
            require(r['prediction']['state'] == 'unavailable' and not r['observation']['eligible'], 'identity carryover')
            bad = h.event('tick', 7., {}); bad['domain'] = old_domain
            rejects(h, bad)
        h = Harness()
        rejects(h, h.prediction_event())
        h=Harness();h.setup();old=h.predict()
        e=h.prediction_event(1.)
        e['payload']['request']['observations'][-1]['samples']['local_rh']=None
        r=h.c.apply(e,e['occurred_s'])
        require(r['prediction']['state']=='unresolved', 'legal missing sample retained old conditional result')
        require(r['prediction']['value']['result']['upper_remaining_s'] is None, 'missing sample became finite time')
        old_key=next(i['key'] for i in old['intents'] if i['kind']=='prediction_conditional')
        require(any(i['kind']=='prediction_revoked' and i['key']==old_key for i in r['intents']), 'missing sample did not supersede old result')
        return dict(transient_causes=len(cases), identity_axes=3, missing_contact_blocks=True,
                    legal_missing_observation_replaces_previous=True)

    def replay_expiry():
        h = Harness(); h.setup(); e = h.prediction_event(); r = h.c.apply(e, 5.1)
        expiry = r['prediction']['value']['expires_s']
        before = h.c.apply(e, expiry-.001)
        require(before['prediction']['value']['expires_s'] == expiry, 'replay extended expiry')
        r = h.c.apply(e, expiry)
        require(r['prediction']['state'] == 'unavailable', 'duplicate prevented expiry')
        require(r['now_s'] == expiry and r['sequence'] == e['seq'], 'replay changed identity or lost time')
        bad = deepcopy(e); bad['payload']['expires_s'] += 1.
        rejects(h, bad, expiry+1.)
        h.send('tick', expiry+1., {})
        rejects(h, e, expiry+2.)
        return dict(expiry_s=expiry, cutoff_equal_expires=True, duplicate_sequence_unchanged=True)

    def rain_states():
        h = Harness()
        forecast = dict(issued_s=0., available_s=0., valid_from_s=10., valid_until_s=30.,
                        expires_s=40., risk='risk', source='synthetic_forecast')
        r = h.send('forecast', 0., forecast)
        require('risk' in r['forecast']['state'], 'upcoming warning missing')
        require(any(i['kind'].startswith('forecast') for i in r['intents']), 'no upcoming intent')
        r = h.send('local', 1., dict(state='wet', source='synthetic_external', expires_s=81., exposure='exposed'))
        require(r['local_wetting']['state'] == 'wet', 'wet masked by missing garment contact')
        require([i['priority'] for i in r['intents']] == sorted(i['priority'] for i in r['intents']), 'priority order')
        r = h.send('tick', 30., {})
        require(r['forecast']['state'] == 'unavailable', 'forecast validity end not exclusive')
        r = h.send('tick', 400., {})
        require(r['local_wetting']['ever_wet'], 'lost historical wet evidence')
        require(r['local_wetting']['state'] == 'unavailable' and not r['local_wetting']['stuck_wet'], 'missing became continuous wet')
        h = Harness()
        for t in (0., 80., 160., 240., 320.):
            r = h.send('local', t, dict(state='wet', source='synthetic_external', expires_s=t+100., exposure='exposed'))
        require(r['local_wetting']['stuck_wet'], 'continuous synthetic wet series unrecognized')
        r = h.send('local', 321., dict(state='unknown', source='operator', expires_s=400., exposure='unknown'))
        require(not r['local_wetting']['stuck_wet'] and r['local_wetting']['ever_wet'], 'unknown retained continuity')
        for kind in ('wet', 'dry'):
            h = Harness()
            r = h.send('local', 0., dict(state=kind, source='operator', expires_s=100., exposure='obstructed'))
            require(r['local_wetting']['state'] == 'unavailable', 'blocked sensor treated valid')
        return dict(upcoming_warning=True, exact_window_end=True, gaps_do_not_prove_continuity=True,
                    repeated_wet_samples=5, truth_rain_classification=False)

    def bad_inputs():
        h = Harness(); h.setup(); h.predict()
        variants = []
        for value in (True, float('nan'), float('inf'), -1., module.MAX_TIME+1.):
            e = h.event('tick', 6., {}); e['occurred_s'] = value; variants.append(e)
        for key, value in [('version','unknown'), ('seq',True), ('seq',0), ('payload',{'true_water':1})]:
            e = h.event('tick', 6., {}); e[key] = value; variants.append(e)
        e = h.event('tick', 6., {}); e['received_s'] = 5.; variants.append(e)
        e = h.event('tick', 5., {}); variants.append(e)
        for e in variants:
            rejects(h, e)
        for mutate in ('true_water', 'actual_future', 'future_available'):
            h = Harness(); h.setup(); e = h.prediction_event()
            req = e['payload']['request']
            if mutate == 'true_water':
                req['candidates'][0]['variants'][0]['true_water'] = 1.
            elif mutate == 'actual_future':
                req['actual_future'] = []
            else:
                req['futures'][0]['segments'][0]['available_at_s'] = 6.
            rejects(h, e)
        return dict(atomic_rejections=len(variants)+3, future_and_hidden_rejected=True)

    def keys_and_counters():
        keys = []
        for device,batch in [('a','x'),('a','y'),('b','x')]:
            h = Harness(device,batch); h.setup(); r=h.predict()
            keys.append(next(i['key'] for i in r['intents'] if i['kind']=='prediction_conditional'))
        require(len(set(keys)) == 3, 'cross-domain notification key collision')
        h = Harness(); h.setup(); r=h.predict()
        old=next(i['key'] for i in r['intents'] if i['kind']=='prediction_conditional')
        r=h.send('fault',6.,dict(state='active'))
        require(next(i['key'] for i in r['intents'] if i['kind']=='prediction_revoked') == old, 'revocation targets wrong result')
        h.send('fault',6.,dict(state='cleared'));h.setup(6.)
        r=h.predict(6.)
        require(next(i['key'] for i in r['intents'] if i['kind']=='prediction_conditional') != old, 'new result reuses revoked key')
        h=Harness();h.c.seq=2**53-1
        r=h.send('tick',0.,{})
        require(r['sequence']==2**53, 'maximum declared counter rejected')
        rejects(h,h.event('tick',1.,{}))
        return dict(distinct_domains=3, exact_counter_max=2**53, revocation_preserves_key=True)

    def replay():
        expected={p:sha(p) for p in [*artifacts,P+'results/manifest.json']}
        events=read(P+'results/events.json')['events']
        saved=read(P+'results/snapshots.json')['snapshots']
        require(len(events)==len(saved) and events, 'trace sizes')
        domain=events[0]['event']['domain']
        c=Controller(**domain)
        for entry,want in zip(events,saved):
            actual=c.apply(entry['event'],entry['decision_s'])
            # JSON intentionally maps M6 tuples to arrays; compare the public representation.
            require(json.loads(json.dumps(actual,allow_nan=False))==want,'saved trace is not actual controller output')
        result=subprocess.run([sys.executable,'-B','-X','utf8','-m','research.integration_t11.p1.replay'],
                              cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
        require(result.returncode==0,result.stdout+result.stderr)
        require(all(sha(p)==h for p,h in expected.items()),'replay bytes differ')
        return dict(events=len(events), artifacts=len(artifacts), byte_identical=True,
                    trace_comparison='JSON representation; M6 tuples serialize to arrays')

    for name, f in [('worker_suite', suite), ('source_integrity', integrity),
                    ('independent_analytic_adapter', analytical_adapter), ('lifecycle_and_identity', lifecycle),
                    ('replay_and_exact_expiry', replay_expiry), ('rain_time_and_continuity', rain_states),
                    ('invalid_input_atomicity', bad_inputs), ('domain_keys_and_counter_boundary', keys_and_counters),
                    ('artifact_replay', replay)]:
        try:
            checks.append(dict(name=name, passed=True, detail=f()))
        except Exception as exc:
            checks.append(dict(name=name, passed=False, error=f'{type(exc).__name__}: {exc}'))
    require(all(sha(p) == h for p, h in before.items()), 'sources changed during review')
    passed = all(c['passed'] for c in checks)
    receipt = dict(schema_version=1, task='T11', milestone='P1',
                   verified_at=datetime.now(timezone.utc).isoformat(), python=sys.version,
                   label='SYNTHETIC_UNCALIBRATED', collection_authorized=False, passed=passed,
                   checks=checks, reviewed_sha256=before,
                   limits='Host event lifecycle only; P2 power/persistent transport and P3 UI remain pending.')
    if args.write_receipt:
        (ROOT/RECEIPT).write_bytes((json.dumps(receipt,ensure_ascii=False,indent=2)+'\n').encode())
    print(json.dumps(dict(passed=passed,checks=[{k:c[k] for k in ('name','passed','error') if k in c}
                                             for c in checks]), ensure_ascii=False,indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())

"""Coordinator acceptance for M6; synthetic equations, never garment accuracy."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
P = 'research/garment_prediction/prediction_m6/'
RECEIPT = 'docs/verification/T10-M6-tests.json'


def require(ok, message):
    if not ok:
        raise AssertionError(message)


def read(path):
    return json.loads((ROOT / path).read_text(encoding='utf-8-sig'))


def sha(path):
    full = (ROOT / path).resolve()
    require(full.is_relative_to(ROOT), 'path outside workspace')
    return hashlib.sha256(full.read_bytes()).hexdigest()


def saturation(t):
    return 610.94 * math.exp(17.625*t/(243.04+t)) / (461.5*(273.15+t))


def analytic_request(config, initial=(.6, .8), rates=(.04, .03), masses=(.3, .1), response=.2):
    """Independent zero-exchange drainage and RH exponential, no generated truth."""
    q = deepcopy(config['request'])
    q.update(reference_s=0., as_of_s=5.1, target_x=.2, step_s=.2)
    d = dict(air_c=25., rh=.5, wind_m_s=0., solar_w_m2=0., surround_c=25., rain_kg_s=0., rain_c=25.)
    q['history_segments'] = [dict(end_s=5.1, drive=deepcopy(d), available_at_s=0.)]
    c = deepcopy(q['candidates'][0])
    c.update(id='analytic', declared_at_s=0., prior_drive=deepcopy(d), bias_interval=[-.03, .03])
    y = []
    for z, mass, k, x in zip(c['zones'], masses, rates, initial):
        z.update(dry_kg=mass, evap_m2=0., proj_m2=0., heat_m2=0., rad_m2=0., drain_per_s=k)
        w = mass*x
        y.extend((w, (mass*1400.+w*4180.)*25.))
    c['coupling'].update(area_cover_m2=0., flow_m3_s=0., sensor_tau_s=4., sensor_bias_rh=0.)
    y.extend((c['coupling']['volume_m3']*.5*saturation(25.), response, 0., 0.))
    c['variants'] = [dict(id='initial', initial_state=y)]
    q['candidates'] = [c]
    q['sensor_history'].update(reference_s=0., issued_at_s=0., initial_rh=response, uncertainty_abs=0.)
    q['assumptions'].update(issued_at_s=0., structural_rh=0., numeric_rh=1e-8, guard_rh=0., temperature_error_c=0.)
    template = q['observations'][0]
    q['observations'] = []
    for t in (.3, 1.7, 5.1):
        packet = deepcopy(template)
        packet['now_s'] = t
        for key in ('contact', 'charging_heat'):
            packet[key].update(coverage_start_s=0., coverage_end_s=t, issued_at_s=t)
        for key, sample in packet['samples'].items():
            sample.update(measured_at_s=t, received_at_s=t)
            sample['value'] = 25. if key.endswith('temp') else .5
        packet['samples']['local_rh'].update(value=.5+(response-.5)*math.exp(-t/4.)+.012, uncertainty_abs=.001)
        q['observations'].append(packet)
    q['futures'] = [dict(id='steady', source='synthetic_scenario', horizon_s=80., segments=[dict(end_s=80., drive=deepcopy(d), available_at_s=0.)])]
    return q


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--check-receipt', action='store_true')
    g.add_argument('--write-receipt', action='store_true')
    args = ap.parse_args()
    if args.check_receipt:
        r = read(RECEIPT)
        require(r['passed'] and r['collection_authorized'] is False and r['checks'] and r['reviewed_sha256'], 'unpassed receipt')
        require(all(c['passed'] for c in r['checks']), 'failed checks')
        for path, h in r['reviewed_sha256'].items():
            require(sha(path) == h, 'stale ' + path)
        print('M6 receipt current and passed')
        return 0
    sys.path.insert(0, str(ROOT / P))
    from integrated import predict
    cfg, manifest = read(P+'config.json'), read(P+'results/manifest.json')
    watched = set(manifest['inputs_sha256']) | set(manifest['artifacts_sha256']) | {
        P+'results/manifest.json', 'docs/GARMENT-PREDICTION-INTEGRATED.md',
        'docs/team/reports/algorithm-t10-m6.md', 'tools/check_garment_integrated.py'}
    before = {path: sha(path) for path in sorted(watched)}
    checks = []

    def check(name, action):
        try:
            checks.append(dict(name=name, passed=True, detail=action()))
        except Exception as e:
            checks.append(dict(name=name, passed=False, error=f'{type(e).__name__}: {e}'))

    def calculate(q):
        r = predict(q)
        require(r['label'] == 'SYNTHETIC_UNCALIBRATED' and r['collection_authorized'] is False, 'unsupported claim')
        require(len(r['rows']) == len(q['candidates']), 'candidate lost')
        require(r['request_version']=='m6-v1' and r['state_version']=='m5-v1' and r['reference_s']==q['reference_s'], 'missing version/time provenance')
        require(r['target_x_kg_kg']==q['target_x'] and r['step_s']==q['step_s'] and r['future_conditions']==q['futures'], 'lost conditional interpretation')
        require(r['future_conditions'] is not q['futures'] and r['future_conditions'][0]['segments'] is not q['futures'][0]['segments'], 'result shares mutable condition objects')
        return r

    def suite():
        cmd = [sys.executable, '-B', '-X', 'utf8', P+'test_integrated.py']
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, encoding='utf-8', timeout=180)
        require(p.returncode == 0, p.stdout+p.stderr)
        m = re.search(r'Ran (\d+) tests?', p.stdout+p.stderr)
        require(m is not None, 'no test count')
        return dict(command=cmd, test_count=int(m.group(1)), output=p.stdout+p.stderr)

    def integrity():
        required, counts = set(), {}
        for path, key in [('docs/team/baseline-t10-m6/snapshot.json', 'inputs_sha256')] + [
                ('docs/verification/T10-'+m+'-tests.json', 'reviewed_sha256') for m in ('M1','M2a','M2b','M3','M4','M5')]:
            hashes = read(path)[key]
            for name, h in hashes.items():
                require(sha(name) == h, 'protected input changed '+name)
            counts[path] = len(hashes)
            required.update(hashes)
            required.add(path)
        required.update(P+x for x in ('integrated.py','test_integrated.py','config.json','replay.py'))
        require(required <= set(manifest['inputs_sha256']), 'incomplete provenance')
        for key in ('inputs_sha256', 'artifacts_sha256'):
            for path, h in manifest[key].items():
                require(sha(path) == h, 'manifest mismatch '+path)
        require(manifest['label'] == 'SYNTHETIC_UNCALIBRATED' and manifest['seed'] is None, 'label/seed')
        return dict(inputs=len(manifest['inputs_sha256']), artifacts=len(manifest['artifacts_sha256']), protected=counts)

    def analytics():
        q = analytic_request(cfg)
        r = calculate(q)
        v = r['rows'][0]['variants'][0]
        require(v['status'] == 'compatible', 'analytic observations not compatible')
        expected_bias = [.012-.001-1e-8, .012+.001+1e-8]
        require(max(abs(x-y) for x,y in zip(v['bias_interval'], expected_bias)) < 2e-8, 'bias intersection differs from exponential reference')
        for i, (x0, rate, mass) in enumerate(zip((.6,.8),(.04,.03),(.3,.1))):
            expected = mass*x0*math.exp(-rate*q['as_of_s'])
            require(abs(v['history_end_state'][2*i]-expected) < 1e-10, 'history water differs from analytic')
        exact = max(math.log(.6/.2)/.04, math.log(.8/.2)/.03)-q['as_of_s']
        future = v['future'][0]
        require(0 <= future['first_sample_s']-q['as_of_s']-exact <= q['step_s']+1e-8, 'sampled target differs from monotone analytic reference')
        conflict = deepcopy(q)
        conflict['observations'][0]['samples']['local_rh']['value'] += .02
        conflict['observations'][-1]['samples']['local_rh']['value'] -= .02
        mismatch = calculate(conflict)
        require(mismatch['rows'][0]['status'] == 'incompatible' and mismatch['upper_sample_s'] is None, 'per-sample bias fit accepted')
        return dict(analytic_remaining_s=exact, sampled_absolute_s=future['first_sample_s'], bias_reference=expected_bias, bias_computed=v['bias_interval'], common_bias_conflict_rejected=True)

    def replay():
        old = {p:sha(p) for p in manifest['artifacts_sha256']}
        old[P+'results/manifest.json'] = sha(P+'results/manifest.json')
        cmd = [sys.executable,'-B','-X','utf8',P+'replay.py']
        p = subprocess.run(cmd,cwd=ROOT,capture_output=True,encoding='utf-8',timeout=180)
        require(p.returncode==0,p.stdout+p.stderr)
        require(all(sha(k)==h for k,h in old.items()),'replay changed outputs')
        return dict(command=cmd,output=p.stdout)

    def ambiguity():
        q = deepcopy(cfg['request'])
        default = calculate(q)
        require([r['status'] for r in default['rows']] == ['compatible','compatible','incompatible'], 'example support changed')
        wrong = deepcopy(q)
        wrong['candidates'] = wrong['candidates'][2:]
        require(calculate(wrong)['set_status'] == 'coverage_or_model_mismatch', 'wrong prior forced to nearest')
        q['candidates'] = q['candidates'][:1]
        other = deepcopy(q['candidates'][0])
        other['id'] = 'different_unobserved_zone'
        y = other['variants'][0]['initial_state']
        md = other['zones'][1]['dry_kg']
        y[2] = md*1.2
        y[3] = (md*1400.+y[2]*4180.)*25.
        q['candidates'].append(other)
        rows = calculate(q)['rows']
        a,b = [r['variants'][0] for r in rows]
        require(all(r['status']=='compatible' for r in rows), 'unobserved uncertainty erased')
        require(max(abs(a['history_end_state'][i]-b['history_end_state'][i]) for i in (0,1,4,5)) < 1e-12, 'unobserved zone affected central dynamics')
        require(abs(a['history_end_state'][2]-b['history_end_state'][2]) > .03, 'counterexample has no water difference')
        require(a['bias_interval']==b['bias_interval'], 'same output got different compatibility')
        return dict(default_statuses=[r['status'] for r in default['rows']],unobserved_water_difference_kg=b['history_end_state'][2]-a['history_end_state'][2],limit='Independent-zone model counterexample; not real garment data.')

    def evidence_and_time():
        cases = []
        for name, change in [
            ('missing',lambda q:q['observations'][0]['samples'].__setitem__('local_rh',None)),
            ('not_contact',lambda q:q['observations'][-1]['contact'].__setitem__('state','not_contact')),
            ('known_heat',lambda q:q['observations'][-1]['charging_heat'].__setitem__('state','known_interference')),
            ('coverage_gap',lambda q:q['observations'][-1]['contact'].__setitem__('coverage_start_s',1.)),
            ('duplicate_time',lambda q:q['observations'][1]['samples']['local_rh'].__setitem__('measured_at_s',10.)),
            ('changed_installation',lambda q:q['observations'][1].__setitem__('installation_id','changed')),
            ('unexplained_temperature',lambda q:q['observations'][1]['samples']['local_temp'].__setitem__('value',30.)),
            ('incomplete_support',lambda q:[c.__setitem__('complete_discrete_support',False) for c in q['candidates']]),
            ('delivery_delay',lambda q:q['policy'].__setitem__('max_delivery_delay_s',0.)),
        ]:
            q = deepcopy(cfg['request'])
            if name=='delivery_delay':
                q['observations'][0]['samples']['local_rh']['measured_at_s']=9.
            change(q)
            r = calculate(q)
            require(all(x['status']=='information_insufficient' for x in r['rows']) and r['upper_sample_s'] is None, 'weak evidence excluded candidates: '+name)
            cases.append(name)
        rejected = []
        for name, change in [
            ('future_packet',lambda q:q['observations'][-1].__setitem__('now_s',q['as_of_s']+1)),
            ('future_assumption',lambda q:q['assumptions'].__setitem__('issued_at_s',q['as_of_s']+1)),
            ('future_history',lambda q:q['history_segments'][0].__setitem__('available_at_s',q['as_of_s']+1)),
            ('future_scenario_issue',lambda q:q['futures'][0]['segments'][0].__setitem__('available_at_s',q['as_of_s']+1)),
            ('future_candidate',lambda q:q['candidates'][0].__setitem__('declared_at_s',q['as_of_s']+1)),
            ('hidden_request',lambda q:q.__setitem__('actual_future',{})),
            ('hidden_candidate',lambda q:q['candidates'][0].__setitem__('true_water',0)),
            ('hidden_segment',lambda q:q['futures'][0]['segments'][0].__setitem__('actual_weather',{})),
            ('empty_support',lambda q:q.__setitem__('candidates',[])),
            ('step_budget',lambda q:q.__setitem__('step_s',1e-12)),
        ]:
            q = deepcopy(cfg['request']);change(q)
            try:
                predict(q)
            except ValueError:
                rejected.append(name)
            else:
                raise AssertionError('accepted invalid request '+name)
        q=deepcopy(cfg['request'])
        q['candidates'][0]['variants'][0]['true_material']='cotton'
        r=calculate(q)
        require(r['rows'][0]['status']=='calculation_failed' and r['upper_sample_s'] is None,
                'malformed member used for inference')
        return dict(insufficient=cases,rejected=rejected,malformed_variant='retained calculation_failed; no finite upper bound')

    def simultaneous_targets():
        q = analytic_request(cfg,initial=(.3,5.),rates=(.01,.01),masses=(.1,.3),response=.5)
        q['step_s']=1.
        d = deepcopy(q['history_segments'][0]['drive'])
        wet = dict(d,rain_kg_s=.0003)
        q['futures'][0].update(horizon_s=1000.,segments=[dict(end_s=80.,drive=d,available_at_s=0.),dict(end_s=1000.,drive=wet,available_at_s=0.)])
        first0=math.log(.3/.2)/.01
        rewet0=80.+math.log((.3-.3*math.exp(-.8))/.1)/.01
        first1=80.+math.log((5.*math.exp(-.8)-.1)/.1)/.01
        require(first0<80.<rewet0<first1, 'bad analytic construction')
        r=calculate(q);f=r['rows'][0]['variants'][0]['future'][0]
        require(f['status']=='not_reached' and f['first_sample_s'] is None and r['upper_sample_s'] is None,'incorrect joint target')
        for i,z in enumerate(q['candidates'][0]['zones']):
            y=q['candidates'][0]['variants'][0]['initial_state'];w=z['dry_kg']*.1
            y[2*i]=w;y[2*i+1]=(z['dry_kg']*1400.+w*4180.)*25.
        r=calculate(q);f=r['rows'][0]['variants'][0]['future'][0]
        require(f['first_sample_s']==q['as_of_s'] and f['remaining_sample_s']==0 and f['later_sample_above_target'], 'initial target/rewet lost')
        q['futures'][0]['segments'][1]['drive']['air_c']=0.
        r=calculate(q);f=r['rows'][0]['variants'][0]['future'][0]
        require(f['status']=='calculation_failed' and f['remaining_sample_s']==0 and r['upper_sample_s'] is None,'later failure erased earlier target')
        return dict(central_first_absolute_s=first0,central_rewet_absolute_s=rewet0,other_first_absolute_s=first1,initial_target_and_later_failure_retained=True)

    def dynamic_continuity():
        # Generate a declared synthetic input with the accepted M5 integrator, then
        # compare state transport. This is consistency, not independent calibration.
        from coupled import Zone,Drive,integrate,transition
        q=deepcopy(cfg['request']);q['candidates']=q['candidates'][:1]
        q.update(as_of_s=5.1,step_s=.1)
        c=q['candidates'][0];zones=[Zone(**z) for z in c['zones']];coupling=c['coupling']
        d1=deepcopy(c['prior_drive']);d2=dict(d1,air_c=27.,rh=.4,wind_m_s=1.2,solar_w_m2=50.)
        q['history_segments']=[dict(end_s=2.7,drive=d1,available_at_s=0.),dict(end_s=5.1,drive=d2,available_at_s=0.)]
        y=tuple(c['variants'][0]['initial_state']);prior=Drive(**d1);t=0.;points=[]
        for end,dr in ((1.3,d1),(2.7,d1),(5.1,d2)):
            d=Drive(**dr);y,_=transition(zones,prior,d,coupling,y)
            y=integrate(zones,d,coupling,y,end-t,.1)['state'];t=end;prior=d
            points.append(y)
        template=q['observations'][0];q['observations']=[]
        for time,state,dr in zip((1.3,2.7,5.1),points,(d1,d2,d2)):
            packet=deepcopy(template);packet['now_s']=time
            for k in ('contact','charging_heat'):
                packet[k].update(coverage_start_s=0.,coverage_end_s=time,issued_at_s=time)
            for key,s in packet['samples'].items():
                s.update(measured_at_s=time,received_at_s=time)
                s['value']=dr['air_c'] if key.endswith('temp') else dr['rh']
            packet['samples']['local_rh']['value']=state[5]+.005
            q['observations'].append(packet)
        d3=dict(d1,air_c=26.,rh=.45,wind_m_s=.2,solar_w_m2=300.)
        q['futures']=[dict(id='changes',source='synthetic_scenario',horizon_s=12.7,segments=[dict(end_s=8.4,drive=d2,available_at_s=0.),dict(end_s=12.7,drive=d3,available_at_s=0.)])]
        r=calculate(q);v=r['rows'][0]['variants'][0]
        require(v['status']=='compatible','dynamic history rejected')
        history_error=max(abs(a-b) for a,b in zip(v['history_end_state'],y))
        require(history_error<1e-7 and v['history_end_drive']==d2,'history state or boundary changed')
        for end,dr in ((8.4,d2),(12.7,d3)):
            d=Drive(**dr);y,_=transition(zones,prior,d,coupling,y)
            y=integrate(zones,d,coupling,y,end-t,.1)['state'];t=end;prior=d
        f=v['future'][0]
        error=max(abs(a-b) for a,b in zip(f['final_state'],y))
        require(error<1e-6 and f['final_drive']==d3,'future lost complete M5 state')
        refined=deepcopy(q);refined['step_s']=.05
        fine=calculate(refined)['rows'][0]['variants'][0]
        require(fine['status']==v['status'],'refined support changed')
        require(max(abs(a-b) for a,b in zip(fine['future'][0]['final_state'],f['final_state']))<1e-5,'refinement differs')
        return dict(history_full_state_max_abs_error=history_error,future_full_state_max_abs_error=error,air_temperatures_c=[25.,27.,26.],limit='M5 consistency and selected numerical refinement; not physical validation.')

    for name, action in [('worker_suite',suite),('source_integrity',integrity),('independent_analytic_history_bias_and_time',analytics),('unobserved_zone_and_wrong_prior',ambiguity),('evidence_and_information_time',evidence_and_time),('simultaneous_targets_and_later_failure',simultaneous_targets),('dynamic_boundary_full_state_continuity',dynamic_continuity),('artifact_replay',replay)]:
        check(name, action)
    require(all(sha(path)==h for path,h in before.items()),'inputs changed during verification')
    passed = all(c['passed'] for c in checks)
    receipt = dict(schema_version=1,task='T10',milestone='M6',verified_at=datetime.now(timezone.utc).isoformat(),
                   python=sys.version,label='SYNTHETIC_UNCALIBRATED',collection_authorized=False,passed=passed,
                   checks=checks,reviewed_sha256=before,limits='Finite candidate and sampled conditional times in prescribed-air M5 model. No real calibration, continuous guarantees or hardware performance.')
    if args.write_receipt:
        (ROOT/RECEIPT).write_bytes((json.dumps(receipt,ensure_ascii=False,indent=2)+'\n').encode())
    print(json.dumps(dict(passed=passed,checks=[{k:c[k] for k in ('name','passed','error') if k in c} for c in checks]),ensure_ascii=False,indent=2))
    return 0 if passed else 1


if __name__=='__main__':
    raise SystemExit(main())

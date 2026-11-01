"""Independent coordinator checks for finite conditional candidates (T10 M4)."""
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
P = 'research/garment_prediction/ensemble_m4/'
RECEIPT = ROOT / 'docs/verification/T10-M4-tests.json'


def require(ok, message):
    if not ok:
        raise AssertionError(message)


def read(path):
    return json.loads((ROOT/path).read_text(encoding='utf-8-sig'))


def sha(path):
    full = (ROOT/path).resolve()
    require(full.is_relative_to(ROOT), 'outside workspace')
    return hashlib.sha256(full.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check-receipt', action='store_true')
    mode.add_argument('--write-receipt', action='store_true')
    args = parser.parse_args()
    if args.check_receipt:
        r = read('docs/verification/T10-M4-tests.json')
        require(r['passed'] and r['collection_authorized'] is False, 'unpassed receipt')
        require(r['checks'] and all(c['passed'] for c in r['checks']), 'missing checks')
        require(r['reviewed_sha256'], 'missing hashes')
        for path, expected in r['reviewed_sha256'].items():
            require(sha(path)==expected, 'stale '+path)
        print('M4 receipt current and passed')
        return 0

    sys.path.insert(0, str(ROOT/P))
    from ensemble import compare
    config = read(P+'config.json')
    manifest = read(P+'results/manifest.json')
    watched = set(manifest['inputs_sha256']) | set(manifest['artifacts_sha256']) | {
        P+'results/manifest.json', 'docs/GARMENT-ENSEMBLE.md', 'docs/team/reports/algorithm-t10-m4.md',
        'tools/check_garment_ensemble.py'}
    before = {p:sha(p) for p in sorted(watched)}
    checks=[]

    def check(name, action):
        try:
            checks.append(dict(name=name, passed=True, detail=action()))
        except Exception as e:
            checks.append(dict(name=name, passed=False, error=f'{type(e).__name__}: {e}'))

    def run(c):
        r=compare(*[c[k] for k in ('packets','policy','history','gate','assumptions','candidates','as_of_s')])
        require(r['collection_authorized'] is False and r['label']=='SYNTHETIC_UNCALIBRATED', 'unsupported authorization')
        require(r['eta']=='unavailable' and r['whole_garment_state']=='unknown', 'unsupported whole garment output')
        require(len(r['candidates'])==len(c['candidates']), 'dropped candidate')
        return r

    def suite():
        cmd=[sys.executable,'-B','-X','utf8',P+'test_ensemble.py']
        proc=subprocess.run(cmd,cwd=ROOT,capture_output=True,encoding='utf-8',timeout=120)
        require(proc.returncode==0,proc.stdout+proc.stderr)
        m=re.search(r'Ran (\d+) tests?',proc.stdout+proc.stderr)
        require(m is not None,'test count missing')
        return dict(command=cmd,test_count=int(m.group(1)),output=proc.stdout+proc.stderr)

    def integrity():
        required=set();counts={}
        for path,key in [('docs/team/baseline-t10-m4/snapshot.json','inputs_sha256')]+[
                ('docs/verification/T10-'+m+'-tests.json','reviewed_sha256') for m in ('M1','M2a','M2b','M3')]:
            hashes=read(path)[key]
            for name,expected in hashes.items():
                require(sha(name)==expected,'protected input changed '+name)
            counts[path]=len(hashes);required.update(hashes);required.add(path)
        required.update(P+f for f in ('ensemble.py','test_ensemble.py','config.json','replay.py'))
        require(set(manifest['inputs_sha256'])==required,'incomplete provenance')
        require(set(manifest['artifacts_sha256'])=={P+'results/compatibility.json'},'unexpected artifacts')
        require(manifest['label']=='SYNTHETIC_UNCALIBRATED' and manifest['seed'] is None,'label/seed')
        for key in ('inputs_sha256','artifacts_sha256'):
            for path,expected in manifest[key].items():
                require(sha(path)==expected,'manifest mismatch '+path)
        return dict(inputs=len(required),artifacts=1,protected=counts)

    def analytical(transient=False, offsets=None):
        c=deepcopy(config);c['candidates']=c['candidates'][:1]
        candidate=c['candidates'][0]
        candidate['parameters'].update(area_open_m2=0.,area_cover_m2=0.,flow_m3_s=0.,wall_source_kg_s=0.,sensor_bias_rh=0.,sensor_tau_s=4.)
        air=.7 if transient else .5
        candidate['initial_variants']=[dict(x0=.6,rh0=air,sensor_rh0=.5)]
        candidate['bias_interval_rh']=[-.03,.03]
        candidate['declared_at_s']=575.
        c['history'].update(reference_time_s=580.,issued_at_s=580.,initial_state_rh=.5,uncertainty_abs=0.)
        c['assumptions'].update(issued_at_s=580.,structural_abs_rh=0.,numerical_abs_rh=1e-8,boundary_guard_rh=1e-7,dt_s=.07)
        template=deepcopy(c['packets'][0]);c['packets']=[];expected=[]
        for i,delta in enumerate((.3,1.7,5.1)):
            t=580.+delta;pk=deepcopy(template);pk['now_s']=t+2.
            for name,sample in pk['samples'].items():
                sample.update(measured_at_s=t,received_at_s=t+1.,uncertainty_abs=.001)
                if name.endswith('temp'):sample['value']=25.
                elif name=='ambient_rh':sample['value']=.5
            for key in ('contact','charging_heat'):
                pk[key].update(coverage_start_s=580.,coverage_end_s=t,issued_at_s=t+1.)
            f=air+(.5-air)*math.exp(-delta/4.)
            offset=.012 if offsets is None else offsets[i]
            pk['samples']['local_rh']['value']=f+offset
            expected.append((f,pk['samples']['local_rh']['value'],.001+1e-8))
            c['packets'].append(pk)
        c['as_of_s']=c['packets'][-1]['now_s']
        return c,expected

    def analytic_bias_time():
        rows=[]
        for transient in (False,True):
            c,triples=analytical(transient)
            lo=max([-.03]+[y-f-e for f,y,e in triples]);hi=min([.03]+[y-f+e for f,y,e in triples])
            r=run(c)['candidates'][0];require(r['status']=='compatible','analytic candidate rejected')
            v=r['variant_results'][0]
            require(abs(v['common_bias_low']-lo)<2e-8 and abs(v['common_bias_high']-hi)<2e-8,'bias intersection differs from analytic reference')
            require(v['stage_domain_bias_low']<=v['common_bias_low']<=v['common_bias_high']<=v['stage_domain_bias_high'],'bias outside checked stage domain')
            rows.append(dict(transient=transient,reference_bias=[lo,hi],computed_bias=[v['common_bias_low'],v['common_bias_high']]))
        c,_=analytical(False,[.02,-.02,.02]);r=run(c)
        require(r['candidates'][0]['status']=='incompatible','per-sample bias silently changed')
        require(r['set_status']=='model_or_candidate_coverage_mismatch','empty compatible set hidden')
        rows.append(dict(conflicting_bias_status=r['candidates'][0]['status']))
        c,_=analytical(False);c['candidates'][0]['bias_interval_rh']=[.6,.7]
        r=run(c)
        require(r['candidates'][0]['status']=='calculation_failed' and r['set_status']=='unresolved','out-of-domain bias treated as observational exclusion')
        rows.append(dict(out_of_domain_bias=r['candidates'][0]['status']))
        return rows

    def ambiguity_and_bounds():
        r=run(deepcopy(config));states=[v['status'] for v in r['candidates']]
        require(states==['compatible','compatible','incompatible'],'expected default ambiguity changed')
        c,_=analytical(False,[.012,.012,.012]);a=run(c)['candidates'][0]['variant_results'][0]
        for p in c['packets']:p['samples']['local_rh']['uncertainty_abs']=.0005
        b=run(c)['candidates'][0]['variant_results'][0]
        require(b['common_bias_low']>=a['common_bias_low'] and b['common_bias_high']<=a['common_bias_high'],'tighter data expanded bias support')
        c,_=analytical(False,[.02,-.02,.02]);c['candidates'][0]['discrete_support_complete']=False
        require(run(c)['candidates'][0]['status']=='information_insufficient','incomplete support treated as excluded')
        return dict(default_statuses=states,tighter_bias_width=b['common_bias_high']-b['common_bias_low'])

    def uncertainty_and_failures():
        rows={}
        for label in ('unknown_assumptions','unknown_history','missing_rh','duplicate_rh','uncovered_contact','temperature_offset','bad_geometry','integration_failure','invalid_initial'):
            c=deepcopy(config)
            if label=='unknown_assumptions':c['assumptions']={k:('unknown' if k=='status' else None) for k in c['assumptions']}
            if label=='unknown_history':c['history']={k:('unknown' if k=='status' else None) for k in c['history']}
            if label=='missing_rh':c['packets'][-1]['samples']['local_rh']=None
            if label=='duplicate_rh':c['packets'][-1]['samples']['local_rh']['measured_at_s']=c['packets'][0]['samples']['local_rh']['measured_at_s']
            if label=='uncovered_contact':c['packets'][-1]['contact']['coverage_start_s']=590.
            if label=='temperature_offset':
                for pk in c['packets']:
                    pk['samples']['local_temp'].update(value=24.8,uncertainty_abs=.01)
                    pk['samples']['ambient_temp'].update(value=25.,uncertainty_abs=.01)
            if label=='bad_geometry':c['candidates'][0]['parameters']['volume_m3']=-1.
            if label=='integration_failure':c['candidates'][0]['parameters']['sensor_tau_s']=.00001
            if label=='invalid_initial':c['candidates'][0]['initial_variants'][0]['x0']=float('nan')
            r=run(c);row=r['candidates'][0]
            expected='calculation_failed' if label in ('bad_geometry','integration_failure','invalid_initial') else 'information_insufficient'
            require(row['status']==expected,'invalid classification '+label)
            require(r['set_status']=='unresolved','lost unresolved member')
            rows[label]=row['status']
        return rows

    def availability_and_isolation():
        c=deepcopy(config);baseline=run(c)
        changed=deepcopy(c);changed['producer_only']={'hidden_water_kg':999,'actual_future_weather':'rain'}
        require(run(changed)==baseline,'unconsumed truth changed output')
        trials=[]
        for label in ('future_observation','future_assumptions','future_candidate','future_history','hidden_packet','hidden_candidate'):
            c=deepcopy(config)
            if label=='future_observation':c['as_of_s']=c['packets'][0]['now_s']
            if label=='future_assumptions':c['assumptions']['issued_at_s']=c['as_of_s']+1
            if label=='future_candidate':c['candidates'][0]['declared_at_s']=c['as_of_s']+1
            if label=='future_history':c['history']['issued_at_s']=c['as_of_s']+1
            if label=='hidden_packet':c['packets'][0]['actual_future_weather']=1
            if label=='hidden_candidate':c['candidates'][0]['true_water_kg']=1
            try:run(c)
            except ValueError:trials.append(label)
            else:raise AssertionError('accepted '+label)
        require(run(deepcopy(config))==baseline,'later checks altered prior result')
        return dict(rejected=trials,scope='No output store: repeat computation of the same historical request stays identical.')

    def replay():
        old={p:sha(p) for p in manifest['artifacts_sha256']};old[P+'results/manifest.json']=sha(P+'results/manifest.json')
        cmd=[sys.executable,'-B','-X','utf8',P+'replay.py']
        proc=subprocess.run(cmd,cwd=ROOT,capture_output=True,encoding='utf-8',timeout=120)
        require(proc.returncode==0,proc.stdout+proc.stderr)
        require(all(sha(p)==h for p,h in old.items()),'replay changed artifacts')
        require(read(P+'results/compatibility.json')==run(deepcopy(config)),'artifact differs from computation')
        return dict(command=cmd,output=proc.stdout)

    for name,action in [('worker_suite',suite),('source_integrity',integrity),('analytic_bias_and_sampling_time',analytic_bias_time),
                        ('nonuniqueness_and_tightening',ambiguity_and_bounds),('unknowns_and_failures',uncertainty_and_failures),
                        ('availability_and_hidden_input',availability_and_isolation),('artifact_replay',replay)]:
        check(name,action)
    require(all(sha(p)==h for p,h in before.items()),'inputs changed during verification')
    passed=all(c['passed'] for c in checks)
    receipt=dict(schema_version=1,task='T10',milestone='M4',verified_at=datetime.now(timezone.utc).isoformat(),python=sys.version,
                 label='SYNTHETIC_UNCALIBRATED',collection_authorized=False,passed=passed,checks=checks,reviewed_sha256=before,
                 limits='Finite synthetic support with asserted model/error bounds; no calibrated probabilities, continuous parameter coverage, whole-garment ETA, or real collection authorization.')
    if args.write_receipt:RECEIPT.write_bytes((json.dumps(receipt,ensure_ascii=False,indent=2)+'\n').encode())
    print(json.dumps(dict(passed=passed,checks=[{k:c[k] for k in ('name','passed','error') if k in c} for c in checks]),ensure_ascii=False,indent=2))
    return 0 if passed else 1


if __name__=='__main__':
    raise SystemExit(main())

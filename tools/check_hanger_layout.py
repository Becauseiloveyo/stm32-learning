"""Independent T12 geometry and energy acceptance; no hardware claims."""
import argparse
from copy import deepcopy
from datetime import datetime,timezone
import hashlib,json,math,re,subprocess,sys
from pathlib import Path
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]
P='research/hanger_layout/'
RECEIPT='docs/verification/T12-tests.json'
def read(p):return json.loads((ROOT/p).read_text(encoding='utf-8-sig'))
def need(v,m):
 if not v:raise AssertionError(m)
def sha(p):
 a=(ROOT/p).resolve();need(a.is_relative_to(ROOT),'outside workspace');return hashlib.sha256(a.read_bytes()).hexdigest()
def main():
 ap=argparse.ArgumentParser();g=ap.add_mutually_exclusive_group();g.add_argument('--check-receipt',action='store_true');g.add_argument('--write-receipt',action='store_true');args=ap.parse_args()
 if args.check_receipt:
  r=read(RECEIPT);need(r['passed'] and r['checks'] and all(c['passed'] for c in r['checks']),'unpassed receipt')
  for p,h in r['reviewed_sha256'].items():need(sha(p)==h,'stale '+p)
  print('T12 receipt current and passed');return 0
 sys.path.insert(0,str(ROOT/P))
 from energy import simulate
 from geometry import overlaps,evaluate
 cfg=read(P+'config.json');manifest=read(P+'results/manifest.json')
 watched=set(manifest['inputs_sha256'])|set(manifest['artifacts_sha256'])|{P+'results/manifest.json','docs/HANGER-LAYOUT.md','docs/team/reports/layout-t12.md','tools/check_hanger_layout.py'}
 previous=read('docs/verification/T10-M6-tests.json')['reviewed_sha256']
 watched.update(previous);before={p:sha(p) for p in sorted(watched)};checks=[]
 def check(name,action):
  try:checks.append(dict(name=name,passed=True,detail=action()))
  except Exception as e:checks.append(dict(name=name,passed=False,error=f'{type(e).__name__}: {e}'))
 def sim(**kw):
  a=dict(capacity_ah=1.,voltage_v=1.,usable_fraction=1.,initial_fraction=1.,reserve_fraction=0.,baseline_load_w=0.,conversion_efficiency=.5,actions=[],charges=[],duration_s=100.);a.update(kw)
  r=simulate(**a);need(r['label']=='DESIGN_ASSUMPTIONS_NOT_MEASURED' and not r['collection_authorized'],'claim')
  residual=r['final_j']-(r['initial_j']+r['charge_after_efficiency_j']-r['curtailed_j']-r['baseline_delivered_j']-r['actions_used_j'])
  need(abs(residual)<1e-8 and abs(r['energy_residual_j'])<1e-8,'energy ledger')
  need(0<=r['final_j']<=r['capacity_j'] and r['baseline_unserved_j']>=-1e-9,'capacity/shortfall domain')
  return r
 def analytic_energy():
  a=sim(initial_fraction=.5,baseline_load_w=.25,duration_s=3600.)
  need(abs(a['final_j'])<1e-10 and a['baseline_delivered_j']==1800. and a['charge_after_efficiency_j']==0,'pure discharge/efficiency')
  b=sim(baseline_load_w=.25,charges=[dict(start_s=0.,end_s=25.,input_w=4.,efficiency=.5)])
  need(abs(b['final_j']-3562.5)<1e-10 and b['curtailed_j']==37.5,'simultaneous load/charge saturation')
  c=sim(initial_fraction=0.,baseline_load_w=.1,charges=[dict(start_s=50.,end_s=100.,input_w=.4,efficiency=.5)])
  need(abs(c['baseline_unserved_j']-10.)<1e-10 and c['final_j']==0,'future energy paid earlier baseline')
  d=sim(initial_fraction=0.,charges=[dict(start_s=50.,end_s=100.,input_w=.4,efficiency=.5)],actions=[dict(id='pulse',period_s=30.,energy_load_j=1.)])
  need(d['skipped']['pulse']==1 and d['performed']['pulse']==2 and abs(d['final_j']-6.)<1e-10,'action borrowed future energy')
  e=sim(initial_fraction=0.,charges=[dict(start_s=10.25,end_s=11.75,input_w=2.,efficiency=.8)],duration_s=20.)
  need(abs(e['final_j']-2.4)<1e-10,'fractional boundary')
  f=sim(initial_fraction=0.,charges=[dict(start_s=10.,end_s=20.,input_w=1.,efficiency=.5)],event_actions=[dict(id='notice',time_s=5.,energy_load_j=1.),dict(id='notice',time_s=20.,energy_load_j=1.)],include_trace=True,duration_s=30.)
  need(f['performed']['notice']==1 and f['skipped']['notice']==1 and f['final_j']==3.,'event notification accounting')
  return dict(discharge_final_j=a['final_j'],saturated_final_j=b['final_j'],curtailed_j=b['curtailed_j'],baseline_unserved_j=c['baseline_unserved_j'],precharge_skipped=d['skipped'],fractional_charge_j=e['final_j'],notification_final_j=f['final_j'])
 def invalid_inputs():
  bad=[dict(conversion_efficiency=0),dict(capacity_ah=-1),dict(initial_fraction=2),dict(reserve_fraction=True),dict(baseline_load_w=float('nan')),dict(charges=[dict(start_s=1,end_s=10,input_w=-1,efficiency=.9)]),dict(charges=[dict(start_s=1,end_s=20,input_w=1,efficiency=.9),dict(start_s=10,end_s=30,input_w=1,efficiency=.9)])]
  for x in bad:
   try:sim(**x)
   except ValueError:pass
   else:raise AssertionError('invalid energy accepted')
  for b in ([0,0,0,0,1,1],[0,0,False,1,1,1],[0,0,0,1,1,float('inf')]):
   try:overlaps(b,[0,0,0,1,1,1])
   except ValueError:pass
   else:raise AssertionError('invalid box accepted')
  need(overlaps([0,0,0,2,2,2],[1,1,1,3,3,3]),'volume overlap lost')
  need(not overlaps([0,0,0,1,1,1],[1,0,0,2,1,1]),'touching misinterpreted')
  need(not overlaps([0,0,0,2,2,2],[0,0,3,2,2,4]),'front projection hides z separation')
  return dict(invalid_energy=len(bad),invalid_geometry=3,boundary_and_depth_cases=3)
 def integrity():
  for p,h in read('docs/team/baseline-t12/snapshot.json')['inputs_sha256'].items():need(sha(p)==h,'frozen input changed '+p)
  for p,h in previous.items():need(sha(p)==h,'T10 changed '+p)
  for k in ('inputs_sha256','artifacts_sha256'):
   for p,h in manifest[k].items():need(sha(p)==h,'manifest drift '+p)
  return dict(inputs=len(manifest['inputs_sha256']),artifacts=len(manifest['artifacts_sha256']),protected_M6_files=len(previous))
 def suite():
  cmd=[sys.executable,'-B','-X','utf8',P+'test_layout.py'];r=subprocess.run(cmd,cwd=ROOT,capture_output=True,encoding='utf-8',timeout=120);need(r.returncode==0,r.stdout+r.stderr)
  count=re.search(r'Ran (\d+) tests?',r.stdout+r.stderr);need(count is not None,'missing test count')
  return dict(command=cmd,test_count=int(count.group(1)),output=r.stdout+r.stderr)
 def geometric_failures():
  # Independently enumerate every non-exempt physical pair, not its projection.
  from itertools import combinations
  entities=('main','battery','hanger_bar','central_probe','reference_head','rain_head','charge_port','garment')
  allowed={frozenset(('main','charge_port')),frozenset(('central_probe','garment'))}
  def intersect(a,b):return not any(a[i+3]<=b[i] or b[i+3]<=a[i] for i in range(3))
  records={}
  for name,case in cfg['layouts'].items():
   expected=[list(p) for p in combinations(entities,2) if frozenset(p) not in allowed and intersect(case[p[0]],case[p[1]])]
   actual=evaluate(case);need(actual['unintended_intersections']==expected,'missed physical intersection '+name)
   records[name]=expected
  for name,field in [('hook','hook_clear'),('neck','neck_clear')]:
   c=deepcopy(cfg['layouts']['left_bar']);c['battery']=list(c[name+'_clearance']);need(evaluate(c)[field] is False,'battery missing from clearance '+name)
  c=deepcopy(cfg['layouts']['left_bar']);c['central_probe']=[100,5,-5,116,15,5]
  need(evaluate(c)['central_contact_proxy'] is False,'unrelated contact zone mistaken for probe contact')
  c=deepcopy(cfg['layouts']['left_bar']);c['neighbor_clearance']=[-200,-10,10,200,220,30]
  need(evaluate(c)['neighbor_clear'] is False,'z neighboring garment missed')
  for name,changes in cfg['failure_mutations'].items():
   c=deepcopy(cfg['layouts']['left_bar']);c.update(changes);out=evaluate(c)
   need(any(v is False for v in out.values()),'failure example silently passes '+name)
  return dict(layout_intersections=records,mutations=len(cfg['failure_mutations']),independent_contact_and_battery_and_z_checks=4)
 def daily_budget():
  p=cfg['power'];records=[]
  for name,actions in p['workloads'].items():
   expected=p['baseline_load_w']*86400/p['conversion_efficiency']+sum(math.floor(86400/a['period_s'])*a['energy_load_j']/p['conversion_efficiency'] for a in actions)
   r=sim(capacity_ah=100.,voltage_v=p['voltage_v'],usable_fraction=p['usable_fraction'],reserve_fraction=p['reserve_fraction'],baseline_load_w=p['baseline_load_w'],conversion_efficiency=p['conversion_efficiency'],actions=actions,duration_s=86400)
   need(abs(r['baseline_delivered_j']+r['actions_used_j']-expected)<1e-8,'double loss or action count '+name)
   need(not any(r['skipped'].values()),'large battery case not admitted')
   records.append(dict(workload=name,battery_Wh_per_day=expected/3600))
  return records
 def interface_and_results():
  contract=read(P+'results/t11-interface.json');out=read(P+'results/analysis.json')
  need(contract['version']=='t11-power-1.0' and contract['collection_authorized'] is False,'interface version/authorization')
  need(contract['baseline']['side']=='load' and contract['charging']['input_power_side']=='external_input','unlabelled energy side')
  need(contract['charging']['future_energy_borrowing'] is False and contract['port_wet_detection']['available'] is False,'unavailable capability claimed')
  need(contract['heat_interference']['after_unplug_recovery_s'] is None,'invented thermal recovery')
  need(out['zero_pv_input_j']==0 and len(out['budget_matrix'])==27,'missing no-solar cases')
  gaps=0
  for row in out['budget_matrix']:
   r=row['actual'];expected=row['capacity_ah']*cfg['power']['voltage_v']*cfg['power']['usable_fraction']*3600
   need(abs(r['capacity_j']-expected)<1e-8 and r['charge_after_efficiency_j']==0,'capacity or no-charge accounting')
   need(abs(r['energy_residual_j'])<1e-7,'matrix conservation')
   need(all(r['performed'][a['id']]+r['skipped'][a['id']]==math.floor(row['days']*86400/a['period_s']) for a in cfg['power']['workloads'][row['workload']]),'actions disappeared')
   gaps+=int(any(r['skipped'].values()) or r['baseline_unserved_j']>1e-9)
  need(gaps>0,'no failure cases')
  return dict(matrix_cases=27,cases_with_service_gap=gaps,unmeasured_heat_and_peak_and_wetting=True)
 def replay():
  hashes={p:sha(p) for p in manifest['artifacts_sha256']};hashes[P+'results/manifest.json']=sha(P+'results/manifest.json')
  cmd=[sys.executable,'-B','-X','utf8',P+'replay.py'];r=subprocess.run(cmd,cwd=ROOT,capture_output=True,encoding='utf-8',timeout=180)
  need(r.returncode==0,r.stdout+r.stderr);need(all(sha(p)==h for p,h in hashes.items()),'replay bytes changed')
  return dict(command=cmd,artifact_count=len(manifest['artifacts_sha256']),byte_identical=True)
 for name,f in [('worker_suite',suite),('source_integrity',integrity),('independent_energy_analytics',analytic_energy),('domain_and_geometric_boundaries',invalid_inputs),('all_entity_clearances_and_failures',geometric_failures),('independent_daily_workload_budget',daily_budget),('interface_and_service_gaps',interface_and_results),('artifact_replay',replay)]:check(name,f)
 need(all(sha(p)==h for p,h in before.items()),'files changed during review')
 passed=all(c['passed'] for c in checks)
 r=dict(task='T12',verified_at=datetime.now(timezone.utc).isoformat(),python=sys.version,label='DESIGN_ASSUMPTIONS_NOT_MEASURED',collection_authorized=False,passed=passed,checks=checks,reviewed_sha256=before,limits='Synthetic AABB and average energy accounting; no mechanical, real battery, thermal, RF or waterproof validation.')
 if args.write_receipt:(ROOT/RECEIPT).write_bytes((json.dumps(r,ensure_ascii=False,indent=2)+'\n').encode())
 print(json.dumps({'passed':passed,'checks':[{k:c[k] for k in ('name','passed','error') if k in c} for c in checks]},indent=2));return 0 if passed else 1
if __name__=='__main__':raise SystemExit(main())

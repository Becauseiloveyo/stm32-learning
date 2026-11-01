"""Replay from workspace root; verify frozen inputs before generating own results."""
import copy,json,hashlib,platform
from pathlib import Path
import matplotlib,PIL
from geometry import evaluate
from energy import simulate
from draw import render
P=Path('research/hanger_layout');O=P/'results'
def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):Path(p).write_bytes((json.dumps(x,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
frozen=read('docs/team/baseline-t12/snapshot.json')['inputs_sha256']
for name,expected in frozen.items():
 if sha(name)!=expected:raise RuntimeError('frozen input changed: '+name)
c=read(P/'config.json');O.mkdir(exist_ok=True);p=c['power']
layouts={name:evaluate(case) for name,case in c['layouts'].items()}
failures={}
for name,mutation in c['failure_mutations'].items():
 case=copy.deepcopy(c['layouts']['left_bar']);case.update(mutation);failures[name]=evaluate(case)
for name,scenario in c['targeted_failure_cases'].items():
 case=copy.deepcopy(c['layouts'][scenario['layout']]);case.update(scenario['mutation']);failures[name]=evaluate(case)
for name,case in c['layouts'].items():render(case,O/(name+'.svg'),O/(name+'.png'),name)
matrix=[]
for capacity in p['capacities_ah']:
 for name,actions in p['workloads'].items():
  for days in p['days']:
   result=simulate(capacity,p['voltage_v'],p['usable_fraction'],p['initial_fraction'],p['reserve_fraction'],p['baseline_load_w'],p['conversion_efficiency'],actions,[],days*86400)
   # A full day of requested load is demand, not actual admitted work.
   daily=p['baseline_load_w']/p['conversion_efficiency']*86400+sum(a['energy_load_j']/p['conversion_efficiency']*86400/a['period_s'] for a in actions)
   matrix.append({'capacity_ah':capacity,'workload':name,'days':days,'requested_j_per_day':daily,'nominal_budget_margin_j':result['capacity_j']-daily*days,'actual':result})
charge_kwargs=[.25,p['voltage_v'],p['usable_fraction'],1,p['reserve_fraction'],p['baseline_load_w'],p['conversion_efficiency'],p['workloads']['normal'],p['charging_demo'],7200]
charging_full=simulate(*charge_kwargs,include_trace=True);charge_kwargs[3]=0;charging_empty=simulate(*charge_kwargs,include_trace=True)
result={'label':c['label'],'seed':None,'layout_checks':layouts,'failure_cases':failures,'budget_matrix':matrix,'charging_start_full':charging_full,'charging_start_empty':charging_empty,'zero_pv_input_j':0,'collection_authorized':False};save(O/'analysis.json',result)
interface={'version':'t11-power-1.0','label':c['label'],'source_config':'research/hanger_layout/config.json','units':{'energy':'J','power':'W','time':'s','capacity':'Ah','voltage':'V'},'baseline':{'power_w':p['baseline_load_w'],'side':'load','battery_power_w':p['baseline_load_w']/p['conversion_efficiency']},'actions':p['workloads'],'event_actions':{'schema':{'id':'string','time_s':'nonnegative seconds','energy_load_j':'J load-side'},'wifi_event_example':{'energy_load_j':4.8,'side':'load','source':'synthetic 0.6 W for 8 s, incremental, not measured'},'rule':'explicit event times; no automatic event count; event cost may differ for retries'},'conversion':{'load_to_battery_efficiency':p['conversion_efficiency'],'rule':'battery_j=load_j/efficiency exactly once'},'capacity':{'voltage_v':p['voltage_v'],'usable_fraction':p['usable_fraction'],'candidates_ah':p['capacities_ah'],'formula':'usable_j=Ah*V*usable_fraction*3600','reserve_fraction':p['reserve_fraction']},'charging':{'source':'manual_external_only','input_power_side':'external_input','interval':'[start_s,end_s)','battery_power_formula':'input_w*efficiency','events_example':p['charging_demo'],'surplus':'curtailed','future_energy_borrowing':False},'heat_interference':{'while_charging':'possible_unknown','after_unplug_recovery_s':None,'source':'no measured thermal recovery','automatic_detection':False},'port_wet_detection':{'available':False,'status':'unknown'},'port_state_contract':{'closed':'no plug engaged; closure unverified','open':'cover opened; insertion geometry only','charging':'external connection declared, heat interference possible','unplugged':'charge stops at end; thermal recovery time unknown'},'unknowns':{'peak_current_a':None,'instantaneous_voltage_drop_v':None,'charge_cc_cv':None,'temperature_aging_capacity_curve':None,'target_mcu_ram_bytes':None},'dispatch_rule':'At a timestamp integrate up to it, then process periodic actions followed by explicit event actions in input order; each consumes battery energy only if reserve remains.','failure_semantics':{'insufficient_reserve':'action_not_performed','baseline_unserved':'service_gap','full_battery':'curtailed_charge','peak_current':'unknown_not_inferred'},'scope':'Timer workload excludes additional rain/dry event notifications unless explicitly supplied; STM32 acquisition/local status; M6 ensemble computed on PC; no PC compute energy attributed to MCU','collection_authorized':False};save(O/'t11-interface.json',interface)
inputs={**frozen,'docs/team/baseline-t12/snapshot.json':sha('docs/team/baseline-t12/snapshot.json')}
for f in ('geometry.py','energy.py','draw.py','test_layout.py','config.json','replay.py'):inputs[(P/f).as_posix()]=sha(P/f)
artifacts=[O/'analysis.json',O/'t11-interface.json',*[O/(n+suffix) for n in c['layouts'] for suffix in ('.svg','.png')]]
save(O/'manifest.json',{'label':c['label'],'seed':None,'commands':['python -B -X utf8 research/hanger_layout/test_layout.py','python -B -X utf8 research/hanger_layout/replay.py'],'dependencies':{'python':platform.python_version(),'matplotlib':matplotlib.__version__,'Pillow':PIL.__version__},'inputs_sha256':dict(sorted(inputs.items())),'artifacts_sha256':{x.as_posix():sha(x) for x in artifacts},'limits':'Synthetic geometry and power, render/float bytes can differ across platforms.'})
print('layouts',layouts);print('matrix',len(matrix),'max residual',max(abs(r['actual']['energy_residual_j']) for r in matrix));print('charge full curtailed',charging_full['curtailed_j'],'empty first skipped',charging_empty['first_skipped_s'])

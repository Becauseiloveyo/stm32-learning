"""Battery energy J, times seconds. Piecewise continuous plus instantaneous actions."""
import math

def n(x):
 if isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x):raise ValueError('finite number required')
 return x

def simulate(capacity_ah, voltage_v, usable_fraction, initial_fraction, reserve_fraction, baseline_load_w, conversion_efficiency, actions, charges, duration_s, include_trace=False, event_actions=None):
 if type(include_trace) is not bool:raise ValueError('include_trace must be bool')
 vals=[capacity_ah,voltage_v,usable_fraction,initial_fraction,reserve_fraction,baseline_load_w,conversion_efficiency,duration_s]
 for x in vals:n(x)
 if not(capacity_ah>0 and voltage_v>0 and 0<usable_fraction<=1 and 0<=initial_fraction<=1 and 0<=reserve_fraction<1 and baseline_load_w>=0 and 0<conversion_efficiency<=1 and duration_s>=0):raise ValueError('invalid config')
 cap=capacity_ah*voltage_v*usable_fraction*3600; energy=cap*initial_fraction; initial=energy; reserve=cap*reserve_fraction
 if len({a.get('id') for a in actions})!=len(actions):raise ValueError('duplicate action id')
 events={0.,duration_s}; periods=[]
 for a in actions:
  if set(a)!={'id','period_s','energy_load_j'} or not isinstance(a['id'],str) or not a['id']:raise ValueError('invalid action')
  period=n(a['period_s']);cost=n(a['energy_load_j'])
  if period<=0 or cost<0:raise ValueError('invalid action cost')
  if duration_s/period>100000:raise ValueError('action budget exceeded')
  times=[];i=1
  while i*period<=duration_s:
   t=i*period;times.append(t);events.add(t);i+=1
  periods.append((a['id'],times,cost/conversion_efficiency))
 prev_end=0
 for e in sorted(charges,key=lambda x:x['start_s']):
  if set(e)!={'start_s','end_s','input_w','efficiency'}:raise ValueError('invalid charging event')
  start,end,p,eff=map(n,(e['start_s'],e['end_s'],e['input_w'],e['efficiency']))
  if not(0<=start<end<=duration_s and start>=prev_end and p>=0 and 0<eff<=1):raise ValueError('invalid/overlapping charges')
  events.update([start,end]);prev_end=end
 if event_actions is None:event_actions=[]
 if type(event_actions) is not list or len(event_actions)>100000:raise ValueError('invalid event actions')
 bytime={}
 for name,times,cost in periods:
  for t in times:bytime.setdefault(t,[]).append((name,cost))
 for a in event_actions:
  if set(a)!={'id','time_s','energy_load_j'} or not isinstance(a['id'],str) or not a['id']:raise ValueError('invalid event action')
  t=n(a['time_s']);cost=n(a['energy_load_j'])
  if not 0<=t<=duration_s or cost<0:raise ValueError('invalid event time/cost')
  events.add(t);bytime.setdefault(t,[]).append((a['id'],cost/conversion_efficiency))
 all_ids=sorted({a['id'] for a in actions}|{a['id'] for a in event_actions})
 generated=curtailed=base_required=base_delivered=action_used=0.;skipped={name:0 for name in all_ids};performed={name:0 for name in all_ids};minimum=energy;previous=0.;first_skipped={name:None for name in all_ids};first_unserved=None;trace=[]
 for t in sorted(events):
  before=energy;h=t-previous;power=sum(e['input_w']*e['efficiency'] for e in charges if e['start_s']<=previous<e['end_s']); gen=power*h;need=baseline_load_w/conversion_efficiency*h;generated+=gen;base_required+=need
  raw=energy+gen-need; unmet=max(0.,-raw);curtail=max(0.,raw-cap);curtailed+=curtail;energy=max(0.,min(cap,raw));base_delivered+=need-unmet
  depleted_at=previous+(before/(need/h-power) if h and need/h>power else 0) if unmet>0 else None
  full_at=previous+((cap-before)/(power-need/h) if h and power>need/h else 0) if curtail>0 else None
  if unmet>0 and first_unserved is None:first_unserved=depleted_at
  admitted=[]
  for name,cost in bytime.get(t,[]):
   if energy-cost>=reserve:energy-=cost;action_used+=cost;performed[name]+=1;admitted.append({'id':name,'status':'performed','battery_cost_j':cost})
   else:
    skipped[name]+=1;first_skipped[name]=t if first_skipped[name] is None else first_skipped[name];admitted.append({'id':name,'status':'insufficient_reserve','battery_cost_j':cost})
  if include_trace:trace.append({'interval_start_s':previous,'time_s':t,'energy_before_j':before,'charge_generated_j':gen,'baseline_required_j':need,'baseline_unserved_j':unmet,'curtailed_j':curtail,'full_at_s':full_at,'depleted_at_s':depleted_at,'actions':admitted,'energy_after_j':energy})
  minimum=min(minimum,energy);previous=t
 return {'capacity_j':cap,'initial_j':initial,'final_j':energy,'reserve_j':reserve,'charge_after_efficiency_j':generated,'curtailed_j':curtailed,'baseline_required_j':base_required,'baseline_delivered_j':base_delivered,'baseline_unserved_j':base_required-base_delivered,'actions_used_j':action_used,'performed':performed,'skipped':skipped,'minimum_j':minimum,'first_skipped_s':first_skipped,'first_baseline_unserved_s':first_unserved,'trace':trace if include_trace else None,'energy_residual_j':energy-(initial+generated-curtailed-base_delivered-action_used),'label':'DESIGN_ASSUMPTIONS_NOT_MEASURED','collection_authorized':False}


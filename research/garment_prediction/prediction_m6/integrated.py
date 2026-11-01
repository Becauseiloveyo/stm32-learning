"""M6 synthetic finite candidate/history/conditional future integration."""
import math,sys
from copy import deepcopy
from pathlib import Path
BASE=Path(__file__).resolve().parent.parent
sys.path[:0]=[str(BASE/'coupled_m5'),str(BASE/'observation_m2')]
from coupled import Zone,Drive,check,step,rhs,transition
from contract import observe

MAX_STEPS=200000

def fields(d,n):
 if type(d)!=dict or set(d)!=set(n):raise ValueError('fields mismatch')
def number(v):
 if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v):raise ValueError('finite number required')
 return v

def coverage(packet,start,end):
 for k,expected in [('contact','confirmed'),('charging_heat','no_known_interference')]:
  e=packet[k]
  if e['state']!=expected or e['coverage_start_s']>start or e['coverage_end_s']<end:return False
 return True

def trace(z,c,y,prior,segments,ref,targets,dt,bias):
 """Propagate continuous complete state; outputs at exact event times, RH domain at RK4 stages."""
 current=ref; prev=prior; lo,hi=bias; output={}
 def bound(v):
  nonlocal lo,hi
  lo=max(lo,-v[5]);hi=min(hi,1-v[5])
 def add(v,k,h):return tuple(a+h*b for a,b in zip(v,k))
 bound(y)
 for end,d in segments:
  y,_=transition(z,prev,d,c,y);prev=d;bound(y)
  for target in sorted(t for t in targets if current<=t<=end):
   while current<target:
    h=min(dt,target-current)
    if current+h<=current:raise ValueError('time step too small for clock precision')
    k1=rhs(z,d,c,y);s2=add(y,k1,h/2);k2=rhs(z,d,c,s2);s3=add(y,k2,h/2);k3=rhs(z,d,c,s3);s4=add(y,k3,h)
    for x in (s2,s3,s4):bound(x)
    y=step(z,d,c,y,h);bound(y);current+=h
   output[target]=(y,prev,lo,hi)
  while current<end:
   h=min(dt,end-current)
   if current+h<=current:raise ValueError('time step too small for clock precision')
   k1=rhs(z,d,c,y);s2=add(y,k1,h/2);k2=rhs(z,d,c,s2);s3=add(y,k2,h/2);k3=rhs(z,d,c,s3);s4=add(y,k3,h)
   for x in (s2,s3,s4):bound(x)
   y=step(z,d,c,y,h);bound(y);current+=h
  current=end
 return output,y,prev,lo,hi

def parse_segments(raw,ref,end,asof,historical):
 if type(raw)!=list or not raw:raise ValueError('segments required')
 result=[];last=ref
 for s in raw:
  fields(s,['end_s','drive','available_at_s']);t=number(s['end_s']);available=number(s['available_at_s'])
  if t<=last or not 0<=available<=asof:raise ValueError('segment order or availability')
  if historical and t>asof:raise ValueError('future in historical drivers')
  result.append((t,Drive(**s['drive'])));last=t
 if last!=end:raise ValueError('segment horizon mismatch')
 return result

def future_run(z,c,y,prior,segs,now,dt,bias,target,fid):
 """Retain successes even if later steps fail; grid includes segment boundaries."""
 blo,bhi=bias;current=now;first=None;previous=None;rewet=False;drive=prior
 def hit(state):return all(state[2*i]/z[i].dry_kg<=target for i in (0,1))
 if hit(y):first=now;previous=now
 try:
  for end,next_drive in segs:
   y,_=transition(z,drive,next_drive,c,y);drive=next_drive
   while current<end:
    t=min(current+dt,end)
    if t<=current:raise ValueError('time step too small for clock precision')
    _,new,_,flo,fhi=trace(z,c,y,drive,[(t,drive)],current,[],dt,(blo,bhi))
    if flo>blo+1e-12 or fhi<bhi-1e-12:raise ValueError('future sensor domain does not cover prior bias interval')
    prior_time=current;y=new;current=t
    if hit(y):
     if first is None:first=current;previous=prior_time
    elif first is not None:rewet=True
  return {'id':fid,'status':'reached_sampled' if first is not None else 'not_reached','first_sample_s':first,'previous_sample_s':previous,'remaining_sample_s':None if first is None else first-now,'later_sample_above_target':rewet,'last_successful_time_s':current,'final_state':y,'final_drive':vars(drive)}
 except (ValueError,OverflowError) as e:
  return {'id':fid,'status':'calculation_failed','reason':str(e),'first_sample_s':first,'previous_sample_s':previous,'remaining_sample_s':None if first is None else first-now,'later_sample_above_target':rewet,'last_successful_time_s':current,'last_state':y,'last_drive':vars(drive)}

def predict(req):
 fields(req,['version','reference_s','as_of_s','history_segments','observations','policy','sensor_history','assumptions','candidates','futures','target_x','step_s'])
 if req['version']!='m6-v1':raise ValueError('version')
 ref=number(req['reference_s']);now=number(req['as_of_s']);dt=number(req['step_s']);target=number(req['target_x'])
 if not 0<=ref<now or dt<=0 or not 0<=target<=5:raise ValueError('time/target')
 hist=parse_segments(req['history_segments'],ref,now,now,True)
 if type(req['observations'])!=list:raise ValueError('observation list required')
 for p in req['observations']:
  if number(p['now_s'])>now:raise ValueError('observation unavailable')
 reports=[observe(p,req['policy']) for p in req['observations']]
 h=req['sensor_history'];fields(h,['status','reference_s','issued_at_s','initial_rh','uncertainty_abs','source'])
 if h['status'] not in ('unknown','externally_characterized'):raise ValueError('history status')
 if h['status']=='unknown':
  if any(h[k] is not None for k in ('reference_s','issued_at_s','initial_rh','uncertainty_abs','source')):raise ValueError('unknown history')
 else:
  if h['source'] not in ('synthetic_external','operator','external_evidence') or number(h['reference_s'])!=ref or not ref<=number(h['issued_at_s'])<=now or not 0<=number(h['initial_rh'])<=1 or not 0<=number(h['uncertainty_abs'])<=1:raise ValueError('history evidence')
 a=req['assumptions'];fields(a,['status','source','issued_at_s','structural_rh','numeric_rh','guard_rh','temperature_error_c'])
 if a['status'] not in ('unknown','asserted'):raise ValueError('assumptions')
 if a['status']=='unknown':
  if any(a[k] is not None for k in ('source','issued_at_s','structural_rh','numeric_rh','guard_rh','temperature_error_c')):raise ValueError('unknown assumptions must be empty')
 else:
  if a['source'] not in ('synthetic_external','operator','external_evidence') or not ref<=number(a['issued_at_s'])<=now or any(number(a[k])<0 for k in ('structural_rh','numeric_rh','guard_rh','temperature_error_c')):raise ValueError('invalid assumptions')
 # No hidden measurement or reordered/duplicate observations; external evidence covers response history.
 usable=len(reports)>=2 and h['status']=='externally_characterized' and a['status']=='asserted' and all(r['local_record_allowed'] for r in reports)
 for first,second in zip(req['observations'],req['observations'][1:]):
  if first['now_s']>=second['now_s']:usable=False
  for k in ('ambient_temp','ambient_rh','local_temp','local_rh'):
   x,y=first['samples'][k],second['samples'][k]
   if x is None or y is None or x['measured_at_s']>=y['measured_at_s']:usable=False
 if len({p['installation_id'] for p in req['observations']})!=1:usable=False
 if not req['observations'] or not coverage(req['observations'][-1],ref,now):usable=False
 # No actual samples beyond analysis time or before reference.
 for p in req['observations']:
  for sample in p['samples'].values():
   if sample is not None and not ref<=sample['measured_at_s']<=now:raise ValueError('sample outside history')
 futures=[]
 for f in req['futures']:
  fields(f,['id','segments','horizon_s','source']);end=number(f['horizon_s'])
  if not isinstance(f['id'],str) or not f['id'] or f['source']!='synthetic_scenario' or end<=now:raise ValueError('future scenario')
  futures.append((f['id'],end,parse_segments(f['segments'],now,end,now,False)))
 if not futures:raise ValueError('futures required')
 if type(req['candidates'])!=list or not req['candidates']:raise ValueError('candidates required')
 def unique(ids):
  if any(not isinstance(x,str) or not x for x in ids) or len(set(ids))!=len(ids):raise ValueError('missing or duplicate identifiers')
 unique([x[0] for x in futures]);unique([x['id'] for x in req['candidates']])
 for c in req['candidates']:
  if type(c.get('variants'))!=list or not c['variants']:raise ValueError('variants required')
  unique([v['id'] for v in c['variants']])
 def steps(duration):
  ratio=duration/dt
  if not math.isfinite(ratio) or ratio>MAX_STEPS:raise ValueError('step budget exceeded')
  return math.ceil(ratio)
 variants=sum(len(c['variants']) for c in req['candidates'])
 estimated=variants*(steps(now-ref)+len(hist)+len(req['observations'])+sum(steps(end-now)+len(segs) for _,end,segs in futures))
 if estimated>MAX_STEPS:raise ValueError('step budget exceeded')
 rows=[]
 for cand in req['candidates']:
  fields(cand,['id','declared_at_s','zones','coupling','prior_drive','variants','complete_discrete_support','bias_interval'])
  if not isinstance(cand['id'],str) or not cand['id'] or not ref<=number(cand['declared_at_s'])<=now or type(cand['complete_discrete_support']) is not bool or not cand['variants']:raise ValueError('candidate declaration')
  lo,hi=cand['bias_interval'];lo=number(lo);hi=number(hi)
  if not -1<=lo<=hi<=1:raise ValueError('bias interval')
  row={'id':cand['id'],'status':'information_insufficient','reason':None,'variants':[]}
  if not usable or not cand['complete_discrete_support']:
   row['reason']='observation_evidence_or_assumptions_unknown';rows.append(row);continue
  try:
   zones=[Zone(**z) for z in cand['zones']];c=dict(cand['coupling']);prior=Drive(**cand['prior_drive'])
   if c['sensor_bias_rh']!=0:raise ValueError('bias must be separate')
   # Temperature and humidity boundaries are compared at each sample's own time.
   def drive_at(t):
    for end,d in hist:
     if t<end:return d
    return hist[-1][1]
   for p in req['observations']:
    for key in ('ambient_temp','ambient_rh','local_temp'):
     sample=p['samples'][key];d=drive_at(sample['measured_at_s']);expected=d.rh if key=='ambient_rh' else d.air_c
     extra=0 if key=='ambient_rh' else a['temperature_error_c']
     if abs(expected-sample['value'])>sample['uncertainty_abs']+extra:raise LookupError('environment_or_temperature_unexplained')
   rh_times=[p['samples']['local_rh']['measured_at_s'] for p in req['observations']]
   for variant in cand['variants']:
    fields(variant,['id','initial_state']);y=tuple(variant['initial_state'])
    check(zones,prior,c,y)
    if abs(y[5]-h['initial_rh'])>h['uncertainty_abs']:
     row['variants'].append({'id':variant['id'],'status':'outside_initial_evidence'});continue
    try:
     samples,endstate,last,blo,bhi=trace(zones,c,y,prior,hist,ref,rh_times,dt,(lo,hi))
     if blo>bhi:raise ValueError('sensor bias outside M5 domain')
     for p in req['observations']:
      s=p['samples']['local_rh'];pred=samples[s['measured_at_s']][0][5];e=s['uncertainty_abs']+a['structural_rh']+a['numeric_rh'];blo=max(blo,s['value']-pred-e);bhi=min(bhi,s['value']-pred+e)
     gap=blo-bhi
     if gap>0:
      row['variants'].append({'id':variant['id'],'status':'information_insufficient' if gap<=a['guard_rh'] else 'incompatible','bias_gap':gap});continue
     vr={'id':variant['id'],'status':'compatible','bias_interval':[blo,bhi],'history_end_state':endstate,'history_end_drive':vars(last),'future':[]}
     for fid,horizon,segs in futures:
      vr['future'].append(future_run(zones,c,endstate,last,segs,now,dt,(blo,bhi),target,fid))
     row['variants'].append(vr)
    except (ValueError,OverflowError) as e:row['variants'].append({'id':variant['id'],'status':'calculation_failed','reason':str(e)})
   statuses=[v['status'] for v in row['variants']]
   row['status']='calculation_failed' if 'calculation_failed' in statuses else 'information_insufficient' if 'information_insufficient' in statuses else 'compatible' if 'compatible' in statuses else 'incompatible'
  except LookupError as e:row['reason']=str(e)
  except (ValueError,TypeError,KeyError,OverflowError) as e:row.update(status='calculation_failed',reason=str(e))
  rows.append(row)
 unresolved=any(r['status'] in ('calculation_failed','information_insufficient') for r in rows)
 all_incompatible=all(r['status']=='incompatible' for r in rows)
 items=[f for r in rows for v in r['variants'] if v['status']=='compatible' for f in v['future']]
 if any(f['status']!='reached_sampled' for f in items):unresolved=True
 upper=None if unresolved or all_incompatible or not items else max(f['first_sample_s'] for f in items)
 return {'request_version':req['version'],'state_version':'m5-v1','reference_s':ref,'target_x_kg_kg':target,'step_s':dt,'future_conditions':deepcopy(req['futures']),'step_budget':MAX_STEPS,'estimated_max_steps':estimated,'label':'SYNTHETIC_UNCALIBRATED','collection_authorized':False,'scope':'finite_conditional_m5','as_of_s':now,'rows':rows,'upper_sample_s':upper,'upper_remaining_s':None if upper is None else upper-now,'unresolved':unresolved,'set_status':'coverage_or_model_mismatch' if all_incompatible else 'unresolved' if unresolved else 'conditional','time_semantics':'grid detection only; continuous crossings may be missed'}

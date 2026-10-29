"""Conditional sampled-time envelope; no observation assimilation."""
import math, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from model import Zone, Drive, state, validate, advance, IntegrationStepError

def fields(o,keys):
    if type(o)!=dict or set(o)!=set(keys): raise ValueError('unexpected or missing fields')
def num(x):
    if isinstance(x,bool) or not isinstance(x,(float,int)) or not math.isfinite(x): raise ValueError('finite number required')
    return x

def calculate(req):
    fields(req,['members','scenarios','target_x','step_s','computed_at_s'])
    if not isinstance(req['members'],list) or not req['members'] or not isinstance(req['scenarios'],list) or not req['scenarios']: raise ValueError('nonempty members and scenarios required')
    target=num(req['target_x']); dt=num(req['step_s']); now=num(req['computed_at_s'])
    if not 0<=target<=5 or dt<=0 or now<0: raise ValueError('invalid target, time or step')
    cases=[]
    for member in req['members']:
        fields(member,['id','zones','x0','temperature_c'])
        if not isinstance(member['id'],str) or not member['id'] or len(member['zones'])!=2 or len(member['x0'])!=2 or len(member['temperature_c'])!=2: raise ValueError('invalid member')
        zones=[Zone(**z) for z in member['zones']]; xs=[num(x) for x in member['x0']]; ts=[num(t) for t in member['temperature_c']]
        for sc in req['scenarios']:
            fields(sc,['id','segments'])
            if not isinstance(sc['id'],str) or not sc['id'] or not isinstance(sc['segments'],list) or not sc['segments']: raise ValueError('invalid scenario')
            segments=[]; last=0
            for seg in sc['segments']:
                fields(seg,['end_s','drives']); end=num(seg['end_s'])
                if end<=last or len(seg['drives'])!=2: raise ValueError('invalid segment')
                drives=[Drive(**d) for d in seg['drives']]; segments.append((end,drives)); last=end
            item={'member_id':member['id'],'scenario_id':sc['id'],'status':None,'first_joint_sample_s':None,'previous_sample_s':None,'later_sample_above_target':False,'error':None,'horizon_s':last}
            try:
                ys=[state(z,x,t) for z,x,t in zip(zones,xs,ts)]
                for z,d,y in zip(zones,segments[0][1],ys): validate(z,d,*y)
                reached=lambda: all(y[0]/z.dry_kg<=target for z,y in zip(zones,ys))
                t=0.; prior=None
                if reached(): item['first_joint_sample_s']=0.; item['previous_sample_s']=0.
                for end,drives in segments:
                    for z,d,y in zip(zones,drives,ys): validate(z,d,*y)
                    while t<end:
                        h=min(dt,end-t); new=[advance(z,d,y,h) for z,d,y in zip(zones,drives,ys)]
                        prior=t; t+=h; ys=new
                        if reached():
                            if item['first_joint_sample_s'] is None: item['first_joint_sample_s']=t; item['previous_sample_s']=prior
                        elif item['first_joint_sample_s'] is not None: item['later_sample_above_target']=True
                item['status']='reached_sampled' if item['first_joint_sample_s'] is not None else 'not_reached_within_horizon'
            except (ValueError,OverflowError) as exc:
                item['status']='numerical_step_failure' if isinstance(exc,IntegrationStepError) else 'outside_domain_or_invalid_member'
                item['error']=str(exc)
            cases.append(item)
    successes=[c for c in cases if c['status']=='reached_sampled']; unresolved=[c for c in cases if c['status']!='reached_sampled']
    return {'mode':'scenario_only','observation_mapping':'unavailable','label':'SYNTHETIC_UNCALIBRATED','collection_authorized':False,'computed_at_s':now,'target_x':target,'step_s':dt,'cases':cases,'earliest_known_sample_s':min((c['first_joint_sample_s'] for c in successes),default=None),'upper_sample_s':None if unresolved else max(c['first_joint_sample_s'] for c in successes),'unresolved_count':len(unresolved),'time_semantics':'first joint sampled point; earlier substep crossings may be missed; not a guaranteed continuous crossing bracket'}

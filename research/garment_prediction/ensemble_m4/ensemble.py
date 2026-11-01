"""Finite hypothesis compatibility, conditional on explicitly supplied bounds."""
import math,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'observation_m3'))
from physics import params,check,advance,rates,rho_sat
from consumer import evaluate

def fields(o,keys):
    if type(o) is not dict or set(o)!=set(keys):raise ValueError('unexpected or missing fields')
def num(x):
    if isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x):raise ValueError('finite number required')
    return x

def compare(packets,policy,history,gate,assumptions,candidates,as_of_s):
    num(as_of_s)
    fields(gate,['temp_delta_c','rh_delta','local_temp_delta_c','temp_offset_c'])
    fields(assumptions,['status','source','issued_at_s','structural_abs_rh','numerical_abs_rh','boundary_guard_rh','dt_s'])
    if type(candidates)!=list or not candidates:raise ValueError('candidates required')
    if as_of_s<0:raise ValueError('invalid as_of')
    for p in packets:
        if num(p['now_s'])>as_of_s:raise ValueError('observation not yet available')
    if assumptions['status'] not in ('asserted','unknown'):raise ValueError('unknown assumptions status')
    if assumptions['status']=='unknown':
        if any(assumptions[k] is not None for k in ('source','issued_at_s','structural_abs_rh','numerical_abs_rh','boundary_guard_rh','dt_s')):raise ValueError('unknown assumptions cannot claim bounds')
    else:
        if assumptions['source'] not in ('synthetic_external','external_evidence','operator'):raise ValueError('source required')
        if not 0<=num(assumptions['issued_at_s'])<=as_of_s:raise ValueError('future assumptions')
        for k in ('structural_abs_rh','numerical_abs_rh','boundary_guard_rh'):
            if num(assumptions[k])<0:raise ValueError('invalid bound')
        if num(assumptions['dt_s'])<=0:raise ValueError('invalid step')
    q=evaluate(packets,policy,gate['temp_delta_c'],gate['rh_delta'],history,gate['local_temp_delta_c'],gate['temp_offset_c'])
    results=[]
    for c in candidates:
        fields(c,['id','declared_at_s','parameters','initial_variants','discrete_support_complete','bias_interval_rh'])
        if not isinstance(c['id'],str) or not c['id'] or not 0<=num(c['declared_at_s'])<=as_of_s:raise ValueError('candidate declaration invalid/future')
        if type(c['discrete_support_complete']) is not bool or type(c['initial_variants']) is not list or not c['initial_variants']:raise ValueError('invalid candidate support')
        if type(c['bias_interval_rh']) is not list or len(c['bias_interval_rh'])!=2:raise ValueError('invalid bias interval')
        blo,bhi=map(num,c['bias_interval_rh'])
        if not -1<=blo<=bhi<=1:raise ValueError('invalid bias domain')
        row={'id':c['id'],'status':None,'reason':None,'matching_variants':0,'failed_variants':0,'variant_results':[]}
        if not q['applicable'] or assumptions['status']=='unknown' or not c['discrete_support_complete']:
            row.update(status='information_insufficient',reason='observable_or_model_assumptions_or_initial_support_unknown');results.append(row);continue
        try:params(c['parameters'])
        except (ValueError,TypeError) as e:row.update(status='calculation_failed',reason=str(e));results.append(row);continue
        if c['parameters']['sensor_bias_rh'] != 0:
            row.update(status='information_insufficient',reason='bias_must_be_separately_declared');results.append(row);continue
        p=dict(c['parameters'])
        # No unverified material/environment estimate: declared environment must lie within every observed bound.
        if any(abs(p['temperature_c']-pk['samples']['ambient_temp']['value'])>pk['samples']['ambient_temp']['uncertainty_abs'] or abs(p['temperature_c']-pk['samples']['local_temp']['value'])>pk['samples']['local_temp']['uncertainty_abs'] or abs(p['ambient_rh']-pk['samples']['ambient_rh']['value'])>pk['samples']['ambient_rh']['uncertainty_abs'] for pk in packets):
            row.update(status='information_insufficient',reason='declared_environment_not_validated');results.append(row);continue
        boundary=False
        for index,v in enumerate(c['initial_variants']):
            fields(v,['x0','rh0','sensor_rh0'])
            try:
                x,r,z=map(num,(v['x0'],v['rh0'],v['sensor_rh0']))
                if not (0<=x<=5 and 0<=r<=1 and 0<=z<=1): raise ValueError('initial state outside domain')
                if abs(z-history['initial_state_rh'])>history['uncertainty_abs']:
                    row['variant_results'].append({'index':index,'status':'outside_declared_initial_evidence'});continue
                sat=rho_sat(p['temperature_c']);y=(p['dry_kg']*x,sat*r,sat*z,0.);check(p,y)
                t=history['reference_time_s']; lo,hi=blo,bhi; domain_lo,domain_hi=blo,bhi
                def domain(state):
                    nonlocal domain_lo,domain_hi
                    response=state[2]/sat
                    domain_lo=max(domain_lo,-response); domain_hi=min(domain_hi,1-response)
                def plus(state,k,h):return tuple(a+h*b for a,b in zip(state,k))
                domain(y)
                for pk in packets:
                    sample=pk['samples']['local_rh'];tgt=sample['measured_at_s']
                    while t<tgt:
                        h=min(assumptions['dt_s'],tgt-t)
                        # M3 RK4 explicitly checks its three intermediate states plus endpoint.
                        k1=rates(p,y);s2=plus(y,k1,h/2);k2=rates(p,s2)
                        s3=plus(y,k2,h/2);k3=rates(p,s3);s4=plus(y,k3,h)
                        for stage in (s2,s3,s4):domain(stage)
                        y=advance(p,y,h);domain(y);t+=h
                    pred=y[2]/sat; e=sample['uncertainty_abs']+assumptions['structural_abs_rh']+assumptions['numerical_abs_rh']
                    lo=max(lo,sample['value']-pred-e);hi=min(hi,sample['value']-pred+e)
                if domain_lo>domain_hi: raise ValueError('no bias keeps checked M3 stages in RH domain')
                lo=max(lo,domain_lo);hi=min(hi,domain_hi)
                gap=lo-hi
                vstatus='compatible' if gap<=0 else 'near_boundary' if gap<=assumptions['boundary_guard_rh'] else 'incompatible'
                row['variant_results'].append({'index':index,'status':vstatus,'common_bias_low':lo,'common_bias_high':hi,'stage_domain_bias_low':domain_lo,'stage_domain_bias_high':domain_hi,'gap_rh':max(0.,gap)})
                if gap<=0:row['matching_variants']+=1
                elif gap<=assumptions['boundary_guard_rh']:boundary=True
            except (ValueError,OverflowError) as e:
                row['failed_variants']+=1;row['reason']=str(e);row['variant_results'].append({'index':index,'status':'calculation_failed','reason':str(e)})
        if row['failed_variants']:row['status']='calculation_failed'
        elif row['matching_variants']:row.update(status='compatible',reason='at_least_one_discrete_variant_has_one_common_bias')
        elif boundary:row.update(status='information_insufficient',reason='near_bound_numerical_assumption')
        else:row.update(status='incompatible',reason='no_discrete_variant_has_a_common_bias')
        results.append(row)
    set_status=('model_or_candidate_coverage_mismatch' if all(r['status']=='incompatible' for r in results) else 'unresolved' if any(r['status'] in ('information_insufficient','calculation_failed') for r in results) else 'conditional_compatible')
    return {'set_status':set_status,'as_of_s':as_of_s,'label':'SYNTHETIC_UNCALIBRATED','collection_authorized':False,'scope':'finite_discrete_conditional','candidates':results,'whole_garment_state':'unknown','eta':'unavailable','gate':q}

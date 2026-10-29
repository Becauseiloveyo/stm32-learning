"""Observable-only short-window gate using accepted M2a quality contract."""
import math,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'observation_m2'))
from contract import observe

def evaluate(packets, policy, temp_delta_c, rh_delta, sensor_history, local_temp_delta_c, temp_offset_c):
    for x in (temp_delta_c,rh_delta,local_temp_delta_c,temp_offset_c):
        if isinstance(x,bool) or not isinstance(x,(float,int)) or not math.isfinite(x) or x<0: raise ValueError('invalid environmental tolerance')
    if type(packets)!=list or not packets: return {'applicable':False,'reasons':['missing_window'],'collection_authorized':False,'mode':'SYNTHETIC_UNCALIBRATED','scope':'necessary_observable_conditions_only'}
    if type(sensor_history)!=dict or set(sensor_history)!={'status','reference_time_s','issued_at_s','initial_state_rh','uncertainty_abs','source'}:
        raise ValueError('invalid sensor history fields')
    h=sensor_history
    if h['status']=='unknown':
        if any(h[k] is not None for k in ('reference_time_s','issued_at_s','initial_state_rh','uncertainty_abs','source')): raise ValueError('unknown history has no evidence')
    elif h['status']=='externally_characterized':
        if h['source'] not in ('synthetic_external','operator','external_evidence'): raise ValueError('invalid history source')
        for k in ('reference_time_s','issued_at_s','initial_state_rh','uncertainty_abs'):
            x=h[k]
            if isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x): raise ValueError('invalid history number')
        if not (0<=h['initial_state_rh']<=1 and 0<=h['uncertainty_abs']<=1 and 0<=h['reference_time_s']<=h['issued_at_s']<=packets[-1]['now_s']): raise ValueError('history outside domain')
    else: raise ValueError('invalid history status')
    reports=[observe(packet,policy) for packet in packets]
    reasons=[]
    if len(packets)<2: reasons.append('insufficient_window')
    if any(b['now_s']<=a['now_s'] for a,b in zip(packets,packets[1:])): reasons.append('nonmonotonic_time')
    if len({p['installation_id'] for p in packets}) != 1: reasons.append('installation_changed')
    for name in ('ambient_temp','ambient_rh','local_temp','local_rh'):
        for first,second in zip(packets,packets[1:]):
            a,b=first['samples'][name],second['samples'][name]
            if a is not None and b is not None and b['measured_at_s']<=a['measured_at_s']:
                reasons.append(name+':nonmonotonic_sample_time')
                break
    if any(not r['local_record_allowed'] for r in reports): reasons.append('observation_invalid_or_contact_unknown')
    if h['status']=='unknown': reasons.append('sensor_history_unknown')
    else:
        if h['uncertainty_abs']>policy['max_uncertainty']['local_rh']: reasons.append('sensor_initial_uncertain')
        times=[v['measured_at_s'] for p in packets for v in p['samples'].values() if v is not None]
        if times and h['reference_time_s']>min(times): reasons.append('history_reference_after_sample')
        # The last available independent evidence must explicitly cover the entire
        # interval from the characterized initial state through every sample.
        for key,state in [('contact','confirmed'),('charging_heat','no_known_interference')]:
            e=packets[-1][key]
            if e['state']!=state or not times or e['coverage_start_s']>h['reference_time_s'] or e['coverage_end_s']<max(times):
                reasons.append(key+':history_uncovered')
    def interval_range(samples):
        return max(x['value']+x['uncertainty_abs'] for x in samples)-min(x['value']-x['uncertainty_abs'] for x in samples)
    ambient=[(p['samples']['ambient_temp'],p['samples']['ambient_rh']) for p in packets]
    local=[p['samples']['local_temp'] for p in packets]
    if any(t is None or h is None for t,h in ambient): reasons.append('missing_environment')
    else:
        if interval_range([t for t,h in ambient])>temp_delta_c or interval_range([h for t,h in ambient])>rh_delta: reasons.append('environment_changed_or_uncertain')
    if any(t is None for t in local): reasons.append('missing_local_temperature')
    else:
        if interval_range(local)>local_temp_delta_c: reasons.append('local_temperature_changed_or_uncertain')
        if all(t is not None for t,h in ambient):
            for (a,_),l in zip(ambient,local):
                if abs(a['value']-l['value'])+a['uncertainty_abs']+l['uncertainty_abs']>temp_offset_c:
                    reasons.append('local_ambient_temperature_mismatch_or_uncertain'); break
    return {'applicable':not reasons,'reasons':reasons,'collection_authorized':False,'mode':'SYNTHETIC_UNCALIBRATED','scope':'necessary_observable_conditions_only'}

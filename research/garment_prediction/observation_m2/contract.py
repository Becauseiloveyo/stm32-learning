"""M2a: observable-only central input gate; no inference, no sensor model."""
import math
from copy import deepcopy

LABEL = 'SYNTHETIC_UNCALIBRATED'
CHANNELS = {'ambient_temp': ('degC', 0, 50), 'ambient_rh': ('fraction', 0, 1),
            'local_temp': ('degC', 0, 50), 'local_rh': ('fraction', 0, 1)}
SOURCES = ('synthetic_input', 'instrument', 'operator')
CONTACT_SOURCES = ('synthetic_external', 'operator', 'external_evidence')

def obj(value, keys, where):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError(where + ': exact fields required')

def number(x, name):
    if isinstance(x, bool) or not isinstance(x, (float,int)) or not math.isfinite(x):
        raise ValueError(name + ' must be finite')
    return x

def stamp(t, now, name):
    number(t, name)
    if t < 0 or t > now: raise ValueError(name + ' must be within [0, now]')

def observe(packet, policy):
    """Strict allowlisted observation packet; no truth, material or future forecast fields."""
    obj(policy, ['max_sample_age_s','max_delivery_delay_s','max_contact_age_s','max_sample_skew_s','max_uncertainty'], 'policy')
    obj(policy['max_uncertainty'], CHANNELS, 'max_uncertainty')
    for k in ('max_sample_age_s','max_delivery_delay_s','max_contact_age_s','max_sample_skew_s'):
        if number(policy[k],k) < 0: raise ValueError(k + ' must be nonnegative')
    for k,limit in policy['max_uncertainty'].items():
        if number(limit,k) < 0 or limit > CHANNELS[k][2]-CHANNELS[k][1]: raise ValueError('invalid uncertainty limit')
    obj(packet, ['now_s','installation_id','contact','charging_heat','samples'], 'packet')
    now = number(packet['now_s'],'now_s')
    if now < 0: raise ValueError('negative time')
    if not isinstance(packet['installation_id'],str) or not packet['installation_id'].strip():
        raise ValueError('installation_id required')
    def evidence(value, name, allowed):
        obj(value,['state','source','coverage_start_s','coverage_end_s','issued_at_s'],name)
        if value['state'] not in allowed: raise ValueError(name+': invalid state')
        fields=('source','coverage_start_s','coverage_end_s','issued_at_s')
        if value['state']=='unknown':
            if any(value[k] is not None for k in fields): raise ValueError(name+': unknown has no evidence')
            return
        if value['source'] not in CONTACT_SOURCES: raise ValueError(name+': evidence source required')
        for key in ('coverage_start_s','coverage_end_s','issued_at_s'): stamp(value[key],now,name+' '+key)
        if not value['coverage_start_s'] <= value['coverage_end_s'] <= value['issued_at_s']:
            raise ValueError(name+': require start <= end <= issued <= now')
    c=packet['contact']; heat=packet['charging_heat']
    evidence(c,'contact',('confirmed','not_contact','unknown'))
    evidence(heat,'charging_heat',('known_interference','no_known_interference','unknown'))
    obj(packet['samples'], CHANNELS, 'samples')
    reasons=[]
    measured_times=[]
    local_times=[]
    for name,(unit,lo,hi) in CHANNELS.items():
        sample=packet['samples'][name]
        if sample is None:
            reasons.append(name+':missing'); continue
        obj(sample,['value','unit','measured_at_s','received_at_s','uncertainty_abs','source'],name)
        x=number(sample['value'],name); uncertainty=number(sample['uncertainty_abs'],name+' uncertainty')
        if not lo <= x <= hi or uncertainty < 0 or uncertainty > hi-lo or sample['unit']!=unit or sample['source'] not in SOURCES:
            raise ValueError(name+': invalid value, uncertainty, unit or source')
        if uncertainty > policy['max_uncertainty'][name]: reasons.append(name+':uncertain')
        stamp(sample['measured_at_s'],now,name+' measured')
        stamp(sample['received_at_s'],now,name+' received')
        if sample['received_at_s'] < sample['measured_at_s']: raise ValueError('received before measured')
        measured_times.append(sample['measured_at_s'])
        if name in ('local_temp','local_rh'): local_times.append(sample['measured_at_s'])
        if now-sample['measured_at_s'] > policy['max_sample_age_s']: reasons.append(name+':stale')
        if sample['received_at_s']-sample['measured_at_s'] > policy['max_delivery_delay_s']: reasons.append(name+':delayed')
    if len(measured_times) == len(CHANNELS) and max(measured_times)-min(measured_times) > policy['max_sample_skew_s']:
        reasons.append('samples:time_skew')
    def covers(e, times):
        return bool(times) and all(e['coverage_start_s'] <= t <= e['coverage_end_s'] for t in times)
    contact=c['state']
    if contact!='unknown':
        if now-c['issued_at_s'] > policy['max_contact_age_s']:
            contact='unknown'; reasons.append('contact:stale')
        elif not covers(c,local_times):
            if c['state']=='not_contact' and any(c['coverage_start_s'] <= t <= c['coverage_end_s'] for t in local_times):
                reasons.append('contact:not_contact_evidenced')
            contact='unknown'; reasons.append('contact:uncovered')
    if contact=='unknown': reasons.append('contact:unknown')
    if contact=='not_contact': reasons.append('contact:not_contact')
    if heat['state']=='known_interference': reasons.append('charging:interference')
    if heat['state']=='unknown': reasons.append('charging:unknown')
    if heat['state']!='unknown':
        if now-heat['issued_at_s'] > policy['max_contact_age_s']:
            reasons.append('charging:stale')
        if not covers(heat,measured_times): reasons.append('charging:uncovered')
    accepted=contact=='confirmed' and not reasons
    # This is an eligible local research record, never a dry decision or global estimate.
    return {'local_record_allowed':accepted, 'contact_state_effective':contact,
            'quality_reasons':reasons, 'diagnostic_only':not accepted,
            'whole_garment_state':'unknown', 'collection_authorized':False, 'label':LABEL}

def project_packet(external_envelope):
    """Producer boundary: copy explicit public fields; no hidden keys forwarded."""
    allowed=('now_s','installation_id','contact','charging_heat','samples')
    return deepcopy({k:external_envelope[k] for k in allowed})

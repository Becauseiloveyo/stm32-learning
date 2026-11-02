"""Offline, single-writer synthetic event controller; no hardware or delivery."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

LABEL = 'SYNTHETIC_UNCALIBRATED'
VERSION = 't11-p1-v1'
MAX_TIME = 1_000_000_000
TTL = 120
WET_STUCK = 300


def fields(value, names):
    if type(value) is not dict or set(value) != set(names):
        raise ValueError('exact fields required: ' + ','.join(names))


def number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= MAX_TIME:
        raise ValueError('finite time in range required')
    return value


def identifier(value):
    if type(value) is not str or not value.strip() or len(value) > 80:
        raise ValueError('invalid identifier')
    return value


def event_id(domain, seq):
    encoded = json.dumps(domain, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    return 'ev:' + hashlib.sha256(encoded).hexdigest() + ':' + str(seq)


def scan(value, key=''):
    if isinstance(value, bool):
        if key != 'complete_discrete_support':
            raise ValueError('unexpected boolean')
    elif isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValueError('nonfinite value')
    elif isinstance(value, dict):
        for k, v in value.items():
            scan(v, k)
    elif isinstance(value, list):
        for v in value:
            scan(v)
    elif value is not None and not isinstance(value, str):
        raise ValueError('non JSON value')


def m6_shapes(value):
    """Enforce exact M6 object shapes even in branches skipped by M6."""
    template = json.loads((Path(__file__).resolve().parents[3] / 'research/garment_prediction/prediction_m6/config.json').read_text(encoding='utf-8'))['request']
    shapes = set()
    def collect(v):
        if isinstance(v, dict):
            shapes.add(frozenset(v))
            for x in v.values(): collect(x)
        elif isinstance(v, list):
            for x in v: collect(x)
    def validate(v):
        if isinstance(v, dict):
            if frozenset(v) not in shapes: raise ValueError('unexpected M6 fields')
            for x in v.values(): validate(x)
        elif isinstance(v, list):
            for x in v: validate(x)
    collect(template)
    validate(value)


class Controller:
    def __init__(self, device, boot, batch, installation):
        self.domain = dict(device=identifier(device), boot=identifier(boot), batch=identifier(batch), installation=identifier(installation))
        self.seq = 0
        self.last_id = self.last_hash = None
        self.now = 0
        self.invalidated_at = -1
        self.invalidation_reason = 'initial_no_prediction'
        self.prediction = None
        self.revoked = None
        self.contact = self.heat = self.local = self.forecast = None
        self.fault = False
        self.ever_wet = False
        self.wet_since = None
        self.wet_id = None

    def invalidate(self, reason, when):
        if self.prediction is not None:
            self.revoked = dict(kind='prediction_revoked', priority=3,
                key=self.prediction['intent_key'],
                evidence_s=when, expires_s=self.prediction['expires_s'], invalidation_reason=reason)
        self.prediction = None
        self.invalidated_at = max(self.invalidated_at, when)
        self.invalidation_reason = reason

    def expire(self, now):
        due = []
        for name in ('contact', 'heat', 'local', 'prediction'):
            evidence = getattr(self, name)
            if evidence is not None and now >= evidence['expires_s']:
                due.append((evidence['expires_s'], name))
        for expiry, name in sorted(due):
            evidence = getattr(self, name)
            if evidence is not None:
                setattr(self, name, None)
                if name == 'local': self.wet_since = self.wet_id = None
                if name == 'prediction':
                    self.prediction = evidence
                self.invalidate(name + '_expired', expiry)

    def apply(self, event, decision_s):
        """Commit atomically; duplicates preserve evidence while processing expiry."""
        candidate = deepcopy(self)
        result = candidate._apply(deepcopy(event), decision_s)
        self.__dict__ = candidate.__dict__
        return result

    def _apply(self, e, decision):
        fields(e, ['version', 'id', 'seq', 'domain', 'occurred_s', 'received_s', 'kind', 'payload'])
        scan(e)
        if e['version'] != VERSION: raise ValueError('event version')
        if type(e['id']) is not str: raise ValueError('invalid event ID')
        if type(e['seq']) is not int or not 1 <= e['seq'] <= 2**53: raise ValueError('sequence')
        fields(e['domain'], ['device', 'boot', 'batch', 'installation'])
        if e['domain'] != self.domain: raise ValueError('old or foreign identity domain')
        if e['id'] != event_id(e['domain'], e['seq']): raise ValueError('event ID must bind domain and sequence')
        occurred, received, decision = map(number, (e['occurred_s'], e['received_s'], decision))
        if not occurred <= received <= decision: raise ValueError('future event or delivery')
        digest = hashlib.sha256(json.dumps(e, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        if e['seq'] == self.seq and e['id'] == self.last_id:
            if digest != self.last_hash: raise ValueError('same ID changed')
            if decision < self.now: raise ValueError('clock rollback')
            self.expire(decision)
            self.now = decision
            return self.snapshot()
        if e['seq'] <= self.seq or e['id'] == self.last_id: raise ValueError('old sequence or reused ID')
        if decision < self.now or received < self.now or occurred < self.now: raise ValueError('clock rollback or late arrival')
        self.expire(decision)
        p, kind = e['payload'], e['kind']
        if kind == 'tick':
            fields(p, [])
        elif kind in ('contact', 'heat', 'local'):
            fields(p, ['state', 'source', 'expires_s'] if kind != 'local' else ['state', 'source', 'expires_s', 'exposure'])
            if p['source'] not in ('synthetic_external', 'operator', 'external_evidence'): raise ValueError('source')
            expires = number(p['expires_s'])
            if not occurred < expires <= occurred + TTL: raise ValueError('evidence TTL')
            states = {'contact': ('confirmed', 'missing', 'unknown'), 'heat': ('clear', 'interference', 'unknown'), 'local': ('wet', 'dry', 'unknown')}
            if p['state'] not in states[kind]: raise ValueError('state')
            if kind == 'local' and p['exposure'] not in ('exposed', 'obstructed', 'unknown'): raise ValueError('exposure')
            evidence = dict(state=p['state'], source=p['source'], occurred_s=occurred, received_s=received, expires_s=expires)
            if kind == 'local': evidence['exposure'] = p['exposure']
            setattr(self, kind, evidence if expires > decision else None)
            if expires <= decision:
                if kind == 'local': self.wet_since = self.wet_id = None
                self.invalidate(kind + '_expired', expires)
            elif kind == 'contact' and p['state'] != 'confirmed': self.invalidate('contact_' + p['state'], occurred)
            elif kind == 'heat' and p['state'] != 'clear': self.invalidate('heat_' + p['state'], occurred)
            elif kind == 'local':
                if p['state'] == 'wet':
                    self.ever_wet = True
                    if p['exposure'] != 'exposed': self.wet_since = self.wet_id = None
                    elif self.wet_since is None: self.wet_since, self.wet_id = occurred, e['id']
                    self.invalidate('local_wet', occurred)
                elif p['state'] != 'dry' or p['exposure'] != 'exposed':
                    self.wet_since = self.wet_id = None
                    self.invalidate('local_unavailable', occurred)
                else: self.wet_since = self.wet_id = None
        elif kind == 'fault':
            fields(p, ['state'])
            if p['state'] not in ('active', 'cleared'): raise ValueError('fault state')
            self.fault = p['state'] == 'active'
            if self.fault: self.invalidate('fault', occurred)
        elif kind == 'identity':
            fields(p, ['boot', 'batch', 'installation'])
            for v in p.values(): identifier(v)
            if all(self.domain[k] == p[k] for k in p): raise ValueError('identity must change')
            self.invalidate('identity_changed', occurred)
            self.domain.update(p)
            self.contact = self.heat = self.local = self.forecast = None
            self.fault = False
            self.ever_wet = False
            self.wet_since = None
            self.wet_id = None
        elif kind == 'forecast':
            fields(p, ['issued_s', 'available_s', 'valid_from_s', 'valid_until_s', 'expires_s', 'risk', 'source'])
            issued, available, start, end, expires = (number(p[k]) for k in ('issued_s', 'available_s', 'valid_from_s', 'valid_until_s', 'expires_s'))
            if not issued <= available <= received or not start < end or not available < expires <= available + TTL or p['risk'] not in ('risk', 'no_risk', 'unknown') or p['source'] != 'synthetic_forecast': raise ValueError('forecast fields')
            if self.forecast is not None and issued <= self.forecast['issued_s']: raise ValueError('old forecast')
            self.forecast = dict(p, received_s=received, occurred_s=occurred, event_id=e['id'])
        elif kind == 'prediction':
            fields(p, ['origin_s', 'expires_s', 'request'])
            origin, expires = number(p['origin_s']), number(p['expires_s'])
            req = p['request']
            if not self.eligible(): raise ValueError('evidence unavailable')
            if not occurred < expires <= occurred + TTL or decision >= expires: raise ValueError('prediction TTL')
            if type(req) is not dict or req.get('version') != 'm6-v1': raise ValueError('M6 request version')
            m6_shapes(req)
            if origin + number(req['as_of_s']) != occurred or decision - occurred > TTL: raise ValueError('analysis clock mapping')
            if origin + number(req['reference_s']) < self.invalidated_at: raise ValueError('history crosses invalidation')
            if len(req.get('observations', [])) < 2: raise ValueError('two fresh observations required')
            sample_expiries = []
            for packet in req['observations']:
                if packet.get('installation_id') != self.domain['installation']: raise ValueError('installation mismatch')
                for sample in packet.get('samples', {}).values():
                    if sample is None: continue
                    if origin + number(sample['measured_at_s']) <= self.invalidated_at: raise ValueError('observation before invalidation')
                    if origin + number(sample['received_at_s']) > occurred or decision - (origin + sample['measured_at_s']) >= TTL: raise ValueError('stale or future observation')
                    sample_expiries.append(origin + sample['measured_at_s'] + min(TTL, number(req['policy']['max_sample_age_s'])))
            # The old model validates all packet/physical/future fields. Runtime failures are
            # conservative; malformed requests are rejected transactionally.
            try:
                from research.garment_prediction.prediction_m6.integrated import predict
            except ImportError:
                self.invalidate('m6_unavailable', occurred)
                self.seq, self.last_id, self.last_hash, self.now = e['seq'], e['id'], digest, decision
                return self.snapshot()
            output = predict(req)
            effective_expiry = min([expires, self.contact['expires_s'], self.heat['expires_s'], self.local['expires_s']] + sample_expiries)
            if decision >= effective_expiry: raise ValueError('prediction evidence expired')
            if self.prediction is not None:
                old = self.prediction
                self.revoked = dict(kind='prediction_revoked', priority=3, key=old['intent_key'], evidence_s=occurred, expires_s=old['expires_s'], invalidation_reason='superseded')
            self.prediction = dict(result=output, origin_s=origin, analysis_s=occurred, received_s=received, expires_s=effective_expiry, intent_key='prediction:' + e['id'])
            self.invalidation_reason = None
        else: raise ValueError('unknown event kind')
        self.seq, self.last_id, self.last_hash, self.now = e['seq'], e['id'], digest, decision
        return self.snapshot()

    def eligible(self):
        return (not self.fault and self.contact is not None and self.contact['state'] == 'confirmed'
                and self.heat is not None and self.heat['state'] == 'clear'
                and self.local is not None and self.local['state'] == 'dry' and self.local['exposure'] == 'exposed')

    def snapshot(self):
        local = self.local
        local_state = 'unavailable' if local is None or local['state'] == 'unknown' or local['exposure'] != 'exposed' else local['state']
        forecast = self.forecast
        forecast_state = 'unavailable' if forecast is None or self.now >= forecast['expires_s'] or self.now >= forecast['valid_until_s'] else ('upcoming_' + forecast['risk'] if self.now < forecast['valid_from_s'] else forecast['risk'])
        status = 'unavailable' if self.prediction is None else self.prediction['result']['set_status']
        intents = []
        if local_state == 'wet':
            intents.append(dict(kind='local_wet', priority=1, key='local_wet:' + self.wet_id, evidence_s=local['occurred_s'], expires_s=local['expires_s'], invalidation_reason=None))
        if forecast_state in ('risk', 'upcoming_risk'):
            intents.append(dict(kind='forecast_risk', priority=2, key='forecast:' + forecast['event_id'], evidence_s=forecast['issued_s'], expires_s=min(forecast['expires_s'], forecast['valid_until_s']), invalidation_reason=None))
        if self.prediction:
            intents.append(dict(kind='prediction_' + status, priority=3, key=self.prediction['intent_key'], evidence_s=self.prediction['analysis_s'], expires_s=self.prediction['expires_s'], invalidation_reason=None))
        if self.revoked and self.now < self.revoked['expires_s']:
            intents.append(deepcopy(self.revoked))
        return deepcopy(dict(version=VERSION, label=LABEL, now_s=self.now, domain=self.domain, sequence=self.seq,
            prediction=dict(state=status, value=self.prediction, invalidation_reason=self.invalidation_reason),
            forecast=dict(state=forecast_state, evidence=forecast),
            local_wetting=dict(state=local_state, evidence=local, ever_wet=self.ever_wet, stuck_wet=None if local_state == 'unavailable' else bool(self.wet_since is not None and self.now - self.wet_since >= WET_STUCK), wet_since_s=self.wet_since),
            observation=dict(contact=self.contact, charging_heat=self.heat, fault=self.fault, eligible=self.eligible()),
            collection_authorized=False, intents=intents))

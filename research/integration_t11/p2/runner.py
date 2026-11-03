"""Second-grid offline coordinator. Each call sees only inputs delivered at that second."""

from copy import deepcopy

from research.energy_t05.contracts import Energy
from research.integration_t11.p1.controller import Controller, TTL, VERSION, WET_STUCK, event_id
from research.notification_t07.storage import digest
from .power import Battery
from .transport import Outbox, Receiver, Service

RADIO = Energy()
SAMPLE_J = 0.02  # T12 load-side passive sample scenario
WAKE_J = 0.005  # synthetic additional local-wet interrupt processing, load side
BATCH_S = 60  # synthetic ordinary state sync period


def reopen_after_boot(path, boot, now_s, capacity=32):
    """Recover the sequence, then commit a fresh unknown head before permitting upload."""
    device = Outbox(path, capacity=capacity)
    try:
        previous = device.envelope()
        if previous is None:
            raise ValueError('no initialized device snapshot')
        old = previous['head']['snapshot']
        domain = old['domain']
        if boot == domain['boot']:
            raise ValueError('fresh boot identifier required')
        controller = Controller(**domain)
        controller.seq = old['sequence']
        controller.now = old['now_s']
        seq = controller.seq + 1
        payload = dict(boot=boot, batch=domain['batch'], installation=domain['installation'])
        event = dict(
            version=VERSION,
            id=event_id(domain, seq),
            seq=seq,
            domain=domain,
            occurred_s=now_s,
            received_s=now_s,
            kind='identity',
            payload=payload,
        )
        device.capture(controller.apply(event, now_s))
        return device, controller
    except Exception:
        device.close()
        raise


class Rig:
    def __init__(self, directory, initial_j=None, queue_size=32):
        self.battery = Battery(initial_j=initial_j)
        self.controller = Controller('device', 'boot', 'batch', 'synthetic')
        self.device_path = directory / 'outbox.db'
        self.queue_size = queue_size
        self.device = Outbox(self.device_path, create=True, capacity=queue_size)
        self.service = Service(directory / 'service.db', create=True)
        self.receiver = Receiver(directory / 'receiver.db', create=True)
        self.trace = []
        self.samples = set()
        self.pending_job = None
        self.previous_intents = []
        self.dirty = self.urgent = False
        self.online = self.ack_available = True
        self.charging = False
        self.radio_start = None
        self.measure_start = None
        self.link_contiguous = True
        self.failures = 0
        self.retry_at = 0
        self.last_step = -1

    def receive(self, action):
        visible = self.service.dispatch(self.receiver, self.battery.now)
        self.log(
            action,
            result=visible,
            newly_notified=[item['key'] for item in getattr(self.receiver, 'notifications', [])],
        )

    def close(self):
        for store in (self.device, self.service, self.receiver):
            store.close()

    def log(self, action, **fields):
        self.trace.append(dict(t=self.battery.now, action=action, **fields))

    def emit(self, kind, payload, occurred=None):
        now = self.battery.now
        c = deepcopy(self.controller)
        seq = c.seq + 1
        event = dict(
            version=VERSION,
            id=event_id(c.domain, seq),
            seq=seq,
            domain=dict(c.domain),
            occurred_s=now if occurred is None else occurred,
            received_s=now,
            kind=kind,
            payload=payload,
        )
        snap = c.apply(event, now)
        self.device.capture(snap)
        self.controller = c
        self.dirty = True
        intents = [(i['kind'], i['key']) for i in snap['intents']]
        if intents != self.previous_intents or kind == 'identity':
            self.urgent = True
        self.previous_intents = intents
        if not c.eligible() or kind == 'identity':
            self.samples.clear()
            self.pending_job = None
        if self.battery.active == 'measurement' and not c.eligible():
            self.battery.cancel_measurement()
            self.measure_start = None
            self.log('measurement_cancelled', reason=kind)
        self.log(
            'state_saved',
            kind=kind,
            seq=seq,
            prediction=snap['prediction']['state'],
            wet=snap['local_wetting']['state'],
            intents=[i[0] for i in intents],
            boot=snap['domain']['boot'],
            batch=snap['domain']['batch'],
            discarded_history=self.device.dropped_history_count,
        )
        return snap

    def expire_due(self, t):
        c = self.controller
        evidence = (c.contact, c.heat, c.local, c.prediction)
        due = any(item and item['expires_s'] <= t for item in evidence)
        if c.forecast:
            due |= any(
                c.now < c.forecast[key] <= t
                for key in ('valid_from_s', 'valid_until_s', 'expires_s')
            )
        if c.wet_since is not None:
            due |= c.now < c.wet_since + WET_STUCK <= t
        if due:
            self.emit('tick', {})

    def input(self, kind, payload):
        t = self.battery.now
        if kind == 'link':
            self.online = payload
        elif kind == 'ack_path':
            self.ack_available = payload
        elif kind == 'cold_boot':
            self.device.close()
            self.device, self.controller = reopen_after_boot(
                self.device_path, payload, t, self.queue_size
            )
            self.samples.clear()
            self.pending_job = None
            self.previous_intents = []
            self.dirty = self.urgent = True
            self.link_contiguous = False
            if self.battery.cancel_measurement():
                self.measure_start = None
                self.log('measurement_cancelled', reason=kind)
            self.log('boot_state_saved', boot=payload, seq=self.controller.seq)
        elif kind == 'charge':
            watts, efficiency = payload
            self.battery.charge(watts, efficiency)
            self.charging = watts > 0
            self.emit(
                'heat',
                dict(
                    state='interference' if self.charging else 'unknown',
                    source='synthetic_external',
                    expires_s=t + 120,
                ),
            )
        elif kind == 'heat' and self.charging and payload['state'] == 'clear':
            raise ValueError('cannot clear charging heat while attached')
        elif kind == 'local':
            cost = SAMPLE_J + (WAKE_J if payload['state'] == 'wet' else 0)
            if self.battery.spend(cost):
                self.emit(kind, payload)
                self.log('local_sample', state=payload['state'], load_j=cost)
            else:
                self.emit(
                    kind,
                    dict(
                        state='unknown',
                        source='synthetic_external',
                        expires_s=t + 120,
                        exposure='unknown',
                    ),
                )
                self.log('sample_denied')
        elif kind == 'observe':
            origin, packet = payload
            if (
                origin + packet['now_s'] != t
                or packet['installation_id'] != self.controller.domain['installation']
            ):
                raise ValueError('packet clock or installation mismatch')
            if self.controller.eligible() and self.battery.spend(SAMPLE_J):
                self.samples.add((origin, digest(packet)))
                self.log('observation_paid')
            else:
                self.log('observation_unavailable')
        elif kind == 'predict':
            origin = payload['origin_s']
            if origin + payload['request']['as_of_s'] != t:
                raise ValueError('prediction analysis clock mismatch')
            if not self.controller.eligible():
                self.log('analysis_ineligible', analysis_s=t)
                return
            self.pending_job = (t, deepcopy(payload))
            self.urgent = True
        else:
            self.emit(kind, payload)

    def compute_uploaded_job(self):
        if self.pending_job is None:
            return
        analysis, job = self.pending_job
        self.pending_job = None
        packets = job['request']['observations']
        if (
            not self.controller.eligible()
            or analysis < self.controller.now
            or job['expires_s'] <= self.battery.now
        ):
            self.log('analysis_obsolete', analysis_s=analysis)
            return
        max_age = min(TTL, job['request']['policy']['max_sample_age_s'])
        for packet in packets:
            if (job['origin_s'], digest(packet)) not in self.samples:
                self.log('analysis_missing_sample', analysis_s=analysis)
                # Explicit missing input reaches M6 instead of reusing the bundled sample.
                packet['samples'] = dict.fromkeys(packet['samples'])
            elif any(
                value is not None
                and self.battery.now >= job['origin_s'] + value['measured_at_s'] + max_age
                for value in packet['samples'].values()
            ):
                self.log('analysis_obsolete', analysis_s=analysis)
                return
        self.emit('prediction', job, occurred=analysis)
        self.log('pc_analysis', analysis_s=analysis)

    def step(self, t, inputs=()):
        if type(t) is not int or t != self.last_step + 1:
            raise ValueError('call once per integer second starting at zero')
        self.last_step = t
        if self.radio_start is not None and t > self.radio_start:
            self.link_contiguous &= self.online
        self.battery.advance(t)
        if self.measure_start is not None and self.battery.active != 'measurement':
            self.log('measurement_budget_complete', started_s=self.measure_start)
            self.measure_start = None
        self.expire_due(t)
        measure = False
        query = False
        for kind, payload in inputs:
            if kind == 'measure':
                measure = True
            elif kind == 'receiver_query':
                query = True
            else:
                self.input(kind, payload)
        if self.radio_start is not None:
            age = t - self.radio_start
            if age == RADIO.wifi_success_s and self.link_contiguous:
                self.compute_uploaded_job()
                envelope = self.device.envelope()
                ack = self.service.submit(envelope, t)
                self.log(
                    'server_saved',
                    started_s=self.radio_start,
                    record_id=envelope['record_id'],
                    revision=envelope['head']['revision'],
                    intents=[i['kind'] for i in envelope['head']['snapshot']['intents']],
                )
                self.receive('simulated_receiver')
                if self.ack_available:
                    self.device.acknowledge(ack)
                    if not self.battery.radio_success():
                        raise RuntimeError('wireless completion disagrees with battery clock')
                    self.log('device_ack', record_id=ack['record_id'])
                    self.radio_start = None
                    self.failures = 0
                    self.dirty = self.device.envelope()['record'] is not None
                    self.urgent = False
            if age == RADIO.wifi_timeout_s:
                self.failures += 1
                self.retry_at = (
                    t + RADIO.backoff_s[min(self.failures - 1, len(RADIO.backoff_s) - 1)]
                )
                self.radio_start = None
                self.log(
                    'radio_timeout',
                    retry_at=(self.retry_at if self.failures < RADIO.wifi_attempt_limit else None),
                )
        wants_link = (
            self.urgent
            or self.pending_job is not None
            or (self.dirty and (self.failures > 0 or t % BATCH_S == 0))
        )
        if (
            wants_link
            and self.battery.active is None
            and t >= self.retry_at
            and self.failures < RADIO.wifi_attempt_limit
        ):
            if self.battery.start_radio():
                self.radio_start = t
                self.link_contiguous = True
                self.log('radio_start')
            else:
                self.retry_at = t + RADIO.backoff_s[0]
                self.log('radio_denied', retry_at=self.retry_at)
        if measure:
            if self.controller.eligible() and self.battery.start_measurement():
                self.measure_start = t
                self.log('measurement_start')
            else:
                self.log('measurement_denied')
        if query:
            self.receive('receiver_query')

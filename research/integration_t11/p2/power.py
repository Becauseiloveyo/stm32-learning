"""T11 P2 synthetic battery-side accounting; times are integer simulated seconds."""

import math

ETA = 0.85
BASE_W = 0.0002
RESERVE_FRACTION = 0.05
LABEL = 'SYNTHETIC_UNCALIBRATED'
MAX_CAPACITY_AH = 1000000
MAX_INPUT_W = 1000000
MAX_LOAD_J = 1000000000000
MAX_TIME_S = 10000000


def numeric(value, name):
    if type(value) not in (int, float) or value < 0:
        raise ValueError(name + ' must be finite and nonnegative')
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(name + ' must be finite and nonnegative') from exc
    if not math.isfinite(result):
        raise ValueError(name + ' must be finite and nonnegative')
    return result


class Battery:

    def __init__(self, initial_j=None, capacity_ah=0.25):
        capacity_ah = numeric(capacity_ah, 'capacity_ah')
        if not 0 < capacity_ah <= MAX_CAPACITY_AH:
            raise ValueError('capacity outside synthetic range')
        self.capacity_j = capacity_ah * 3.7 * 0.8 * 3600
        self.reserve_j = self.capacity_j * RESERVE_FRACTION
        self.stored_j = self.capacity_j if initial_j is None else numeric(initial_j, 'initial_j')
        if self.stored_j > self.capacity_j:
            raise ValueError('initial exceeds capacity')
        self.now = 0
        self.active = None
        self.started = None
        self.vent_open = False
        self.input_w = 0.0
        self.charge_efficiency = 0.0
        self.account = dict(
            initial_j=self.stored_j,
            charge_input_j=0.0,
            charge_battery_j=0.0,
            curtailed_j=0.0,
            baseline_j=0.0,
            measurement_j=0.0,
            radio_j=0.0,
            external_j=0.0,
            unserved_baseline_j=0.0,
        )

    def _remaining(self):
        if self.active is None:
            return 0.0
        elapsed = self.now - self.started
        if self.active == 'radio':
            return (20 - elapsed) * (BASE_W + 0.6) / ETA
        baseline = (140 - elapsed) * (BASE_W + 0.002)
        fan = max(0, 50 - max(elapsed, 30)) * 0.2
        edges = (0.05 if elapsed < 30 else 0) + (0.05 if elapsed < 50 else 0)
        return (baseline + fan + edges) / ETA

    def _start(self, kind):
        if self.active is not None:
            return False
        self.active, self.started = (kind, self.now)
        if self.stored_j < self.reserve_j + self._remaining():
            self.active, self.started = (None, None)
            return False
        return True

    def start_measurement(self):
        return self._start('measurement')

    def start_radio(self):
        return self._start('radio')

    def spend(self, load_j):
        load_j = numeric(load_j, 'load_j')
        if load_j > MAX_LOAD_J:
            raise ValueError('load outside synthetic range')
        cost = load_j / ETA
        if self.stored_j < self.reserve_j + self._remaining() + cost:
            return False
        self.stored_j -= cost
        self.account['external_j'] += cost
        return True

    def charge(self, input_w, efficiency):
        power = numeric(input_w, 'input_w')
        efficiency = numeric(efficiency, 'efficiency')
        if power > MAX_INPUT_W or efficiency > 1 or (power > 0 and efficiency == 0):
            raise ValueError('invalid charge input')
        self.input_w, self.charge_efficiency = (power, efficiency)

    def _pay(self, category, amount):
        paid = min(self.stored_j, amount)
        self.stored_j -= paid
        self.account[category] += paid
        if paid + 1e-09 < amount:
            if category != 'baseline_j':
                raise RuntimeError('reserved action energy exhausted')
            self.account['unserved_baseline_j'] += amount - paid

    def advance(self, to_s):
        if type(to_s) is not int or not self.now <= to_s <= MAX_TIME_S:
            raise ValueError('integer clock outside synthetic range')
        while self.now < to_s:
            elapsed = None if self.active is None else self.now - self.started
            self.account['charge_input_j'] += self.input_w
            available = self.input_w * self.charge_efficiency
            self.account['charge_battery_j'] += available
            self.stored_j += available
            self._pay('baseline_j', BASE_W / ETA)
            if self.active == 'measurement':
                self._pay('measurement_j', (0.002 + (0.2 if 30 <= elapsed < 50 else 0)) / ETA)
            elif self.active == 'radio':
                self._pay('radio_j', 0.6 / ETA)
            self.now += 1
            if self.stored_j > self.capacity_j:
                self.account['curtailed_j'] += self.stored_j - self.capacity_j
                self.stored_j = self.capacity_j
            if self.active == 'measurement':
                elapsed = self.now - self.started
                if elapsed == 30:
                    self._pay('measurement_j', 0.05 / ETA)
                    self.vent_open = True
                if elapsed == 50:
                    self._pay('measurement_j', 0.05 / ETA)
                    self.vent_open = False
                if elapsed == 140:
                    self.active = self.started = None
            elif self.active == 'radio' and self.now - self.started == 20:
                self.active = self.started = None

    def cancel_measurement(self):
        if self.active != 'measurement':
            return False
        if self.vent_open:
            self._pay('measurement_j', 0.05 / ETA)
            self.vent_open = False
        self.active = self.started = None
        return True

    def radio_success(self):
        if self.active != 'radio' or self.now - self.started != 6:
            return False
        self.active = self.started = None
        return True

    def ledger(self):
        return dict(
            version='t11-p2-v1',
            label=LABEL,
            seed=None,
            now_s=self.now,
            stored_j=self.stored_j,
            capacity_j=self.capacity_j,
            reserve_j=self.reserve_j,
            active=self.active,
            vent_open=self.vent_open,
            charge_input_w=self.input_w,
            charge_efficiency=self.charge_efficiency,
            remaining_reserved_j=self._remaining(),
            **self.account
        )

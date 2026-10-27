"""Explicit second-grid inputs and load-side energy assumptions, not hardware specs."""
from dataclasses import asdict, dataclass, field
import math

from research.state_machine.machine import Configuration, DEFAULT_CONFIG, valid_id
from theory.model import Parameters, DEFAULT_SEED

LABEL = "SYNTHETIC_UNCALIBRATED"
EPS = 1e-12
CATEGORIES = ("background_run", "background_stop", "misc", "measurement_baseline",
              "measurement_flush", "measurement_recovery", "fan", "vent_open", "vent_close",
              "wifi", "boot")


def nonnegative(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative")


def seconds(value, name, positive=False):
    if type(value) is not int or value < (1 if positive else 0):
        raise ValueError(f"{name} must be an integer second")


@dataclass(frozen=True)
class Energy:
    capacity_wh: float = 3.885
    initial_wh: float = 3.885
    eta: float = 0.85
    base_w: float = 0.001
    misc_w: float = 0.004 / 24
    stopped_w: float = 0.00005
    baseline_w: float = 0.002
    flush_w: float = 0.002
    recovery_w: float = 0.002
    fan_w: float = 0.2
    vent_open_j: float = 0.05
    vent_close_j: float = 0.05
    wifi_w: float = 0.6
    wifi_success_s: int = 6
    wifi_timeout_s: int = 20
    boot_wh: float = 0.002
    stop_wh: float = 0.05
    restart_wh: float = 0.15
    low_enter_wh: float = 0.30
    low_exit_wh: float = 0.45
    wifi_daily_wh: float = 0.08
    wifi_attempt_limit: int = 5
    backoff_s: tuple = (60, 300, 900, 3600)
    fixed_period_s: int = 600
    fixed_phase_s: int = 60
    tick_s: int = 60

    def validate(self, configuration):
        for key, value in asdict(self).items():
            if key != "backoff_s":
                nonnegative(value, key)
        for key in ("wifi_success_s", "wifi_timeout_s", "wifi_attempt_limit", "fixed_period_s", "tick_s"):
            seconds(getattr(self, key), key, True)
        seconds(self.fixed_phase_s, "fixed_phase_s")
        if not self.backoff_s:
            raise ValueError("backoff_s must not be empty")
        for value in self.backoff_s:
            seconds(value, "backoff_s", True)
        if not 0 < self.eta <= 1 or not 0 <= self.initial_wh <= self.capacity_wh:
            raise ValueError("invalid efficiency or initial store")
        if not 0 <= self.stop_wh < self.restart_wh <= self.low_enter_wh < self.low_exit_wh <= self.capacity_wh:
            raise ValueError("require stop < restart <= low_enter < low_exit <= capacity")
        if self.stopped_w > self.base_w + self.misc_w:
            raise ValueError("stopped load exceeds running background")
        if self.restart_wh - self.boot_wh / self.eta <= self.stop_wh:
            raise ValueError("restart cannot fund boot above stop reserve")
        if self.wifi_success_s > self.wifi_timeout_s:
            raise ValueError("success duration exceeds timeout")
        if self.tick_s >= configuration.policy.max_event_gap_s:
            raise ValueError("tick must be shorter than maximum event gap")


@dataclass(frozen=True)
class Request:
    id: str
    at_s: int
    deadline_s: int
    batch_id: str
    kind: str = "measurement"
    critical: bool = True
    reason: str = "external_assumed_trigger"


@dataclass(frozen=True)
class Installation:
    at_s: int
    batch_id: str
    installation_id: str


@dataclass(frozen=True)
class Solar:
    start_s: int
    end_s: int
    battery_w: float


@dataclass(frozen=True)
class Network:
    start_s: int
    end_s: int
    available: bool


@dataclass(frozen=True)
class ObservationCondition:
    start_s: int
    end_s: int
    parameters: dict


@dataclass(frozen=True)
class Scenario:
    name: str
    duration_s: int
    energy: Energy = field(default_factory=Energy)
    configuration: dict = field(default_factory=lambda: DEFAULT_CONFIG.snapshot())
    requests: tuple = ()
    installations: tuple = ()
    solar: tuple = ()
    network: tuple = ()
    observations: tuple = ()
    default_parameters: dict = field(default_factory=lambda: asdict(Parameters(surface_activity=0.60, noise_rh_pct=0)))
    seed: int = DEFAULT_SEED
    label: str = LABEL
    assumption: str = "External synthetic workload; not observed drying time."

    def validate(self):
        seconds(self.duration_s, "duration_s", True)
        config = Configuration.from_snapshot(self.configuration)
        self.energy.validate(config)
        # The accepted protocol is not edited: explicitly reject unsupported grids.
        for value in (config.protocol.baseline_s, config.protocol.flush_s, config.protocol.recovery_s,
                      config.protocol.sample_period_s):
            if value != int(value) or value < 1:
                raise ValueError("T05 supports integer-second phase and sampling grids only")
        if self.label != LABEL or type(self.seed) is not int:
            raise ValueError("require synthetic label and explicit integer seed")
        ids = set()
        for r in self.requests:
            if not valid_id(r.id) or r.id in ids or not valid_id(r.batch_id):
                raise ValueError("invalid/duplicate request identity")
            ids.add(r.id)
            seconds(r.at_s, "at_s")
            seconds(r.deadline_s, "deadline_s")
            if not r.at_s < min(r.deadline_s, self.duration_s):
                raise ValueError("request must arrive within horizon before deadline")
            if r.kind not in ("measurement", "wifi") or type(r.critical) is not bool or not r.reason:
                raise ValueError("invalid request kind/critical/reason")
        installation_ids, previous = set(), -1
        for i in self.installations:
            seconds(i.at_s, "installation time")
            if not previous < i.at_s < self.duration_s:
                raise ValueError("installations must be strictly ordered within horizon")
            if not valid_id(i.batch_id) or not valid_id(i.installation_id) or i.installation_id in installation_ids:
                raise ValueError("invalid/reused installation identity")
            installation_ids.add(i.installation_id)
            previous = i.at_s
        Parameters(**self.default_parameters).validate()
        for intervals in (self.solar, self.network, self.observations):
            previous = 0
            for interval in intervals:
                seconds(interval.start_s, "interval start")
                seconds(interval.end_s, "interval end")
                if not previous <= interval.start_s < interval.end_s <= self.duration_s:
                    raise ValueError("intervals must be ordered, disjoint, within horizon")
                previous = interval.end_s
                if isinstance(interval, Solar):
                    nonnegative(interval.battery_w, "battery_w")
                elif isinstance(interval, Network):
                    if type(interval.available) is not bool:
                        raise ValueError("network state must be boolean")
                else:
                    Parameters(**interval.parameters).validate()
        for data in [self.default_parameters] + [i.parameters for i in self.observations]:
            p = Parameters(**data)
            for value in (p.ambient_step_at_s, p.clip_at_s, p.invalid_at_s):
                if value >= 0 and (value > config.protocol.total_s or not config.protocol.aligned(value)):
                    raise ValueError("observation stimulus outside protocol grid")
        return config


def from_dict(data):
    data = dict(data)
    data["energy"] = Energy(**data["energy"])
    for name, cls in (("requests", Request), ("installations", Installation), ("solar", Solar),
                      ("network", Network), ("observations", ObservationCondition)):
        data[name] = tuple(cls(**row) for row in data[name])
    result = Scenario(**data)
    result.validate()
    return result


def measurement_components(energy, protocol):
    return {"measurement_baseline": energy.baseline_w * protocol.baseline_s / 3600,
            "measurement_flush": energy.flush_w * protocol.flush_s / 3600,
            "measurement_recovery": energy.recovery_w * protocol.recovery_s / 3600,
            "fan": energy.fan_w * protocol.flush_s / 3600,
            "vent_open": energy.vent_open_j / 3600, "vent_close": energy.vent_close_j / 3600}


def required_battery_wh(energy, protocol, kind):
    duration = protocol.total_s if kind == "measurement" else energy.wifi_timeout_s
    work = sum(measurement_components(energy, protocol).values()) if kind == "measurement" else energy.wifi_w * duration / 3600
    return (work + (energy.base_w + energy.misc_w) * duration / 3600) / energy.eta

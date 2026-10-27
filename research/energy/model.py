"""Synthetic, minute-resolution energy accounting; no garment model or dry decision.

All energy is Wh. Solar and store quantities are battery-side; action costs and
background power are load-side. An admitted action reserves its entire worst-case
energy plus background until completion, without crediting future solar.
"""

from dataclasses import asdict, dataclass
import math

EPS = 1e-12
LABEL = "SYNTHETIC_UNMEASURED_ENERGY_ONLY"


def finite(name, value, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} must be finite and >= {minimum}")


def integer(name, value, minimum=0):
    finite(name, value, minimum)
    if not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")


@dataclass(frozen=True)
class Config:
    capacity_wh: float = 3.885
    initial_wh: float = 3.885
    dc_efficiency: float = 0.85
    running_base_w: float = 0.001 + 0.004 / 24
    stopped_base_w: float = 0.00005
    stop_wh: float = 0.05
    restart_wh: float = 0.15
    low_enter_wh: float = 0.30
    low_exit_wh: float = 0.45
    boot_load_wh: float = 0.002
    measurement_load_wh: float = (0.2 * 20 + 0.1) / 3600
    measurement_minutes: int = 5
    wifi_success_load_wh: float = 0.6 * 6 / 3600
    wifi_timeout_load_wh: float = 0.6 * 20 / 3600
    wifi_daily_battery_limit_wh: float = 0.08
    max_wifi_attempts: int = 5
    retry_minutes: tuple = (1, 5, 15, 60)
    fixed_period_minutes: int = 10

    def validate(self):
        for name, value in asdict(self).items():
            if name != "retry_minutes":
                finite(name, value)
        for name in ("measurement_minutes", "max_wifi_attempts", "fixed_period_minutes"):
            integer(name, getattr(self, name), 1)
        for value in self.retry_minutes:
            integer("retry_minutes", value, 1)
        if not self.retry_minutes:
            raise ValueError("retry_minutes must not be empty")
        if not 0 < self.dc_efficiency <= 1:
            raise ValueError("dc_efficiency must be in (0, 1]")
        if not 0 <= self.initial_wh <= self.capacity_wh:
            raise ValueError("initial_wh outside capacity")
        if not 0 <= self.stop_wh < self.restart_wh <= self.low_enter_wh < self.low_exit_wh <= self.capacity_wh:
            raise ValueError("require stop < restart <= low_enter < low_exit <= capacity")
        if self.stopped_base_w > self.running_base_w:
            raise ValueError("stopped base must not exceed running base")
        if self.wifi_success_load_wh > self.wifi_timeout_load_wh:
            raise ValueError("success cost must not exceed timeout reservation")
        if self.restart_wh - self.boot_load_wh / self.dc_efficiency <= self.stop_wh:
            raise ValueError("restart threshold cannot cover boot above stop threshold")


@dataclass(frozen=True)
class Request:
    event_id: str
    at_minute: int
    deadline_minute: int
    kind: str
    reason: str
    critical: bool = True
    session_id: str = "synthetic-garment-1"
    protocol_version: str = "energy-placeholder-v1"


@dataclass(frozen=True)
class Solar:
    start_minute: int
    end_minute: int
    battery_w: float


@dataclass(frozen=True)
class Network:
    start_minute: int
    end_minute: int
    available: bool


@dataclass(frozen=True)
class Scenario:
    name: str
    minutes: int
    config: Config
    requests: tuple
    solar: tuple = ()
    network: tuple = ()
    description: str = ""
    label: str = LABEL
    seed: object = None

    def validate(self):
        self.config.validate()
        integer("minutes", self.minutes, 1)
        if self.label != LABEL or self.seed is not None:
            raise ValueError("this deterministic prototype requires the synthetic label and seed=null")
        seen = set()
        for request in self.requests:
            if not request.event_id or request.event_id in seen:
                raise ValueError("event IDs must be unique and nonempty")
            seen.add(request.event_id)
            integer("at_minute", request.at_minute)
            integer("deadline_minute", request.deadline_minute)
            if not request.at_minute < request.deadline_minute <= self.minutes:
                raise ValueError("requests require 0 <= at < deadline <= horizon")
            if request.kind not in ("measurement", "wifi"):
                raise ValueError("unknown request kind")
            if type(request.critical) is not bool:
                raise ValueError("critical must be boolean")
            if not request.reason or not request.session_id or not request.protocol_version:
                raise ValueError("request provenance must be nonempty")
        for intervals in (self.solar, self.network):
            previous_end = 0
            for interval in intervals:
                integer("interval start", interval.start_minute)
                integer("interval end", interval.end_minute)
                if not previous_end <= interval.start_minute < interval.end_minute <= self.minutes:
                    raise ValueError("intervals must be ordered, nonoverlapping and within horizon")
                previous_end = interval.end_minute
                if isinstance(interval, Solar):
                    finite("battery_w", interval.battery_w)
                elif type(interval.available) is not bool:
                    raise ValueError("network available must be boolean")


def from_dict(data):
    values = dict(data)
    values["config"] = Config(**values["config"])
    values["requests"] = tuple(Request(**row) for row in values["requests"])
    values["solar"] = tuple(Solar(**row) for row in values.get("solar", []))
    values["network"] = tuple(Network(**row) for row in values.get("network", []))
    scenario = Scenario(**values)
    scenario.validate()
    return scenario


def mode_for(mode, stored, cfg):
    if mode == "stopped":
        return mode  # Only a paid boot may leave stopped.
    if stored <= cfg.stop_wh + EPS:
        return "stopped"
    if mode == "normal" and stored <= cfg.low_enter_wh + EPS:
        return "low"
    if mode == "low" and stored >= cfg.low_exit_wh - EPS:
        return "normal"
    return mode


def simulate(scenario, policy):
    """Run fixed opportunities or externally triggered demand, never using dry truth.

    Each measurement attempt serves at most one already-arrived request of the
    same session/protocol. Fixed opportunities without a request are retained as
    extra work. Network environment is sampled only after energy admission.
    """
    scenario.validate()
    if policy not in ("fixed", "demand"):
        raise ValueError("policy must be fixed or demand")
    cfg = scenario.config
    eta = cfg.dc_efficiency
    stored = cfg.initial_wh
    mode = mode_for("normal", stored, cfg)
    records = {}
    arrivals = {}
    for request in scenario.requests:
        arrivals.setdefault(request.at_minute, []).append(request)
    ledger, attempts, transitions = [], [], []
    active = None
    wifi_daily = {}

    def transition(next_mode, minute, reason):
        nonlocal mode
        if next_mode != mode:
            transitions.append({"minute": minute, "from": mode, "to": next_mode,
                                "reason": reason, "stored_wh": stored})
            mode = next_mode

    def worst_cost(kind):
        return cfg.measurement_load_wh if kind == "measurement" else cfg.wifi_timeout_load_wh

    def close_request(record, status, minute):
        record["status"] = status
        record["finished_minute"] = minute
        record["wait_minutes"] = minute - record["at_minute"]

    for minute in range(scenario.minutes):
        start_wh = stored
        # Half-open input intervals: energy from [t,t+1) cannot admit an action at t.
        solar_wh = sum(i.battery_w / 60 for i in scenario.solar
                       if i.start_minute <= minute < i.end_minute)
        for request in arrivals.get(minute, []):
            records[request.event_id] = {
                **asdict(request), "status": "pending", "attempts": 0,
                "next_attempt_minute": minute, "last_block": "", "block_reasons": [],
                "finished_minute": None, "wait_minutes": None,
                "reference_load_wh": worst_cost(request.kind),
                "research_state": "unconfirmed", "collection_authorized": False,
            }
        for record in records.values():
            if record["status"] == "pending" and minute >= record["deadline_minute"]:
                close_request(record, "expired", minute)

        boot_load = 0.0
        if mode == "stopped" and stored >= cfg.restart_wh - EPS:
            boot_load = cfg.boot_load_wh
            stored -= boot_load / eta
            transition("normal" if stored >= cfg.low_exit_wh else "low", minute, "paid_restart")
        transition(mode_for(mode, stored, cfg), minute, "threshold")
        mode_during = mode

        pending = sorted((r for r in records.values() if r["status"] == "pending"),
                         key=lambda r: (not r["critical"], r["at_minute"], r["event_id"]))
        candidate = None
        if active is None:
            measure = next((r for r in pending if r["kind"] == "measurement"), None)
            if policy == "fixed" and minute % cfg.fixed_period_minutes == 0:
                candidate = ("measurement", measure)
            elif policy == "demand" and measure is not None:
                candidate = ("measurement", measure)
            else:
                wifi = next((r for r in pending if r["kind"] == "wifi"
                             and minute >= r["next_attempt_minute"]), None)
                if wifi is not None:
                    candidate = ("wifi", wifi)

        blocked_reason = ""
        if candidate:
            kind, record = candidate
            duration = cfg.measurement_minutes if kind == "measurement" else 1
            cost = worst_cost(kind)
            reserve = (cost + cfg.running_base_w * duration / 60) / eta
            deadline = record["deadline_minute"] if record else scenario.minutes
            if minute + duration > deadline:
                blocked_reason = "insufficient_time_before_deadline"
            elif mode == "stopped":
                blocked_reason = "stopped"
            elif mode == "low" and record and not record["critical"]:
                blocked_reason = "low_priority_shed"
            elif stored - cfg.stop_wh + EPS < reserve:
                blocked_reason = "insufficient_energy"
            elif kind == "wifi" and wifi_daily.get(minute // 1440, 0) + cost / eta > cfg.wifi_daily_battery_limit_wh + EPS:
                blocked_reason = "wifi_daily_budget"
            else:
                # No future network state is consulted by scheduling/admission.
                success = kind == "measurement" or all(i.available for i in scenario.network
                          if i.start_minute <= minute < i.end_minute)
                actual_cost = cost if kind == "measurement" or not success else cfg.wifi_success_load_wh
                active = {"attempt_id": len(attempts) + 1, "kind": kind,
                          "event_id": record["event_id"] if record else "",
                          "start_minute": minute, "end_minute": minute + duration,
                          "load_wh": actual_cost, "paid_load_wh": 0.0,
                          "duration_minutes": duration, "reserve_battery_wh": reserve,
                          "available_above_stop_wh": stored - cfg.stop_wh,
                          "network_available_at_start": success if kind == "wifi" else None,
                          "outcome": "running", "collection_authorized": False}
                attempts.append(active)
                if record:
                    record["attempts"] += 1
                    record["status"] = "running"
                if kind == "wifi":
                    day = minute // 1440
                    wifi_daily[day] = wifi_daily.get(day, 0.0) + actual_cost / eta
            if record and blocked_reason:
                record["last_block"] = blocked_reason
                if blocked_reason not in record["block_reasons"]:
                    record["block_reasons"].append(blocked_reason)

        base_requested = (cfg.stopped_base_w if mode == "stopped" else cfg.running_base_w) / 60
        action_requested = active["load_wh"] / active["duration_minutes"] if active else 0.0
        available = stored + solar_wh
        base_served = min(base_requested, available * eta)
        available -= base_served / eta
        action_served = min(action_requested, max(0.0, available) * eta)
        available -= action_served / eta
        spill = max(0.0, available - cfg.capacity_wh)
        stored = max(0.0, min(cfg.capacity_wh, available))
        requested = base_requested + action_requested + boot_load
        served = base_served + action_served + boot_load
        unmet = max(0.0, requested - served)
        if active:
            active["paid_load_wh"] += action_served
            record = records.get(active["event_id"])
            if unmet > EPS:
                active["outcome"] = "interrupted_energy"
                active["end_minute"] = minute + 1
                if record:
                    close_request(record, "interrupted_energy", minute + 1)
                active = None
            elif minute + 1 == active["end_minute"]:
                success = active["kind"] == "measurement" or active["network_available_at_start"]
                active["outcome"] = "budget_completed" if success else "network_failed"
                if record and success:
                    close_request(record, "budget_completed", minute + 1)
                elif record:
                    if record["attempts"] >= cfg.max_wifi_attempts:
                        close_request(record, "retry_exhausted", minute + 1)
                    else:
                        record["status"] = "pending"
                        delay = cfg.retry_minutes[min(record["attempts"] - 1, len(cfg.retry_minutes) - 1)]
                        record["next_attempt_minute"] = minute + 1 + delay
                active = None
        transition(mode_for(mode, stored, cfg), minute + 1, "threshold")
        residual = start_wh + solar_wh - served / eta - spill - stored
        ledger.append({"minute": minute, "mode_during": mode_during, "mode_end": mode,
                       "store_start_wh": start_wh, "solar_battery_wh": solar_wh,
                       "base_requested_load_wh": base_requested, "base_served_load_wh": base_served,
                       "action_requested_load_wh": action_requested, "action_served_load_wh": action_served,
                       "boot_load_wh": boot_load, "served_load_wh": served,
                       "conversion_loss_wh": served / eta - served,
                       "unmet_requested_load_wh": unmet,
                       "shed_background_reference_load_wh": (cfg.running_base_w / 60 - base_requested),
                       "spill_battery_wh": spill, "store_end_wh": stored,
                       "blocked_reason": blocked_reason, "balance_residual_wh": residual})

    for record in records.values():
        if record["status"] in ("pending", "running"):
            close_request(record, "expired" if record["deadline_minute"] <= scenario.minutes else "horizon_pending",
                          scenario.minutes)
    # Admission forbids a completion after the horizon; no running attempt can disappear.
    if active is not None:
        raise AssertionError("active action beyond horizon")
    return {"label": LABEL, "scenario": scenario.name, "policy": policy,
            "ledger": ledger, "requests": list(records.values()), "attempts": attempts,
            "transitions": transitions, "wifi_daily_battery_wh": wifi_daily,
            "summary": summarize(scenario, policy, ledger, records, attempts, transitions)}


def summarize(scenario, policy, ledger, records, attempts, transitions):
    requests = list(records.values())
    served = sum(r["served_load_wh"] for r in ledger)
    solar = sum(r["solar_battery_wh"] for r in ledger)
    spill = sum(r["spill_battery_wh"] for r in ledger)
    waits = sorted(r["wait_minutes"] for r in requests if r["status"] == "budget_completed")
    incomplete = [r for r in requests if r["status"] != "budget_completed"]
    energy_blocked = [r for r in incomplete if set(r["block_reasons"]) & {"stopped", "insufficient_energy"}]
    summary = {"label": LABEL, "scenario": scenario.name, "policy": policy,
            "minutes": scenario.minutes, "initial_wh": scenario.config.initial_wh,
            "final_wh": ledger[-1]["store_end_wh"], "solar_battery_wh": solar,
            "served_load_wh": served, "battery_consumed_wh": served / scenario.config.dc_efficiency,
            "spill_battery_wh": spill,
            "balance_residual_wh": scenario.config.initial_wh + solar - served / scenario.config.dc_efficiency - spill - ledger[-1]["store_end_wh"],
            "max_step_balance_residual_wh": max(abs(r["balance_residual_wh"]) for r in ledger),
            "unmet_requested_load_wh": sum(r["unmet_requested_load_wh"] for r in ledger),
            "unserved_jobs_reference_load_wh": sum(r["reference_load_wh"] for r in incomplete),
            "energy_blocked_jobs_reference_load_wh": sum(r["reference_load_wh"] for r in energy_blocked),
            "shed_background_reference_load_wh": sum(r["shed_background_reference_load_wh"] for r in ledger),
            "requested_services": len(requests), "budget_completed_services": len(waits),
            "no_budget_result_services": len(incomplete),
            "no_budget_result_fraction": len(incomplete) / len(requests) if requests else None,
            "successful_wait_mean_min": sum(waits) / len(waits) if waits else None,
            "successful_wait_p95_min": waits[math.ceil(0.95 * len(waits)) - 1] if waits else None,
            "unresolved_wait_total_min": sum(r["wait_minutes"] for r in incomplete),
            "measurement_cycles": sum(a["kind"] == "measurement" and a["outcome"] == "budget_completed" for a in attempts),
            "wifi_attempts": sum(a["kind"] == "wifi" for a in attempts),
            "wifi_failed_attempts": sum(a["outcome"] == "network_failed" for a in attempts),
            "stopped_minutes": sum(r["mode_during"] == "stopped" for r in ledger),
            "low_minutes": sum(r["mode_during"] == "low" for r in ledger),
            "restart_count": sum(r["reason"] == "paid_restart" for r in transitions),
            "collection_authorizations": 0, "measurement_validity": "not_evaluated",
            "dry_detection_latency": None}
    for kind in ("measurement", "wifi"):
        group = [r for r in requests if r["kind"] == kind]
        completed = [r for r in group if r["status"] == "budget_completed"]
        kind_waits = sorted(r["wait_minutes"] for r in completed)
        summary.update({
            f"{kind}_requests": len(group),
            f"{kind}_budget_completed_requests": len(completed),
            f"{kind}_no_result_fraction": (len(group) - len(completed)) / len(group) if group else None,
            f"{kind}_successful_wait_p95_min": kind_waits[math.ceil(0.95 * len(kind_waits)) - 1] if kind_waits else None,
        })
    return summary

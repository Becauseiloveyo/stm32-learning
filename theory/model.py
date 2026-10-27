"""Uncalibrated, isothermal short-window chamber model. No hardware dependencies.

This model does not simulate whole-garment water content or validate a dry threshold.
All defaults except physical constants are illustrative engineering assumptions.
"""

from dataclasses import dataclass
from collections.abc import Mapping
import math
import random
import statistics

if __package__:
    from .protocol import (DEFAULT_GATES, DEFAULT_PROTOCOL, Gates, MeasurementProtocol,
                           OBSERVATION_VERSION, PHASES, UNIT_SYSTEM, finite_number)
else:
    from protocol import (DEFAULT_GATES, DEFAULT_PROTOCOL, Gates, MeasurementProtocol,
                          OBSERVATION_VERSION, PHASES, UNIT_SYSTEM, finite_number)

RV = 461.5  # J / (kg K), water-vapor specific gas constant
DEFAULT_SEED = 20261023


def saturation_pa(temp_c):
    if not math.isfinite(temp_c) or not 0 <= temp_c <= 50:
        raise ValueError("This prototype approximation is restricted to 0..50 C")
    return 610.94 * math.exp(17.625 * temp_c / (temp_c + 243.04))


def vapor_pa(temp_c, rh_pct):
    if not math.isfinite(rh_pct) or not 0 <= rh_pct <= 100:
        raise ValueError("RH must be finite and in 0..100 percent")
    return saturation_pa(temp_c) * rh_pct / 100


def independent_delta_uncertainty(temp_c, rh_pct, u_rh_pct, u_temp_c):
    """Two equal independent probes; inputs are STANDARD uncertainties, not specs."""
    if min(u_rh_pct, u_temp_c) < 0 or not all(
        math.isfinite(x) for x in (u_rh_pct, u_temp_c)
    ):
        raise ValueError("Uncertainties must be finite and nonnegative")
    e = vapor_pa(temp_c, rh_pct)
    d_rh = saturation_pa(temp_c) / 100
    d_t = e * 17.625 * 243.04 / (temp_c + 243.04) ** 2
    return math.sqrt(2 * ((d_rh * u_rh_pct) ** 2 + (d_t * u_temp_c) ** 2))


@dataclass(frozen=True)
class Parameters:
    volume_ml: float = 5.0
    exchange_ml_s: float = 0.02
    leak_ml_s: float = 0.02
    passive_extra_ml_s: float = 0.38
    flush_extra_ml_s: float = 0.98
    temp_c: float = 25.0
    ambient_rh_pct: float = 60.0
    surface_activity: float = 1.0
    wall_source_ug_s: float = 0.0
    sensor_tau_s: float = 4.0
    noise_rh_pct: float = 0.10
    local_rh_bias_pct: float = 0.0
    local_temp_bias_c: float = 0.0
    ambient_step_at_s: float = -1.0
    ambient_step_rh_pct: float = 0.0
    clip_at_s: float = -1.0
    invalid_at_s: float = -1.0

    def validate(self):
        for name, value in vars(self).items():
            if not finite_number(value):
                raise ValueError(f"{name} must be finite")
        for name in ("volume_ml",):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        for name in ("exchange_ml_s", "leak_ml_s", "passive_extra_ml_s",
                     "flush_extra_ml_s", "wall_source_ug_s", "sensor_tau_s", "noise_rh_pct"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be nonnegative")
        if not 0 <= self.surface_activity <= 1:
            raise ValueError("surface_activity must be in 0..1")
        vapor_pa(self.temp_c, self.ambient_rh_pct)
        vapor_pa(self.temp_c, self.ambient_rh_pct + self.ambient_step_rh_pct)
        saturation_pa(self.temp_c + self.local_temp_bias_c)


def chamber_and_sensor_step(pc, ps, source, rate, tau, dt):
    """Exact solution of dpc/dt=source-rate*pc; dps/dt=(pc-ps)/tau.

    Constants are frozen over this interval. No clipping or hidden condensation sink.
    """
    if dt <= 0 or rate < 0 or tau < 0:
        raise ValueError("Invalid time step, decay rate, or sensor time constant")
    if rate == 0:
        pc_next = pc + source * dt
        if tau == 0:
            return pc_next, pc_next
        decay = math.exp(-dt / tau)
        ps_next = pc + source * (dt - tau) + (ps - pc + source * tau) * decay
        return pc_next, ps_next
    equilibrium = source / rate
    e_chamber = math.exp(-rate * dt)
    pc_next = equilibrium + (pc - equilibrium) * e_chamber
    if tau == 0:
        return pc_next, pc_next
    e_sensor = math.exp(-dt / tau)
    if abs(1 - rate * tau) < 1e-9:
        coupling = (dt / tau) * e_sensor
    else:
        coupling = (e_chamber - e_sensor) / (1 - rate * tau)
    ps_next = equilibrium + (ps - equilibrium) * e_sensor + (pc - equilibrium) * coupling
    return pc_next, ps_next


def simulate(p, seed=DEFAULT_SEED, *, protocol=DEFAULT_PROTOCOL):
    """Generate versioned observations using one explicit measurement protocol."""
    p.validate()
    if not isinstance(protocol, MeasurementProtocol):
        raise ValueError("protocol must be a MeasurementProtocol")
    protocol.validate()
    dt, total = protocol.sample_period_s, protocol.total_s
    events = [t for t in (p.ambient_step_at_s, p.clip_at_s, p.invalid_at_s) if t >= 0]
    if any(t > total + protocol.time_tolerance_s or not protocol.aligned(t) for t in events):
        raise ValueError("Enabled events must be within the cycle and align with sample_period_s")
    protocol_id = protocol.fingerprint

    def event_at(time_s, event_s):
        return event_s >= 0 and math.isclose(time_s, event_s, rel_tol=0, abs_tol=protocol.time_tolerance_s)

    rng = random.Random(seed)
    volume = p.volume_ml * 1e-6
    exchange = p.exchange_ml_s * 1e-6
    sat = saturation_pa(p.temp_c)
    surface = p.surface_activity * sat
    ambient0 = p.ambient_rh_pct / 100 * sat
    wall = RV * (p.temp_c + 273.15) * p.wall_source_ug_s * 1e-9
    q0 = (p.leak_ml_s + p.passive_extra_ml_s) * 1e-6
    if exchange + q0 == 0:
        if wall:
            raise ValueError("No initial steady state for a sealed chamber with a wall source")
        chamber = ambient0
    else:
        chamber = (exchange * surface + q0 * ambient0 + wall) / (exchange + q0)
    local_sensor, ambient_sensor = chamber, ambient0
    rows = []
    for index in range(round(total / dt) + 1):
        time = index * dt
        phase = protocol.phase_at(time)
        ambient_rh = p.ambient_rh_pct + (
            p.ambient_step_rh_pct if p.ambient_step_at_s >= 0 and time >= p.ambient_step_at_s - protocol.time_tolerance_s else 0
        )
        ambient = ambient_rh / 100 * sat
        # Out-of-domain physical saturation is explicit: no invented condensation model.
        if not 0 <= chamber <= sat:
            raise ValueError("Chamber outside unsaturated model domain; condensation not modeled")
        observed_a = ambient_sensor / sat * 100 + rng.gauss(0, p.noise_rh_pct)
        observed_c = local_sensor / sat * 100 + p.local_rh_bias_pct + rng.gauss(0, p.noise_rh_pct)
        valid = (0 <= observed_a <= 100 and 0 <= observed_c <= 100
                 and not event_at(time, p.invalid_at_s))
        temp_local = p.temp_c + p.local_temp_bias_c
        delta = vapor_pa(temp_local, observed_c) - vapor_pa(p.temp_c, observed_a) if valid else None
        rows.append({
            "observation_version": OBSERVATION_VERSION, "unit_system": UNIT_SYSTEM,
            "protocol_sha256": protocol_id,
            "time_s": time, "phase": phase, "ambient_T_C": p.temp_c,
            "ambient_RH_pct": observed_a, "local_T_C": temp_local,
            "local_RH_pct": observed_c, "sensor_valid": valid,
            "clip_event": event_at(time, p.clip_at_s),
            "delta_e_Pa": delta, "synthetic_true_chamber_Pa": chamber,
        })
        if index == round(total / dt):
            break
        extra = p.passive_extra_ml_s if phase == "baseline" else (p.flush_extra_ml_s if phase == "flush" else 0)
        flow = (p.leak_ml_s + extra) * 1e-6
        source = (exchange * surface + flow * ambient + wall) / volume
        rate = (exchange + flow) / volume
        chamber, local_sensor = chamber_and_sensor_step(
            chamber, local_sensor, source, rate, p.sensor_tau_s, dt
        )
        ambient_sensor = ambient if p.sensor_tau_s == 0 else (
            ambient + (ambient_sensor - ambient) * math.exp(-dt / p.sensor_tau_s)
        )
    return rows


def assess(rows, gates=DEFAULT_GATES, *, protocol=DEFAULT_PROTOCOL):
    """Consumes observations only: never reads model parameters or truth labels.

    No READY_TO_COLLECT output exists in this uncalibrated theoretical prototype.
    """
    # Configuration errors belong to the caller, malformed measurements to UNKNOWN.
    if not isinstance(protocol, MeasurementProtocol) or not isinstance(gates, Gates):
        raise ValueError("Expected MeasurementProtocol and Gates configuration objects")
    protocol.validate()
    gates.validate()
    protocol_id = protocol.fingerprint

    def result(state, reasons, rise=None, tail=None):
        return {"state": state, "reasons": ";".join(dict.fromkeys(reasons)),
                "recovery_rise_Pa": rise, "tail_delta_Pa": tail,
                "protocol_sha256": protocol_id, "collection_authorized": False}

    measured_keys = ("time_s", "ambient_T_C", "ambient_RH_pct", "local_T_C", "local_RH_pct")
    required = (*measured_keys, "phase", "sensor_valid", "clip_event",
                "observation_version", "unit_system", "protocol_sha256")
    if not isinstance(rows, (list, tuple)) or not rows:
        return result("UNKNOWN", ["missing_data"])
    if any(not isinstance(r, Mapping) or any(k not in r for k in required) for r in rows):
        return result("UNKNOWN", ["missing_field"])
    reasons = []
    if any(r["observation_version"] != OBSERVATION_VERSION for r in rows):
        reasons.append("unsupported_observation_version")
    if any(r["unit_system"] != UNIT_SYSTEM for r in rows):
        reasons.append("unsupported_units")
    if any(r["protocol_sha256"] != protocol_id for r in rows):
        reasons.append("protocol_mismatch")
    if reasons:
        return result("UNKNOWN", reasons)
    if any(type(r["sensor_valid"]) is not bool or type(r["clip_event"]) is not bool for r in rows):
        return result("UNKNOWN", ["invalid_flag"])
    if any(not r["sensor_valid"] or not all(finite_number(r[k]) for k in measured_keys) for r in rows):
        return result("UNKNOWN", ["invalid_sensor"])
    if any(not (0 <= r["ambient_RH_pct"] <= 100 and 0 <= r["local_RH_pct"] <= 100
                and 0 <= r["ambient_T_C"] <= 50 and 0 <= r["local_T_C"] <= 50) for r in rows):
        return result("UNKNOWN", ["outside_model_domain"])
    tolerance = protocol.time_tolerance_s
    if any(b["time_s"] <= a["time_s"] for a, b in zip(rows, rows[1:])):
        reasons.append("non_monotonic_time")
    if any(b["time_s"] - a["time_s"] > protocol.max_sample_gap_s + tolerance for a, b in zip(rows, rows[1:])):
        reasons.append("sample_gap")
    if any(not any(r["phase"] == phase for r in rows) for phase in PHASES):
        reasons.append("missing_phase")
    if any(r["phase"] not in PHASES or r["phase"] != protocol.phase_at(r["time_s"]) for r in rows):
        reasons.append("phase_time_mismatch")
    if (abs(rows[0]["time_s"]) > tolerance or abs(rows[-1]["time_s"] - protocol.total_s) > tolerance
            or any(r["time_s"] < -tolerance or r["time_s"] > protocol.total_s + tolerance for r in rows)):
        reasons.append("incomplete_cycle")
    if any(sum(r["phase"] == phase for r in rows) < protocol.min_phase_samples for phase in PHASES):
        reasons.append("insufficient_phase_samples")
    recovery = [r for r in rows if r["phase"] == "recovery"]
    head_rows = [r for r in recovery if r["time_s"] <= protocol.recovery_start_s + protocol.head_window_s + tolerance]
    tail_rows = [r for r in recovery if r["time_s"] >= protocol.total_s - protocol.tail_window_s - tolerance]
    if min(len(head_rows), len(tail_rows)) < protocol.min_window_samples:
        reasons.append("insufficient_feature_samples")
    if reasons:
        return result("UNKNOWN", reasons)
    ambient = [r["ambient_RH_pct"] for r in rows]
    if max(ambient) >= gates.high_ambient_rh_pct:
        reasons.append("high_ambient_humidity")
    if max(r["local_RH_pct"] for r in rows) >= gates.saturation_rh_pct:
        reasons.append("near_saturation")
    if max(ambient) - min(ambient) > gates.ambient_change_rh_pct:
        reasons.append("ambient_changed")
    if max(abs(r["local_T_C"] - r["ambient_T_C"]) for r in rows) > gates.temp_mismatch_c:
        reasons.append("temperature_mismatch")
    if any(r["clip_event"] for r in rows):
        reasons.append("clip_changed")

    def observed_delta(row):
        return vapor_pa(row["local_T_C"], row["local_RH_pct"]) - vapor_pa(row["ambient_T_C"], row["ambient_RH_pct"])

    # Exported delta_e_Pa and every synthetic truth/label field are diagnostic only.
    head = statistics.mean(observed_delta(r) for r in head_rows)
    tail = statistics.mean(observed_delta(r) for r in tail_rows)
    rise = tail - head
    if reasons:
        state = "UNKNOWN"
    elif rise >= gates.strong_delta_pa and tail >= gates.strong_delta_pa:
        state = "VAPOR_SOURCE_PRESENT"
    elif abs(rise) <= gates.weak_delta_pa and abs(tail) <= gates.weak_delta_pa:
        state = "LOW_SIGNAL_UNCONFIRMED"
        reasons.append("low_signal_does_not_prove_dry")
    else:
        state = "UNKNOWN"
        reasons.append("ambiguous_response")
    return result(state, reasons, rise, tail)


@dataclass
class EvidenceTracker:
    """Repeated weak evidence is recorded, but cannot manufacture calibration."""
    weak_cycles: int = 0

    def update(self, result):
        if result["state"] == "LOW_SIGNAL_UNCONFIRMED":
            self.weak_cycles += 1
        else:
            self.weak_cycles = 0
        return {"state": result["state"], "weak_cycles": self.weak_cycles, "collection_authorized": False}

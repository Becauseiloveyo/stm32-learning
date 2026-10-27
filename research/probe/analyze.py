"""Independent, deterministic probe research. SYNTHETIC_UNCALIBRATED only.

Reads baseline-v1 hashes, never imports the active theory model. Uses only stdlib.
No structure-specific leak assignment, classifier, garment mass, or dry time.
"""

from dataclasses import asdict, dataclass, replace
from pathlib import Path
import csv
import hashlib
import itertools
import json
import math
import statistics

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "docs/team/baseline-v1"
OUTPUT = Path(__file__).resolve().parent / "results"
RV = 461.5  # J/(kg K), inherited physical constant
LABEL = "SYNTHETIC_UNCALIBRATED"


def saturation_pa(temp_c):
    if not math.isfinite(temp_c) or not 0 <= temp_c <= 50:
        raise ValueError("Inherited approximation domain: 0..50 degC")
    return 610.94 * math.exp(17.625 * temp_c / (temp_c + 243.04))


@dataclass(frozen=True)
class Parameters:
    volume_ml: float = 5.0
    exchange_ml_s: float = 0.02
    leak_ml_s: float = 0.02
    temp_c: float = 25.0
    ambient_rh_pct: float = 60.0
    surface_activity: float = 1.0
    wall_source_ug_s: float = 0.0
    sensor_tau_s: float = 4.0

    def validate(self):
        if not all(math.isfinite(v) for v in asdict(self).values()):
            raise ValueError("Parameters must be finite")
        if self.volume_ml <= 0 or self.exchange_ml_s + self.leak_ml_s <= 0:
            raise ValueError("Positive volume and exchange sum required")
        if min(self.exchange_ml_s, self.leak_ml_s, self.wall_source_ug_s, self.sensor_tau_s) < 0:
            raise ValueError("Negative exchange, desorption, or time constant")
        if not 0 <= self.ambient_rh_pct <= 100 or not 0 <= self.surface_activity <= 1:
            raise ValueError("RH/activity out of range")
        saturation_pa(self.temp_c)


def coefficients(p, extra_ml_s=0.0):
    """Return B/V [Pa/s], D/V [1/s] for delta pressure y; constant ambient."""
    p.validate()
    k, q, v = p.exchange_ml_s * 1e-6, (p.leak_ml_s + extra_ml_s) * 1e-6, p.volume_ml * 1e-6
    b = k * saturation_pa(p.temp_c) * (p.surface_activity - p.ambient_rh_pct / 100)
    b += RV * (p.temp_c + 273.15) * p.wall_source_ug_s * 1e-9
    return b / v, (k + q) / v


def step(y, z, source, rate, tau_s, dt):
    """Exact interval solution, y'=source-rate*y; z'=(y-z)/tau_s.

    Rates are strictly positive in this research grid. tau=0 is an ideal sensor.
    """
    if rate <= 0 or dt <= 0 or tau_s < 0:
        raise ValueError("Invalid integration interval")
    eq = source / rate
    ec = math.exp(-rate * dt)
    yn = eq + (y - eq) * ec
    if tau_s == 0:
        return yn, yn
    es = math.exp(-dt / tau_s)
    if math.isclose(rate * tau_s, 1.0, rel_tol=1e-10, abs_tol=1e-12):
        kernel = dt / tau_s * es
    else:
        kernel = (ec - es) / (1 - rate * tau_s)
    return yn, eq + (z - eq) * es + (y - eq) * kernel


# Frozen baseline protocol: sample on integer seconds, state at phase boundary
# belongs to the new phase; its command affects the following interval.
PROTOCOL = {
    "dt_s": 1,
    "baseline_s": 30,
    "flush_s": 20,
    "recovery_s": 90,
    "passive_extra_ml_s": 0.38,
    "flush_extra_ml_s": 0.98,
    "head_relative_s_inclusive": [0, 5],
    "tail_relative_s_inclusive": [80, 90],
}


def cycle(p):
    source, rate = coefficients(p, PROTOCOL["passive_extra_ml_s"])
    y = z = source / rate  # assumed initial passive equilibrium
    sat = saturation_pa(p.temp_c)
    ambient = sat * p.ambient_rh_pct / 100
    rows = []
    for t in range(141):
        phase = "baseline" if t < 30 else "flush" if t < 50 else "recovery"
        if not 0 <= y + ambient < sat:
            raise ValueError("Outside unsaturated model: no condensation clipping")
        rows.append({"time_s": t, "phase": phase, "synthetic_true_delta_Pa": y,
                     "synthetic_observed_delta_Pa": z})
        if t == 140:
            break
        extra = PROTOCOL["passive_extra_ml_s"] if phase == "baseline" else (
            PROTOCOL["flush_extra_ml_s"] if phase == "flush" else 0.0)
        source, rate = coefficients(p, extra)
        y, z = step(y, z, source, rate, p.sensor_tau_s, 1.0)
    return rows


def features(p, rows):
    head = statistics.mean(r["synthetic_observed_delta_Pa"] for r in rows if 50 <= r["time_s"] <= 55)
    tail = statistics.mean(r["synthetic_observed_delta_Pa"] for r in rows if 130 <= r["time_s"] <= 140)
    source, rate = coefficients(p)
    eq = source / rate
    return {"rise_Pa": tail - head, "tail_Pa": tail, "tau_chamber_s": 1 / rate,
            "formal_equilibrium_delta_Pa": eq,
            "equilibrium_in_unsaturated_domain": 0 <= eq + saturation_pa(p.temp_c) * p.ambient_rh_pct / 100 < saturation_pa(p.temp_c)}


def matched_wall_ug_s(p):
    """Replace the positive cloth source with an algebraically equal wall source."""
    b = p.exchange_ml_s * 1e-6 * saturation_pa(p.temp_c) * (p.surface_activity - p.ambient_rh_pct / 100)
    return b / (RV * (p.temp_c + 273.15)) * 1e9


def common_step_delta(t_s, step_pa=-300.0, local_tau_s=30.0, ambient_tau_s=4.0):
    """Both true vapor pressures equal at all times; only sensors differ."""
    return step_pa * (math.exp(-t_s / ambient_tau_s) - math.exp(-t_s / local_tau_s))


def decaying_wall(t_s, wall_tau_s, p=Parameters(surface_activity=0.6, wall_source_ug_s=0.1)):
    """Recovery-only: y(0)=0, aw=RH/100, finite wall inventory, no sensor lag.

    S(t)=S0*exp(-t/tauw); M0=S0*tauw; dM/dt=-S. No uptake model.
    """
    if wall_tau_s <= 0 or t_s < 0 or p.surface_activity != p.ambient_rh_pct / 100:
        raise ValueError("Requires finite wall time, nonnegative time, and equilibrium cloth")
    source, rate = coefficients(p)
    b = 1 / wall_tau_s
    if math.isclose(rate, b, rel_tol=1e-10, abs_tol=1e-12):
        delta = source * t_s * math.exp(-rate * t_s)
    else:
        delta = source * (math.exp(-b * t_s) - math.exp(-rate * t_s)) / (rate - b)
    mass = p.wall_source_ug_s * wall_tau_s * math.exp(-b * t_s)
    return delta, mass


def uncertainty_example(temp_c=25.0, rh_pct=60.0, u_rh_pct=1.0, u_temp_c=0.1):
    sat = saturation_pa(temp_c)
    drh = sat / 100
    dt = sat * rh_pct / 100 * 17.625 * 243.04 / (temp_c + 243.04) ** 2
    return {"d_e_d_RH_Pa_per_percentage_point": drh, "d_e_d_T_Pa_per_C": dt,
            "u_delta_Pa_independent": math.sqrt(2 * ((drh * u_rh_pct) ** 2 + (dt * u_temp_c) ** 2)),
            "u_RH_percentage_points_assumed": u_rh_pct, "u_T_C_assumed": u_temp_c}


def baseline_fingerprints():
    snapshot = json.loads((BASELINE / "snapshot.json").read_text(encoding="utf-8"))
    for name, expected in snapshot["files"].items():
        if hashlib.sha256((BASELINE / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Frozen baseline changed: {name}")
    return {"snapshot_sha256": hashlib.sha256((BASELINE / "snapshot.json").read_bytes()).hexdigest(),
            "snapshot_version": snapshot["snapshot_version"], "files_checked": snapshot["files"]}


def write_csv(name, rows):
    with (OUTPUT / name).open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    frozen = baseline_fingerprints()
    base = Parameters()
    cases = {
        "wet_like_nominal": base,
        "wet_like_large_leak": replace(base, leak_ml_s=1.5),
        "equilibrium_surface": replace(base, surface_activity=0.6),
        "equilibrium_with_matched_wall": replace(base, surface_activity=0.6, wall_source_ug_s=matched_wall_ug_s(base)),
        "same_AB_different_parameters": replace(base, exchange_ml_s=0.04, leak_ml_s=0.0, surface_activity=0.8),
        "sensor_30s": replace(base, sensor_tau_s=30.0),
        "sensor_120s_stress": replace(base, sensor_tau_s=120.0),
        "small_K_stress": replace(base, exchange_ml_s=0.002),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    traces, summaries = {}, []
    for name, p in cases.items():
        traces[name] = cycle(p)
        summaries.append({"case": name, **asdict(p), **features(p, traces[name]), "data_status": LABEL})
    write_csv("case-summary.csv", summaries)
    write_csv("case-traces.csv", [{"case": name, **row, "data_status": LABEL} for name, rows in traces.items() for row in rows])
    grid = {"volume_ml": [2, 5, 10], "leak_ml_s": [0.005, 0.02, 0.1, 0.5, 1.5],
            "wall_source_ug_s": [0.0, 0.02, 0.1], "sensor_tau_s": [4.0, 30.0, 120.0]}
    sweep = []
    for values in itertools.product(*grid.values()):
        p = replace(base, **dict(zip(grid, values)))
        try:
            f = features(p, cycle(p))
            status = "within_short_window_domain"
        except ValueError as exc:
            f = {key: None for key in features(base, traces["wet_like_nominal"])}
            status = str(exc)
        sweep.append({**asdict(p), **f, "domain_status": status, "data_status": LABEL})
    write_csv("sensitivity.csv", sweep)
    peak_t = math.log(30 / 4) / (1 / 4 - 1 / 30)
    write_csv("common-step.csv", [{"time_s": t, "true_delta_Pa": 0.0, "observed_delta_Pa": common_step_delta(t), "data_status": LABEL} for t in range(91)])
    write_csv("decaying-wall.csv", [{"wall_tau_s": tau, "time_s": t,
                                     "true_delta_Pa": decaying_wall(t, tau)[0],
                                     "remaining_wall_water_ug": decaying_wall(t, tau)[1], "data_status": LABEL}
                                    for tau in (30, 120, 600) for t in range(181)])
    temp_rows = []
    ambient = saturation_pa(25) * 0.6
    for offset in (-3.0, -1.0, -0.5, 0.0, 0.5, 1.0, 3.0):
        temp_rows.append({"local_offset_C": offset,
                         "temperature_readout_bias_only_delta_Pa": 0.6 * saturation_pa(25 + offset) - ambient,
                         "consistent_local_RH_at_same_vapor_pct": 100 * ambient / saturation_pa(25 + offset),
                         "consistent_same_vapor_delta_Pa": 0.0, "data_status": LABEL})
    write_csv("temperature.csv", temp_rows)
    # Two independent faces, same fixed activity and K. Only assumed gas exchange
    # changes. This is instantaneous steady flux, NOT a predicted drying-time ratio.
    conductance = lambda k, q: k * q / (k + q)
    reference = 2 * conductance(0.02, 0.4)
    coverage = []
    for name, front_q, back_q in [("both_faces_exposed_assumption", 0.4, 0.4),
                                  ("front_exchange_restricted_assumption", 0.02, 0.4),
                                  ("both_faces_exchange_restricted_assumption", 0.02, 0.005)]:
        coverage.append({"case": name, "K_each_face_ml_s": 0.02, "front_Q_ml_s": front_q,
                         "back_Q_ml_s": back_q, "relative_instantaneous_flux":
                         (conductance(0.02, front_q) + conductance(0.02, back_q)) / reference,
                         "data_status": LABEL})
    write_csv("coverage.csv", coverage)
    getgap = lambda name: max(abs(a["synthetic_observed_delta_Pa"] - b["synthetic_observed_delta_Pa"])
                             for a, b in zip(traces["wet_like_nominal"], traces[name]))
    manifest = {
        "schema_version": 1, "data_status": LABEL, "date": "2026-10-23", "seed": None,
        "randomness": "None: deterministic analytic calculations; no measurement noise added",
        "scope": "independent short-window preparation for T02/T03; no current theory imports",
        "baseline": frozen, "protocol": PROTOCOL, "cases": {k: asdict(v) for k, v in cases.items()},
        "sweep_grid": grid, "sweep_count": len(sweep),
        "out_of_domain_window_count": sum(r["domain_status"] != "within_short_window_domain" for r in sweep),
        "formal_equilibrium_out_of_domain_count": sum(r["equilibrium_in_unsaturated_domain"] is False for r in sweep),
        "structure_mapping": "None; all structures have unknown actual leakage and wall source",
        "matched_wall_source_ug_s": matched_wall_ug_s(base),
        "matched_wall_max_gap_Pa": getgap("equilibrium_with_matched_wall"),
        "same_AB_max_gap_Pa": getgap("same_AB_different_parameters"),
        "common_step": {"step_Pa": -300.0, "local_tau_s": 30.0, "ambient_tau_s": 4.0,
                        "analytic_peak_time_s": peak_t, "analytic_peak_delta_Pa": common_step_delta(peak_t)},
        "decaying_wall": {"S0_ug_s": 0.1, "wall_tau_s": [30, 120, 600], "y0_Pa": 0.0,
                          "sensor_filter": "not applied", "model": "finite desorption inventory only; no uptake"},
        "uncertainty_example": uncertainty_example(),
        "source_sha256": {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                          for name in ("analyze.py", "test_analysis.py")},
        "outputs_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(OUTPUT.glob("*.csv"))},
        "limitations": ["No hardware data or calibrated flow", "No cloth moisture or thermal dynamics",
                        "No product thresholds or classifier", "Open placement has no established mixed volume",
                        "Formal saturated equilibria must not be extrapolated beyond the unsaturated window"],
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{LABEL}: {len(cases)} cases, {len(sweep)} grid points; baseline {len(frozen['files_checked'])} hashes verified")
    print(f"Equal source pair gap: {manifest['matched_wall_max_gap_Pa']:.3g} Pa; same A/B pair gap: {manifest['same_AB_max_gap_Pa']:.3g} Pa")
    print(f"Common step false delta: {common_step_delta(peak_t):.6f} Pa at {peak_t:.6f} s")
    print(f"Out-of-domain short windows: {manifest['out_of_domain_window_count']}; formal equilibria: {manifest['formal_equilibrium_out_of_domain_count']}")


if __name__ == "__main__":
    main()

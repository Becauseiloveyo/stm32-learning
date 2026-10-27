"""T03: deterministic, uncalibrated sensitivity research; standard library only.

No hardware, model edits, dry threshold, or collection authorization. --verify
checks provenance, replays saved observations and recomputes every output in RAM.
"""

import sys
sys.dont_write_bytecode = True

import argparse
import csv
from dataclasses import asdict, replace
import hashlib
import io
import json
import math
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESULTS = HERE / "results"
sys.path.insert(0, str(ROOT))
from theory import model
from theory.protocol import OBSERVATION_VERSION, UNIT_SYSTEM, DEFAULT_GATES
from theory.run_simulation import verification_protocols

SYNTHETIC = "SYNTHETIC_UNCALIBRATED"
OBS_KEYS = ("observation_version", "unit_system", "protocol_sha256", "time_s",
            "phase", "ambient_T_C", "ambient_RH_pct", "local_T_C",
            "local_RH_pct", "sensor_valid", "clip_event")
MAX_STEP_S = 0.05  # RK4 numerical resolution, not a measurement protocol setting.
SOURCE_FILES = ("run.py", "test_validity.py", "sources.md", "README.md")


class OutsideDomain(ValueError):
    """No observation/assessment should be produced from this research case."""


def profiles():
    available = verification_protocols()
    return {key: available[key] for key in ("default", "three_second_long")}


def times(protocol):
    protocol.validate()
    return [i * protocol.sample_period_s
            for i in range(round(protocol.total_s / protocol.sample_period_s) + 1)]


def window_indices(protocol):
    grid = times(protocol)
    eps = protocol.time_tolerance_s
    head = [i for i, t in enumerate(grid) if protocol.recovery_start_s - eps <= t
            <= protocol.recovery_start_s + protocol.head_window_s + eps]
    tail = [i for i, t in enumerate(grid) if t >= protocol.total_s - protocol.tail_window_s - eps]
    return head, tail


def feature_values(values, protocol):
    if len(values) != len(times(protocol)):
        raise ValueError("Expected a complete protocol grid")
    head, tail = window_indices(protocol)
    tail_mean = statistics.mean(values[i] for i in tail)
    return tail_mean - statistics.mean(values[i] for i in head), tail_mean


def legal_rows(rows):
    return [{key: row[key] for key in OBS_KEYS} for row in rows]


def measured_delta(row):
    return model.vapor_pa(row["local_T_C"], row["local_RH_pct"]) - model.vapor_pa(
        row["ambient_T_C"], row["ambient_RH_pct"])


def biased_rows(rows, rh_bias=0.0, temp_bias=0.0, pressure_offset=0.0, drift=0.0):
    """Apply specified readout errors after the physical simulation, without clipping."""
    result = legal_rows(rows)
    for row in result:
        true_t = row["local_T_C"]
        pressure_bias = pressure_offset + drift * row["time_s"]
        row["local_RH_pct"] += rh_bias + pressure_bias / model.saturation_pa(true_t) * 100
        row["local_T_C"] += temp_bias
        if not (0 <= row["local_T_C"] <= 50 and 0 <= row["local_RH_pct"] <= 100):
            raise OutsideDomain("Readout outside T=0..50 C or RH=0..100%; no clipping")
    return result


def common_step_error(elapsed_s, amplitude_pa, local_tau_s, ambient_tau_s):
    if min(local_tau_s, ambient_tau_s) < 0:
        raise ValueError("Negative response time")
    if elapsed_s < 0:
        return 0.0
    decay = lambda tau: math.exp(-elapsed_s / tau) if tau else 0.0
    return amplitude_pa * (decay(ambient_tau_s) - decay(local_tau_s))


def wall_constants(p):
    rt = model.RV * (p.temp_c + 273.15)
    sat = model.saturation_pa(p.temp_c)
    ambient = sat * p.ambient_rh_pct / 100
    return rt, sat, ambient, p.surface_activity * sat - ambient


def validate_wall_parameters(p, wall_tau_s):
    p.validate()
    if not math.isfinite(wall_tau_s) or wall_tau_s <= 0:
        raise ValueError("wall_tau_s must be positive and finite")
    if any(getattr(p, key) != 0 for key in (
        "wall_source_ug_s", "noise_rh_pct", "local_rh_bias_pct", "local_temp_bias_c",
        "ambient_step_rh_pct")) or any(getattr(p, key) >= 0 for key in (
            "ambient_step_at_s", "clip_at_s", "invalid_at_s")):
        raise ValueError("Finite-wall extension requires constant ambient, no readout errors/events or separate wall source")


def integrate_wall(state, duration_s, p, flow_ml_s, wall_tau_s, max_step_s=MAX_STEP_S):
    """RK4 for (gas excess Pa, sensor excess Pa, wall ug, cloth ug, outlet ug).

    Cloth and outlet integrals are SIGNED transfers. Wall release is counted once.
    Temperature, volume, surface potential and flow are fixed within an interval.
    """
    validate_wall_parameters(p, wall_tau_s)
    if not all(math.isfinite(x) for x in (*state, duration_s, flow_ml_s, max_step_s)):
        raise ValueError("Nonfinite integration input")
    if len(state) != 5 or duration_s < 0 or flow_ml_s < 0 or max_step_s <= 0:
        raise ValueError("Invalid integration input")
    rt, sat, ambient, drive = wall_constants(p)
    volume, exchange, flow = p.volume_ml * 1e-6, p.exchange_ml_s * 1e-6, flow_ml_s * 1e-6

    def domain(s):
        if not all(math.isfinite(v) for v in s) or not 0 <= ambient + s[0] <= sat or s[2] < 0:
            raise OutsideDomain("Finite-wall gas/inventory outside unsaturated nonnegative domain; no condensation sink")

    def derivative(s):
        gas, sensor, inventory, _, _ = s
        release = inventory / wall_tau_s
        cloth = exchange * (drive - gas) / rt * 1e9
        outlet = flow * gas / rt * 1e9
        return (rt * 1e-9 * (cloth + release - outlet) / volume,
                (gas - sensor) / p.sensor_tau_s if p.sensor_tau_s else 0.0,
                -release, cloth, outlet)

    state = tuple(state)
    domain(state)
    limits = [max_step_s, wall_tau_s / 10]
    if exchange + flow:
        limits.append(volume / (exchange + flow) / 10)
    if p.sensor_tau_s:
        limits.append(p.sensor_tau_s / 10)
    count = max(1, math.ceil(duration_s / min(limits)))
    h = duration_s / count
    for _ in range(count):
        k1 = derivative(state)
        k2 = derivative(tuple(v + h * k / 2 for v, k in zip(state, k1)))
        k3 = derivative(tuple(v + h * k / 2 for v, k in zip(state, k2)))
        k4 = derivative(tuple(v + h * k for v, k in zip(state, k3)))
        state = tuple(v + h * (a + 2*b + 2*c + d) / 6
                      for v, a, b, c, d in zip(state, k1, k2, k3, k4))
        if not p.sensor_tau_s:
            state = (state[0], state[0], *state[2:])
        domain(state)
    return state


def finite_wall_cycle(p, initial_source_ug_s, wall_tau_s, protocol, max_step_s=MAX_STEP_S):
    validate_wall_parameters(p, wall_tau_s)
    if not math.isfinite(initial_source_ug_s) or initial_source_ug_s < 0:
        raise ValueError("Initial release must be finite and nonnegative")
    rt, sat, ambient, drive = wall_constants(p)
    exchange = p.exchange_ml_s * 1e-6
    q0 = (p.leak_ml_s + p.passive_extra_ml_s) * 1e-6
    # A specified initial condition, NOT a finite-wall steady-state history.
    if not exchange + q0:
        raise ValueError("Initial passive equilibrium needs nonzero total exchange")
    y0 = (exchange * drive + rt * initial_source_ug_s * 1e-9) / (exchange + q0)
    inventory0 = initial_source_ug_s * wall_tau_s
    state = (y0, y0, inventory0, 0.0, 0.0)
    state = integrate_wall(state, 0, p, q0 * 1e6, wall_tau_s, max_step_s)
    rows, trace = [], []
    grid = times(protocol)
    for index, t in enumerate(grid):
        gas, sensor, inventory, cloth, outlet = state
        sensor_rh = (ambient + sensor) / sat * 100
        if not 0 <= sensor_rh <= 100:
            raise OutsideDomain("Finite-wall sensor readout outside RH domain")
        rows.append(dict(zip(OBS_KEYS, (
            OBSERVATION_VERSION, UNIT_SYSTEM, protocol.fingerprint, t, protocol.phase_at(t),
            p.temp_c, p.ambient_rh_pct, p.temp_c, sensor_rh, True, False))))
        gas_change = p.volume_ml * 1e-6 * (gas - y0) / rt * 1e9
        trace.append({"synthetic": SYNTHETIC, "time_s": t, "phase": protocol.phase_at(t),
                      "true_excess_Pa": gas, "sensor_excess_Pa": sensor,
                      "wall_inventory_ug": inventory, "wall_release_ug_s": inventory / wall_tau_s,
                      "cloth_transfer_ug": cloth, "outlet_excess_ug": outlet,
                      "gas_mass_change_ug": gas_change,
                      "mass_residual_ug": gas_change - (cloth + inventory0 - inventory - outlet)})
        if index + 1 < len(grid):
            phase = protocol.phase_at(t)
            extra = p.passive_extra_ml_s if phase == "baseline" else p.flush_extra_ml_s if phase == "flush" else 0
            state = integrate_wall(state, grid[index+1]-t, p, p.leak_ml_s + extra,
                                   wall_tau_s, max_step_s)
    return rows, trace


def residual_budget(components, covariance):
    """First-order c^T Sigma c for residual measurement uncertainty ONLY.

    Reject repeated mechanism IDs and non-residual entries. This cannot establish
    physical independence: users still must justify the covariance and partition.
    """
    n = len(components)
    if not n or len({c["id"] for c in components}) != n:
        raise ValueError("Empty budget or duplicate mechanism IDs")
    if any(c["kind"] != "residual_measurement" for c in components):
        raise ValueError("Explicit dynamics/systematics and unknown model errors cannot be added as residual variance")
    if len(covariance) != n or any(len(row) != n for row in covariance):
        raise ValueError("Covariance shape mismatch")
    coeff = [c["coefficient"] for c in components]
    if not all(math.isfinite(v) for v in coeff + [v for row in covariance for v in row]):
        raise ValueError("Nonfinite budget")
    scale = max(1.0, max(abs(v) for row in covariance for v in row))
    tol = 1e-12 * scale
    if any(abs(covariance[i][j] - covariance[j][i]) > tol for i in range(n) for j in range(n)):
        raise ValueError("Covariance must be symmetric")
    # Semidefinite Cholesky: zero pivots require zero remaining column residuals.
    lower = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i+1):
            v = covariance[i][j] - sum(lower[i][k] * lower[j][k] for k in range(j))
            if i == j:
                if v < -tol:
                    raise ValueError("Covariance must be positive semidefinite")
                lower[i][j] = math.sqrt(max(0.0, v))
            elif lower[j][j] > math.sqrt(tol):
                lower[i][j] = v / lower[j][j]
            elif abs(v) > tol:
                raise ValueError("Covariance must be positive semidefinite")
    variance = sum(coeff[i] * covariance[i][j] * coeff[j] for i in range(n) for j in range(n))
    return math.sqrt(max(0.0, variance))


def uncertainty_examples():
    t, rh, u_rh, u_t = 25.0, 60.0, 1.0, 0.1
    d_rh = model.saturation_pa(t) / 100
    d_t = model.vapor_pa(t, rh) * 17.625 * 243.04 / (t + 243.04)**2
    components = [{"id": name, "kind": "residual_measurement", "coefficient": c}
                  for name, c in zip(("local_RH", "local_T", "ambient_RH", "ambient_T"),
                                     (d_rh, d_t, -d_rh, -d_t))]
    examples = []
    for rho in (-0.8, 0.0, 0.8, 1.0):
        cov = [[0.0]*4 for _ in range(4)]
        for i, u in enumerate((u_rh, u_t, u_rh, u_t)):
            cov[i][i] = u*u
        cov[0][2] = cov[2][0] = rho*u_rh*u_rh
        cov[1][3] = cov[3][1] = rho*u_t*u_t
        examples.append({"rho_same_quantity_between_probes": rho, "covariance": cov,
                         "residual_standard_uncertainty_Pa": residual_budget(components, cov)})
    return {"synthetic": SYNTHETIC, "scope": "single-point differential vapor pressure; not a cycle feature or system accuracy",
            "assumed_standard_uncertainties": {"RH_percentage_point": u_rh, "T_degC": u_t},
            "operating_point": {"T_degC": t, "RH_pct": rh}, "components": components,
            "covariance_variable_units": ["percentage_point", "degC", "percentage_point", "degC"],
            "examples": examples, "spec_accuracy_conversion": False,
            "explicit_dynamic_effects": ["sensor response", "specified RH/T bias", "slow bias", "finite wall source"],
            "unknown_model_bias": None, "system_accuracy": None,
            "note": "All covariance examples assumed; temporal covariance and calibration are absent. Do not add explicit effects again as random variance."}


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def csv_text(rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def build_outputs():
    summaries, saved, wall_rows, cases = [], [], [], {}
    response_rows, response_summary, protocol_info = [], [], {}
    base = replace(model.Parameters(), noise_rh_pct=0.0)
    equilibrium = replace(base, surface_activity=base.ambient_rh_pct / 100)

    def add(case_id, family, profile, protocol, p, scenario, generate):
        cases[case_id] = {"family": family, "profile": profile, "parameters": asdict(p),
                          "scenario": scenario, "synthetic": SYNTHETIC}
        result, trace, reason = None, [], ""
        try:
            rows, trace = generate()
            rows = legal_rows(rows)
            result = model.assess(rows, protocol=protocol)
            if result["collection_authorized"]:
                raise AssertionError("Collection must never be authorized")
            saved.append({"case_id": case_id, "profile": profile, "synthetic": SYNTHETIC,
                          "observations": rows, "assessment": result})
            for row in trace:
                wall_rows.append({"case_id": case_id, "profile": profile, **row})
        except OutsideDomain as exc:
            reason = str(exc)
        summaries.append({"case_id": case_id, "profile": profile, "family": family,
                          "synthetic": SYNTHETIC, "applicability": "APPLICABLE" if result else "NOT_APPLICABLE",
                          "state": result["state"] if result else "",
                          "reasons": result["reasons"] if result else reason,
                          "recovery_rise_Pa": result["recovery_rise_Pa"] if result else None,
                          "tail_delta_Pa": result["tail_delta_Pa"] if result else None,
                          "max_mass_residual_ug": max((abs(r["mass_residual_ug"]) for r in trace), default=None),
                          "collection_authorized": False})

    for profile, protocol in profiles().items():
        grid = times(protocol)
        head, tail = window_indices(protocol)
        protocol_info[profile] = {"snapshot": protocol.snapshot(), "sha256": protocol.fingerprint,
                                  "head_times_s": [grid[i] for i in head], "tail_times_s": [grid[i] for i in tail],
                                  "centroid_separation_s": feature_values(grid, protocol)[0]}
        for tau in (0.0, 4.0, 30.0, 120.0):
            p = replace(base, sensor_tau_s=tau)
            add(f"{profile}/response/{tau:g}", "local_response", profile, protocol, p,
                {"local_tau_s": tau, "ambient_is_constant": True},
                lambda p=p: (model.simulate(p, protocol=protocol), []))
        for label, p in (("surface_source", base), ("equilibrium", equilibrium)):
            raw = model.simulate(p, protocol=protocol)
            for rb in (-2.0, 0.0, 2.0):
                for tb in (-1.0, 0.0, 1.0):
                    add(f"{profile}/bias/{label}/{rb:g}/{tb:g}", "RH_T_readout_bias", profile, protocol, p,
                        {"RH_bias_percentage_point": rb, "T_readout_bias_degC": tb},
                        lambda rb=rb, tb=tb: (biased_rows(raw, rh_bias=rb, temp_bias=tb), []))
        raw = model.simulate(equilibrium, protocol=protocol)
        for offset in (0.0, 30.0):
            for drift in (-0.5, 0.0, 0.5):
                add(f"{profile}/drift/{offset:g}/{drift:g}", "slow_readout_bias", profile, protocol, equilibrium,
                    {"offset_Pa": offset, "drift_Pa_s": drift,
                     "expected_rise_bias_Pa": drift * feature_values(grid, protocol)[0]},
                    lambda offset=offset, drift=drift: (biased_rows(raw, pressure_offset=offset, drift=drift), []))
        add(f"{profile}/outside_readout", "domain_stress", profile, protocol, equilibrium,
            {"offset_Pa": 2000.0}, lambda: (biased_rows(raw, pressure_offset=2000), []))
        for source, tau in ((0.0, 120.0), (0.1, 30.0), (0.1, 120.0), (0.1, 125.0), (0.1, 600.0)):
            add(f"{profile}/wall/{source:g}/{tau:g}", "finite_wall", profile, protocol, equilibrium,
                {"initial_source_ug_s": source, "wall_tau_s": tau, "initial_inventory_ug": source*tau,
                 "initial_condition": "gas and sensor at instantaneous-source passive equilibrium; wall decays from cycle start"},
                lambda source=source, tau=tau: finite_wall_cycle(equilibrium, source, tau, protocol))
        stress = replace(equilibrium, volume_ml=2.0)
        add(f"{profile}/outside_wall", "domain_stress", profile, protocol, stress,
            {"initial_source_ug_s": 1.0, "wall_tau_s": 600.0, "initial_inventory_ug": 600.0},
            lambda: finite_wall_cycle(stress, 1.0, 600.0, protocol))

        for amp, local, ambient in ((-300, 4, 4), (-300, 30, 4), (-300, 4, 30), (-300, 120, 4), (300, 30, 4)):
            key = f"{profile}/common_step/{amp}/{local}/{ambient}"
            errors = [common_step_error(t-protocol.recovery_start_s, amp, local, ambient) for t in grid]
            for t, error in zip(grid, errors):
                response_rows.append({"case_id": key, "profile": profile, "synthetic": SYNTHETIC,
                                      "time_s": t, "step_elapsed_s": t-protocol.recovery_start_s,
                                      "error_Pa": error})
            rise, tail_error = feature_values(errors, protocol)
            response_summary.append({"case_id": key, "profile": profile, "synthetic": SYNTHETIC,
                                     "kind": "ERROR_ONLY", "step_at_s": protocol.recovery_start_s,
                                     "amplitude_Pa": amp, "local_tau_s": local, "ambient_tau_s": ambient,
                                     "max_sampled_abs_error_Pa": max(map(abs, errors)),
                                     "rise_error_Pa": rise, "tail_error_Pa": tail_error,
                                     "assessment": None, "collection_authorized": False})
    temperatures = []
    t, rh = base.temp_c, base.ambient_rh_pct
    ea = model.vapor_pa(t, rh)
    for offset in (-3.0, -1.0, -0.5, 0.0, 0.5, 1.0, 3.0):
        corrected_rh = 100 * ea / model.saturation_pa(t+offset)
        for kind, value, local_rh in (
            ("readout_T_bias", model.vapor_pa(t+offset, rh)-ea, rh),
            ("true_air_T_same_vapor", model.vapor_pa(t+offset, corrected_rh)-ea, corrected_rh),
            ("true_surface_T_potential_only_aw1", model.saturation_pa(t+offset)-ea, None)):
            temperatures.append({"synthetic": SYNTHETIC, "kind": kind, "offset_degC": offset,
                                 "ambient_T_C": t, "ambient_RH_pct": rh, "local_RH_pct": local_rh,
                                 "delta_or_potential_Pa": value, "assessment": None,
                                 "collection_authorized": False})
    payloads = {"cycle-summary.csv": csv_text(summaries),
                "observations.jsonl": "".join(json.dumps(x, sort_keys=True, allow_nan=False)+"\n" for x in saved),
                "wall-traces.csv": csv_text(wall_rows), "response-error.csv": csv_text(response_rows),
                "response-summary.csv": csv_text(response_summary), "temperature.csv": csv_text(temperatures),
                "uncertainty.json": json_text(uncertainty_examples())}
    metadata = {"cases": cases, "protocols": protocol_info,
                "counts": {"cycle_cases": len(summaries), "complete_cycles": len(saved),
                           "not_applicable": len(summaries)-len(saved), "wall_trace_rows": len(wall_rows),
                           "error_only_step_cases": len(response_summary), "temperature_rows": len(temperatures)},
                "max_mass_residual_ug": max(abs(r["mass_residual_ug"]) for r in wall_rows)}
    return {k: v.encode("utf-8") for k, v in payloads.items()}, metadata


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    return sha_bytes(path.read_bytes())


def check_hashes(base, mapping):
    for name, expected in mapping.items():
        if sha_file(base / name) != expected:
            raise ValueError(f"Stale or changed file: {base / name}")


def input_hashes():
    """Validate accepted upstream manifests; include transitive provenance."""
    t01_path = ROOT / "theory/results/manifest.json"
    t01 = json.loads(t01_path.read_text(encoding="utf-8"))
    check_hashes(ROOT / "theory", t01["source_sha256"])
    t02_path = ROOT / "research/probe/results-t02/manifest.json"
    t02 = json.loads(t02_path.read_text(encoding="utf-8"))
    paths = set(t02["input_sha256"])
    check_hashes(ROOT, t02["input_sha256"])
    for key, folder in (("source_sha256", "research/probe"), ("output_sha256", "research/probe/results-t02")):
        check_hashes(ROOT / folder, t02[key])
        paths.update(f"{folder}/{name}" for name in t02[key])
    paths.update(("theory/results/manifest.json", "research/probe/results-t02/manifest.json",
                  "docs/PROBE-CONCEPTS.md", "docs/MEASUREMENT-CONTRACT.md",
                  "docs/MODEL-VALIDITY-DRAFT.md", "research/probe/sources.md",
                  "docs/team/assignments/probe-t03.md"))
    return {name: sha_file(ROOT / name) for name in sorted(paths)}


def make_manifest(inputs, outputs, metadata):
    return {"manifest_version": 1, "synthetic": SYNTHETIC, "collection_authorized": False,
            "input_sha256": inputs, "source_sha256": {name: sha_file(HERE/name) for name in SOURCE_FILES},
            "output_sha256": {name: sha_bytes(data) for name, data in outputs.items()},
            "randomness": {"noise_rh_pct": 0.0, "main_simulator_seed": model.DEFAULT_SEED,
                           "extension_seed": None, "deterministic": True},
            "units": {"pressure": "Pa", "temperature": "degC", "RH": "percentage points",
                      "time": "s", "volume": "mL", "flow": "mL/s", "mass": "ug",
                      "wall_release": "ug/s", "pressure_drift": "Pa/s"},
            "numerics": {"wall_method": "RK4", "max_internal_step_s": MAX_STEP_S,
                         "step_also_limited_by_each_dynamic_time_constant_divided_by": 10},
            "observation_keys": list(OBS_KEYS), "gates_are_illustrative": asdict(DEFAULT_GATES),
            "scope": "isothermal, unsaturated, single chamber; no garment water mass, calibration or dry-time prediction",
            **metadata}


def verify(result_dir=RESULTS):
    manifest = json.loads((result_dir / "manifest.json").read_text(encoding="utf-8"))
    check_hashes(HERE, manifest["source_sha256"])
    check_hashes(ROOT, manifest["input_sha256"])
    check_hashes(result_dir, manifest["output_sha256"])
    inputs = input_hashes()
    protocols = profiles()
    for line in (result_dir / "observations.jsonl").read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        if any(set(row) != set(OBS_KEYS) for row in item["observations"]):
            raise ValueError("Saved observation whitelist mismatch")
        actual = model.assess(item["observations"], protocol=protocols[item["profile"]])
        if actual != item["assessment"] or actual["collection_authorized"]:
            raise ValueError("Saved observation replay mismatch")
    outputs, metadata = build_outputs()
    for name, data in outputs.items():
        if (result_dir / name).read_bytes() != data:
            raise ValueError(f"Recalculation mismatch: {name}")
    if make_manifest(inputs, outputs, metadata) != manifest or inputs != input_hashes():
        raise ValueError("Manifest/recomputed metadata/input stability mismatch")
    return metadata["counts"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="Read-only hashes, saved observation replay and full recomputation")
    args = parser.parse_args()
    if args.verify:
        counts = verify()
        print("VERIFIED (read-only): " + json.dumps(counts, sort_keys=True))
        return
    inputs = input_hashes()
    outputs, metadata = build_outputs()
    manifest = make_manifest(inputs, outputs, metadata)
    if inputs != input_hashes():
        raise ValueError("Upstream inputs changed during computation; nothing written")
    RESULTS.mkdir(exist_ok=True)
    for name, data in outputs.items():
        (RESULTS/name).write_bytes(data)
    (RESULTS/"manifest.json").write_bytes(json_text(manifest).encode("utf-8"))
    print("GENERATED: " + json.dumps(metadata["counts"], sort_keys=True))


if __name__ == "__main__":
    main()

"""T02 replay against accepted T01, preserving all first-round evidence.

No default timing is defined here. Protocols come from theory.run_simulation.
The research solver uses the old independent delta-pressure analytic kernel;
the main solver integrates absolute pressure. Neither is physical validation.
"""

import argparse
from dataclasses import asdict, replace
import csv
import hashlib
import io
import itertools
import json
import math
from pathlib import Path
import statistics
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.probe import analyze as legacy
from theory import model
from theory.protocol import DEFAULT_GATES, OBSERVATION_UNITS, OBSERVATION_VERSION, UNIT_SYSTEM
from theory.run_simulation import verification_protocols

OUTPUT = HERE / "results-t02"
LABEL = "SYNTHETIC_UNCALIBRATED"
TOLERANCE_PA = 1e-8  # numerical agreement only, NOT a measurement uncertainty
OBSERVATION_FIELDS = (
    "observation_version", "unit_system", "protocol_sha256", "time_s", "phase",
    "ambient_T_C", "ambient_RH_pct", "local_T_C", "local_RH_pct", "sensor_valid", "clip_event",
)


class OutsideResearchDomain(ValueError):
    pass


def sha256_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def fingerprint(value):
    return hashlib.sha256(json_text(value).encode("utf-8")).hexdigest()


def input_fingerprints():
    """Check accepted inputs and first-round artifacts before reading parameters."""
    main_manifest = json.loads((ROOT / "theory/results/manifest.json").read_text(encoding="utf-8"))
    paths = {"theory/results/manifest.json", "docs/MEASUREMENT-CONTRACT.md", "docs/verification/T01-review.md",
             "docs/team/reports/probe.md", "docs/MODEL-VALIDITY-DRAFT.md", "research/probe/sources.md",
             "research/probe/results/manifest.json", "docs/team/baseline-v1/snapshot.json"}
    for name in ("protocol.py", "model.py", "run_simulation.py"):
        rel = f"theory/{name}"
        if sha256_file(ROOT / rel) != main_manifest["source_sha256"][name]:
            raise RuntimeError(f"T01 source differs from accepted output manifest: {rel}")
        paths.add(rel)
    old = json.loads((legacy.OUTPUT / "manifest.json").read_text(encoding="utf-8"))
    for field, parent in (("source_sha256", HERE), ("outputs_sha256", legacy.OUTPUT)):
        for name, expected in old[field].items():
            path = parent / name
            if sha256_file(path) != expected:
                raise RuntimeError(f"First-round evidence is stale: {path.name}")
            paths.add(path.relative_to(ROOT).as_posix())
    frozen = legacy.baseline_fingerprints()
    paths.update(f"docs/team/baseline-v1/{name}" for name in frozen["files_checked"])
    return {name: sha256_file(ROOT / name) for name in sorted(paths)}


def source_fingerprints():
    return {name: sha256_file(HERE / name) for name in ("t02_replay.py", "test_t02_replay.py")}


def protocols():
    available = verification_protocols()
    return {name: available[name] for name in ("default", "three_second_long")}


def case_parameters():
    old = json.loads((legacy.OUTPUT / "manifest.json").read_text(encoding="utf-8"))["cases"]
    base = replace(model.Parameters(), noise_rh_pct=0.0)
    equilibrium = base.ambient_rh_pct / 100
    return {
        "wet_like_nominal": base,
        "wet_like_large_leak": replace(base, leak_ml_s=old["wet_like_large_leak"]["leak_ml_s"]),
        "equilibrium_surface": replace(base, surface_activity=equilibrium),
        "equilibrium_with_matched_wall": replace(base, surface_activity=equilibrium,
                                                 wall_source_ug_s=legacy.matched_wall_ug_s(research_parameters(base))),
        "same_AB_different_parameters": replace(base, **{key: old["same_AB_different_parameters"][key]
                                                        for key in ("exchange_ml_s", "leak_ml_s", "surface_activity")}),
        "sensor_30s": replace(base, sensor_tau_s=old["sensor_30s"]["sensor_tau_s"]),
        "sensor_120s_stress": replace(base, sensor_tau_s=old["sensor_120s_stress"]["sensor_tau_s"]),
        "small_K_stress": replace(base, exchange_ml_s=old["small_K_stress"]["exchange_ml_s"]),
    }


def research_parameters(p):
    return legacy.Parameters(**{key: getattr(p, key) for key in legacy.Parameters.__dataclass_fields__})


def research_cycle(p, protocol):
    """Constant-ambient, noiseless analytic solution evaluated from each phase start.

    Explicit restrictions prevent silently dropping biases or event physics.
    Stage states are advanced only at phase boundaries, not once per sample.
    """
    p.validate()
    protocol.validate()
    if any(getattr(p, name) != 0 for name in ("noise_rh_pct", "local_rh_bias_pct", "local_temp_bias_c", "ambient_step_rh_pct")):
        raise ValueError("Analytic comparison requires zero noise, biases and ambient step")
    if any(getattr(p, name) >= 0 for name in ("ambient_step_at_s", "clip_at_s", "invalid_at_s")):
        raise ValueError("Analytic comparison does not model enabled events")
    rp = research_parameters(p)
    sat = legacy.saturation_pa(p.temp_c)
    ambient = sat * p.ambient_rh_pct / 100
    source, rate = legacy.coefficients(rp, p.passive_extra_ml_s)
    starts = {"baseline": (source / rate, source / rate)}
    stages = {
        "baseline": (0.0, protocol.baseline_s, p.passive_extra_ml_s),
        "flush": (protocol.baseline_s, protocol.flush_s, p.flush_extra_ml_s),
        "recovery": (protocol.recovery_start_s, protocol.recovery_s, 0.0),
    }
    for previous, following in (("baseline", "flush"), ("flush", "recovery")):
        _, duration, extra = stages[previous]
        source, rate = legacy.coefficients(rp, extra)
        starts[following] = legacy.step(*starts[previous], source, rate, p.sensor_tau_s, duration)
    rows = []
    for i in range(round(protocol.total_s / protocol.sample_period_s) + 1):
        time = i * protocol.sample_period_s
        phase = protocol.phase_at(time)
        start, _, extra = stages[phase]
        elapsed = time - start
        source, rate = legacy.coefficients(rp, extra)
        y, z = starts[phase] if abs(elapsed) <= protocol.time_tolerance_s else legacy.step(
            *starts[phase], source, rate, p.sensor_tau_s, elapsed)
        if not 0 <= ambient + y < sat:
            raise OutsideResearchDomain(f"unsaturated domain crossed at {time:g} s; no clipping")
        rows.append({"time_s": time, "phase": phase, "synthetic_true_delta_Pa": y,
                     "synthetic_observed_delta_Pa": z})
    return rows


def legal_observations(rows):
    """Whitelist required observation fields; no physical parameters or truth."""
    return [{key: row[key] for key in OBSERVATION_FIELDS} for row in rows]


def observed_delta(row):
    return model.vapor_pa(row["local_T_C"], row["local_RH_pct"]) - model.vapor_pa(row["ambient_T_C"], row["ambient_RH_pct"])


def window_rows(rows, protocol):
    recovery = [row for row in rows if row["phase"] == "recovery"]
    tol = protocol.time_tolerance_s
    return ([r for r in recovery if r["time_s"] <= protocol.recovery_start_s + protocol.head_window_s + tol],
            [r for r in recovery if r["time_s"] >= protocol.total_s - protocol.tail_window_s - tol])


def research_features(rows, protocol):
    head, tail = window_rows(rows, protocol)
    tail_value = statistics.mean(r["synthetic_observed_delta_Pa"] for r in tail)
    return tail_value - statistics.mean(r["synthetic_observed_delta_Pa"] for r in head), tail_value


def compare(p, protocol):
    # Validate caller configuration first; only physical domain failures are caught.
    protocol.validate()
    p.validate()
    research_error = main_error = ""
    try:
        research = research_cycle(p, protocol)
    except OutsideResearchDomain as exc:
        research_error = str(exc)
        research = []
    try:
        raw = model.simulate(p, seed=model.DEFAULT_SEED, protocol=protocol)
    except ValueError as exc:
        if "Chamber outside unsaturated model domain" not in str(exc):
            raise
        main_error = str(exc)
        raw = []
    source, rate = legacy.coefficients(research_parameters(p))
    eq = source / rate
    ambient = legacy.saturation_pa(p.temp_c) * p.ambient_rh_pct / 100
    metrics = {
        "domain_status": "not_applicable" if research_error or main_error else "within_short_window_domain",
        "research_domain_error": research_error, "main_domain_error": main_error,
        "formal_equilibrium_delta_Pa": eq,
        "equilibrium_in_unsaturated_domain": 0 <= ambient + eq < legacy.saturation_pa(p.temp_c),
        "max_true_gap_Pa": None, "max_observed_gap_Pa": None, "max_feature_gap_Pa": None,
        "research_rise_Pa": None, "research_tail_Pa": None,
        "state": "NOT_APPLICABLE", "reasons": "physical_domain_not_applicable",
        "recovery_rise_Pa": None, "tail_delta_Pa": None, "collection_authorized": False,
        "protocol_sha256": protocol.fingerprint,
    }
    if research_error or main_error:
        return metrics, [], []  # no invented or clipped observations sent to assess
    assert [(r["time_s"], r["phase"]) for r in research] == [(r["time_s"], r["phase"]) for r in raw]
    observations = legal_observations(raw)
    assessment = model.assess(observations, DEFAULT_GATES, protocol=protocol)
    rise, tail = research_features(research, protocol)
    true_gap = max(abs(r["synthetic_true_delta_Pa"] - (m["synthetic_true_chamber_Pa"] - ambient)) for r, m in zip(research, raw))
    observed_gap = max(abs(r["synthetic_observed_delta_Pa"] - observed_delta(m)) for r, m in zip(research, observations))
    feature_gap = max(abs(rise - assessment["recovery_rise_Pa"]), abs(tail - assessment["tail_delta_Pa"]))
    if max(true_gap, observed_gap, feature_gap) > TOLERANCE_PA:
        raise AssertionError("Research/main numerical comparison failed")
    if assessment["collection_authorized"] is not False:
        raise AssertionError("Uncalibrated research cannot authorize collection")
    metrics.update(assessment, max_true_gap_Pa=true_gap, max_observed_gap_Pa=observed_gap,
                   max_feature_gap_Pa=feature_gap, research_rise_Pa=rise, research_tail_Pa=tail)
    traces = [{**r, "main_true_delta_Pa": m["synthetic_true_chamber_Pa"] - ambient,
               "main_observed_delta_Pa": observed_delta(o)} for r, m, o in zip(research, raw, observations)]
    return metrics, observations, traces


def csv_text(rows):
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return text.getvalue()


def protocol_mapping(protocol):
    times = [i * protocol.sample_period_s for i in range(round(protocol.total_s / protocol.sample_period_s) + 1)]
    rows = [{"time_s": t, "phase": protocol.phase_at(t)} for t in times]
    head, tail = window_rows(rows, protocol)
    return {"snapshot": protocol.snapshot(), "protocol_sha256": protocol.fingerprint,
            "baseline_interval_s": [0, protocol.baseline_s],
            "flush_interval_s": [protocol.baseline_s, protocol.recovery_start_s],
            "recovery_interval_s": [protocol.recovery_start_s, protocol.total_s],
            "head_times_s": [r["time_s"] for r in head], "tail_times_s": [r["time_s"] for r in tail],
            "phase_sample_counts": {phase: sum(r["phase"] == phase for r in rows) for phase in ("baseline", "flush", "recovery")},
            "boundary_rule": "transition sample in new phase; command acts on following interval; final sample retained"}


def build_artifacts():
    inputs = input_fingerprints()
    selected = protocols()
    cases = case_parameters()
    old = json.loads((legacy.OUTPUT / "manifest.json").read_text(encoding="utf-8"))
    grid = old["sweep_grid"]
    base = cases["wet_like_nominal"]
    stress = replace(base, volume_ml=2.0, leak_ml_s=0.005, wall_source_ug_s=0.5)
    case_rows, grid_rows, domain_rows, stored, traces, pairs = [], [], [], [], [], []
    for name, protocol in selected.items():
        observations_by_case = {}
        for case, p in cases.items():
            metrics, observations, trace = compare(p, protocol)
            row = {"protocol": name, "case": case, "data_status": LABEL,
                   "parameters_sha256": fingerprint(asdict(p)), **asdict(p), **metrics}
            case_rows.append(row)
            stored.append({"protocol": name, "case": case, "data_status": LABEL, "observations": observations})
            traces.extend({"protocol": name, "case": case, "data_status": LABEL, **r} for r in trace)
            observations_by_case[case] = observations
        for other in ("equilibrium_with_matched_wall", "same_AB_different_parameters"):
            gap = max(abs(observed_delta(a) - observed_delta(b)) for a, b in zip(
                observations_by_case["wet_like_nominal"], observations_by_case[other]))
            if gap > TOLERANCE_PA:
                raise AssertionError("Counterexample no longer matches")
            pairs.append({"protocol": name, "reference": "wet_like_nominal", "other": other,
                          "max_observed_gap_Pa": gap, "data_status": LABEL})
        for index, values in enumerate(itertools.product(*grid.values())):
            p = replace(base, **dict(zip(grid, values)))
            metrics, _, _ = compare(p, protocol)
            grid_rows.append({"protocol": name, "point": index, "data_status": LABEL,
                              "parameters_sha256": fingerprint(asdict(p)), **asdict(p), **metrics})
        metrics, _, _ = compare(stress, protocol)
        domain_rows.append({"protocol": name, "case": "deliberate_wall_domain_crossing", "data_status": LABEL,
                            "parameters_sha256": fingerprint(asdict(stress)), **asdict(stress), **metrics})
    default = selected["default"]
    noisy = model.Parameters()  # actual accepted defaults, including noise, with its source seed
    noisy_observations = legal_observations(model.simulate(noisy, seed=model.DEFAULT_SEED, protocol=default))
    noisy_result = model.assess(noisy_observations, DEFAULT_GATES, protocol=default)
    if noisy_result["collection_authorized"] is not False:
        raise AssertionError("Noisy reference cannot authorize collection")
    payloads = {
        "case-summary.csv": csv_text(case_rows), "envelope.csv": csv_text(grid_rows),
        "domain-summary.csv": csv_text(domain_rows), "research-traces.csv": csv_text(traces),
        "observations.jsonl": "".join(json.dumps(r, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n" for r in stored),
        "pair-comparison.json": json_text(pairs),
        "protocol-mapping.json": json_text({name: protocol_mapping(p) for name, p in selected.items()}),
        "noise-reference.json": json_text({"data_status": LABEL, "parameters": asdict(noisy),
                                           "parameters_sha256": fingerprint(asdict(noisy)), "seed": model.DEFAULT_SEED,
                                           "assessment": noisy_result, "observations": noisy_observations}),
    }
    all_valid = [r for r in case_rows + grid_rows if r["domain_status"] == "within_short_window_domain"]
    manifest = {
        "schema_version": 1, "date": "2026-10-24", "task": "T02", "data_status": LABEL,
        "input_sha256": inputs, "source_sha256": source_fingerprints(),
        "protocols": {name: {"snapshot": p.snapshot(), "sha256": p.fingerprint} for name, p in selected.items()},
        "protocol_source": "theory.run_simulation.verification_protocols; no local timing defaults",
        "observation_version": OBSERVATION_VERSION, "unit_system": UNIT_SYSTEM,
        "observation_fields": list(OBSERVATION_FIELDS), "observation_units": OBSERVATION_UNITS,
        "gates_illustrative": asdict(DEFAULT_GATES), "gates_sha256": fingerprint(asdict(DEFAULT_GATES)),
        "main_simulator_seed": model.DEFAULT_SEED, "research_random_seed": None,
        "comparison_noise_rh_pct": 0.0, "noise_reference_separate": True,
        "case_parameters": {name: {"values": asdict(p), "sha256": fingerprint(asdict(p))} for name, p in cases.items()},
        "case_source": "accepted Parameters defaults with noise=0; explicit first-round counterexample overrides",
        "envelope_grid": grid, "envelope_source": "unchanged first-round results/manifest.json sweep_grid; no structure assignment",
        "domain_stress_parameters": asdict(stress), "domain_stress_source": "deliberate new uncalibrated wall-source stress; no material evidence",
        "numerical_tolerance_Pa": TOLERANCE_PA,
        "counts": {"representative_cycles": len(case_rows), "envelope_points": len(grid_rows),
                   "domain_stresses": len(domain_rows), "saved_observation_cycles": len(stored),
                   "saved_trace_rows": len(traces), "saved_noisy_reference_cycles": 1},
        "maxima_Pa": {field: max(r[field] for r in all_valid) for field in ("max_true_gap_Pa", "max_observed_gap_Pa", "max_feature_gap_Pa")},
        "envelope_outside_short_window": sum(r["domain_status"] != "within_short_window_domain" for r in grid_rows),
        "envelope_formal_equilibrium_outside": sum(not r["equilibrium_in_unsaturated_domain"] for r in grid_rows),
        "collection_authorized": False,
        "limits": ["no hardware calibration", "no open-probe mixed volume claim", "no demonstrated backshield leakage reduction",
                   "fixed ambient and surface potential; no thermal or two-chamber extension", "no garment mass or drying-time prediction"],
        "output_sha256": {name: hashlib.sha256(content.encode("utf-8")).hexdigest() for name, content in payloads.items()},
    }
    if inputs != input_fingerprints():
        raise RuntimeError("Read-only inputs changed during computation")
    return payloads, manifest


def verify(output=OUTPUT):
    saved = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if saved["source_sha256"] != source_fingerprints() or saved["input_sha256"] != input_fingerprints():
        raise RuntimeError("Stale T02 source/input fingerprints")
    for name, digest in saved["output_sha256"].items():
        if sha256_file(output / name) != digest:
            raise RuntimeError(f"Output fingerprint mismatch: {name}")
    # Replay stored legal observations independently of simulator truth/parameters.
    summaries = list(csv.DictReader(io.StringIO((output / "case-summary.csv").read_text(encoding="utf-8"))))
    index = {(r["protocol"], r["case"]): r for r in summaries}
    selected = protocols()
    for line in (output / "observations.jsonl").read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if any(set(row) != set(OBSERVATION_FIELDS) for row in record["observations"]):
            raise AssertionError("Saved classifier input contains non-contract fields")
        assessed = model.assess(record["observations"], DEFAULT_GATES, protocol=selected[record["protocol"]])
        row = index[(record["protocol"], record["case"])]
        if assessed["state"] != row["state"] or assessed["reasons"] != row["reasons"] or assessed["collection_authorized"] is not False:
            raise AssertionError("Saved observation replay differs")
        for key in ("recovery_rise_Pa", "tail_delta_Pa"):
            if abs(assessed[key] - float(row[key])) > TOLERANCE_PA:
                raise AssertionError("Saved feature replay differs")
    # Recompute every envelope/domain result and every serialized payload, read-only.
    expected, manifest = build_artifacts()
    if manifest != saved:
        raise AssertionError("Manifest cannot be reproduced")
    for name, content in expected.items():
        if (output / name).read_bytes() != content.encode("utf-8"):
            raise AssertionError(f"Artifact cannot be reproduced: {name}")
    return saved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="read-only fingerprints and complete semantic replay")
    args = parser.parse_args()
    if args.verify:
        manifest = verify()
        print("Verified T02 input/source/output fingerprints, saved observations and all deterministic artifacts")
    else:
        payloads, manifest = build_artifacts()
        OUTPUT.mkdir(parents=True, exist_ok=True)
        for name, content in payloads.items():
            (OUTPUT / name).write_bytes(content.encode("utf-8"))
        (OUTPUT / "manifest.json").write_bytes(json_text(manifest).encode("utf-8"))
        print("Generated T02 outputs only; first-round outputs unchanged")
    print(json_text({"status": LABEL, "counts": manifest["counts"], "maxima_Pa": manifest["maxima_Pa"],
                     "envelope_outside_short_window": manifest["envelope_outside_short_window"],
                     "envelope_formal_equilibrium_outside": manifest["envelope_formal_equilibrium_outside"],
                     "collection_authorized": manifest["collection_authorized"]}))


if __name__ == "__main__":
    main()

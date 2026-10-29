"""Coordinator verification of T10 M3, a synthetic isothermal observation model."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
P = "research/garment_prediction/observation_m3/"
RECEIPT = ROOT / "docs/verification/T10-M3-tests.json"


def read(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8-sig"))


def sha(path):
    full = (ROOT / path).resolve()
    require(full.is_relative_to(ROOT), "outside workspace: " + path)
    return hashlib.sha256(full.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check-receipt", action="store_true")
    mode.add_argument("--write-receipt", action="store_true")
    args = parser.parse_args()
    if args.check_receipt:
        receipt = read("docs/verification/T10-M3-tests.json")
        require(receipt["passed"] is True and receipt["collection_authorized"] is False, "unpassed receipt")
        require(bool(receipt["checks"]) and all(c["passed"] for c in receipt["checks"]), "missing checks")
        require(bool(receipt["reviewed_sha256"]), "missing source hashes")
        for path, expected in receipt["reviewed_sha256"].items():
            require(sha(path) == expected, "stale " + path)
        print("M3 receipt current and passed")
        return 0

    sys.path.insert(0, str(ROOT / P))
    from physics import simulate, rates
    from consumer import evaluate
    config = read(P + "config.json")
    manifest = read(P + "results/manifest.json")
    c2 = read("research/garment_prediction/observation_m2/config.json")
    watched = set(manifest["inputs_sha256"]) | set(manifest["artifacts_sha256"]) | {
        P + "results/manifest.json", "docs/GARMENT-OBSERVATION-PHYSICS.md",
        "docs/team/reports/algorithm-t10-m3.md", "tools/check_garment_observation.py"}
    before = {p: sha(p) for p in sorted(watched)}
    checks = []

    def check(name, action):
        try:
            checks.append(dict(name=name, passed=True, detail=action()))
        except Exception as exc:
            checks.append(dict(name=name, passed=False, error=f"{type(exc).__name__}: {exc}"))

    def saturated(t):
        # Same declared empirical saturation law, independently evaluated here.
        # This checks implementation, not the empirical law against real measurements.
        return 610.94 * math.exp(17.625 * t / (t + 243.04)) / (461.5 * (t + 273.15))

    def worker_suite():
        cmd = [sys.executable, "-B", "-X", "utf8", P + "test_model.py"]
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, encoding="utf-8", timeout=120)
        output = proc.stdout + proc.stderr
        require(proc.returncode == 0, output)
        count = re.search(r"Ran (\d+) tests?", output)
        require(count is not None, "test count missing")
        return dict(command=cmd, test_count=int(count.group(1)), output=output)

    def integrity():
        require(manifest["label"] == "SYNTHETIC_UNCALIBRATED" and manifest["seed"] is None, "provenance")
        required = set()
        counts = {}
        for path, key in (("docs/team/baseline-t10-m3/snapshot.json", "inputs_sha256"),
                          ("docs/verification/T10-M1-tests.json", "reviewed_sha256"),
                          ("docs/verification/T10-M2a-tests.json", "reviewed_sha256"),
                          ("docs/verification/T10-M2b-tests.json", "reviewed_sha256")):
            hashes = read(path)[key]
            for name, expected in hashes.items():
                require(sha(name) == expected, "accepted source changed " + name)
            required.update(hashes)
            required.add(path)
            counts[path] = len(hashes)
        required.add("docs/team/assignments/algorithm-t10-m3.md")
        required.update(P + x for x in ("physics.py", "consumer.py", "test_model.py", "config.json", "replay.py"))
        require(set(manifest["inputs_sha256"]) == required, "incomplete manifest inputs")
        require(set(manifest["artifacts_sha256"]) == {P + "results/simulation.json"}, "unexpected artifact set")
        for key in ("inputs_sha256", "artifacts_sha256"):
            for path, expected in manifest[key].items():
                require(sha(path) == expected, "manifest mismatch " + path)
        return dict(inputs=len(required), artifacts=1, protected=counts)

    def flux_partition():
        p = dict(config["parameters"], sensor_bias_rh=0., wall_source_kg_s=2e-10)
        sat = saturated(p["temperature_c"])
        rows = []
        for x, rh in ((.6, .2), (.02, .8), (0., .5)):
            w = p["dry_kg"] * x
            y = (w, rh * sat, .5 * sat, 0.)
            dw, dr, dz, dl = rates(p, y)
            aw = 1 - math.exp(-x / p["xb"])
            mo = p["area_open_m2"] / p["r_open_s_m"] * sat * (aw - p["ambient_rh"])
            mc = p["area_cover_m2"] / p["r_cover_s_m"] * sat * (aw - rh)
            f = p["flow_m3_s"] * sat * (p["ambient_rh"] - rh)
            external = -mo + f + p["wall_source_kg_s"]
            for actual, expected in ((dw, -mo-mc), (p["volume_m3"]*dr, mc+f+p["wall_source_kg_s"]),
                                     (dw+p["volume_m3"]*dr, external), (dl, external)):
                require(abs(actual - expected) < 1e-18, "water partition mismatch")
            if x == 0:
                require(dw > 0, "empty cloth cannot absorb")
            rows.append(dict(x=x, rh=rh, open_kg_s=mo, cover_kg_s=mc, external_kg_s=external))
        return rows

    def analytic_response():
        rows = []
        for lam in (0., .004, .25, .5):
            p = dict(config["parameters"], area_open_m2=0., area_cover_m2=0., wall_source_kg_s=0.,
                     sensor_bias_rh=.01, sensor_tau_s=4.)
            p["flow_m3_s"] = lam * p["volume_m3"]
            mu, duration, initial_rh, initial_sensor, ambient = .25, 30., .2, .7, p["ambient_rh"]
            r = simulate(p, .6, initial_rh, initial_sensor, duration, .05)
            e1, e2 = math.exp(-lam*duration), math.exp(-mu*duration)
            extra = mu * (initial_rh-ambient) * (duration*e2 if lam == mu else (e1-e2)/(mu-lam))
            exact = ambient + (initial_sensor-ambient)*e2 + extra + p["sensor_bias_rh"]
            actual = r["samples"][-1]["local_rh"]
            exact_air = saturated(p["temperature_c"]) * (ambient+(initial_rh-ambient)*e1)
            require(abs(actual-exact) < 1e-10, "sensor convolution analytic mismatch")
            require(abs(r["producer_only"]["air_density_kg_m3"]-exact_air) < 1e-12, "air analytic mismatch")
            require(r["producer_only"]["water_kg"] == p["dry_kg"]*.6, "zero cover modifies water")
            rows.append(dict(lambda_s=lam, exact_rh=exact, computed_rh=actual, error=actual-exact))
        return rows

    def closed_equilibrium():
        p = dict(config["parameters"], dry_kg=1e-5, volume_m3=.001, area_open_m2=0.,
                 area_cover_m2=.0001, r_cover_s_m=100., flow_m3_s=0., wall_source_kg_s=0., sensor_bias_rh=0.)
        x0, rh0, t, step = .3, .2, 6000., .25
        sat = saturated(p["temperature_c"])
        total = p["dry_kg"]*x0 + p["volume_m3"]*sat*rh0
        lo, hi = 0., total
        for _ in range(100):
            mid = (lo+hi)/2
            v = mid + p["volume_m3"]*sat*(1-math.exp(-mid/(p["dry_kg"]*p["xb"])))
            if v > total:
                hi = mid
            else:
                lo = mid
        equilibrium = (lo+hi)/2
        r = simulate(p, x0, rh0, rh0, t, step)["producer_only"]
        computed_total = r["water_kg"] + p["volume_m3"]*r["air_density_kg_m3"]
        require(abs(computed_total-total) < 1e-15, "closed total water mismatch")
        require(abs(r["water_kg"]-equilibrium) < 1e-14, "closed equilibrium mismatch")
        return dict(parameters=p, duration_s=t, step_s=step, total_water_kg=total,
                    total_error_kg=computed_total-total, equilibrium_kg=equilibrium,
                    computed_kg=r["water_kg"], error_kg=r["water_kg"]-equilibrium)

    def open_balance_convergence():
        p = config["parameters"]
        rows = []
        for dt in (.4, .2, .1):
            r = simulate(p, config["x0"], config["rh0"], config["sensor_rh0"], 60., dt)
            d = r["producer_only"]
            require(abs(d["total_water_change_kg"]-d["boundary_integral_kg"]) < 1e-14, "RK mass ledger mismatch")
            rows.append(dict(dt=dt, trapezoid_residual_kg=d["balance_residual_kg"], water_kg=d["water_kg"], rh=r["samples"][-1]["local_rh"]))
        for a, b in zip(rows, rows[1:]):
            require(abs(b["trapezoid_residual_kg"]) < abs(a["trapezoid_residual_kg"])*.4, "boundary quadrature not converging")
        ref = simulate(p, config["x0"], config["rh0"], config["sensor_rh0"], 60., .01, "midpoint")
        require(abs(rows[-1]["rh"]-ref["samples"][-1]["local_rh"]) < 1e-7, "midpoint comparison")
        return dict(rows=rows, midpoint_rh=ref["samples"][-1]["local_rh"], note="Independent quadrature and same-equation numerical cross-check, not empirical accuracy.")

    def rejected_inputs():
        rejected = []
        cases = [("nan_temperature", {"temperature_c": float("nan")}),
                 ("bool_flow", {"flow_m3_s": True}), ("negative_volume", {"volume_m3": -1.}),
                 ("zero_tau", {"sensor_tau_s": 0.}), ("invalid_temperature", {"temperature_c": 51.}),
                 ("unmodelled_condensation", {"wall_source_kg_s": 1e-3}),
                 ("hidden_parameter", {"real_water_kg": .06})]
        for label, change in cases:
            try:
                simulate(dict(config["parameters"], **change), .6, .5, .5, 60., .1)
            except ValueError:
                rejected.append(label)
            else:
                raise AssertionError("accepted " + label)
        return rejected

    def observable_gate():
        a = deepcopy(c2["packet"])
        a["samples"]["local_temp"]["value"] = 25.
        b = deepcopy(a)
        b["now_s"] = 610.
        for sample in b["samples"].values():
            sample["measured_at_s"] = 600.
            sample["received_at_s"] = 602.
        for key in ("contact", "charging_heat"):
            b[key]["coverage_end_s"] = 605.
            b[key]["issued_at_s"] = 605.
        h = dict(status="externally_characterized", reference_time_s=580., issued_at_s=595.,
                 initial_state_rh=.5, uncertainty_abs=.02, source="synthetic_external")
        def result(packets, history=h):
            r = evaluate(packets, c2["policy"], .5, .05, history, .5, .5)
            require(r["collection_authorized"] is False, "gate authorized collection")
            require(r["scope"] == "necessary_observable_conditions_only", "gate overclaims physical validity")
            return r
        require(result([a,b])["applicable"], "explicit synthetic necessary conditions rejected")
        variants = {}
        duplicate = deepcopy(b)
        duplicate["samples"] = deepcopy(a["samples"])
        variants["duplicate_measurements"] = [a,duplicate]
        offset = [deepcopy(a),deepcopy(b)]
        for q in offset:
            q["samples"]["local_temp"]["value"] = 40.
        variants["constant_temperature_offset"] = offset
        jump = [deepcopy(a),deepcopy(b)]
        jump[-1]["samples"]["local_temp"]["value"] = 40.
        variants["local_temperature_jump"] = jump
        for label, key in (("missing_local", "local_rh"), ("missing_environment", "ambient_rh")):
            v = deepcopy(b); v["samples"][key] = None
            variants[label] = [a,v]
        for key in ("contact", "charging_heat"):
            v = deepcopy(b); v[key]["coverage_start_s"] = 590.
            variants[key+"_uncovered_history"] = [a,v]
        v = deepcopy(b); v["samples"]["ambient_rh"]["value"] = .8
        variants["changed_environment"] = [a,v]
        v = deepcopy(b); v["installation_id"] = "different"
        variants["changed_installation"] = [a,v]
        outcome = {}
        for label, v in variants.items():
            r = result(v)
            require(not r["applicable"] and r["reasons"], "incorrect gate acceptance " + label)
            outcome[label] = r["reasons"]
        unknown = {k: None for k in h}; unknown["status"] = "unknown"
        require(not result([a,b], unknown)["applicable"], "unknown history accepted")
        for label, v in (("large_initial_error", dict(h, uncertainty_abs=.5)),
                         ("reference_after_sample", dict(h, reference_time_s=591.))):
            require(not result([a,b],v)["applicable"], "unsupported initial state " + label)
        for label, v, hist in (("hidden_truth", [a,dict(b,water_kg=.01)], h),
                               ("future_history", [a,b], dict(h,issued_at_s=611.))):
            try:
                result(v,hist)
            except ValueError:
                outcome[label] = "ValueError"
            else:
                raise AssertionError("accepted " + label)
        return dict(rejections=outcome, initial_history="Externally declared synthetic initial state, not automatic detection.")

    def artifacts_and_ambiguity():
        prior = {p: sha(p) for p in manifest["artifacts_sha256"]}
        prior[P+"results/manifest.json"] = sha(P+"results/manifest.json")
        cmd = [sys.executable, "-B", "-X", "utf8", P + "replay.py"]
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, encoding="utf-8", timeout=120)
        require(proc.returncode == 0, proc.stdout + proc.stderr)
        require(all(sha(p)==expected for p,expected in prior.items()), "replay changed artifacts")
        r = read(P+"results/simulation.json")
        require(r["label"] == "SYNTHETIC_UNCALIBRATED" and r["collection_authorized"] is False, "artifact claim")
        a,b = r["baseline"],r["alternative"]
        diff = max(abs(x["local_rh"]-y["local_rh"]) for x,y in zip(a["samples"],b["samples"]))
        require(0 < diff < .001, "missing bounded ambiguity witness")
        water_diff = abs(a["producer_only"]["initial_water_kg"]-b["producer_only"]["initial_water_kg"])
        require(water_diff > .02, "same initial water")
        require(not r["consumer_gate"]["applicable"], "example lacks real evidence but proceeds")
        p=config["parameters"]; aw=1-math.exp(-config["x0"]/p["xb"])
        transfer=p["area_open_m2"]/p["r_open_s_m"]+p["area_cover_m2"]/p["r_cover_s_m"]
        for v in r["hypothetical_surface_temperature_sensitivity"]:
            expected=transfer*(aw*saturated(v["surface_c"])-p["ambient_rh"]*saturated(p["temperature_c"]))
            require(abs(v["initial_evap_kg_s"]-expected)<1e-18, "thermal sensitivity mismatch")
        return dict(command=cmd, max_rh_difference_fraction=diff, initial_water_difference_kg=water_diff,
                    latent_net_energy_proxy_j=r["latent_energy_proxy_j"], note="Net latent-energy scale only; no passive thermal closure or calibrated inversion.")

    for name, action in (("worker_suite", worker_suite), ("source_integrity", integrity),
                         ("open_covered_flux_partition", flux_partition), ("analytic_air_and_sensor", analytic_response),
                         ("closed_mass_and_equilibrium", closed_equilibrium), ("open_balance_and_convergence", open_balance_convergence),
                         ("out_of_domain_rejection", rejected_inputs), ("observable_gate", observable_gate),
                         ("replay_and_nonuniqueness", artifacts_and_ambiguity)):
        check(name, action)
    require(all(sha(p)==expected for p,expected in before.items()), "sources changed during coordinator checks")
    passed = all(c["passed"] for c in checks)
    receipt = dict(schema_version=1, task="T10", milestone="M3", label="SYNTHETIC_UNCALIBRATED",
                   collection_authorized=False, verified_at=datetime.now(timezone.utc).isoformat(),
                   python=sys.version, passed=passed, checks=checks, reviewed_sha256=before,
                   limits="Fixed-temperature single-region synthetic mapping only. No hardware calibration, full energy closure, ensemble update, real probability or collection authorization.")
    if args.write_receipt:
        RECEIPT.write_bytes((json.dumps(receipt, ensure_ascii=False, indent=2)+"\n").encode("utf-8"))
    print(json.dumps(dict(passed=passed, checks=[{k:c[k] for k in ("name","passed","error") if k in c} for c in checks]), ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

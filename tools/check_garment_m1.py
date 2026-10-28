"""Coordinator numerical review; never calibrates a garment or authorizes collection.

Run from any directory. --write-receipt replaces only this review's own receipt.
Analytic expectations below do not call the model's RHS/flux/ledger functions.
"""
import argparse
from dataclasses import fields, replace
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path, PurePosixPath
import platform
import re
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
P = "research/garment_prediction/"
RECEIPT = ROOT / "docs/verification/T10-M1-tests.json"


def read(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def close(actual, expected, tolerance, name):
    error = abs(actual - expected)
    require(math.isfinite(actual) and error <= tolerance, f"{name}: {error} > {tolerance}")
    return {"actual": actual, "expected": expected, "abs_error": error,
            "abs_tolerance": tolerance}


def inspect_receipt():
    """Read-only freshness check for the project recovery entry point."""
    if not RECEIPT.is_file():
        return ["T10 M1 缺少协调数值收据"], 0
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    issues = []
    checks = receipt.get("checks", [])
    hashes = receipt.get("reviewed_sha256", {})
    required = {P + f for f in ("model.py", "test_model.py", "config.json", "replay.py",
                               "parameter_registry.json", "results/manifest.json", "results/example.json")}
    required |= {"tools/check_garment_m1.py", "docs/GARMENT-PREDICTION.md",
                 "docs/team/reports/algorithm-t10.md", "docs/team/baseline-t10-m1/snapshot.json"}
    if (receipt.get("passed") is not True or receipt.get("task") != "T10"
            or receipt.get("collection_authorized") is not False
            or receipt.get("label") != "SYNTHETIC_UNCALIBRATED"
            or not checks or any(c.get("passed") is not True for c in checks)
            or not required <= set(hashes)):
        issues.append("T10 M1 收据未证明完整数值检查通过")
    for p, expected in hashes.items():
        path = (ROOT / p).resolve()
        if (not path.is_relative_to(ROOT) or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            issues.append("T10 M1 验收来源已变化：" + p)
    return issues, len(checks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-receipt", action="store_true")
    parser.add_argument("--check-receipt", action="store_true",
                        help="read-only freshness check; does not run numerical tests")
    args = parser.parse_args()
    if args.check_receipt:
        if args.write_receipt:
            parser.error("--check-receipt cannot be combined with --write-receipt")
        issues, count = inspect_receipt()
        for issue in issues:
            print(issue)
        print(f"M1 receipt current and passed: {not issues}; groups: {count}")
        return 1 if issues else 0
    manifest = read(P + "results/manifest.json")
    watched = sorted(set(manifest["inputs_sha256"]) | set(manifest["artifacts_sha256"]) | {
        P + "results/manifest.json", "tools/check_garment_m1.py",
        "docs/GARMENT-PREDICTION.md", "docs/team/reports/algorithm-t10.md"})
    before = {p: sha(p) for p in watched}
    checks = []

    def check(name, action):
        try:
            detail = action()
            checks.append({"name": name, "passed": True, "detail": detail})
        except Exception as exc:
            checks.append({"name": name, "passed": False,
                           "error": f"{type(exc).__name__}: {exc}"})

    spec = importlib.util.spec_from_file_location("garment_m1_review", ROOT / P / "model.py")
    model = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = model
    spec.loader.exec_module(model)
    config = read(P + "config.json")
    zones = [model.Zone(**config[name]) for name in ("ordinary", "slow")]
    drive = model.Drive(**config["drive"])
    states = [model.state(z, config["initial_x_kg_kg"], config["initial_temperature_c"])
              for z in zones]

    def worker_suite():
        command = [sys.executable, "-B", "-X", "utf8", P + "test_model.py"]
        proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", timeout=120)
        log = proc.stdout + proc.stderr
        require(proc.returncode == 0, log)
        match = re.search(r"Ran (\d+) tests?", log)
        require(match is not None, "missing unittest count")
        return {"command": command, "exit_code": proc.returncode,
                "test_count": int(match.group(1)), "output": log}

    def integrity():
        require(manifest["label"] == "SYNTHETIC_UNCALIBRATED" and manifest["seed"] is None,
                "manifest provenance")
        for kind in ("inputs_sha256", "artifacts_sha256"):
            for p, expected in manifest[kind].items():
                pp = PurePosixPath(p)
                require("\\" not in p and not pp.is_absolute() and ".." not in pp.parts,
                        f"nonportable path {p}")
                require(sha(p) == expected, f"hash mismatch {p}")
        snapshot_path = "docs/team/baseline-t10-m1/snapshot.json"
        frozen = read(snapshot_path)["files"]
        required = {snapshot_path} | {v["snapshot"] for v in frozen.values()} | {
            P + f for f in ("model.py", "test_model.py", "config.json",
                           "parameter_registry.json", "replay.py")}
        require(set(manifest["inputs_sha256"]) == required, "incomplete source manifest")
        require(set(manifest["artifacts_sha256"]) == {P + "results/example.json"},
                "unexpected output set")
        for entry in frozen.values():
            require(sha(entry["snapshot"]) == entry["sha256"], "frozen input changed")
        for p in (P + "results/example.json", P + "results/manifest.json"):
            raw = (ROOT / p).read_bytes()
            require(b"\r" not in raw and raw.endswith(b"\n"), f"not LF {p}")
        return {"inputs": len(required), "artifacts": 1, "frozen_inputs": len(frozen)}

    def registry():
        reg = read(P + "parameter_registry.json")
        require(reg["label"] == "SYNTHETIC_UNCALIBRATED", "registry provenance")
        required = {f.name for cls in (model.Zone, model.Drive) for f in fields(cls)}
        required |= set(config) - {"ordinary", "slow"}
        require(required <= set(reg["fields"]), "unregistered input")
        for p, entry in reg["fields"].items():
            require(all(entry.get(k) for k in ("unit", "software_domain", "source_or_assumption")), p)
        for p, entry in reg["constants"].items():
            require(all(entry.get(k) for k in ("unit", "value", "source_or_assumption")), p)
        for name in ("SIGMA", "CW", "CD", "LV", "CV"):
            require(float(reg["constants"][name]["value"]) == getattr(model, name), name)
        return {"fields": len(reg["fields"]), "constants": len(reg["constants"]),
                "limit": "Presence/units checked; provenance and physical validity need manual review."}

    def replay():
        actual = {"config": config,
                  "two_zone": model.run_two(zones, [drive, drive], states,
                                            config["duration_s"], config["step_s"]),
                  "ordinary_convergence": {str(h): model.run(zones[0], drive, states[0],
                                                            config["duration_s"], h)
                                           for h in (20, 10, 2, 1)},
                  "ordinary_midpoint_reference": model.run(zones[0], drive, states[0],
                                                            config["duration_s"], .25, "midpoint")}
        expected = read(P + "results/example.json")
        # On this interpreter, deterministic recomputation should be exactly equal.
        require(actual == expected, "example differs from current model/config")
        outputs = actual["two_zone"] + list(actual["ordinary_convergence"].values())
        outputs.append(actual["ordinary_midpoint_reference"])
        require(all(r["collection_authorized"] is False and r["label"] == "SYNTHETIC_UNCALIBRATED"
                    for r in outputs), "research authorization/label changed")
        coarse, fine = [actual["ordinary_convergence"][str(h)] for h in (20, 10)]
        ratios = {key: abs(coarse[key] / fine[key])
                  for key in ("water_residual_kg", "energy_residual_j")}
        require(all(3.5 < r < 4.5 for r in ratios.values()), "ledger convergence changed")
        return {"match": "exact parsed values on current interpreter", "outputs": len(outputs),
                "coarse_to_fine_ledger_error_ratio": ratios, "seed": None}

    def analytical(solar, rain, convection=False):
        # Isolate terms. All geometry and coefficients are synthetic, not garment data.
        z = replace(zones[0], evap_m2=0., heat_m2=.45 if convection else 0.,
                    rad_m2=0., drain_per_s=0.)
        d = replace(drive, wind_m_s=0., solar_w_m2=solar, rain_kg_s=rain, rain_c=20.)
        t0, duration = (15., 1200.) if convection else (25., 600.)
        w0 = .3 * .6
        u0 = (.3 * 1400. + w0 * 4180.) * t0
        result = model.run(z, d, (w0, u0), duration, 2.)
        water = w0 + rain * duration
        if convection:
            capacity = .3 * 1400. + w0 * 4180.
            temperature = 25. + (t0 - 25.) * math.exp(-4. * .45 * duration / capacity)
            energy = capacity * temperature
        else:
            energy = u0 + (.6 * solar * .18 + rain * 4180. * 20.) * duration
            temperature = energy / (.3 * 1400. + water * 4180.)
        return {"parameters": {"solar_w_m2": solar, "rain_kg_s": rain,
                               "convection_only": convection, "duration_s": duration, "step_s": 2.},
                "water_kg": close(result["water_kg"], water, 1e-12, "water kg"),
                "energy_j": close(result["energy_j"], energy, 1e-5, "energy J"),
                "temperature_c": close(result["temperature_c"], temperature, 1e-8, "temperature C")}

    def error_contract():
        calls = [lambda value=v: model.advance(zones[0], drive, states[0], value)
                 for v in (0, -1, float("nan"), float("inf"), True)]
        calls += [lambda duration=t: model.run(zones[0], drive, states[0], duration, 1, "typo")
                  for t in (0, 1)]
        calls.append(lambda: model.advance(zones[0], drive, (-.1, states[0][1]), 1))
        for call in calls:
            try:
                call()
            except ValueError as exc:
                require(not isinstance(exc, model.IntegrationStepError), "invalid input misclassified")
            else:
                raise AssertionError("invalid input accepted")
        try:
            model.advance(zones[0], drive, model.state(zones[0], .1, 25), 1e6)
        except model.IntegrationStepError as exc:
            require(isinstance(exc.__cause__, ValueError), "lost stage error cause")
        else:
            raise AssertionError("large step did not reject")
        return {"invalid_input_cases": len(calls), "stage_failure_cases": 1}

    check("worker_unittest_suite", worker_suite)
    check("manifest_and_frozen_inputs", integrity)
    check("parameter_registry", registry)
    check("recompute_example_without_writing_artifacts", replay)
    check("analytic_solar_only", lambda: analytical(200., 0.))
    check("analytic_rain_only", lambda: analytical(0., 1e-5))
    check("analytic_solar_and_rain", lambda: analytical(200., 1e-5))
    check("analytic_convection_only", lambda: analytical(0., 0., True))
    check("input_and_stage_error_separation", error_contract)
    check("inputs_unchanged_during_review", lambda: require(
        before == {p: sha(p) for p in watched}, "inputs changed during review"))
    passed = all(c["passed"] for c in checks)
    receipt = {"schema_version": 1, "task": "T10", "milestone": "M1 numerical review",
               "created_at_utc": datetime.now(timezone.utc).isoformat(), "passed": passed,
               "label": "SYNTHETIC_UNCALIBRATED", "collection_authorized": False, "seed": None,
               "command": "python -B -X utf8 tools/check_garment_m1.py --write-receipt",
               "runtime": {"python": sys.version, "platform": platform.platform()},
               "reviewed_sha256": before, "checks": checks,
               "limits": ["Numerical tests of the declared synthetic closure, not calibration.",
                          "Analytic tolerances describe numerical error only.",
                          "Manual physical derivation/scope review is in T10-M1-review.md.",
                          "No observation adapter, whole-garment ETA or real collection authorization.",
                          "Same-platform replay checked; no Linux run or cross-platform byte guarantee."]}
    if args.write_receipt:
        RECEIPT.write_bytes((json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    for c in checks:
        print(("PASS " if c["passed"] else "FAIL ") + c["name"])
        if not c["passed"]:
            print(c["error"])
    print(f"{sum(c['passed'] for c in checks)}/{len(checks)} review groups passed")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

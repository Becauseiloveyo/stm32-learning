"""Coordinator M2a contract/counterexample checks; no calibrated drying inference."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
P = "research/garment_prediction/observation_m2/"
RECEIPT = ROOT / "docs/verification/T10-M2a-tests.json"


def read(p):
    return json.loads((ROOT / p).read_text(encoding="utf-8-sig"))


def sha(p):
    return hashlib.sha256((ROOT / p).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-receipt", action="store_true")
    parser.add_argument("--check-receipt", action="store_true")
    args = parser.parse_args()
    if args.check_receipt:
        if args.write_receipt:
            parser.error("read and write receipt modes are exclusive")
        r = json.loads(RECEIPT.read_text(encoding="utf-8"))
        require(r["passed"] is True and r["collection_authorized"] is False,
                "receipt did not pass")
        require(bool(r["checks"]) and all(c["passed"] for c in r["checks"]), "missing checks")
        require(bool(r["reviewed_sha256"]), "empty hashes")
        for p, value in r["reviewed_sha256"].items():
            require((ROOT / p).resolve().is_relative_to(ROOT) and sha(p) == value, "stale " + p)
        print("M2a receipt current and passed")
        return 0
    sys.path.insert(0, str(ROOT / P))
    from contract import observe, project_packet, CHANNELS
    from witness import build
    from model import Zone, Drive, state, run
    config, m1 = read(P + "config.json"), read("research/garment_prediction/config.json")
    manifest = read(P + "results/manifest.json")
    watched = set(manifest["inputs_sha256"]) | set(manifest["artifacts_sha256"]) | {
        P + "results/manifest.json", "tools/check_garment_m2.py", "docs/GARMENT-OBSERVATION.md",
        "docs/team/reports/algorithm-t10-m2.md"}
    before = {p: sha(p) for p in sorted(watched)}
    checks = []

    def check(name, action):
        try:
            detail = action()
            checks.append({"name": name, "passed": True, "detail": detail})
        except Exception as exc:
            checks.append({"name": name, "passed": False, "error": f"{type(exc).__name__}: {exc}"})

    def suite():
        command = [sys.executable, "-B", "-X", "utf8", P + "test_contract.py"]
        r = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120)
        log = r.stdout + r.stderr
        require(r.returncode == 0, log)
        count = re.search(r"Ran (\d+) tests?", log)
        require(count is not None, "missing test count")
        return {"command": command, "exit_code": r.returncode, "test_count": int(count.group(1)), "output": log}

    def integrity():
        require(manifest["label"] == "SYNTHETIC_UNCALIBRATED" and manifest["seed"] is None, "provenance")
        for group in ("inputs_sha256", "artifacts_sha256"):
            for p, digest in manifest[group].items():
                require("\\" not in p and (ROOT / p).resolve().is_relative_to(ROOT), "path " + p)
                require(sha(p) == digest, "hash " + p)
        frozen = read("docs/team/baseline-t10-m2/snapshot.json")["inputs_sha256"]
        m1_receipt = read("docs/verification/T10-M1-tests.json")["reviewed_sha256"]
        for group in (frozen, m1_receipt):
            for p, digest in group.items():
                require(sha(p) == digest, "accepted input changed " + p)
        required = set(frozen) | set(m1_receipt) | {
            "docs/team/baseline-t10-m2/snapshot.json", "docs/team/assignments/algorithm-t10-m2.md"}
        required |= {P + f for f in ("contract.py", "witness.py", "config.json", "test_contract.py", "replay.py")}
        require(set(manifest["inputs_sha256"]) == required, "incomplete input manifest")
        require(set(manifest["artifacts_sha256"]) == {P + "results/witness.json"}, "artifact manifest")
        return {"inputs": len(required), "artifacts": 1, "M1_unchanged_files": len(m1_receipt),
                "frozen_M2_inputs": len(frozen)}

    def result(packet, expected):
        value = observe(packet, config["policy"])
        require(value["local_record_allowed"] is expected, str(value))
        require(value["collection_authorized"] is False and value["whole_garment_state"] == "unknown",
                "unsupported collection/global inference")
        return value

    def coverage_sweep():
        n = 0
        for key in ("contact", "charging_heat"):
            for start, end, expected in ((570, 580, False), (591, 595, False),
                                         (595, 595, False), (590, 590, True), (580, 595, True)):
                p = deepcopy(config["packet"])
                p[key].update(coverage_start_s=start, coverage_end_s=end, issued_at_s=595)
                result(p, expected)
                n += 1
            p = deepcopy(config["packet"])
            p["samples"]["local_rh"]["measured_at_s"] = 591
            p[key].update(coverage_start_s=580, coverage_end_s=590, issued_at_s=595)
            result(p, False)
            n += 1
            for start, end, issued in ((591, 590, 595), (580, 596, 595), (-1, 590, 595),
                                       (580, 595, 601), (True, 590, 595)):
                p = deepcopy(config["packet"])
                p[key].update(coverage_start_s=start, coverage_end_s=end, issued_at_s=issued)
                try:
                    observe(p, config["policy"])
                except ValueError:
                    pass
                else:
                    raise AssertionError("invalid evidence interval accepted")
                n += 1
        return {"cases": n, "semantics": "asserted coverage, not automatic contact detection"}

    def quality_boundaries():
        n = 0
        for measured, received, expected in ((480, 480, True), (479, 479, False),
                                              (580, 600, True), (579, 600, False)):
            p = deepcopy(config["packet"])
            for sample in p["samples"].values():
                sample.update(measured_at_s=measured, received_at_s=received)
            for key in ("contact", "charging_heat"):
                p[key].update(coverage_start_s=measured, coverage_end_s=measured, issued_at_s=600)
            result(p, expected)
            n += 1
        for channel in CHANNELS:
            for extra, expected in ((0, True), (.001, False)):
                p = deepcopy(config["packet"])
                p["samples"][channel]["uncertainty_abs"] = config["policy"]["max_uncertainty"][channel] + extra
                result(p, expected)
                n += 1
            p = deepcopy(config["packet"])
            p["samples"][channel] = None
            result(p, False)
            n += 1
        return {"cases": n, "limit": "software policy boundaries only"}

    def hidden_isolation():
        class Unreadable:
            def __deepcopy__(self, memo):
                raise AssertionError("hidden truth read or copied")
        envelope = {**deepcopy(config["packet"]), "hidden_state": Unreadable(),
                    "material": Unreadable(), "future_weather": Unreadable()}
        public = project_packet(envelope)
        require(public == config["packet"], "projection changed observable fields")
        require(observe(public, config["policy"]) == observe(config["packet"], config["policy"]),
                "hidden input influenced consumer")
        envelope["samples"]["local_rh"]["value"] = .1
        require(public["samples"]["local_rh"]["value"] == config["packet"]["samples"]["local_rh"]["value"],
                "consumer input aliases producer")
        return {"hidden_fields_unread": 3, "consumer_input_detached": True}

    def witness_review():
        saved = read(P + "results/witness.json")
        require(build(config, m1) == saved, "current source/config differs from artifact")
        a, b = saved["cases"]
        require(a["producer_only"]["ideal_central_trajectory"] == b["producer_only"]["ideal_central_trajectory"],
                "ideal central state differs")
        errors = []
        for case in (a, b):
            for i, name in enumerate(("ordinary", "slow")):
                z, d = Zone(**m1[name]), Drive(**m1["drive"])
                x = (config["central_condition"]["central_x_initial_kg_kg"] if i == 0
                     else case["producer_only"]["slow_x_initial_kg_kg"])
                y = state(z, x, config["central_condition"]["central_initial_temperature_c"])
                reference = run(z, d, y, config["duration_s"], .25, "midpoint")
                stored = case["producer_only"]["zones"][i]
                error = {"water_kg": abs(reference["water_kg"] - stored["water_kg"]),
                         "temperature_c": abs(reference["temperature_c"] - stored["temperature_c"])}
                require(error["water_kg"] < 1e-7 and error["temperature_c"] < 1e-4, "integration reference")
                errors.append(error)
        delta = abs(a["producer_only"]["zones"][1]["water_kg"] - b["producer_only"]["zones"][1]["water_kg"])
        require(delta > .08, "witness lacks distinct unobserved water")
        require(a["public_observation"] == b["public_observation"] and a["consumer"] == b["consumer"],
                "manual interface examples differ")
        return {"same_platform_replay": "exact", "hidden_water_difference_kg": delta,
                "midpoint_reference_errors": errors,
                "limits": "Same M1 physics; conditional ideal-state witness. Manual RH packet is not a sensor simulation."}

    check("worker_suite", suite)
    check("manifest_frozen_inputs_and_M1", integrity)
    check("evidence_coverage_and_invalid_intervals", coverage_sweep)
    check("quality_boundaries_and_missing_channels", quality_boundaries)
    check("hidden_data_isolation", hidden_isolation)
    check("replay_and_conditional_witness", witness_review)
    check("sources_unchanged_during_review", lambda: require(before == {p: sha(p) for p in before}, "source changed"))
    passed = all(c["passed"] for c in checks)
    receipt = {"schema_version": 1, "task": "T10", "milestone": "M2a restricted observation interface",
               "created_at_utc": datetime.now(timezone.utc).isoformat(), "passed": passed,
               "collection_authorized": False, "label": "SYNTHETIC_UNCALIBRATED", "seed": None,
               "command": "python -B -X utf8 tools/check_garment_m2.py --write-receipt",
               "python": sys.version, "reviewed_sha256": before, "checks": checks,
               "limits": ["No coupled sensor/garment observation model or calibration.",
                          "Evidence coverage is an externally asserted input, not automatic detection.",
                          "No posterior probabilities, ETA or real collection decisions."]}
    if args.write_receipt:
        RECEIPT.write_bytes((json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    for c in checks:
        print(("PASS " if c["passed"] else "FAIL ") + c["name"])
        if not c["passed"]:
            print(c["error"])
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

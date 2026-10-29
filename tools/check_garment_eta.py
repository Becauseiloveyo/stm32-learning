"""Independent coordinator checks for synthetic conditional target times (T10 M2b)."""
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
P = "research/garment_prediction/estimation_m2b/"
RECEIPT = ROOT / "docs/verification/T10-M2b-tests.json"


def read(p):
    return json.loads((ROOT / p).read_text(encoding="utf-8-sig"))


def sha(p):
    return hashlib.sha256((ROOT / p).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def analytical_request(rain=False):
    # Deliberately degenerate artificial conditions, not fitted fabric properties.
    zones = [dict(dry_kg=mass, evap_m2=0., proj_m2=0., heat_m2=0., rad_m2=0.,
                  xb=.15, resistance_s_m=140., drain_per_s=k, alpha=0., emissivity=0.)
             for mass, k in ((.3, .001), (.1, .0005))]
    drives = [dict(air_c=25., rh=0., wind_m_s=0., solar_w_m2=0., surround_c=25.,
                   rain_kg_s=r, rain_c=25.)
              for r in ((.00003, .0000025) if rain else (0., 0.))]
    return dict(members=[dict(id="analytic", zones=zones, x0=[.6, .8], temperature_c=[25., 25.])],
                scenarios=[dict(id="constant", segments=[dict(end_s=4000., drives=drives)])],
                target_x=.2, step_s=7., computed_at_s=1234.)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--write-receipt", action="store_true")
    modes.add_argument("--check-receipt", action="store_true")
    args = parser.parse_args()
    if args.check_receipt:
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
        require(receipt["passed"] is True and receipt["collection_authorized"] is False, "unpassed receipt")
        require(bool(receipt["checks"]) and all(c["passed"] for c in receipt["checks"]), "missing checks")
        require(bool(receipt["reviewed_sha256"]), "missing source hashes")
        for path, digest in receipt["reviewed_sha256"].items():
            require((ROOT / path).resolve().is_relative_to(ROOT) and sha(path) == digest, "stale " + path)
        print("M2b receipt current and passed")
        return 0

    sys.path.insert(0, str(ROOT / P))
    from eta import calculate
    config = read(P + "config.json")
    manifest = read(P + "results/manifest.json")
    watched = set(manifest["inputs_sha256"]) | set(manifest["artifacts_sha256"]) | {
        P + "results/manifest.json", "docs/GARMENT-ETA.md", "docs/team/reports/algorithm-t10-m2b.md",
        "tools/check_garment_eta.py"}
    before = {path: sha(path) for path in sorted(watched)}
    checks = []

    def check(name, action):
        try:
            detail = action()
            checks.append(dict(name=name, passed=True, detail=detail))
        except Exception as exc:
            checks.append(dict(name=name, passed=False, error=f"{type(exc).__name__}: {exc}"))

    def bounded_result(req):
        value = calculate(req)
        require(value["collection_authorized"] is False and value["mode"] == "scenario_only"
                and value["observation_mapping"] == "unavailable", "unsupported inference")
        require(value["label"] == "SYNTHETIC_UNCALIBRATED", "missing synthetic label")
        require(len(value["cases"]) == len(req["members"]) * len(req["scenarios"]), "dropped combination")
        return value

    def worker_suite():
        command = [sys.executable, "-B", "-X", "utf8", P + "test_eta.py"]
        proc = subprocess.run(command, cwd=ROOT, capture_output=True, encoding="utf-8", timeout=120)
        output = proc.stdout + proc.stderr
        require(proc.returncode == 0, output)
        count = re.search(r"Ran (\d+) tests?", output)
        require(count is not None, "missing test count")
        return dict(command=command, test_count=int(count.group(1)), exit_code=proc.returncode, output=output)

    def integrity():
        require(manifest["label"] == "SYNTHETIC_UNCALIBRATED" and manifest["seed"] is None, "provenance")
        required = set()
        protected_counts = {}
        for path, key in (("docs/team/baseline-t10-m2b/snapshot.json", "inputs_sha256"),
                          ("docs/verification/T10-M1-tests.json", "reviewed_sha256"),
                          ("docs/verification/T10-M2a-tests.json", "reviewed_sha256")):
            hashes = read(path)[key]
            for name, expected in hashes.items():
                require(sha(name) == expected, "accepted input changed " + name)
            required |= set(hashes) | {path}
            protected_counts[path] = len(hashes)
        required |= {"docs/team/assignments/algorithm-t10-m2b.md"} | {
            P + f for f in ("eta.py", "test_eta.py", "config.json", "replay.py")}
        require(set(manifest["inputs_sha256"]) == required, "incomplete manifest")
        require(set(manifest["artifacts_sha256"]) == {P + "results/scenarios.json"}, "unexpected outputs")
        for group in ("inputs_sha256", "artifacts_sha256"):
            for path, expected in manifest[group].items():
                require("\\" not in path and (ROOT / path).resolve().is_relative_to(ROOT), "invalid path")
                require(sha(path) == expected, "manifest hash " + path)
        return dict(inputs=len(required), artifacts=1, protected=protected_counts)

    def analytic_crossings():
        records = []
        for rain in (False, True):
            req = analytical_request(rain)
            m = req["members"][0]
            drives = req["scenarios"][0]["segments"][0]["drives"]
            exacts = []
            for x0, z, d in zip(m["x0"], m["zones"], drives):
                eq = d["rain_kg_s"] / (z["dry_kg"] * z["drain_per_s"])
                exacts.append(math.log((x0 - eq) / (req["target_x"] - eq)) / z["drain_per_s"])
            exact = max(exacts)  # Valid here only because both analytic paths decrease monotonically.
            for step in (7., 1.):
                req["step_s"] = step
                result = bounded_result(req)
                actual = result["cases"][0]["first_joint_sample_s"]
                require(actual is not None and -1e-8 <= actual - exact <= step + 1e-8,
                        f"analytic crossing error {actual} vs {exact}")
                records.append(dict(rain=rain, step_s=step, analytic_joint_s=exact,
                                    sampled_joint_s=actual, error_s=actual-exact))
        return dict(cases=records, limit="Sampling bound applies only to these monotone analytic examples.")

    def equilibrium_and_zero():
        req = analytical_request(True)
        for z, d in zip(req["members"][0]["zones"], req["scenarios"][0]["segments"][0]["drives"]):
            d["rain_kg_s"] = .2 * z["dry_kg"] * z["drain_per_s"]
        req["scenarios"][0]["segments"][0]["end_s"] = 10000.
        for target in (.2, .1):
            req["target_x"] = target
            value = bounded_result(req)
            require(value["upper_sample_s"] is None and value["cases"][0]["first_joint_sample_s"] is None,
                    "unreachable analytic target given finite time")
        req = analytical_request(False)
        req["target_x"] = 0.
        value = bounded_result(req)
        require(value["upper_sample_s"] is None, "positive exponential claimed to reach zero")
        return dict(cases=3, limit="Finite test horizon; analytic result independently explains no crossing.")

    def nonoverlap_and_rewet():
        req = analytical_request(False)
        member = req["members"][0]
        member["x0"] = [.3, 5.]
        for z, mass in zip(member["zones"], (.1, .3)):
            z.update(dry_kg=mass, drain_per_s=.01)
        dry = req["scenarios"][0]["segments"][0]["drives"]
        wet = deepcopy(dry)
        for d in wet:
            d["rain_kg_s"] = .0003
        req["scenarios"][0]["segments"] = [dict(end_s=80., drives=dry), dict(end_s=1000., drives=wet)]
        central_first = math.log(.3/.2)/.01
        central_rewet = 80 + math.log((.3-.3*math.exp(-.8))/(.3-.2))/.01
        other_first = 80 + math.log((5*math.exp(-.8)-.1)/(.2-.1))/.01
        require(central_first < 80 < central_rewet < other_first, "reference construction")
        out = bounded_result(req)
        require(out["upper_sample_s"] is None and out["cases"][0]["first_joint_sample_s"] is None,
                "incorrect max of individual first times")
        member["x0"] = [.1, .1]
        out2 = bounded_result(req)
        require(out2["cases"][0]["first_joint_sample_s"] == 0
                and out2["cases"][0]["later_sample_above_target"] is True, "rewet ignored")
        return dict(central_first_s=central_first, central_rewet_s=central_rewet,
                    other_first_s=other_first, simultaneous_crossing="none in constructed case",
                    initially_reached_then_rewet_detected=True)

    def boundary_and_failure_retention():
        req = analytical_request(False)
        member = req["members"][0]
        member["x0"] = [1., 1.]
        for z in member["zones"]:
            z["drain_per_s"] = .001
        req["target_x"] = math.exp(-.001*13.5)
        drive = req["scenarios"][0]["segments"][0]["drives"]
        req["scenarios"][0]["segments"] = [dict(end_s=10.5, drives=drive), dict(end_s=20.5, drives=deepcopy(drive))]
        value = bounded_result(req)["cases"][0]
        require(value["previous_sample_s"] == 10.5 and value["first_joint_sample_s"] == 17.5,
                "segment boundary not split exactly")
        req = analytical_request(False)
        bad = deepcopy(req["members"][0]); bad["id"] = "invalid_mass"; bad["zones"][0]["dry_kg"] = -1.
        stiff = deepcopy(req["members"][0]); stiff["id"] = "large_step"
        stiff["zones"][0]["drain_per_s"] = 1.
        req["members"] += [bad, stiff]
        invalid_drive = deepcopy(req["scenarios"][0]); invalid_drive["id"] = "outside_drive"
        invalid_drive["segments"][0]["drives"][0]["air_c"] = 60.
        req["scenarios"].append(invalid_drive)
        out = bounded_result(req)
        require(len(out["cases"]) == 6 and out["unresolved_count"] == 5 and out["upper_sample_s"] is None,
                "failed combinations omitted")
        req = analytical_request(False); req["members"][0]["x0"] = [.1, .1]
        d = req["scenarios"][0]["segments"][0]["drives"]
        bad_drive = deepcopy(d); bad_drive[0]["air_c"] = 60.
        req["scenarios"][0]["segments"] = [dict(end_s=10, drives=d), dict(end_s=20, drives=bad_drive)]
        later = bounded_result(req)
        require(later["cases"][0]["first_joint_sample_s"] == 0 and later["upper_sample_s"] is None,
                "failure after initial target was ignored")
        return dict(exact_split_first_s=17.5, retained_cases=6,
                    statuses=[c["status"] for c in out["cases"]], failure_after_reaching_retained=True)

    def replay_refinement_and_isolation():
        req = config["request"]
        stored = read(P + "results/scenarios.json")
        actual = bounded_result(req)
        require(actual == stored, "saved result differs")
        fine = deepcopy(req); fine["step_s"] = req["step_s"]/4
        refined = bounded_result(fine)
        differences = []
        for a, b in zip(actual["cases"], refined["cases"]):
            require(a["status"] == b["status"], "classification changes on refinement")
            if a["first_joint_sample_s"] is not None:
                delta = abs(a["first_joint_sample_s"]-b["first_joint_sample_s"])
                require(delta <= req["step_s"], "example sampling difference exceeds coarse step")
                differences.append(dict(member=a["member_id"], scenario=a["scenario_id"],
                                        coarse_s=a["first_joint_sample_s"], fine_s=b["first_joint_sample_s"], difference_s=delta))
        rejected = 0
        for where in ("request", "member", "scenario", "segment"):
            r = analytical_request(False)
            location = {"request": r, "member": r["members"][0], "scenario": r["scenarios"][0],
                        "segment": r["scenarios"][0]["segments"][0]}[where]
            location["hidden_truth_or_future_actual"] = {"x": 0}
            try:
                calculate(r)
            except ValueError:
                rejected += 1
            else:
                raise AssertionError("hidden input accepted at " + where)
        return dict(combinations=len(actual["cases"]), unresolved=actual["unresolved_count"],
                    earliest_known_sample_s=actual["earliest_known_sample_s"], upper_sample_s=actual["upper_sample_s"],
                    refinement=differences, hidden_fields_rejected=rejected,
                    limit="Refinement evidence for these configurations only; no continuous-event guarantee.")

    check("worker_suite", worker_suite)
    check("source_artifact_and_frozen_integrity", integrity)
    check("independent_analytic_crossings", analytic_crossings)
    check("equilibrium_and_positive_inventory_zero_target", equilibrium_and_zero)
    check("nonoverlapping_targets_and_rewet", nonoverlap_and_rewet)
    check("segment_split_and_failure_retention", boundary_and_failure_retention)
    check("replay_refinement_and_input_isolation", replay_refinement_and_isolation)
    check("sources_unchanged_during_review", lambda: require(before == {p: sha(p) for p in before}, "sources changed"))
    passed = all(c["passed"] for c in checks)
    receipt = dict(schema_version=1, task="T10", milestone="M2b scenario-only sampled target times",
                   created_at_utc=datetime.now(timezone.utc).isoformat(), passed=passed,
                   label="SYNTHETIC_UNCALIBRATED", collection_authorized=False, seed=None,
                   command="python -B -X utf8 tools/check_garment_eta.py --write-receipt",
                   python=sys.version, reviewed_sha256=before, checks=checks,
                   limits=["No real garment accuracy, observation assimilation or probability calibration.",
                           "Analytic cases are degenerate synthetic limits with explicitly monotone branches.",
                           "General first sampled target does not bound first continuous crossing.",
                           "No physical tests, firmware deployment, weather forecast or hardware procurement."])
    if args.write_receipt:
        RECEIPT.write_bytes((json.dumps(receipt, ensure_ascii=False, indent=2)+"\n").encode("utf-8"))
    for c in checks:
        print(("PASS " if c["passed"] else "FAIL ")+c["name"])
        if not c["passed"]:
            print(c["error"])
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

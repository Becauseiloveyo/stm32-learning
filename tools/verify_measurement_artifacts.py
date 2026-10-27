"""Read-only T01 artifact audit; stdout can be saved as an integration receipt.

Replays saved observation CSVs, checks declared hashes and frozen baseline values.
This is not a substitute for unit tests, physical calibration or source review.
"""

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from theory.model import assess
from theory.protocol import Gates, MeasurementProtocol


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def verify():
    results = ROOT / "theory/results"
    frozen = ROOT / "docs/team/baseline-v1"
    manifest = json.loads((results / "manifest.json").read_text(encoding="utf-8"))
    snapshot = json.loads((frozen / "snapshot.json").read_text(encoding="utf-8"))
    checked = {}
    for base, hashes in ((ROOT / "theory", manifest["source_sha256"]),
                         (results, manifest["output_sha256"]),
                         (frozen, snapshot["files"])):
        require(bool(hashes), "Missing hash set")
        for name, expected in hashes.items():
            path = (base / name).resolve()
            require(path.is_relative_to(base.resolve()), "Path outside declared scope")
            require(sha(path) == expected, f"Stale evidence: {path}")
            checked[path.relative_to(ROOT).as_posix()] = expected
    require(manifest["status"] == "SYNTHETIC_UNCALIBRATED", "Missing synthetic label")
    require(set(manifest["source_sha256"]) == {"model.py", "protocol.py", "run_simulation.py"},
            "Unexpected source scope; review this audit before accepting a new format")
    protocol = MeasurementProtocol(**manifest["measurement_protocol"])
    gates = Gates(**manifest["gates_are_illustrative"])
    require(protocol.fingerprint == manifest["protocol_sha256"], "Protocol fingerprint mismatch")
    summaries = read_csv(results / "case-summary.csv")
    require(len(summaries) == 12, "Expected twelve T01 cases")
    require({r["case"] for r in summaries} == set(manifest["cases"]), "Case identity mismatch")
    features = ("recovery_rise_Pa", "tail_delta_Pa")
    for summary in summaries:
        rows = read_csv(results / f"trace-{summary['case']}.csv")
        for row in rows:
            for key in ("time_s", "ambient_T_C", "ambient_RH_pct", "local_T_C", "local_RH_pct"):
                row[key] = float(row[key]) if row[key] else None
            for key in ("sensor_valid", "clip_event"):
                require(row[key] in ("True", "False"), f"Invalid CSV boolean: {key}")
                row[key] = row[key] == "True"
        actual = assess(rows, gates, protocol=protocol)
        require(actual["collection_authorized"] is False, "Unexpected collection authorization")
        for key in ("state", "reasons", "protocol_sha256"):
            require(actual[key] == summary[key], f"Trace/summary mismatch: {summary['case']} {key}")
        require(summary["collection_authorized"] == "False", "Summary authorization mismatch")
        for key in features:
            expected = float(summary[key]) if summary[key] else None
            require(actual[key] == expected, f"Trace feature mismatch: {summary['case']} {key}")
    compared = 0
    for filename, identities, count in (("case-summary.csv", ("case",), 12),
                                        ("parameter-sweep.csv", ("volume_ml", "ambient_rh_pct", "leak_ml_s"), 60)):
        old, new = read_csv(frozen / "theory/results" / filename), read_csv(results / filename)
        require(len(old) == len(new) == count, f"Unexpected row count: {filename}")
        for before, after in zip(old, new):
            for key in (*identities, "state", "reasons", *features):
                require(before[key] == after[key], f"Frozen baseline differs: {filename} {key}")
            compared += 1
    return {
        "schema_version": 1, "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "passed": True, "saved_traces_replayed": len(summaries), "baseline_rows_compared": compared,
        "protocol_sha256": protocol.fingerprint, "checked_sha256": checked,
        "audit_sha256": sha(Path(__file__)), "manifest_sha256": sha(results / "manifest.json"),
        "scope": "T01 stored artifacts and baseline compatibility; synthetic and uncalibrated",
    }


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False, indent=2))

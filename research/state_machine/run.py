"""Generate T04 deterministic event replays, or verify saved artifacts read-only."""

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.state_machine.machine import Configuration, VERSION, replay
from research.state_machine.scenarios import scenarios
from theory.model import DEFAULT_SEED

RESULTS = HERE / "results"
SYNTHETIC = "SYNTHETIC_UNCALIBRATED"
INPUT_FILES = ("theory/model.py", "theory/protocol.py", "theory/results/manifest.json",
               "docs/MEASUREMENT-CONTRACT.md", "docs/MODEL-VALIDITY.md",
               "research/validity/results/manifest.json", "hanger-drying-architecture.md",
               "docs/team/assignments/measurement-t04.md", "docs/STATE-MACHINE.md")
UNITS = {"monotonic_s": "s since current boot time origin", "start_s": "s in current boot domain",
         "end_s": "s in current boot domain", "observations.time_s": "s since cycle start",
         "temperature": "degC", "humidity": "%RH", "pressure": "Pa", "weak_cycles": "count"}


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def hashes(base, names):
    return {name: digest((base / name).read_bytes()) for name in sorted(names)}


def check_hashes(base, expected):
    if not expected:
        raise ValueError("Empty hash mapping")
    for name, value in expected.items():
        path = (base / name).resolve()
        if not path.is_relative_to(ROOT):
            raise ValueError("Manifest path outside project")
        if digest(path.read_bytes()) != value:
            raise ValueError(f"Changed file: {path}")


def input_hashes():
    # Check accepted executable inputs against their existing provenance without rewriting it.
    upstream = json.loads((ROOT / "theory/results/manifest.json").read_text(encoding="utf-8"))
    check_hashes(ROOT / "theory", upstream["source_sha256"])
    return hashes(ROOT, INPUT_FILES)


def build_outputs():
    streams = scenarios()
    steps, summary, configs = [], [], {}
    observation_rows = assessed_cycles = 0
    for stream in streams:
        config = Configuration.from_snapshot(stream["initial_config"])
        configs[config.sha256] = config.snapshot()
        results = replay(stream["events"], config)
        for index, (event, result) in enumerate(zip(stream["events"], results)):
            if result["collection_authorized"] is not False or (result["assessment"] and result["assessment"]["collection_authorized"] is not False):
                raise ValueError("Research output attempted collection authorization")
            if event["kind"] == "configure":
                changed = Configuration.from_snapshot(event["new_config"])
                configs[changed.sha256] = changed.snapshot()
            observation_rows += len(event.get("observations") or [])
            assessed_cycles += result["assessment"] is not None
            steps.append({"scenario": stream["name"], "index": index, "event_id": event["event_id"],
                          "kind": event["kind"], "output": result})
        summary.append({"scenario": stream["name"], "events": len(results),
                        "assessed_cycles": sum(r["assessment"] is not None for r in results),
                        "weak_recorded": sum(r["reason"] == "weak_evidence_unconfirmed" for r in results),
                        "duplicate_rejections": sum(r["disposition"] == "IGNORED_DUPLICATE" for r in results),
                        "max_weak_cycles": max(r["weak_cycles"] for r in results),
                        "final_weak_cycles": results[-1]["weak_cycles"], "final_state": results[-1]["state"],
                        "collection_authorized": False})
    csv_handle = io.StringIO(newline="")
    writer = csv.DictWriter(csv_handle, fieldnames=list(summary[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(summary)
    payloads = {
        "events.json": json_bytes({"synthetic": SYNTHETIC, "units": UNITS, "scenarios": streams}),
        "steps.jsonl": ("".join(json.dumps(step, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n" for step in steps)).encode("utf-8"),
        "summary.csv": csv_handle.getvalue().encode("utf-8"),
    }
    metadata = {"counts": {"scenarios": len(streams), "events": len(steps), "assessed_cycles": assessed_cycles,
                           "saved_observation_rows_including_retries": observation_rows,
                           "weak_records": sum(row["weak_recorded"] for row in summary)},
                "configurations": configs,
                "scenario_hypotheses": {stream["name"]: stream["hypothesis"] for stream in streams}}
    return payloads, metadata


def make_manifest(inputs, outputs, metadata):
    return {"manifest_version": 1, "state_machine_version": VERSION,
            "synthetic": SYNTHETIC, "collection_authorized": False,
            "source_sha256": hashes(HERE, [p.name for p in HERE.glob("*.py")]),
            "input_sha256": inputs, "output_sha256": {name: digest(data) for name, data in outputs.items()},
            "randomness": {"deterministic": True, "noise_rh_pct": 0.0, "simulator_seed": DEFAULT_SEED,
                           "event_random_seed": None}, "units": UNITS,
            "scope": "computer-only event ordering and evidence isolation; no physical stability or garment calibration",
            **metadata}


def verify(result_dir=RESULTS):
    manifest = json.loads((result_dir / "manifest.json").read_text(encoding="utf-8"))
    check_hashes(HERE, manifest["source_sha256"])
    check_hashes(ROOT, manifest["input_sha256"])
    check_hashes(result_dir, manifest["output_sha256"])
    inputs = input_hashes()
    # Replay saved raw inputs first, independently of regenerating their fixtures.
    saved = json.loads((result_dir / "events.json").read_text(encoding="utf-8"))
    saved_steps = [json.loads(line) for line in (result_dir / "steps.jsonl").read_text(encoding="utf-8").splitlines()]
    offset = 0
    for stream in saved["scenarios"]:
        results = replay(stream["events"], Configuration.from_snapshot(stream["initial_config"]))
        expected = [row["output"] for row in saved_steps[offset:offset + len(results)]]
        if results != expected:
            raise ValueError(f"Saved event replay mismatch: {stream['name']}")
        offset += len(results)
    if offset != len(saved_steps):
        raise ValueError("Extra saved outputs")
    outputs, metadata = build_outputs()
    for name, data in outputs.items():
        if (result_dir / name).read_bytes() != data:
            raise ValueError(f"Deterministic recomputation mismatch: {name}")
    if make_manifest(inputs, outputs, metadata) != manifest or inputs != input_hashes():
        raise ValueError("Manifest or input stability mismatch")
    return metadata["counts"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="Read-only hash checks, saved event replay and deterministic recomputation")
    args = parser.parse_args()
    if args.verify:
        print("VERIFIED (read-only): " + json.dumps(verify(), sort_keys=True))
        return
    inputs = input_hashes()
    outputs, metadata = build_outputs()
    manifest = make_manifest(inputs, outputs, metadata)
    if inputs != input_hashes():
        raise ValueError("Inputs changed during computation; nothing written")
    RESULTS.mkdir(parents=True, exist_ok=True)
    for name, data in outputs.items():
        (RESULTS / name).write_bytes(data)
    (RESULTS / "manifest.json").write_bytes(json_bytes(manifest))
    print("GENERATED SYNTHETIC_UNCALIBRATED: " + json.dumps(metadata["counts"], sort_keys=True))


if __name__ == "__main__":
    main()

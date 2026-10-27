"""Generate complete T05 artifacts or replay them read-only, using the standard library."""
import argparse
import csv
from dataclasses import asdict
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

from research.state_machine.machine import Configuration, replay
from research.energy_t05.contracts import LABEL, from_dict
from research.energy_t05.model import simulate
from research.energy_t05.scenarios import scenarios

RESULTS = HERE / "results"
INPUT_FILES = ("theory/model.py", "theory/protocol.py", "theory/results/manifest.json",
               "research/state_machine/machine.py", "research/state_machine/results/manifest.json",
               "docs/STATE-MACHINE.md", "docs/MEASUREMENT-CONTRACT.md", "analysis/energy_budget.py",
               "power-notification-design.md", "docs/team/assignments/energy-t05.md",
               "research/energy/model.py", "docs/ENERGY-SCHEDULING-DRAFT.md", "docs/team/reports/energy.md")


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def hashes(base, names):
    return {name: digest((base / name).read_bytes()) for name in sorted(names)}


def check_hashes(base, expected):
    if not expected:
        raise ValueError("empty provenance mapping")
    for name, value in expected.items():
        path = (base / name).resolve()
        if not path.is_relative_to(base.resolve()) or digest(path.read_bytes()) != value:
            raise ValueError(f"changed/mislocated artifact: {name}")


def inputs():
    theory = json.loads((ROOT / "theory/results/manifest.json").read_text(encoding="utf-8"))
    check_hashes(ROOT / "theory", theory["source_sha256"])
    machine = json.loads((ROOT / "research/state_machine/results/manifest.json").read_text(encoding="utf-8"))
    check_hashes(ROOT / "research/state_machine", machine["source_sha256"])
    check_hashes(ROOT, machine["input_sha256"])
    return hashes(ROOT, INPUT_FILES)


def payloads_for(streams):
    payloads = {"inputs.json": json_bytes({"label": LABEL, "scenarios": [asdict(s) for s in streams]})}
    summary, counts = [], {"scenarios": len(streams), "policy_runs": 0, "events": 0, "raw_observation_rows": 0}
    for index, scenario in enumerate(streams, 1):
        for policy in ("fixed", "demand"):
            result = simulate(scenario, policy)
            name = f"case-{index:02d}-{policy}"
            ledger = result.pop("ledger")
            events = result.pop("events")
            payloads[f"{name}-ledger.jsonl"] = b"".join(json_bytes(row) for row in ledger)
            payloads[f"{name}-events.jsonl"] = b"".join(json_bytes(row) for row in events)
            payloads[f"{name}-service.json"] = json_bytes(result)
            summary.append(result["summary"])
            counts["policy_runs"] += 1
            counts["events"] += len(events)
            counts["raw_observation_rows"] += sum(len(e["event"].get("observations", ())) for e in events)
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=list(summary[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(summary)
    payloads["summary.csv"] = text.getvalue().encode("utf-8")
    metadata = {"counts": counts, "configurations": {s.validate().sha256: s.configuration for s in streams},
                "energy_configurations": {s.name: asdict(s.energy) for s in streams},
                "scenario_assumptions": {s.name: s.assumption for s in streams},
                "seeds": {s.name: s.seed for s in streams}}
    return payloads, metadata, summary


def manifest_for(payloads, metadata):
    return {"schema_version": 1, "label": LABEL, "collection_authorized": False,
            "input_sha256": inputs(),
            "source_sha256": hashes(HERE, [p.name for p in HERE.glob('*.py')]),
            "output_sha256": {name: digest(content) for name, content in sorted(payloads.items())},
            "units": {"wall_s": "elapsed integer seconds, synthetic day starts at 0",
                      "monotonic_s": "seconds since the current boot, strictly increasing events",
                      "observation_time_s": "relative seconds since cycle start",
                      "energy": "Wh; load-side unless battery/store/solar/spill is named",
                      "power": "W", "actuator_energy": "J", "temperature": "degC", "humidity": "%RH"},
            "policy_boundary": "Scheduling sees arrived requests and current state/store; not future weather, raw result or connection outcome.",
            **metadata}


def verify_saved(results=RESULTS):
    """Check hashes, replay raw T04 events, replay saved inputs, then regenerate definitions."""
    manifest = json.loads((results / "manifest.json").read_text(encoding="utf-8"))
    check_hashes(ROOT, manifest["input_sha256"])
    check_hashes(HERE, manifest["source_sha256"])
    check_hashes(results, manifest["output_sha256"])
    saved = json.loads((results / "inputs.json").read_text(encoding="utf-8"))
    streams = tuple(from_dict(s) for s in saved["scenarios"])
    for index, scenario in enumerate(streams, 1):
        config = Configuration.from_snapshot(scenario.configuration)
        for policy in ("fixed", "demand"):
            path = results / f"case-{index:02d}-{policy}-events.jsonl"
            evidence = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            outputs = replay([item["event"] for item in evidence], config)
            if outputs != [item["output"] for item in evidence]:
                raise ValueError(f"T04 replay mismatch: {path.name}")
    payloads, metadata, _ = payloads_for(streams)
    for name, content in payloads.items():
        if content != (results / name).read_bytes():
            raise ValueError(f"saved-input simulation mismatch: {name}")
    if json_bytes({"label": LABEL, "scenarios": [asdict(s) for s in scenarios()]}) != payloads["inputs.json"]:
        raise ValueError("built-in scenario definitions changed")
    if manifest_for(payloads, metadata) != manifest:
        raise ValueError("full manifest mismatch")
    print(f"Verified {len(payloads)} output hashes, {metadata['counts']['events']} actual T04 events, and all {metadata['counts']['policy_runs']} full energy replays; no files written.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        verify_saved()
        return
    payloads, metadata, summary = payloads_for(scenarios())
    manifest = manifest_for(payloads, metadata)
    RESULTS.mkdir(parents=True, exist_ok=True)
    for name, content in payloads.items():
        (RESULTS / name).write_bytes(content)
    (RESULTS / "manifest.json").write_bytes(json_bytes(manifest))
    print(LABEL + ": no collection authorization; no physical endurance claim.")
    for row in summary:
        print(f"{row['scenario']}/{row['policy']}: {row['battery_consumed_wh']:.6f} Wh; "
              f"measurement {row['measurement_served_requests']}/{row['measurement_requests']}; "
              f"wifi {row['wifi_served_requests']}/{row['wifi_requests']}; "
              f"weak-chain max={row['max_weak_chain']}, expiry={row['idle_evidence_expirations']}")


if __name__ == "__main__":
    main()

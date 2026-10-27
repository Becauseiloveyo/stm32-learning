"""Run/export only the independent energy lane; standard library only."""

import argparse
import csv
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import platform

from research.energy.model import from_dict, simulate
from research.energy.scenarios import scenarios

ROOT = Path(__file__).resolve().parents[2]
LANE = ROOT / "research/energy"
BASELINE = ROOT / "docs/team/baseline-v1"
BASELINE_FILES = ("analysis/energy_budget.py", "power-notification-design.md")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def baseline_hashes():
    snapshot = json.loads((BASELINE / "snapshot.json").read_text(encoding="utf-8-sig"))
    hashes = {}
    for relative in BASELINE_FILES:
        path = BASELINE / relative
        value = digest(path)
        if snapshot["files"][relative] != value:
            raise ValueError(f"frozen baseline hash mismatch: {relative}")
        hashes[path.relative_to(ROOT).as_posix()] = value
    return hashes


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path, rows):
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def export(out, inputs):
    out = out.resolve()
    if not out.is_relative_to(LANE) or out == LANE:
        raise ValueError("outputs must be in a child directory of research/energy")
    baseline = baseline_hashes()
    out.mkdir(parents=True, exist_ok=True)
    summaries = []
    outputs = []
    for index, scenario in enumerate(inputs):
        scenario.validate()
        # An index, not user-supplied scenario name, owns each output directory.
        folder = out / f"case-{index + 1:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        input_path = folder / "input.json"
        write_json(input_path, asdict(scenario))
        outputs.append(input_path)
        for policy in ("fixed", "demand"):
            result = simulate(scenario, policy)
            summaries.append(result["summary"])
            ledger_path = folder / f"{policy}-ledger.csv"
            write_csv(ledger_path, result.pop("ledger"))
            trace_path = folder / f"{policy}-trace.json"
            write_json(trace_path, result)
            outputs.extend((ledger_path, trace_path))
    summary_path = out / "summary.csv"
    write_csv(summary_path, summaries)
    outputs.append(summary_path)
    source_files = ("model.py", "scenarios.py", "run.py", "test_energy.py")
    manifest = {
        "schema_version": 1, "label": "SYNTHETIC_UNMEASURED_ENERGY_ONLY", "seed": None,
        "time_axis": "elapsed integer minutes; t=0 is synthetic day 0 midnight, no wall-clock timezone",
        "python": platform.python_version(), "source_baseline_sha256": baseline,
        "source_sha256": {(LANE / name).relative_to(ROOT).as_posix(): digest(LANE / name) for name in source_files},
        "output_sha256": {p.relative_to(out).as_posix(): digest(p) for p in outputs},
        "config_source": "Each case input.json is the complete executable parameter snapshot.",
        "test_receipt": "This manifest is not a test receipt; run test_energy separately.",
    }
    write_json(out / "manifest.json", manifest)
    return summaries


def verify(out):
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    baseline_hashes()
    for key in ("source_baseline_sha256", "source_sha256"):
        for relative, expected in manifest[key].items():
            if digest(ROOT / relative) != expected:
                raise ValueError(f"stale source: {relative}")
    for relative, expected in manifest["output_sha256"].items():
        if digest(out / relative) != expected:
            raise ValueError(f"changed output: {relative}")
    print(f"Verified source/baseline hashes and {len(manifest['output_sha256'])} output hashes.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="one complete scenario JSON; default uses five built-ins")
    parser.add_argument("--out", type=Path, default=LANE / "results")
    parser.add_argument("--verify", action="store_true", help="read-only freshness and output integrity check")
    args = parser.parse_args()
    if args.verify:
        verify(args.out.resolve())
        return
    inputs = (from_dict(json.loads(args.input.read_text(encoding="utf-8-sig"))),) if args.input else scenarios()
    rows = export(args.out, inputs)
    print("SYNTHETIC / UNMEASURED. Completion means budget service only; no valid dry result or runtime claim.")
    for row in rows:
        print(f"{row['scenario']}/{row['policy']}: battery={row['battery_consumed_wh']:.6f} Wh, "
              f"no-result={row['no_budget_result_services']}/{row['requested_services']}, "
              f"restarts={row['restart_count']}, balance={row['balance_residual_wh']:.3g} Wh")


if __name__ == "__main__":
    main()

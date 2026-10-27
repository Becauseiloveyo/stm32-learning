"""Owned T07 evidence runner; --verify is read-only and replays saved raw inputs."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import platform
import sqlite3
import sys
import traceback
import unittest

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from research.notification_t07.contract import SYNTHETIC, TTL_S, RETRY_S
from research.notification_t07.fixtures import DEFAULT_SEED, WALL_ORIGIN_S
from research.notification_t07.scenarios import check_replays, run_trace

INPUTS = ["theory/model.py", "theory/protocol.py", "research/state_machine/machine.py",
          "research/state_machine/scenarios.py", "docs/STATE-MACHINE.md", "docs/CORE-INTERFACE.md",
          "docs/MEASUREMENT-CONTRACT.md", "docs/NOTIFICATION-CONTRACT.md", "power-notification-design.md"]
OUTPUTS = {"tests.txt", "trace.json", "device.sqlite3", "service.sqlite3", "receiver.sqlite3", "receiver-b.sqlite3"}


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes(paths):
    return {path.relative_to(ROOT).as_posix(): file_hash(path) for path in sorted(paths)}


def sources():
    return hashes(list(HERE.glob("*.py")))


def inputs():
    # Actual C interface/source dependencies, never coordinator tools or test receipts.
    core = list((ROOT / "firmware/core").glob("*.c")) + list((ROOT / "firmware/core").glob("*.h"))
    return hashes([ROOT / name for name in INPUTS] + core)


def save(path, data):
    path.write_text(json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


class NamedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed_ids = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed_ids.append(test.id())


def verify(receipt_path):
    receipt_path = Path(receipt_path).resolve()
    if not receipt_path.is_relative_to(HERE):
        raise ValueError("receipt must remain in owned notification directory")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if (receipt.get("kind") != "T07_NOTIFICATION_ROLE_EVIDENCE" or receipt.get("all_passed") is not True
            or receipt.get("inputs_unchanged_during_run") is not True
            or type(receipt.get("tests_run")) is not int or receipt["tests_run"] < 1
            or receipt.get("failures") != 0 or receipt.get("errors") != 0 or receipt.get("skipped") != 0
            or len(set(receipt.get("passed_test_ids", []))) != receipt["tests_run"]):
        raise ValueError("incomplete, zero, skipped, or failed test receipt")
    if receipt.get("source_sha256") != sources() or receipt.get("input_sha256") != inputs():
        raise ValueError("source/input set or fingerprint changed")
    if set(receipt.get("output_sha256", {})) != OUTPUTS | {"manifest.json"}:
        raise ValueError("output fingerprint set is incomplete")
    for name, expected in receipt["output_sha256"].items():
        if file_hash(receipt_path.parent / name) != expected:
            raise ValueError(f"changed output: {name}")
    manifest = json.loads((receipt_path.parent / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("synthetic") != SYNTHETIC or manifest.get("collection_authorized") is not False
            or manifest.get("source_sha256") != receipt["source_sha256"]
            or manifest.get("input_sha256") != receipt["input_sha256"]
            or manifest.get("output_sha256") != {k:v for k,v in receipt["output_sha256"].items() if k != "manifest.json"}):
        raise ValueError("manifest/receipt inconsistency")
    trace = json.loads((receipt_path.parent / "trace.json").read_text(encoding="utf-8"))
    replay = check_replays(trace)
    if replay != manifest["reference_replay"] or trace["reference_replay"] != replay:
        raise ValueError("raw replay counts mismatch")
    return dict(fresh_and_passed=True, tests=receipt["tests_run"], reference_replay=replay,
                cumulative_simulated_alerts=trace["cumulative_simulated_alerts"], read_only=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--output", type=Path, help="New directory within research/notification_t07; existing directory refused")
    group.add_argument("--verify", type=Path, help="Existing receipt.json; hash verification and in-memory raw replay only")
    args = parser.parse_args()
    if args.verify:
        try:
            print(json.dumps(verify(args.verify), ensure_ascii=False))
            return 0
        except (ValueError, KeyError, OSError, TypeError) as exc:
            print(json.dumps(dict(fresh_and_passed=False, error=str(exc)), ensure_ascii=False))
            return 1
    stamp = datetime.now(timezone(timedelta(hours=8)))
    output = (args.output or HERE / "results" / stamp.strftime("run-%Y%m%d-%H%M%S-%f")).resolve()
    if not output.is_relative_to(HERE) or output == HERE:
        parser.error("output must be a new directory under research/notification_t07")
    if output.exists():
        parser.error("output already exists; preserve evidence and choose a new directory")
    before_source, before_inputs = sources(), inputs()
    output.mkdir(parents=True)
    suite = unittest.defaultTestLoader.discover(str(HERE), pattern="test_*.py")
    log = io.StringIO()
    result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=NamedResult).run(suite)
    (output / "tests.txt").write_text(log.getvalue(), encoding="utf-8")
    trace, scenario_error = None, None
    if result.wasSuccessful() and result.testsRun > 0 and not result.skipped:
        try:
            trace = run_trace(output)
            save(output / "trace.json", trace)
        except Exception:
            scenario_error = traceback.format_exc()
    stable = before_source == sources() and before_inputs == inputs()
    passed = result.wasSuccessful() and result.testsRun > 0 and not result.skipped and trace is not None and stable
    output_hashes = {name: file_hash(output / name) for name in sorted(OUTPUTS) if (output / name).is_file()}
    manifest = dict(manifest_version=1, synthetic=SYNTHETIC, collection_authorized=False,
                    source_sha256=before_source, input_sha256=before_inputs, output_sha256=output_hashes,
                    parameters=dict(ttl_s=TTL_S, server_retry_s=RETRY_S, outbox_capacity=32,
                                    virtual_wall_origin_s=WALL_ORIGIN_S, observation_noise_rh_pct=0,
                                    simulator_seed=DEFAULT_SEED, event_seed=None),
                    units=dict(wall_time="Unix seconds, synthetic", monotonic_time="seconds in generated boot domain",
                               temperature="degC", humidity="%RH"),
                    reference_replay=trace["reference_replay"] if trace else None,
                    sqlite_integrity=trace["sqlite_integrity"] if trace else None)
    save(output / "manifest.json", manifest)
    output_hashes = {**output_hashes, "manifest.json": file_hash(output / "manifest.json")}
    receipt = dict(kind="T07_NOTIFICATION_ROLE_EVIDENCE", generated_at=stamp.isoformat(),
                   command=[sys.executable, "-B", "-X", "utf8", *sys.argv],
                   python_version=platform.python_version(), sqlite_version=sqlite3.sqlite_version,
                   tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
                   passed_test_ids=result.passed_ids, all_passed=bool(passed), scenario_error=scenario_error,
                   inputs_unchanged_during_run=stable, source_sha256=before_source, input_sha256=before_inputs,
                   output_sha256=output_hashes, synthetic=SYNTHETIC, collection_authorized=False)
    save(output / "receipt.json", receipt)
    print(log.getvalue(), end="")
    if scenario_error:
        print(scenario_error)
    print(f"Evidence: {output / 'receipt.json'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

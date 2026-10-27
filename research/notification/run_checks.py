"""Run owned checks and save inspectable evidence, without global test records."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import platform
import sqlite3
import sys
import unittest

from protocol import ALLOWED, MAX_TTL_S, RETRY_S, SIMULATION_AUTH, Device, Receiver, Service


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASELINE = ROOT / "docs/team/baseline-v1/power-notification-design.md"
FINGERPRINTS = [HERE / name for name in ("protocol.py", "test_protocol.py", "crash_worker.py", "run_checks.py")]
FINGERPRINTS += [ROOT / "docs/NOTIFICATION-CONTRACT-DRAFT.md", BASELINE,
                 ROOT / "docs/team/baseline-v1/snapshot.json"]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class NamedResults(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed_ids = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed_ids.append(test.id())


def sample_trace(output):
    """One continuous synthetic trace, plus SQLite files suitable for inspection."""
    device = Device(output / "device.sqlite3", device_id="demo_hanger", producer_id="demo_producer")
    service = Service(output / "service.sqlite3")
    receiver = Receiver(output / "receiver.sqlite3", "demo_owner")
    trace = []

    def checkpoint(step, now, explanation):
        state = service.inspect()
        notices = receiver.inspect()["notices"]
        trace.append(dict(step=step, virtual_now_s=now, explanation=explanation,
                          event_count=len(state["events"]), jobs=state["jobs"],
                          simulated_push_attempts=len(state["push_attempts"]),
                          simulated_alerts=len(notices), notices=notices))

    def upload(now):
        device.acknowledge(service.submit(*device.envelope(), "demo_fixture_token", now))

    try:
        service.bind("demo_hanger", "demo_producer", "demo_owner", "demo_fixture_token", 1000)
        old = device.emit(ALLOWED, 1000, authorization=SIMULATION_AUTH)
        device.emit("RESEARCH_WET", 1010)
        upload(1020)
        service.dispatch(receiver, 1020)
        checkpoint("offline_change", 1020, "Latest wet snapshot fences older simulated authorization; no task created.")

        event = device.emit(ALLOWED, 1030, authorization=SIMULATION_AUTH)
        upload(1030)
        service.dispatch(receiver, 1030, phone_reachable=False)
        checkpoint("phone_unreachable", 1030, "Server saved and simulated push accepted; receiver and user-read remain absent.")

        service.close()
        service = Service(output / "service.sqlite3")
        receiver.close()
        receiver = Receiver(output / "receiver.sqlite3", "demo_owner")
        service.dispatch(receiver, 1090)
        checkpoint("restarted_recovery", 1090, "Persistent retry reaches simulated receiver once after both stores reopen.")

        upload(1091)
        service.dispatch(receiver, 1091)
        receiver.read(event.notice_key, service, 1092)
        checkpoint("duplicate_then_explicit_read", 1092, "Repeated saved event adds no alert; explicit simulated read has its own timestamp.")

        device.emit("LOW_SIGNAL_UNCONFIRMED", 1100)
        upload(1100)
        late_result = receiver.receive(old, service, 1100)
        receiver.sync(service, 1100)
        checkpoint("research_state_invalidates", 1100, f"Weak signal is history only; old data hint rejected: {late_result}.")

        device.emit(ALLOWED, 1110, ttl_s=10, authorization=SIMULATION_AUTH)
        upload(1110)
        service.dispatch(receiver, 1110, phone_reachable=False)
        service.dispatch(receiver, 1120)
        checkpoint("expiry_at_boundary", 1120, "Unavailable phone does not receive a task at its expiry boundary.")

        epoch = service.bind("demo_hanger", "demo_producer", "demo_new_owner", "demo_new_token", 1130)
        device.rebind(epoch)
        receiver.sync(service, 1130)
        checkpoint("rebind", 1130, "Old owner loses eligibility; new binding requires a new device snapshot.")

        snapshots = dict(device=device.inspect(), service=service.inspect(), receiver=receiver.inspect())
        integrity = {name: store.db.execute("PRAGMA integrity_check").fetchone()[0]
                     for name, store in (("device", device), ("service", service), ("receiver", receiver))}
        if set(integrity.values()) != {"ok"}:
            raise RuntimeError(f"SQLite integrity failure: {integrity}")
        return dict(kind="synthetic_offline_trace", seed=None, randomness="none", time_unit="seconds",
                    epoch_note="1000..1130 are virtual Unix seconds, not real measurements", steps=trace,
                    final_store_exports=snapshots, sqlite_integrity=integrity)
    finally:
        device.close()
        service.close()
        receiver.close()


def verify(receipt_path):
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    errors = []
    for path, expected in receipt["input_sha256"].items():
        actual = ROOT / path
        if not actual.is_file() or digest(actual) != expected:
            errors.append(f"input changed: {path}")
    for name, expected in receipt["output_sha256"].items():
        actual = receipt_path.parent / name
        if not actual.is_file() or digest(actual) != expected:
            errors.append(f"output changed: {name}")
    if not receipt["all_passed"]:
        errors.append("recorded tests did not all pass")
    print(json.dumps(dict(fresh_and_passed=not errors, errors=errors), ensure_ascii=False))
    return bool(errors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New directory under research/notification; existing directories are refused")
    parser.add_argument("--verify", type=Path, help="Verify an existing receipt without rerunning tests")
    args = parser.parse_args()
    if args.verify:
        return verify(args.verify.resolve())
    stamp = datetime.now(timezone(timedelta(hours=8)))
    output = (args.output or HERE / "results" / stamp.strftime("run-%Y%m%d-%H%M%S-%f")).resolve()
    if not output.is_relative_to(HERE) or output == HERE:
        parser.error("output must stay within research/notification")
    output.mkdir(parents=True, exist_ok=False)
    frozen = json.loads((BASELINE.parent / "snapshot.json").read_text(encoding="utf-8-sig"))
    if digest(BASELINE) != frozen["files"]["power-notification-design.md"]:
        raise RuntimeError("frozen input does not match baseline manifest")
    before = {str(path.relative_to(ROOT)).replace("\\", "/"): digest(path) for path in FINGERPRINTS}
    suite = unittest.defaultTestLoader.discover(str(HERE), pattern="test_protocol.py")
    log = io.StringIO()
    result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=NamedResults).run(suite)
    (output / "tests.txt").write_text(log.getvalue(), encoding="utf-8")
    outputs = ["tests.txt"]
    if result.wasSuccessful():
        save_json(output / "trace.json", sample_trace(output))
        outputs += ["trace.json", "device.sqlite3", "service.sqlite3", "receiver.sqlite3"]
    after = {str(path.relative_to(ROOT)).replace("\\", "/"): digest(path) for path in FINGERPRINTS}
    passed = result.wasSuccessful() and before == after
    receipt = dict(kind="notification_lane_only_not_T07_acceptance", generated_at=stamp.isoformat(),
                   python_version=platform.python_version(), sqlite_version=sqlite3.sqlite_version,
                   command=[sys.executable, "-B", "-X", "utf8", *sys.argv], synthetic=True, seed=None,
                   parameters=dict(max_ttl_s=MAX_TTL_S, retry_s=RETRY_S), tests_run=result.testsRun,
                   failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
                   passed_test_ids=result.passed_ids, all_passed=passed,
                   inputs_unchanged_during_run=before == after, input_sha256=before,
                   output_sha256={name: digest(output / name) for name in outputs})
    save_json(output / "receipt.json", receipt)
    print(log.getvalue(), end="")
    print(f"Evidence: {output / 'receipt.json'}")
    return not passed


if __name__ == "__main__":
    raise SystemExit(main())

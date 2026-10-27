"""Coordinator-owned T07 receipt. Standard-library offline checks only."""

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import sqlite3
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / "docs/verification/notification-tests.json"
ARTIFACTS = ROOT / "research/notification_t07/results/accepted-v2"
GROUPS = {"protocol", "boundaries"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes():
    paths = set()
    worker_receipt = ARTIFACTS / "receipt.json"
    if worker_receipt.is_file():
        inputs = json.loads(worker_receipt.read_text(encoding="utf-8")).get("input_sha256", {})
        for name in inputs:
            path = (ROOT / name).resolve()
            if not path.is_relative_to(ROOT):
                raise ValueError("Notification input path outside workspace")
            paths.add(path)
    for folder in ("theory", "research/state_machine", "research/notification_t07"):
        paths.update((ROOT / folder).glob("*.py"))
    for pattern in ("*.c", "*.h"):
        paths.update((ROOT / "firmware/core").glob(pattern))
    paths.update((ROOT / "tools").glob("test_notification_*.py"))
    paths.update(ROOT / name for name in (
        "tools/notification_acceptance.py", "tools/project_status.py",
        "docs/NOTIFICATION-CONTRACT.md", "docs/STATE-MACHINE.md", "docs/MEASUREMENT-CONTRACT.md",
        "docs/CORE-INTERFACE.md", "docs/team/assignments/notification-t07.md",
        "power-notification-design.md"))
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(paths)}


def artifact_hashes():
    return {p.relative_to(ROOT).as_posix(): digest(p)
            for p in sorted(ARTIFACTS.rglob("*")) if p.is_file()}


def parse_unittest(result):
    output = result.stdout + result.stderr
    match = re.search(r"^Ran (\d+) tests? in ", output, re.MULTILINE)
    count = int(match.group(1)) if match else 0
    # A skipped/expected-failure suite is not a complete acceptance run.
    passed = result.returncode == 0 and count > 0 and bool(re.search(r"^OK\s*$", output, re.MULTILINE))
    return dict(passed=passed, exit_code=result.returncode, test_count=count, output=output)


def verify_trace():
    """Independent DB-to-trace and full deterministic chain recomputation."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from research.notification_t07.scenarios import check_replays, run_trace
    trace = json.loads((ARTIFACTS / "trace.json").read_text(encoding="utf-8"))
    counts = check_replays(trace)
    databases = {"device.sqlite3": "device", "service.sqlite3": "service",
                 "receiver.sqlite3": "receiver_a", "receiver-b.sqlite3": "receiver_b"}
    for filename, store in databases.items():
        with closing(sqlite3.connect((ARTIFACTS / filename).as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError(f"Database integrity failed: {filename}")
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            actual = {table: [dict(row) for row in db.execute(f'SELECT * FROM "{table}" ORDER BY rowid')] for table in tables}
            if actual != trace["final_stores"][store]:
                raise ValueError(f"Database/trace mismatch: {filename}")
    with tempfile.TemporaryDirectory(prefix="hanger-t07-coordinator-") as temp:
        if run_trace(temp) != trace:
            raise ValueError("Full notification trace differs on recomputation")
    return dict(passed=True, replay=counts, databases_checked=len(databases),
                checkpoints=len(trace["steps"]), cumulative_simulated_alerts=trace["cumulative_simulated_alerts"])


def validate_receipt(receipt, sources, artifacts):
    errors = []
    groups = receipt.get("groups", {})
    if not isinstance(groups, dict):
        groups = {}
    if (receipt.get("suite") != "notification" or receipt.get("passed") is not True
            or receipt.get("exit_code") != 0 or receipt.get("sources_stable_during_run") is not True
            or receipt.get("artifacts_stable_during_run") is not True
            or receipt.get("synthetic") != "SYNTHETIC_UNCALIBRATED"
            or receipt.get("collection_authorized") is not False):
        errors.append("T07 主记录没有证明完整离线验收通过")
    if set(groups) != GROUPS or any(
            not isinstance(g, dict) or g.get("passed") is not True or g.get("exit_code") != 0
            or type(g.get("test_count")) is not int or g["test_count"] <= 0 for g in groups.values()):
        errors.append("T07 检查组缺失或未通过")
    else:
        count = receipt.get("test_count")
        if type(count) is not int or count != sum(g["test_count"] for g in groups.values()):
            errors.append("T07 检查数量不一致")
    if not sources or receipt.get("source_sha256") != sources:
        errors.append("T07 通知验收来源已变化；需核对并重跑对应检查")
    if (not artifacts or not any(p.endswith("/receipt.json") for p in artifacts)
            or receipt.get("artifact_sha256") != artifacts):
        errors.append("T07 通知场景证据已变化或缺失")
    verification = receipt.get("artifact_verification", {})
    if not isinstance(verification, dict) or verification.get("exit_code") != 0:
        errors.append("T07 场景来源核验未通过")
    trace_check = receipt.get("trace_verification", {})
    if not isinstance(trace_check, dict) or trace_check.get("passed") is not True:
        errors.append("T07 连续链路／数据库复算未通过")
    return errors


def inspect():
    if not RECEIPT.exists():
        return ["T07 缺少协调通知检查记录；需要运行 --record-notification-tests"], None
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    return validate_receipt(receipt, source_hashes(), artifact_hashes()), receipt.get("test_count")


def run_command(command, timeout=180):
    result = subprocess.run(command, cwd=ROOT, capture_output=True, encoding="utf-8",
                            errors="replace", timeout=timeout)
    return result


def record():
    receipt = dict(schema_version=1, suite="notification", passed=False, exit_code=1,
                   synthetic="SYNTHETIC_UNCALIBRATED", collection_authorized=False,
                   recorded_at=datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
                   python_version=sys.version.split()[0], working_directory=str(ROOT),
                   sources_stable_during_run=False, artifacts_stable_during_run=False,
                   source_sha256={}, artifact_sha256={}, groups={}, test_count=0,
                   scope="T07 local SQLite/process recovery and reference-state integration; no real phone, MCU flash, network or garment evidence.")
    try:
        before, artifacts = source_hashes(), artifact_hashes()
        receipt.update(source_sha256=before, artifact_sha256=artifacts)
        command = [sys.executable, "-B", "-X", "utf8", "research/notification_t07/run_checks.py",
                   "--verify", "research/notification_t07/results/accepted-v2/receipt.json"]
        result = run_command(command)
        receipt["artifact_verification"] = dict(command=command, exit_code=result.returncode,
                                                output=result.stdout + result.stderr)
        if result.returncode:
            raise ValueError("T07 saved scenario verification failed")
        receipt["trace_verification"] = verify_trace()
        for name, folder, pattern in (("protocol", "research/notification_t07", "test_*.py"),
                                      ("boundaries", "tools", "test_notification_*.py")):
            command = [sys.executable, "-B", "-X", "utf8", "-m", "unittest", "discover",
                       "-s", folder, "-p", pattern, "-v"]
            group = parse_unittest(run_command(command))
            group["command"] = command
            receipt["groups"][name] = group
        receipt["test_count"] = sum(g["test_count"] for g in receipt["groups"].values())
        receipt["sources_stable_during_run"] = before == source_hashes()
        receipt["artifacts_stable_during_run"] = artifacts == artifact_hashes()
        receipt["passed"] = (receipt["sources_stable_during_run"] and receipt["artifacts_stable_during_run"]
                             and bool(artifacts) and all(g["passed"] for g in receipt["groups"].values()))
        receipt["exit_code"] = 0 if receipt["passed"] else 1
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, subprocess.TimeoutExpired) as exc:
        receipt["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        RECEIPT.parent.mkdir(parents=True, exist_ok=True)
        RECEIPT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()
    if args.record:
        receipt = record()
        print(json.dumps({"passed": receipt["passed"], "test_count": receipt["test_count"],
                          "error": receipt.get("error")}, ensure_ascii=False))
        return 0 if receipt["passed"] else 1
    errors, count = inspect()
    print(json.dumps({"fresh_and_passed": not errors, "test_count": count, "errors": errors}, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

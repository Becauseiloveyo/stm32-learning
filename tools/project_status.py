"""Restore project state from local files and detect stale evidence.

Default is read-only. Each --record-*-tests flag runs only its named suite
and records a separate source-bound receipt.
This tool does not regenerate simulations, modify status, or deploy anything.
"""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / "docs/verification/latest-tests.json"
PROBE_RECEIPT = ROOT / "docs/verification/probe-tests.json"
VALIDITY_RECEIPT = ROOT / "docs/verification/validity-tests.json"
STATE_MACHINE_RECEIPT = ROOT / "docs/verification/state-machine-tests.json"
ENERGY_RECEIPT = ROOT / "docs/verification/energy-tests.json"
CORE_RECEIPT = ROOT / "docs/verification/core-tests.json"
NOTIFICATION_RECEIPT = ROOT / "docs/verification/notification-tests.json"


def project_path(relative):
    candidate = (ROOT / relative).resolve()
    if not candidate.is_relative_to(ROOT):
        raise ValueError(f"Path outside project: {relative}")
    return candidate


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_test_sources(suite="model"):
    if suite == "core":
        # Shared exact scope; build outputs are intentionally excluded.
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from firmware.host.run import source_hashes
        return source_hashes()
    # Update this scope alongside the test command when future tasks add C/config tests.
    paths = set((ROOT / "theory").rglob("*.py"))
    if suite == "probe":
        paths.update((ROOT / "research/probe").rglob("*.py"))
        paths.update(p for p in (ROOT / "docs/team/baseline-v1").rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts)
        paths.update(ROOT / p for p in ("docs/MEASUREMENT-CONTRACT.md", "docs/PROBE-CONCEPTS.md"))
    elif suite == "validity":
        paths.update((ROOT / "research/validity").rglob("*.py"))
        paths.update(ROOT / p for p in ("docs/MEASUREMENT-CONTRACT.md", "docs/PROBE-CONCEPTS.md",
                                       "docs/MODEL-VALIDITY.md", "research/validity/sources.md"))
    elif suite == "state_machine":
        paths.update((ROOT / "research/state_machine").rglob("*.py"))
        paths.update(ROOT / p for p in ("docs/MEASUREMENT-CONTRACT.md", "docs/MODEL-VALIDITY.md",
                                       "docs/STATE-MACHINE.md", "tools/verify_state_machine_boundaries.py"))
    elif suite == "energy":
        paths.update((ROOT / "research/state_machine").rglob("*.py"))
        paths.update((ROOT / "research/energy_t05").rglob("*.py"))
        paths.update(ROOT / p for p in ("analysis/energy_budget.py", "power-notification-design.md",
                                       "docs/MEASUREMENT-CONTRACT.md", "docs/STATE-MACHINE.md",
                                       "docs/ENERGY-SCHEDULING.md", "tools/verify_energy_boundaries.py"))
    elif suite != "model":
        raise ValueError(f"Unknown test suite: {suite}")
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(paths)}


def record_tests(suite="model"):
    if suite == "notification":
        from notification_acceptance import record
        receipt = record()
        print(f"通知主检查记录已保存；{receipt['test_count']} 项；passed={receipt['passed']}")
        if not receipt["passed"]:
            raise ValueError("T07 离线验收未通过；详见 docs/verification/notification-tests.json")
        return
    if suite == "core":
        command = [sys.executable, "-B", "-X", "utf8", "firmware/host/run.py", "--record"]
        try:
            result = subprocess.run(command, cwd=ROOT, capture_output=True,
                                    encoding="utf-8", errors="replace", timeout=240)
        except subprocess.TimeoutExpired as exc:
            # A killed subprocess cannot leave an older passing receipt usable.
            CORE_RECEIPT.write_text(json.dumps({"suite": "core", "passed": False,
                "exit_code": 124, "test_count": 0, "source_sha256": current_test_sources("core"),
                "error": "Host C build/verification timed out after 240 seconds."}, indent=2) + "\n",
                encoding="utf-8")
            raise ValueError("C 主机构建／验证超时；未记录通过") from exc
        if result.returncode != 0:
            raise ValueError("C 主机构建／验证未通过：" + result.stdout[-5000:] + result.stderr[-2000:])
        print("C 主机构建和对照检查已完成；收据：docs/verification/core-tests.json")
        return
    suites = {
        "model": (RECEIPT, "theory", "test_model.py",
                  "Model implementation and decision invariants only; no hardware validation."),
        "probe": (PROBE_RECEIPT, "research/probe", "test_*.py",
                  "Probe research and T01 protocol integration; synthetic, no hardware validation."),
        "validity": (VALIDITY_RECEIPT, "research/validity", "test_*.py",
                     "T03 sensitivity, conservation and validity checks; synthetic, no hardware validation."),
        "state_machine": (STATE_MACHINE_RECEIPT, "research/state_machine", "test_*.py",
                          "T04 multicycle evidence boundaries and replay; synthetic, no hardware validation."),
        "energy": (ENERGY_RECEIPT, "research/energy_t05", "test_*.py",
                   "T05 integrated scheduling, energy accounting and state transitions; synthetic, no hardware validation.")
    }
    if suite not in suites:
        raise ValueError(f"Unknown test suite: {suite}")
    receipt_path, directory, pattern, scope = suites[suite]
    command = [sys.executable, "-X", "utf8", "-m", "unittest", "discover",
               "-s", directory, "-p", pattern, "-v"]
    before = current_test_sources(suite)
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True,
                                encoding="utf-8", errors="replace", timeout=120)
        output, code = result.stdout + result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        output, code = "Timed out after 120 seconds; checks are not recorded as passed.", 124
    after = current_test_sources(suite)
    match = re.search(r"Ran (\d+) tests? in", output)
    count = int(match.group(1)) if match else 0
    stable = before == after
    receipt = {
        "schema_version": 1,
        "suite": suite,
        "recorded_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "command": command,
        "working_directory": str(ROOT),
        "python_version": sys.version.split()[0],
        "exit_code": code,
        "test_count": count,
        "passed": code == 0 and count > 0 and stable,
        "sources_stable_during_run": stable,
        "source_sha256": before,
        "output": output,
        "scope": scope
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"测试记录已保存：{receipt_path.relative_to(ROOT)}；{count} 项；passed={receipt['passed']}")
    if not receipt["passed"]:
        print(output)


def inspect():
    issues = []
    state = json.loads((ROOT / "docs/STATUS.json").read_text(encoding="utf-8"))
    if state.get("schema_version") != 1:
        raise ValueError("Unsupported STATUS schema_version")
    tasks = state["tasks"]
    valid_states = {"done", "ready", "pending", "in_progress", "deferred"}
    roadmap = (ROOT / "docs/ROADMAP.md").read_text(encoding="utf-8")
    sections = set(re.findall(r"^## ([TH]\d+)\b", roadmap, re.MULTILINE))
    for task_id, task in tasks.items():
        if task_id not in sections:
            issues.append(f"任务 {task_id} 没有对应路线图小节")
        if task["status"] not in valid_states:
            issues.append(f"任务 {task_id} 状态无效")
        for dependency in task["depends_on"]:
            if dependency not in tasks:
                issues.append(f"任务 {task_id} 的依赖 {dependency} 不存在")
            elif task["status"] in {"done", "ready", "in_progress"} and tasks[dependency]["status"] != "done":
                issues.append(f"任务 {task_id} 的依赖 {dependency} 尚未完成")
    running = [name for name, task in tasks.items() if task["status"] == "in_progress"]
    active = state["active_task"]
    if running != ([active] if active else []):
        issues.append("active_task 与 in_progress 任务不一致，或存在多个活跃任务")
    next_task = state["next_task"]
    if next_task is not None:
        if next_task not in tasks or tasks[next_task]["status"] not in {"ready", "in_progress"}:
            issues.append("next_task 不是可继续的任务")
        elif any(tasks.get(dep, {}).get("status") != "done" for dep in tasks[next_task]["depends_on"]):
            issues.append("next_task 的前置任务未完成")
        if active and next_task != active:
            issues.append("仍有活跃任务，但 next_task 指向其他任务")

    required = ["README.md", "AGENTS.md", "docs/PROJECT.md", "docs/DECISIONS.md",
                "theory/README.md", "analysis/energy_budget.py"]
    required += state.get("last_handoff", {}).get("changed_files", [])
    for relative in sorted(set(required)):
        if not project_path(relative).is_file():
            issues.append(f"记录引用的文件不存在：{relative}")

    manifest_path = ROOT / "theory/results/manifest.json"
    simulation_current = False
    if not manifest_path.exists():
        issues.append("缺少仿真 manifest")
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        hashes = manifest.get("source_sha256", {})
        stale = []
        for name, expected in hashes.items():
            path = project_path(f"theory/{name}")
            if not path.is_file() or digest(path) != expected:
                stale.append(name)
        if not hashes:
            issues.append("仿真 manifest 没有源码指纹")
        elif stale:
            issues.append("仿真来源已变化，结果需核对／重生成：" + ", ".join(stale))
        else:
            simulation_current = True

    receipt_current, count = False, None
    if not RECEIPT.exists():
        issues.append("缺少可核对的测试记录；需要时运行 --record-tests")
    else:
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
        count = receipt.get("test_count")
        if receipt.get("source_sha256") != current_test_sources():
            issues.append("测试记录与当前源码不匹配；不能沿用旧的通过结论")
        elif not receipt.get("passed") or receipt.get("exit_code") != 0 or not isinstance(count, int) or count <= 0:
            issues.append("最新记录未证明测试通过")
        else:
            receipt_current = True

    print(f"项目阶段：{state['phase']}")
    print(f"当前任务：{active or '无'}；下一任务：{next_task or '无'}")
    print(f"模型源码与生成清单匹配：{simulation_current}")
    print(f"测试记录与当前源码匹配且通过：{receipt_current}；记录数量：{count}")
    if tasks.get("T02", {}).get("status") == "done" or PROBE_RECEIPT.exists():
        probe_current, probe_count = False, None
        if not PROBE_RECEIPT.exists():
            issues.append("T02 缺少协调测试记录；需要运行 --record-probe-tests")
        else:
            probe = json.loads(PROBE_RECEIPT.read_text(encoding="utf-8"))
            probe_count = probe.get("test_count")
            if probe.get("source_sha256") != current_test_sources("probe"):
                issues.append("气路检查记录与当前输入不匹配；需要核对／重跑对应检查")
            elif (probe.get("suite") != "probe" or not probe.get("passed")
                  or probe.get("exit_code") != 0 or type(probe_count) is not int or probe_count <= 0):
                issues.append("气路最新记录未证明测试通过")
            else:
                probe_current = True
        print(f"气路检查记录与当前输入匹配且通过：{probe_current}；记录数量：{probe_count}")
        artifact_manifest = ROOT / "research/probe/results-t02/manifest.json"
        artifact_issues = []
        if not artifact_manifest.exists():
            artifact_issues.append("缺少 T02 产物清单")
        else:
            artifacts = json.loads(artifact_manifest.read_text(encoding="utf-8"))
            for key, prefix in (("input_sha256", ""), ("source_sha256", "research/probe/"),
                                ("output_sha256", "research/probe/results-t02/")):
                hashes = artifacts.get(key, {})
                if not hashes:
                    artifact_issues.append(f"T02 清单缺少 {key}")
                for name, expected in hashes.items():
                    path = project_path(prefix + name)
                    if not path.is_file() or digest(path) != expected:
                        artifact_issues.append(f"T02 证据已变化：{prefix + name}")
        issues.extend(artifact_issues)
        print(f"T02 清单声明的输入、源码、产物哈希匹配：{not artifact_issues}")
    if tasks.get("T03", {}).get("status") == "done" or VALIDITY_RECEIPT.exists():
        validity_current, validity_count = False, None
        if not VALIDITY_RECEIPT.exists():
            issues.append("T03 缺少协调测试记录；需要运行 --record-validity-tests")
        else:
            validity = json.loads(VALIDITY_RECEIPT.read_text(encoding="utf-8"))
            validity_count = validity.get("test_count")
            if validity.get("source_sha256") != current_test_sources("validity"):
                issues.append("误差检查记录与当前输入不匹配；需要核对／重跑对应检查")
            elif (validity.get("suite") != "validity" or not validity.get("passed")
                  or validity.get("exit_code") != 0 or type(validity_count) is not int or validity_count <= 0):
                issues.append("误差最新记录未证明测试通过")
            else:
                validity_current = True
        print(f"误差检查记录与当前输入匹配且通过：{validity_current}；记录数量：{validity_count}")
        validity_manifest = ROOT / "research/validity/results/manifest.json"
        artifact_issues = []
        if not validity_manifest.exists():
            artifact_issues.append("缺少 T03 产物清单")
        else:
            artifacts = json.loads(validity_manifest.read_text(encoding="utf-8"))
            for key, prefix in (("input_sha256", ""), ("source_sha256", "research/validity/"),
                                ("output_sha256", "research/validity/results/")):
                hashes = artifacts.get(key, {})
                if not hashes:
                    artifact_issues.append(f"T03 清单缺少 {key}")
                for name, expected in hashes.items():
                    path = project_path(prefix + name)
                    if not path.is_file() or digest(path) != expected:
                        artifact_issues.append(f"T03 证据已变化：{prefix + name}")
        issues.extend(artifact_issues)
        print(f"T03 清单声明的输入、源码、产物哈希匹配：{not artifact_issues}")
    if tasks.get("T04", {}).get("status") == "done" or STATE_MACHINE_RECEIPT.exists():
        machine_current, machine_count = False, None
        if not STATE_MACHINE_RECEIPT.exists():
            issues.append("T04 缺少协调测试记录；需要运行 --record-state-machine-tests")
        else:
            machine = json.loads(STATE_MACHINE_RECEIPT.read_text(encoding="utf-8"))
            machine_count = machine.get("test_count")
            if machine.get("source_sha256") != current_test_sources("state_machine"):
                issues.append("状态机检查记录与当前输入不匹配；需要核对／重跑对应检查")
            elif (machine.get("suite") != "state_machine" or not machine.get("passed")
                  or machine.get("exit_code") != 0 or type(machine_count) is not int or machine_count <= 0):
                issues.append("状态机最新记录未证明测试通过")
            else:
                machine_current = True
        print(f"状态机检查记录与当前输入匹配且通过：{machine_current}；记录数量：{machine_count}")
        machine_manifest = ROOT / "research/state_machine/results/manifest.json"
        artifact_issues = []
        if not machine_manifest.exists():
            artifact_issues.append("缺少 T04 产物清单")
        else:
            artifacts = json.loads(machine_manifest.read_text(encoding="utf-8"))
            for key, prefix in (("input_sha256", ""), ("source_sha256", "research/state_machine/"),
                                ("output_sha256", "research/state_machine/results/")):
                hashes = artifacts.get(key, {})
                if not hashes:
                    artifact_issues.append(f"T04 清单缺少 {key}")
                for name, expected in hashes.items():
                    path = project_path(prefix + name)
                    if not path.is_file() or digest(path) != expected:
                        artifact_issues.append(f"T04 证据已变化：{prefix + name}")
        issues.extend(artifact_issues)
        print(f"T04 清单声明的输入、源码、产物哈希匹配：{not artifact_issues}")
    if tasks.get("T05", {}).get("status") == "done" or ENERGY_RECEIPT.exists():
        energy_current, energy_count = False, None
        if not ENERGY_RECEIPT.exists():
            issues.append("T05 缺少协调测试记录；需要运行 --record-energy-tests")
        else:
            energy = json.loads(ENERGY_RECEIPT.read_text(encoding="utf-8"))
            energy_count = energy.get("test_count")
            if energy.get("source_sha256") != current_test_sources("energy"):
                issues.append("能量调度检查记录与当前输入不匹配；需要核对／重跑对应检查")
            elif (energy.get("suite") != "energy" or not energy.get("passed")
                  or energy.get("exit_code") != 0 or type(energy_count) is not int or energy_count <= 0):
                issues.append("能量调度最新记录未证明测试通过")
            else:
                energy_current = True
        print(f"能量调度检查记录与当前输入匹配且通过：{energy_current}；记录数量：{energy_count}")
        energy_manifest = ROOT / "research/energy_t05/results/manifest.json"
        artifact_issues = []
        if not energy_manifest.exists():
            artifact_issues.append("缺少 T05 产物清单")
        else:
            artifacts = json.loads(energy_manifest.read_text(encoding="utf-8"))
            for key, prefix in (("input_sha256", ""), ("source_sha256", "research/energy_t05/"),
                                ("output_sha256", "research/energy_t05/results/")):
                hashes = artifacts.get(key, {})
                if not hashes:
                    artifact_issues.append(f"T05 清单缺少 {key}")
                for name, expected in hashes.items():
                    path = project_path(prefix + name)
                    if not path.is_file() or digest(path) != expected:
                        artifact_issues.append(f"T05 证据已变化：{prefix + name}")
        issues.extend(artifact_issues)
        print(f"T05 清单声明的输入、源码、产物哈希匹配：{not artifact_issues}")
    if tasks.get("T06", {}).get("status") == "done" or CORE_RECEIPT.exists():
        core_current, core_count = False, None
        if not CORE_RECEIPT.exists():
            issues.append("T06 缺少协调 C 编译／对照记录；需要运行 --record-core-tests")
        else:
            core = json.loads(CORE_RECEIPT.read_text(encoding="utf-8"))
            core_count = core.get("test_count")
            if core.get("source_sha256") != current_test_sources("core"):
                issues.append("C 核心检查记录与当前来源不匹配；需要核对／重跑对应检查")
            elif (core.get("suite") != "core" or core.get("passed") is not True
                  or core.get("exit_code") != 0 or type(core_count) is not int or core_count <= 0
                  or core.get("sources_stable_during_run") is not True
                  or not core.get("compiler", {}).get("target")
                  or set(core.get("results", {})) != {"measurement", "energy", "integration"}
                  or any(r.get("passed") is not True or type(r.get("check_count")) is not int
                         or r["check_count"] <= 0 for r in core.get("results", {}).values())
                  or sum(r["check_count"] for r in core["results"].values()) != core_count):
                issues.append("C 核心最新记录未证明完整主机编译与对照通过")
            else:
                core_current = True
        print(f"C 核心编译／对照记录与当前来源匹配且通过：{core_current}；记录检查数量：{core_count}")
    if tasks.get("T07", {}).get("status") == "done" or NOTIFICATION_RECEIPT.exists():
        from notification_acceptance import inspect as inspect_notifications
        notification_issues, notification_count = inspect_notifications()
        issues.extend(notification_issues)
        print(f"通知检查来源及场景产物匹配且通过：{not notification_issues}；记录数量：{notification_count}")
    print("注意：只读恢复检查不重跑计算或 C 编译；另核对 T02–T05、T07 产物哈希，不能据此证明实物性能。")
    print("继续动作：" + state.get("last_handoff", {}).get("resume_action", "请补充 resume_action"))
    for issue in issues:
        print("待处理：" + issue)
    if not issues:
        print("项目入口、任务状态和已记录证据的一致性检查通过。")
    return issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-tests", action="store_true", help="run existing model checks and replace the local test receipt")
    parser.add_argument("--record-probe-tests", action="store_true", help="run probe research/integration checks and record their separate receipt")
    parser.add_argument("--record-validity-tests", action="store_true", help="run T03 validity checks and record their separate receipt")
    parser.add_argument("--record-state-machine-tests", action="store_true", help="run T04 state machine checks and record their separate receipt")
    parser.add_argument("--record-energy-tests", action="store_true", help="run T05 energy/scheduling checks and record their separate receipt")
    parser.add_argument("--record-core-tests", action="store_true", help="compile T06 C core and run host/reference checks, then record their receipt")
    parser.add_argument("--record-notification-tests", action="store_true", help="run T07 offline protocol and independent acceptance checks")
    parser.add_argument("--check", action="store_true", help="exit nonzero if state or evidence checks find issues")
    args = parser.parse_args()
    try:
        if args.record_tests:
            record_tests()
        if args.record_probe_tests:
            record_tests("probe")
        if args.record_validity_tests:
            record_tests("validity")
        if args.record_state_machine_tests:
            record_tests("state_machine")
        if args.record_energy_tests:
            record_tests("energy")
        if args.record_core_tests:
            record_tests("core")
        if args.record_notification_tests:
            record_tests("notification")
        issues = inspect()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"无法恢复项目状态：{exc}", file=sys.stderr)
        return 1
    return 1 if issues and (args.check or args.record_tests or args.record_probe_tests
                           or args.record_validity_tests or args.record_state_machine_tests or args.record_energy_tests
                           or args.record_core_tests or args.record_notification_tests) else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Build and execute T06 host checks; record only actual, source-stable results.

No tool downloads, device I/O, deployment or upstream simulation generation.
"""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
RECEIPT = ROOT / "docs/verification/core-tests.json"
BUILD = ROOT / "firmware/host/build"


def source_hashes():
    paths = set()
    for directory in ("theory", "research/state_machine", "research/energy_t05",
                      "firmware/core", "firmware/host"):
        paths.update(p for p in (ROOT / directory).rglob("*")
                     if p.is_file() and p.suffix in (".py", ".c", ".h")
                     and "build" not in p.relative_to(ROOT).parts)
    for name in ("docs/MEASUREMENT-CONTRACT.md", "docs/STATE-MACHINE.md",
                 "docs/ENERGY-SCHEDULING.md", "docs/CORE-INTERFACE.md",
                 "research/state_machine/results/events.json",
                 "research/state_machine/results/manifest.json",
                 "research/energy_t05/results/inputs.json",
                 "research/energy_t05/results/manifest.json",
                 "docs/team/assignments/measurement-t06.md",
                 "docs/team/assignments/energy-t06.md",
                 "tools/project_status.py"):
        paths.add(ROOT / name)
    paths.update((ROOT / "research/energy_t05/results").glob("*-ledger.jsonl"))
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths)}


def resolve_compiler(explicit=None):
    candidates = [explicit] if explicit else [os.environ.get("CC"), shutil.which("gcc"),
                                             "C:/msys64=/ucrt64/bin/gcc.exe"]
    for value in candidates:
        if value:
            p = Path(value)
            if not p.is_file():
                found = shutil.which(value)
                p = Path(found) if found else p
            if p.is_file():
                return str(p.resolve())
    raise FileNotFoundError("No host GCC found. Supply --compiler with an existing executable; no download is performed.")


def compiler_identity(compiler):
    env = dict(os.environ)
    env["PATH"] = str(Path(compiler).parent) + os.pathsep + env.get("PATH", "")
    info = {}
    for flag, key in (("--version", "version"), ("-dumpmachine", "target")):
        proc = subprocess.run([compiler, flag], cwd=ROOT, env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=30,
                              check=True)
        info[key] = proc.stdout.strip()
    info.update(path=compiler, executable_sha256=hashlib.sha256(Path(compiler).read_bytes()).hexdigest())
    return info


def run(compiler=None, record=False):
    # A missing declared source is an error, never an empty successful suite.
    before = source_hashes()
    receipt = {"schema_version": 1, "suite": "core", "passed": False,
               "recorded_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
               "command": [sys.executable, "-B", "-X", "utf8", *sys.argv],
               "working_directory": str(ROOT), "python_version": sys.version.split()[0],
               "test_count": 0, "source_sha256": before, "results": {}, "exit_code": 1,
               "scope": "T06 host-compiled C against synthetic Python reference vectors; no STM32/device validation."}
    sys.path.insert(0, str(ROOT))
    try:
        compiler = resolve_compiler(compiler)
        receipt["compiler"] = compiler_identity(compiler)
        for name in ("measurement", "energy", "integration"):
            module = importlib.import_module(f"firmware.host.{name}_validation")
            result = module.run_validation(compiler, BUILD / name)
            if not isinstance(result, dict):
                raise ValueError(f"{name}: expected structured validation result")
            receipt["results"][name] = result
            count = result.get("check_count")
            if result.get("passed") is not True or type(count) is not int or count <= 0:
                raise ValueError(f"{name}: no positive successful check count")
            receipt["test_count"] += count
        receipt["exit_code"] = 0
    except Exception:
        receipt["error"] = traceback.format_exc()
    after = source_hashes()
    receipt["sources_stable_during_run"] = before == after
    receipt["passed"] = (receipt["exit_code"] == 0 and receipt["test_count"] > 0
                         and receipt["sources_stable_during_run"])
    if not receipt["passed"]:
        receipt["exit_code"] = 1
    if record:
        RECEIPT.parent.mkdir(parents=True, exist_ok=True)
        RECEIPT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                           encoding="utf-8")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", help="existing GCC executable path")
    parser.add_argument("--record", action="store_true", help="replace coordinator C test receipt")
    args = parser.parse_args()
    try:
        result = run(args.compiler, args.record)
    except (OSError, ValueError) as exc:
        print(f"Cannot run C checks: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({k: v for k, v in result.items() if k != "source_sha256"},
                     ensure_ascii=False, indent=2, allow_nan=False))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())

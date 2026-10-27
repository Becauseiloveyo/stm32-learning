"""Coordinator-owned linked C boundary checks and build evidence."""

import json
import ctypes
import math
import os
from pathlib import Path
import random
import statistics
import subprocess

from firmware.host.test_runner import run_checks

ROOT = Path(__file__).resolve().parents[2]


def run_validation(compiler, build_dir):
    build_dir = Path(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PATH"] = str(Path(compiler).parent) + os.pathsep + env.get("PATH", "")
    commands = []

    def relative(path):
        # Existing Windows binutils cannot create Unicode absolute output paths.
        return os.path.relpath(Path(path).resolve(), ROOT)

    def invoke(args):
        commands.append([str(a) for a in args])
        p = subprocess.run(commands[-1], cwd=ROOT, env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60)
        if p.returncode:
            raise AssertionError(f"Command failed ({p.returncode}): {commands[-1]}\n{p.stdout}\n{p.stderr}")
        return p.stdout

    sources = sorted((ROOT / "firmware/core").glob("*.c"))
    if not sources:
        raise AssertionError("No C core sources")
    common = [compiler, "-std=c99", "-Wall", "-Wextra", "-Werror", "-pedantic",
              "-fno-fast-math", "-ffp-contract=off", "-I", "firmware/core"]
    runs = {}
    for optimization in ("-O0", "-O2"):
        exe = build_dir / ("integration" + optimization[1:] + ".exe")
        invoke([*common, optimization, *map(relative, sources),
                "firmware/host/integration_tests.c", "-lm", "-o", relative(exe)])
        result = json.loads(invoke([str(exe.resolve())]))
        if result.get("passed") is not True or type(result.get("check_count")) is not int or result["check_count"] <= 0:
            raise AssertionError(f"Invalid integration output: {result}")
        runs[optimization] = result
    if runs["-O0"] != runs["-O2"]:
        raise AssertionError("Coordinator boundary results changed with optimization")
    # The numerical fix introduces a nontrivial integer accumulator; compare it
    # independently against statistics.mean's exact rational summation, including
    # cancellation, ties and subnormals, not only generated garment curves.
    dll = build_dir / ("integration_mean_" + str(os.getpid()) + (".dll" if os.name == "nt" else ".so"))
    invoke([*common, "-O2", "-shared", "firmware/host/integration_mean_bridge.c", "-lm", "-o", relative(dll)])
    search = os.add_dll_directory(str(Path(compiler).parent)) if hasattr(os,"add_dll_directory") else None
    library = ctypes.CDLL(str(dll.resolve()))
    mean = library.integration_mean
    mean.argtypes = [ctypes.POINTER(ctypes.c_double), ctypes.c_size_t, ctypes.POINTER(ctypes.c_double)]
    mean.restype = ctypes.c_int
    tiny = math.ulp(0.0)
    vectors = [[0.0], [-0.0], [tiny,0.0], [tiny,tiny,0.0], [-tiny,0.0],
               [16384.0]*512, [-16384.0]*512, [16384.0,-16384.0,tiny],
               [1.0,math.nextafter(1.0,math.inf)], [1.0,math.nextafter(1.0,-math.inf)]]
    rng = random.Random(2026102606)
    for _ in range(250):
        values = [math.ldexp(rng.uniform(-1,1),rng.randint(-1073,14)) for _ in range(rng.randint(1,512))]
        vectors.append(values)
        vectors.append(list(reversed(values)))
    for i, values in enumerate(vectors):
        raw = (ctypes.c_double*len(values))(*values)
        out = ctypes.c_double()
        if mean(raw,len(values),ctypes.byref(out)) != 1 or out.value != statistics.mean(values):
            raise AssertionError(f"Exact mean differs from rational oracle at vector {i}")
    if search is not None:
        search.close()
    object_files = []
    for source in sources:
        obj = build_dir / (source.stem + ".o")
        invoke([*common, "-O2", "-fstack-usage", "-c", relative(source), "-o", relative(obj)])
        object_files.append(obj)
    stack = {p.name: p.read_text(encoding="utf-8") for p in sorted(build_dir.glob("*.su"))}
    if not stack:
        raise AssertionError("No compiler stack usage evidence")
    nm = Path(compiler).with_name("nm.exe" if os.name == "nt" else "nm")
    symbols = invoke([str(nm), "-u", *map(relative, object_files)])
    for forbidden in ("malloc", "calloc", "realloc", "alloca"):
        if any(line.strip().split()[-1].lstrip("_") == forbidden
               for line in symbols.splitlines() if line.strip()):
            raise AssertionError("Unexpected dynamic allocation dependency: " + forbidden)
    size = Path(compiler).with_name("size.exe" if os.name == "nt" else "size")
    object_sizes = invoke([str(size), *map(relative, object_files)])
    runner = run_checks()
    return {"passed": True, "check_count": runs["-O0"]["check_count"] + runner["check_count"] + 2 + len(vectors),
            "commands": commands, "metrics": {"linked_boundary_runs": runs,
            "exact_mean_vectors":len(vectors), "exact_mean_seed":2026102606,
            "stack_usage_O2": stack, "object_sizes_host_only": object_sizes,
            "undefined_symbols": symbols, "runner_checks": runner},
            "scope": "Independent linked C boundary cases run at O0/O2; no heap imports; receipt failure cases. Host ABI only."}

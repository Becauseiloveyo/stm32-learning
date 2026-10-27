"""Actual GCC/ctypes differential validation, synthetic inputs only.

The adapter translates raw fields and known immutable configuration identities;
it never supplies an assessment to C. All decisions are compared with the real
upstream Python assess/StateMachine. Artifacts stay under build/measurement.
"""
import argparse
import ctypes as C
from copy import deepcopy
from dataclasses import asdict, fields, replace
import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from theory.model import assess, simulate, Parameters, DEFAULT_SEED
from theory.protocol import DEFAULT_PROTOCOL, DEFAULT_GATES, PHASES, finite_number
from theory.run_simulation import cases, verification_protocols
from research.state_machine.machine import (Configuration, DEFAULT_CONFIG, StateMachine,
                                          OBSERVATION_KEYS, EVENT_KEYS, EVENT_KINDS)
from research.state_machine.scenarios import Sequence, observations

I, U, D, ID = C.c_int32, C.c_uint32, C.c_double, C.c_char * 33


class Protocol(C.Structure):
    _fields_ = [("version", I)] + [(x, D) for x in (
        "baseline_s", "flush_s", "recovery_s", "sample_period_s", "max_gap_factor",
        "head_window_s", "tail_window_s", "time_tolerance_s")] + [("min_phase_samples", U), ("min_window_samples", U)]


class Gates(C.Structure):
    _fields_ = [(f.name, D) for f in fields(DEFAULT_GATES)]


class Policy(C.Structure):
    _fields_ = [(f.name, U if f.name == "review_after_weak_cycles" else D) for f in fields(DEFAULT_CONFIG.policy)]


class Config(C.Structure):
    _fields_ = [("version", I), ("protocol", Protocol), ("gates", Gates), ("policy", Policy)]


class Observation(C.Structure):
    _fields_ = [("present", U), ("observation_version", I), ("units", I), ("protocol", C.POINTER(Protocol)),
                ("phase", I), ("sensor_valid", I), ("clip_event", I)] + [(x, D) for x in (
                    "time_s", "ambient_T_C", "ambient_RH_pct", "local_T_C", "local_RH_pct")]


class Result(C.Structure):
    _fields_ = [("state", I), ("features_valid", I), ("collection_authorized", I), ("reasons", U),
                ("recovery_rise_Pa", D), ("tail_delta_Pa", D)]


class Event(C.Structure):
    _fields_ = [("present", U), ("version", I), ("kind", I)] + [(x, ID) for x in (
        "event_id", "boot_id", "batch_id", "installation_id", "cycle_id")] + [(x, D) for x in (
            "monotonic_s", "start_s", "end_s")] + [("context", C.POINTER(Config)),
            ("new_config", C.POINTER(Config)), ("observations", C.POINTER(Observation)), ("observation_count", C.c_size_t)]


class Snapshot(C.Structure):
    _fields_ = [(x, I) for x in ("state", "has_time", "has_stable", "has_window", "has_evidence_end", "has_pending")] + [
        (x, D) for x in ("last_monotonic_s", "stable_after_s", "last_window_end_s", "last_evidence_end_s",
                         "pending_start_s", "pending_end_s", "pending_deadline_s")] + [(x, ID) for x in (
            "boot_id", "batch_id", "installation_id", "pending_cycle_id")] + [
                ("evidence_cycle_ids", ID * 16), ("weak_cycles", U)]


class Output(C.Structure):
    _fields_ = [(x, I) for x in ("accepted", "disposition", "reason", "assessment_present", "independent_review_due",
                               "collection_authorized", "capacity_latched")] + [
                                   ("assessment", Result), ("snapshot", Snapshot), ("config", Config)]


STATES = ["AWAITING_INSTALLATION", "STABILIZING", "READY", "MEASURING", "AWAITING_RECHECK", "SOURCE_PRESENT", "FAULT"]
MSTATES = ["UNKNOWN", "VAPOR_SOURCE_PRESENT", "LOW_SIGNAL_UNCONFIRMED"]
REASONS = ["missing_data", "missing_field", "unsupported_observation_version", "unsupported_units", "protocol_mismatch",
           "invalid_flag", "invalid_sensor", "outside_model_domain", "non_monotonic_time", "sample_gap", "missing_phase",
           "phase_time_mismatch", "incomplete_cycle", "insufficient_phase_samples", "insufficient_feature_samples",
           "high_ambient_humidity", "near_saturation", "ambient_changed", "temperature_mismatch", "clip_changed",
           "ambiguous_response", "low_signal_does_not_prove_dry"]
SREASONS = ["malformed_event", "stale_or_unknown_boot", "retired_boot", "configuration_mismatch", "batch_or_installation_mismatch",
            "duplicate_event", "event_id_conflict", "boot_id_reused", "invalid_installation_identity", "retired_installation_or_batch",
            "invalid_monotonic_time", "boot_requires_installation", "invalid_cycle_id", "cycle_id_reused", "non_increasing_event_time",
            "event_time_jump", "cycle_timeout", "installation_wait", "invalid_configuration", "configuration_unchanged",
            "configuration_changed_reinstall", "clip_requires_reinstallation", "recovery_requires_fault_and_installation",
            "recovery_wait", "clock_observed", "cycle_already_active", "not_ready", "cycle_gap_too_short", "unrepresentable_deadline",
            "cycle_started", "no_matching_cycle_start", "cycle_interval_mismatch", "observed_clip_requires_reinstallation",
            "invalid_cycle", "source_observed", "weak_evidence_unconfirmed", "capacity_exhausted", "uninitialized"]


def number(value):
    return float(value) if finite_number(value) else float("nan")


def identifier(value):
    if value is None:
        return b""
    if (not isinstance(value, str) or not 0 < len(value) <= 32 or value != value.strip()
            or not all(32 <= ord(ch) <= 126 for ch in value)):
        return b"\x01"  # explicit invalid, never silently truncate
    return value.encode("ascii")


def protocol(p):
    result = Protocol(version=1 if p.version == "1.0" else -1)
    for key, field_type in Protocol._fields_[1:]:
        value = getattr(p, key)
        setattr(result, key, uint32_exact(value) if field_type is U else number(value))
    return result


def uint32_exact(value):
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError("unsigned configuration count must be an exact int in 0..2**32-1")
    return value


def configuration(c):
    return Config(1 if c.version == "1.0" else -1, protocol(c.protocol),
                  Gates(**{f.name: number(getattr(c.gates, f.name)) for f in fields(c.gates)}),
                  Policy(**{f.name: uint32_exact(getattr(c.policy, f.name)) if f.name == "review_after_weak_cycles"
                            else number(getattr(c.policy, f.name)) for f in fields(c.policy)}))


class Adapter:
    def __init__(self, lib, config=DEFAULT_CONFIG):
        self.lib = lib
        self.configs, self.protocols = {}, {}
        self.register(config)
        # create_string_buffer is suitably aligned on CPython for native structs;
        # retain guard regions and use an 8-byte aligned interior address.
        self.size = lib.mb_size(0)
        self.storage = C.create_string_buffer(self.size + 32)
        C.memset(C.addressof(self.storage), 0xA5, self.size + 32)
        self.machine = C.c_void_p(C.addressof(self.storage) + 16)
        assert lib.mb_init(self.machine, C.byref(self.configs[config.sha256])) == 1

    def register(self, config):
        self.configs[config.sha256] = configuration(config)
        self.protocols[config.protocol.fingerprint] = protocol(config.protocol)

    def raw(self, rows):
        if not isinstance(rows, (list, tuple)) or not rows:
            return None, 0
        array = (Observation * len(rows))()
        for r, target in zip(rows, array):
            r = r if isinstance(r, dict) else {}
            target.present = sum(1 << i for i, k in enumerate(OBSERVATION_KEYS) if k in r)
            target.observation_version = 1 if r.get("observation_version") == "1.0" else -1
            target.units = 1 if r.get("unit_system") == "s_degC_pctRH_Pa" else -1
            p = self.protocols.get(str(r.get("protocol_sha256")))
            target.protocol = C.pointer(p) if p is not None else None
            target.phase = PHASES.index(r["phase"]) if r.get("phase") in PHASES else -1
            for key in ("sensor_valid", "clip_event"):
                value = r.get(key)
                setattr(target, key, int(value) if type(value) is bool else -1)
            for key in ("time_s", "ambient_T_C", "ambient_RH_pct", "local_T_C", "local_RH_pct"):
                setattr(target, key, number(r.get(key)))
        return array, len(rows)

    def event(self, event):
        if not isinstance(event, dict):
            return None
        e = Event()
        e.present = sum(1 << i for i, k in enumerate((*EVENT_KEYS, "observations")) if k in event)
        e.version = 1 if event.get("event_version") == "1.0" else -1
        e.kind = EVENT_KINDS.index(event["kind"]) if event.get("kind") in EVENT_KINDS else -1
        for key in ("event_id", "boot_id", "batch_id", "installation_id", "cycle_id"):
            setattr(e, key, identifier(event.get(key)))
        for key in ("monotonic_s", "start_s", "end_s"):
            setattr(e, key, number(event.get(key)))
        if "new_config" in event:
            try:
                new = Configuration.from_snapshot(event["new_config"])
            except (ValueError, TypeError):
                new = None
            if new is not None:
                try:
                    self.register(new)
                    e.new_config = C.pointer(self.configs[new.sha256])
                except ValueError:
                    e.new_config = C.pointer(Config(version=-1))
            else:
                e.new_config = C.pointer(Config(version=-1))
        context = self.configs.get(str(event.get("config_sha256")))
        e.context = C.pointer(context) if context is not None else None
        array, count = self.raw(event.get("observations"))
        e.observations, e.observation_count = array, count
        return e

    def handle(self, event):
        e = self.event(event)
        out = Output()
        assert self.lib.mb_handle(self.machine, C.byref(e) if e else None, C.byref(out)) == 1
        assert self.storage.raw[:16] == b"\xA5" * 16 and self.storage.raw[-16:] == b"\xA5" * 16
        return out

    def assess(self, rows, config=DEFAULT_CONFIG):
        self.register(config)
        array, count = self.raw(rows)
        out = Result()
        c = self.configs[config.sha256]
        status = self.lib.mb_assess(array, count, C.byref(c.protocol), C.byref(c.gates), C.byref(out))
        return status, out

    def counts(self):
        counts = (U * 5)()
        self.lib.mb_counts(self.machine, counts)
        return list(counts)


class Checks:
    def __init__(self):
        self.count = 0
        self.max_error = 0.0
        self.groups = {}

    def require(self, condition, label):
        self.count += 1
        if not condition:
            raise AssertionError(label)

    def numeric(self, actual, expected, label):
        error = abs(actual - expected)
        self.max_error = max(error, self.max_error)
        self.require(math.isfinite(actual) and error <= 1e-10, f"{label}: C={actual!r} Python={expected!r} error={error}")

    def measurement(self, actual, expected, label):
        self.require(MSTATES[actual.state] == expected["state"], f"{label} measurement state C={MSTATES[actual.state]} Python={expected['state']} features C={actual.recovery_rise_Pa!r},{actual.tail_delta_Pa!r} Python={expected['recovery_rise_Pa']!r},{expected['tail_delta_Pa']!r}")
        mask = sum(1 << REASONS.index(r) for r in expected["reasons"].split(";") if r)
        self.require(actual.reasons == mask, f"{label} reasons: {actual.reasons} != {mask}")
        self.require(actual.collection_authorized == 0, label + " authorization")
        self.require(bool(actual.features_valid) == (expected["tail_delta_Pa"] is not None), label + " feature presence")
        if actual.features_valid:
            for key in ("tail_delta_Pa", "recovery_rise_Pa"):
                self.numeric(getattr(actual, key), expected[key], label + "/" + key)

    def state(self, actual, expected, reference, label):
        s = actual.snapshot
        self.require(STATES[s.state] == expected["state"], f"{label} state {STATES[s.state]} != {expected['state']}")
        self.require(bool(actual.accepted) == expected["accepted"], label + " accepted")
        self.require(["REJECTED", "APPLIED", "IGNORED_DUPLICATE"][actual.disposition] == expected["disposition"], label + " disposition")
        self.require(SREASONS[actual.reason] == expected["reason"].split(":")[0], f"{label} reason {SREASONS[actual.reason]} != {expected['reason']}")
        self.require(s.weak_cycles == expected["weak_cycles"], label + " weak count")
        self.require(bool(actual.independent_review_due) == expected["independent_review_due"], label + " review")
        self.require(actual.collection_authorized == 0 and actual.capacity_latched == 0, label + " authorization/capacity")
        for key in ("boot_id", "batch_id", "installation_id"):
            self.require((getattr(s, key).decode() or None) == expected[key], label + "/" + key)
        for flag, key in (("has_time", "last_monotonic_s"), ("has_stable", "stable_after_s"),
                          ("has_window", "last_window_end_s"), ("has_evidence_end", "last_evidence_end_s")):
            self.require(bool(getattr(s, flag)) == (expected[key] is not None), label + "/" + flag)
            if getattr(s, flag):
                self.require(getattr(s, key) == expected[key], label + "/" + key)  # exact clocks
        self.require([x.value.decode() for x in s.evidence_cycle_ids[:s.weak_cycles]] == expected["evidence_cycle_ids"], label + " evidence identities")
        pending = expected["pending_cycle"]
        self.require(bool(s.has_pending) == bool(pending), label + " pending presence")
        if pending:
            self.require(s.pending_cycle_id.decode() == pending["cycle_id"], label + " pending identity")
            for key in ("start_s", "end_s", "deadline_s"):
                self.require(getattr(s, "pending_" + key) == pending[key], label + "/pending/" + key)
        c = configuration(reference.config)
        # compare fields, not padding (also covers config SHA's full meaning).
        for key in ("protocol", "gates", "policy"):
            for f, _ in getattr(c, key)._fields_:
                self.require(getattr(getattr(actual.config, key), f) == getattr(getattr(c, key), f), label + "/config/" + f)
        self.require(actual.config.version == c.version, label + " config version")
        self.require(bool(actual.assessment_present) == (expected["assessment"] is not None), label + " assessment presence")
        if actual.assessment_present:
            self.measurement(actual.assessment, expected["assessment"], label)


def raw_constant(p=DEFAULT_PROTOCOL, ambient=50.0, local=50.0, temperature=25.0):
    return [{"observation_version": "1.0", "unit_system": "s_degC_pctRH_Pa", "protocol_sha256": p.fingerprint,
             "time_s": i * p.sample_period_s, "phase": p.phase_at(i * p.sample_period_s), "ambient_T_C": temperature,
             "local_T_C": temperature, "ambient_RH_pct": ambient, "local_RH_pct": local,
             "sensor_valid": True, "clip_event": False} for i in range(round(p.total_s / p.sample_period_s) + 1)]


def run_validation(compiler, build_dir):
    compiler_path = Path(compiler)
    if not compiler_path.is_absolute() or not compiler_path.is_file():
        raise ValueError("compiler must be an existing absolute executable")
    build_dir = Path(build_dir).resolve()
    allowed = (ROOT / "firmware/host/build/measurement").resolve()
    if build_dir != allowed:
        raise ValueError(f"this worker writes artifacts only in {allowed}")
    build_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = build_dir / "measurement-result.json"
    receipt_path.write_text(json.dumps({"passed": False, "status": "running; no current success receipt"}) + "\n", encoding="utf-8")
    inputs = ["theory/model.py", "theory/protocol.py", "theory/run_simulation.py", "research/state_machine/machine.py",
              "research/state_machine/scenarios.py", "research/state_machine/results/events.json",
              "firmware/core/hanger_measurement.h", "firmware/core/hanger_measurement.c", "firmware/core/hanger_state.h",
              "firmware/core/hanger_state.c", "firmware/core/hanger_mean_internal.h", "firmware/core/hanger_sha256_internal.h",
              "firmware/host/measurement_bridge.c", "firmware/host/measurement_validation.py"]
    def source_hashes():
        return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in inputs}
    source_before = source_hashes()
    env = dict(os.environ)
    env["PATH"] = str(compiler_path.parent) + os.pathsep + env.get("PATH", "")
    commands = []

    def run(args):
        commands.append(args)
        proc = subprocess.run(args, cwd=build_dir, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=90)
        if proc.returncode:
            raise RuntimeError(f"Command failed ({proc.returncode}): {args!r}\n{proc.stdout}\n{proc.stderr}")
        return proc

    compiler_version = run([compiler, "--version"]).stdout.strip()
    flags = ["-std=c99", "-Wall", "-Wextra", "-Werror", "-pedantic", "-O2", "-ffp-contract=off", "-fstack-usage"]
    sources = [ROOT / "firmware/core/hanger_measurement.c", ROOT / "firmware/core/hanger_state.c", ROOT / "firmware/host/measurement_bridge.c"]
    objects = []
    for source in sources:
        obj = build_dir / (source.stem + ".o")
        run([compiler, *flags, "-I", os.path.relpath(ROOT / "firmware/core", build_dir),
             "-c", os.path.relpath(source, build_dir), "-o", obj.name])
        objects.append(obj.name)
    # Process-specific name permits repeated validation while a previous ctypes
    # module is loaded; do not delete artifacts owned by other runs.
    dll = build_dir / f"measurement_{os.getpid()}.dll"
    run([compiler, "-shared", *objects, "-o", dll.name, "-lm"])
    if hasattr(os, "add_dll_directory"):
        dll_search = os.add_dll_directory(str(compiler_path.parent))
    else:
        dll_search = None
    lib = C.CDLL(str(dll))
    lib.mb_size.argtypes, lib.mb_size.restype = [U], C.c_size_t
    lib.mb_init.argtypes, lib.mb_init.restype = [C.c_void_p, C.POINTER(Config)], I
    lib.mb_handle.argtypes, lib.mb_handle.restype = [C.c_void_p, C.POINTER(Event), C.POINTER(Output)], I
    lib.mb_assess.argtypes, lib.mb_assess.restype = [C.POINTER(Observation), C.c_size_t, C.POINTER(Protocol), C.POINTER(Gates), C.POINTER(Result)], I
    lib.mb_counts.argtypes = [C.c_void_p, C.POINTER(U)]
    lib.mb_sha.argtypes = [C.c_void_p, C.c_size_t, C.POINTER(C.c_uint8)]
    lib.mb_mean.argtypes, lib.mb_mean.restype = [C.POINTER(D), C.c_size_t, C.POINTER(D)], I
    lib.mb_rounding_guard.argtypes, lib.mb_rounding_guard.restype = [C.POINTER(Config)], I
    check = Checks()
    check.require(lib.mb_rounding_guard(C.byref(configuration(DEFAULT_CONFIG))) == 1, "non-nearest rounding rejected and restored")
    for code, cls in ((1, Observation), (2, Event), (3, Output), (4, Config), (5, Protocol), (6, Result), (7, Snapshot)):
        check.require(lib.mb_size(code) == C.sizeof(cls), f"ABI size {cls.__name__}")
    for raw in (b"", b"abc", b"x" * 55, b"x" * 56, b"x" * 64, b"x" * 513, b"a" * 1000000):
        digest = (C.c_uint8 * 32)()
        lib.mb_sha(raw, len(raw), digest)
        check.require(bytes(digest) == hashlib.sha256(raw).digest(), f"SHA256 {len(raw)}")
    # The accumulator is a new algorithm, so verify it independently against
    # statistics.mean's exact rational sum, including cancellation/subnormals.
    mean_vectors = [[0.0], [61.093999999999994] * 11, [1e4, 1e-300, -1e4],
                    [5e-324, 0], [5e-324, 5e-324, 0], [-5e-324, 0], [12359.0] * 512,
                    [-12359.0] * 512, [1e4, -1e4] * 256]
    mean_rng = random.Random(20261027)
    for n in range(240):
        mean_vectors.append([math.ldexp(mean_rng.uniform(-1, 1), mean_rng.randint(-1074, 13))
                             for _ in range(mean_rng.randint(1, 512))])
    for n, values in enumerate(mean_vectors):
        out = D()
        check.require(lib.mb_mean((D * len(values))(*values), len(values), C.byref(out)) == 1, "mean valid domain")
        check.require(out.value == statistics.mean(values), f"exact mean {n}: {out.value!r} != {statistics.mean(values)!r}")
    check.groups["exact_mean_vectors"] = len(mean_vectors)
    adapter = Adapter(lib)
    measurement_vectors = 0

    def compare(rows, config=DEFAULT_CONFIG, name="measurement"):
        nonlocal measurement_vectors
        status, result = adapter.assess(rows, config)
        check.require(status == 0, name + " status")
        check.measurement(result, assess(rows, config.gates, protocol=config.protocol), name)
        measurement_vectors += 1
        return result

    for pname, p in verification_protocols().items():
        for name, (params, _) in cases().items():
            if pname != "default" and name not in ("wet_normal", "dry_equilibrium", "wet_leaky", "ambiguous_A", "ambiguous_B"):
                continue  # same variant fixtures as upstream; event times may not align
            compare(simulate(params, seed=DEFAULT_SEED, protocol=p), replace(DEFAULT_CONFIG, protocol=p), pname + "/" + name)
    base = raw_constant()
    for rows in (None, [], {}, [None]):
        compare(rows, name="missing container/row")
    for key in OBSERVATION_KEYS:
        rows = deepcopy(base)
        del rows[60][key]
        compare(rows, name="missing/" + key)
    invalids = {"observation_version": [None, "2.0", True], "unit_system": [None, "kelvin"],
                "protocol_sha256": [None, "forged"], "phase": [None, "bad", "baseline"],
                "sensor_valid": [False, 1, "true", None], "clip_event": [True, 1, "false", None]}
    for key in ("time_s", "ambient_T_C", "local_T_C", "ambient_RH_pct", "local_RH_pct"):
        invalids[key] = [None, True, "25", float("nan"), float("inf"), -float("inf"), -1, 101]
    for key, values in invalids.items():
        for value in values:
            rows = deepcopy(base)
            rows[60][key] = value
            compare(rows, name="invalid/" + key + "/" + repr(value))
    for name, rows in (("duplicate-time", base[:60] + [base[59]] + base[61:]), ("missing-tail", base[:-1]),
                       ("gap", base[:60] + base[64:]), ("no-flush", [r for r in base if r["phase"] != "flush"]),
                       ("backwards", list(reversed(base))), ("head-thin", base[:51] + base[56:])):
        compare(rows, name=name)
    for temp in (0.0, 50.0):
        for humidity in (0.0, 100.0):
            compare(raw_constant(temperature=temp, ambient=humidity, local=humidity), name="closed domain bounds")
    for edge in (90.0, 98.0, 3.0, 1.0):
        for v in (math.nextafter(edge, -math.inf), edge, math.nextafter(edge, math.inf)):
            rows = deepcopy(base)
            if edge == 90:
                for r in rows: r["ambient_RH_pct"] = v
            elif edge == 98:
                rows[60]["local_RH_pct"] = v
            elif edge == 3:
                # subtraction takes place in binary64 on both sides
                rows[60]["ambient_RH_pct"] += v
            else:
                rows[60]["local_T_C"] += v
            compare(rows, name=f"environment threshold {edge}/{v}")
    # Feature-gate exact boundaries and adjacent representable gate values.
    # Gates are only a test vector: reference computes them from the same raw
    # vector; C still receives and evaluates raw measurements, never the result.
    for strong in (False, True):
        rows = raw_constant(local=52.0)
        if strong:
            for r in rows:
                r["local_RH_pct"] = 50 if r["time_s"] <= 55 else 60
        result = assess(rows)
        edge = min(result["recovery_rise_Pa"], result["tail_delta_Pa"]) if strong else max(abs(result["recovery_rise_Pa"]), abs(result["tail_delta_Pa"]))
        for threshold in (math.nextafter(edge, -math.inf), edge, math.nextafter(edge, math.inf)):
            g = replace(DEFAULT_GATES, **({"strong_delta_pa": threshold} if strong else {"weak_delta_pa": threshold}))
            compare(rows, replace(DEFAULT_CONFIG, gates=g), f"feature gate {strong}/{threshold}")
    rng = random.Random(20261026)
    for n in range(120):
        rows = raw_constant(temperature=rng.uniform(0, 49), ambient=rng.uniform(5, 85), local=rng.uniform(5, 95))
        for row in rows:
            row["local_RH_pct"] = max(0, min(100, row["local_RH_pct"] + rng.uniform(-0.2, 0.2)))
        compare(rows, name=f"seeded raw {n}")
    # Stress gate equality across different exp arguments, not just 25 degC.
    for temperature in (0.0, 1.234, 10.0, 23.456, 37.0, 49.0, 50.0):
        for strong in (False, True):
            rows = raw_constant(temperature=temperature, local=52.0)
            if strong:
                for row in rows: row["local_RH_pct"] = 50 if row["time_s"] <= 55 else 60
            py = assess(rows)
            edge = min(py["recovery_rise_Pa"], py["tail_delta_Pa"]) if strong else max(abs(py["recovery_rise_Pa"]), abs(py["tail_delta_Pa"]))
            for value in (math.nextafter(edge, -math.inf), edge, math.nextafter(edge, math.inf)):
                gates = replace(DEFAULT_GATES, **({"strong_delta_pa": value, "weak_delta_pa": 0.0} if strong else {"weak_delta_pa": value, "strong_delta_pa": value+100}))
                compare(rows, replace(DEFAULT_CONFIG, gates=gates), f"temperature gate {temperature}/{strong}/{value}")
    check.groups["measurement_vectors"] = measurement_vectors

    saved = json.loads((ROOT / "research/state_machine/results/events.json").read_text(encoding="utf-8"))["scenarios"]
    replay_events = 0

    def replay(events, config=DEFAULT_CONFIG, label="events"):
        nonlocal replay_events
        c, py = Adapter(lib, config), StateMachine(config)
        for index, event in enumerate(events):
            result = py.handle(event)
            check.state(c.handle(event), result, py, f"{label}/{index}")
            replay_events += 1
        return c, py

    for scenario in saved:
        replay(scenario["events"], Configuration.from_snapshot(scenario["initial_config"]), scenario["name"])
    check.groups["saved_T04_scenarios"] = len(saved)
    check.groups["saved_T04_events"] = replay_events

    # Independent extensions: exact deadlines, long idle vs pending, diagnostic
    # tamper, payload conflict, detached scope, retired IDs, full config identity.
    s = Sequence()
    s.cycle()
    duplicate = deepcopy(s.events[-1]); duplicate["claimed_state"] = "READY_TO_COLLECT"
    for row in duplicate["observations"]: row["synthetic_truth"] = "dry"; row["delta_e_Pa"] = 1e9
    s.events.append(duplicate)
    replay(s.events, label="ignored diagnostics")
    for change in ("time", "raw", "optional"):
        s = Sequence(); s.cycle(); duplicate = deepcopy(s.events[-1])
        if change == "time": duplicate["monotonic_s"] += 1
        if change == "raw": duplicate["observations"][60]["local_RH_pct"] += 0.01
        if change == "optional": duplicate["new_config"] = DEFAULT_CONFIG.snapshot()
        s.events.append(duplicate); replay(s.events, label="receipt conflict/" + change)
    for offset in (-2e-6, -0.5e-6, 0.0, 10.0, 10.0 + 0.5e-6, 10.0 + 2e-6):
        s = Sequence(); pending = s.begin(); e = s.finish(pending); e["monotonic_s"] += offset
        replay(s.events, label="completion edge/" + str(offset))
    for gap in (30 - 1e-6, 30.0, 1800.0, 1800.0 + 1e-6):
        s = Sequence(); s.cycle(); s.begin(s.now + gap)
        replay(s.events, label="gap/" + str(gap))
    s = Sequence(); s.cycle(); pending = s.begin(s.now + 1799); s.finish(pending)
    replay(s.events, label="pending retains evidence")
    for event in (None, {}, {"event_version": "2.0"}):
        replay([event], label="malformed")
    for kind, keys in (("boot", EVENT_KEYS[:6]), ("install", ("batch_id", "installation_id")),
                       ("start_cycle", ("cycle_id",)), ("finish_cycle", ("start_s", "end_s", "observations")),
                       ("configure", ("new_config",))):
        for key in keys:
            s = Sequence()
            if kind == "boot":
                events = [deepcopy(s.events[0])]
            elif kind == "install":
                events = s.events
            elif kind == "start_cycle":
                s.begin(); events = s.events
            elif kind == "finish_cycle":
                s.cycle(); events = s.events
            else:
                s.emit("configure", new_config=DEFAULT_CONFIG.snapshot()); events = s.events
            del events[-1][key]
            replay(events, label="missing event/" + key)
    for invalid in (float("nan"), float("inf"), -1, True, None):
        s = Sequence(); s.emit("tick", invalid); replay(s.events, label="invalid time")
    for field in ("batch_id", "installation_id", "boot_id", "config_sha256"):
        s = Sequence(); s.cycle(); s.emit("tick" if field in ("boot_id", "config_sha256") else "start_cycle", cycle_id="foreign", **{field:"foreign"})
        replay(s.events, label="foreign/" + field)
    s = Sequence(); s.install("batch-b"); s.emit("install", batch_id="batch-a", installation_id="brand-new")
    replay(s.events, label="retired batch")
    s = Sequence(); s.emit("install", batch_id=s.batch_id, installation_id=s.installation_id)
    replay(s.events, label="retired installation")
    s = Sequence(); s.emit("boot", boot_id="boot-b"); s.emit("boot", boot_id="boot-a")
    replay(s.events, label="retired boot")
    s = Sequence(); s.emit("configure", new_config=DEFAULT_CONFIG.snapshot())
    s.emit("configure", new_config={}); replay(s.events, label="configuration unchanged invalid")
    # Complete max-length IDs, and rejection (not truncation) at one over.
    s = Sequence(); s.events[0]["boot_id"] = "b" * 32; s.events[0]["event_id"] = "e" * 32
    replay(s.events[:1], label="max ID")
    c = Adapter(lib); e = deepcopy(s.events[0]); e["boot_id"] += "x"
    out = c.handle(e); check.require(out.reason == 0 and out.snapshot.state == 6, "overlong ID rejected")
    for bad in ("abc\0hidden", "a\nb", "a\tb", " leading", "trailing ", "", 12, False):
        e = deepcopy(Sequence().events[0]); e["event_id"] = bad
        replay([e], label="invalid ID must not truncate")
    e = deepcopy(Sequence().events[0]); e["event_id"] = "安装"
    check.require(Adapter(lib).handle(e).reason == 0, "unsupported Unicode ID explicit rejection")
    check.groups["total_reference_events"] = replay_events

    # Exact capacities, no unsafe eviction, latch blocks boot/recover thereafter.
    for kind, limit in (("messages",64), ("cycles",16), ("boots",8), ("installations",32), ("batches",16)):
        c = Adapter(lib); s = Sequence(); last = None
        if kind == "boots":
            events = [dict(s.events[0], event_id=f"b-{i}", boot_id=f"boot-{i}") for i in range(limit + 1)]
        elif kind == "messages":
            events = [s.events[0]]
            events += [dict(s.events[0], kind="tick", event_id=f"t-{i}", monotonic_s=i) for i in range(1, limit + 1)]
        elif kind == "cycles":
            for _ in range(limit + 1): s.cycle()
            events = s.events[:-1]  # overflow occurs at final start
        else:
            events = [s.events[0]]
            count = limit + 1 if kind == "installations" else limit + 2
            events += [dict(s.events[1], event_id=f"i-{i}", monotonic_s=i+1,
                            installation_id=f"i-{i}", batch_id=f"batch-{i}" if kind == "batches" else "batch-a") for i in range(count)]
        for e in events[:-1]:
            last = c.handle(e)
            check.require(not last.capacity_latched, kind + " premature capacity")
        check.require(last.accepted == 1, kind + " capacity exactly full accepts last record")
        if kind == "cycles":
            check.require(last.snapshot.weak_cycles == 16 and last.independent_review_due == 1, "weak count exact capacity")
        duplicate = c.handle(events[-2])
        check.require(duplicate.disposition == 2 and not duplicate.capacity_latched, kind + " duplicate at full")
        out = c.handle(events[-1])
        check.require(out.reason == 36 and out.capacity_latched and out.snapshot.state == 6 and out.snapshot.weak_cycles == 0 and not out.snapshot.has_pending,
                      kind + " overflow fails closed")
        counts = c.counts()
        for i in range(200):
            again = c.handle(dict(s.events[0], boot_id="fresh-boot", event_id=f"fresh-{i}"))
            check.require(again.reason == 36 and again.snapshot.weak_cycles == 0 and c.counts() == counts, kind + " persistent latch")
        check.require(lib.mb_init(c.machine, C.byref(c.configs[DEFAULT_CONFIG.sha256])) == 1, kind + " true init")
        out = c.handle(dict(s.events[0], boot_id="fresh-trusted-epoch"))
        check.require(out.accepted and out.snapshot.weak_cycles == 0, kind + " cold init no evidence restore")
    # A new boot before capacity retains receipt and seen-boot histories, but
    # resets boot-local cycles and evidence; repeated old boot stays rejected.
    s = Sequence(); s.cycle(); c, _ = replay(s.events, label="pre reboot")
    before = c.counts(); out = c.handle(dict(s.events[0], boot_id="new-boot", event_id="new-boot-event"))
    check.require(out.accepted and out.snapshot.weak_cycles == 0 and c.counts()[0] == before[0]+1 and c.counts()[2] == before[2]+1 and c.counts()[1] == 0, "boot vs init")
    check.require(c.handle(s.events[0]).reason == 2, "old boot still retired")

    p512 = replace(DEFAULT_PROTOCOL, recovery_s=461.0)
    rows = raw_constant(p512)
    check.require(len(rows) == 512, "fixture exact samples")
    compare(rows, replace(DEFAULT_CONFIG, protocol=p512), "512 samples")
    fractional512 = replace(DEFAULT_PROTOCOL, baseline_s=9.0, flush_s=6.0, recovery_s=138.3,
                            sample_period_s=0.3, head_window_s=1.5, tail_window_s=3.0)
    compare(raw_constant(fractional512), replace(DEFAULT_CONFIG, protocol=fractional512), "512 fractional period with rounded ratio")
    status, out = adapter.assess(rows + [rows[-1]], replace(DEFAULT_CONFIG, protocol=p512))
    check.require(status == 2 and out.state == 0 and out.reasons == 1<<23, "513 samples reject")
    # Public C configurations reject unsupported capacity/time/count domains.
    for field, value in (("recovery_s", 462.0), ("sample_period_s", 0.0005), ("time_tolerance_s", float("nan")), ("min_phase_samples", 1)):
        cconfig = configuration(DEFAULT_CONFIG); setattr(cconfig.protocol, field, value)
        check.require(lib.mb_init(adapter.machine, C.byref(cconfig)) == 0, "invalid config " + field)
    for field, value in (("review_after_weak_cycles", 17), ("review_after_weak_cycles", 0), ("max_event_gap_s", float("inf"))):
        cconfig = configuration(DEFAULT_CONFIG); setattr(cconfig.policy, field, value)
        check.require(lib.mb_init(adapter.machine, C.byref(cconfig)) == 0, "invalid policy " + field)
    for value in (2**32+3, 2**32, -1, True, 3.0, "3", None):
        for domain, key in (("policy", "review_after_weak_cycles"), ("protocol", "min_phase_samples"), ("protocol", "min_window_samples")):
            changed = replace(DEFAULT_CONFIG, **{domain: replace(getattr(DEFAULT_CONFIG, domain), **{key:value})})
            try:
                configuration(changed)
            except ValueError:
                check.require(True, "no count truncation")
            else:
                raise AssertionError(f"count {domain}/{key} accepted {value!r}")
    check.require(uint32_exact(0xFFFFFFFF) == 0xFFFFFFFF, "uint32 exact upper bound")
    for domain, key in (("policy", "review_after_weak_cycles"), ("protocol", "min_phase_samples"), ("protocol", "min_window_samples")):
        changed = replace(DEFAULT_CONFIG, **{domain: replace(getattr(DEFAULT_CONFIG, domain), **{key:0xFFFFFFFF})})
        cc = configuration(changed)
        check.require(lib.mb_init(adapter.machine, C.byref(cc)) == 0, "uint32 max exceeds supported capacity")
    s = Sequence(); s.emit("configure", new_config=replace(DEFAULT_CONFIG,
        policy=replace(DEFAULT_CONFIG.policy, review_after_weak_cycles=2**32+3)).snapshot())
    c = Adapter(lib)
    for e in s.events: out = c.handle(e)
    check.require(out.reason == 18 and not out.accepted and out.snapshot.weak_cycles == 0, "configure count wrap explicitly rejected")
    c = Adapter(lib); e = deepcopy(Sequence().events[0]); e["monotonic_s"] = 1e9
    check.require(c.handle(e).accepted == 1, "time upper bound accepted")
    e["monotonic_s"] = math.nextafter(1e9, math.inf); e["boot_id"] = "next-boot"; e["event_id"] = "over-time"
    check.require(c.handle(e).reason == 10, "time over bound rejected")
    c = Adapter(lib); s = Sequence(); s.events[0]["monotonic_s"] = 1e9-2; s.events[1]["monotonic_s"] = 1e9-1
    check.require(c.handle(s.events[0]).accepted == 1 and c.handle(s.events[1]).reason == 28, "deadline beyond time domain")
    c = Adapter(lib); s = Sequence(); c.handle(s.events[0]); e = c.event(s.events[1]); spoof = configuration(DEFAULT_CONFIG)
    spoof.gates.weak_delta_pa += 1; e.context = C.pointer(spoof); out = Output()
    lib.mb_handle(c.machine, C.byref(e), C.byref(out))
    check.require(out.reason == 3 and not out.accepted and out.snapshot.last_monotonic_s == 0, "same supplied context cannot hide changed parameters")
    # Presence is checked in C as well as by the adapter: populated values cannot
    # hide an explicitly absent required field.
    for bit, reason in ((0,0),(1,0),(2,0),(3,0),(4,10),(5,3)):
        c = Adapter(lib); e = c.event(Sequence().events[0]); e.present &= ~(1<<bit)
        lib.mb_handle(c.machine, C.byref(e), C.byref(out))
        check.require(out.reason == reason and not out.accepted, "C required presence bit")
    c = Adapter(lib); s = Sequence(); s.cycle()
    for e in s.events[:-1]: c.handle(e)
    e = c.event(s.events[-1]); e.observation_count = 513
    lib.mb_handle(c.machine, C.byref(e), C.byref(out))
    check.require(out.reason == 36 and out.capacity_latched and not out.snapshot.has_pending and out.snapshot.weak_cycles == 0, "oversized state raw buffer rejected before dereference")
    check.groups["measurement_vectors"] = measurement_vectors
    check.groups["total_reference_events"] = replay_events

    sizes = {name: int(lib.mb_size(i)) for i, name in enumerate(("hs_machine", "hm_observation", "hs_event", "hs_output", "hs_config",
             "hm_protocol", "hm_result", "hs_snapshot", "hs_receipt", "sha_context", "long_double"))}
    stack = {}
    core_frames_sum = 0
    for path in build_dir.glob("*.su"):
        for line in path.read_text().splitlines():
            location, size, category = line.split("\t")
            stack[location.rsplit(":", 1)[-1]] = {"bytes": int(size), "kind": category}
            if path.stem in ("hanger_measurement", "hanger_state"):
                core_frames_sum += int(size)
                check.require(category == "static", "bounded compiler stack frame")
    nm = compiler_path.with_name("nm.exe" if os.name == "nt" else "nm")
    symbols = run([str(nm), "-u", *objects[:2]]).stdout
    check.require(not any(s in symbols for s in ("malloc", "realloc", "calloc", "alloca", " free")), "no allocator reference in compiled core")
    (build_dir / "measurement-undefined-symbols.txt").write_text(symbols, encoding="utf-8")
    check.require(source_before == source_hashes(), "sources stable during build and comparison")
    result = {"passed": True, "check_count": check.count, "commands": commands, "command_cwd": str(build_dir), "source_sha256": source_before,
              "metrics": {"compiler": compiler_version, "numeric_max_abs_error_Pa": check.max_error,
                          "numeric_tolerance_Pa": 1e-10, "random_seed": 20261026, "simulation_seed": DEFAULT_SEED,
                          **check.groups, "sizeof_bytes": sizes, "raw_buffer_512_bytes": 512 * sizes["hm_observation"],
                          "stack_frames_host_bytes": stack, "core_frames_sum_excluding_libraries_bytes": core_frames_sum,
                          "limits": {"samples":512,"messages":64,"cycles":16,"boots":8,"installations":32,"retired_batches":16,"id_ascii_bytes":32,"time_s":1e9}},
              "reference_configuration": DEFAULT_CONFIG.snapshot(),
              "verification_protocols": {k:p.snapshot() for k,p in verification_protocols().items()},
              "simulation_case_parameters": {k:asdict(p) for k,(p,_) in cases().items()},
              "scope": "Synthetic host C/Python differential comparison; no device, persistent storage or real garment calibration."}
    receipt_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # Keep search handle alive through all C calls.
    if dll_search is not None: dll_search.close()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--compiler", default="C:/msys64=/ucrt64/bin/gcc.exe")
    args = parser.parse_args()
    result = run_validation(args.compiler, ROOT / "firmware/host/build/measurement")
    print(json.dumps(result, ensure_ascii=False, indent=2))

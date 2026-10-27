"""Build and execute real C99 energy primitives against the accepted T05 Python.

Only the bridge transports native values; expected outputs never enter C.
No upstream files, global status or primary test receipts are written.
"""
import argparse
import ctypes as ct
from dataclasses import asdict, replace
import hashlib
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.energy_t05.contracts import Energy, Scenario, CATEGORIES, measurement_components, required_battery_wh
from research.energy_t05.model import Scheduler
from research.state_machine.machine import Configuration, DEFAULT_CONFIG
from theory.protocol import MeasurementProtocol

DEFAULT_COMPILER = r"C:\msys64=\ucrt64\bin\gcc.exe"
SEED = 20261026
ABS_TOL = REL_TOL = 2e-12
U64_MAX = (1 << 64) - 1
U32_MAX = (1 << 32) - 1
DAY = 86400
OK, ARGUMENT, RANGE, CONFIG, OVERFLOW, TIME, STATE, UNINITIALIZED = range(8)
SOURCES = ("firmware/core/hanger_energy.h", "firmware/core/hanger_energy.c",
           "firmware/host/energy_bridge.c", "firmware/host/energy_validation.py")
INPUTS = ("research/energy_t05/contracts.py", "research/energy_t05/model.py",
          "research/energy_t05/results/manifest.json", "research/state_machine/machine.py",
          "theory/protocol.py", "theory/model.py", "docs/ENERGY-SCHEDULING.md",
          "docs/team/assignments/energy-t06.md")
REFERENCE_ARTIFACTS = ("inputs.json",) + tuple(
    f"case-{index:02d}-{strategy}-ledger.jsonl" for index in range(1, 7) for strategy in ("fixed", "demand"))
INPUTS += tuple("research/energy_t05/results/" + name for name in REFERENCE_ARTIFACTS)
D, U64, U32, U8, INT = ct.c_double, ct.c_uint64, ct.c_uint32, ct.c_uint8, ct.c_int


class Config(ct.Structure):
    _fields_ = [(name, D) for name in (
        "capacity_wh", "eta", "base_w", "misc_w", "stopped_w", "baseline_w", "flush_w",
        "recovery_w", "fan_w", "vent_open_j", "vent_close_j", "wifi_w", "boot_wh",
        "stop_wh", "restart_wh", "low_enter_wh", "low_exit_wh", "wifi_daily_wh")]
    _fields_ += [("wifi_success_s", U64), ("wifi_timeout_s", U64),
                 ("wifi_attempt_limit", U32), ("backoff_s", U64 * 4)]


class Protocol(ct.Structure):
    _fields_ = [(name, U32) for name in ("baseline_s", "flush_s", "recovery_s")]


class Budget(ct.Structure):
    _fields_ = [("duration_s", U64), ("components_wh", D * 11), ("action_load_wh", D),
                ("background_load_wh", D), ("required_battery_wh", D)]


class Balance(ct.Structure):
    _fields_ = [("store_start_wh", D), ("solar_battery_wh", D),
                ("requested_load_wh", D * 11), ("served_load_wh", D * 11)]
    _fields_ += [(name, D) for name in ("unmet_load_wh", "conversion_loss_wh", "spill_wh",
                                      "store_end_wh", "balance_residual_wh")]


class Power(ct.Structure):
    _fields_ = [("powered", U8), ("mode", INT), ("store_wh", D)]


class PowerResult(ct.Structure):
    _fields_ = [("state", Power), ("booted", U8), ("stopped", U8), ("stop_deferred", U8), ("boot_load_wh", D)]


class Quota(ct.Structure):
    _fields_ = [("last_time_s", U64), ("day", U64), ("used_battery_wh", D * 2), ("initialized", U8)]


class Wifi(ct.Structure):
    _fields_ = [("first_day", U64), ("battery_wh", D * 2), ("allowed", U8)]


class Admission(ct.Structure):
    _fields_ = [("kind", INT)]
    _fields_ += [(name, U8) for name in ("powered", "installation_queued", "installed", "batch_matches",
                "has_request", "critical", "low_mode", "can_emit", "has_reserved_window")]
    _fields_ += [("readiness", INT)]
    _fields_ += [(name, U64) for name in ("now_s", "horizon_s", "deadline_s", "stable_after_s", "reserved_end_s", "min_cycle_gap_s")]
    _fields_ += [("store_wh", D)]


class AdmissionResult(ct.Structure):
    _fields_ = [("reason", INT), ("complete_at_s", U64), ("required_battery_wh", D)]


class Retry(ct.Structure):
    _fields_ = [("exhausted", U8), ("next_attempt_s", U64)]


STRUCTS = (Config, Protocol, Budget, Balance, Power, PowerResult, Quota, Wifi, Admission, AdmissionResult, Retry)


def plain(value):
    if isinstance(value, ct.Structure):
        return {name: plain(getattr(value, name)) for name, _ in value._fields_}
    if isinstance(value, ct.Array):
        return [plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    return value


def config(e):
    c = Config()
    for name, _ in c._fields_:
        if name == "backoff_s":
            c.backoff_s[:] = [checked_unsigned(v, 64, name) for v in e.backoff_s]
        else:
            value = getattr(e, name)
            if name in ("wifi_success_s", "wifi_timeout_s", "wifi_attempt_limit"):
                value = checked_unsigned(value, 32 if name == "wifi_attempt_limit" else 64, name)
            setattr(c, name, value)
    return c


def protocol(p):
    values = []
    for name in ("baseline_s", "flush_s", "recovery_s"):
        value = getattr(p, name)
        if type(value) not in (int, float) or not math.isfinite(value) or value != int(value):
            raise ValueError(name + ' must be an integer second')
        values.append(checked_unsigned(int(value), 32, name))
    return Protocol(*values)


def checked_unsigned(value, bits, name):
    if type(value) is not int or not 0 <= value < (1 << bits):
        raise ValueError(f'{name} is outside uint{bits}')
    return value


def reference(e=Energy(), p=MeasurementProtocol()):
    return Scheduler(Scenario("c-reference", 400000, energy=e,
                              configuration=Configuration(protocol=p).snapshot()), "demand")


def admission(**overrides):
    values = dict(kind=0, powered=1, installation_queued=0, installed=1, batch_matches=1,
                  has_request=1, critical=1, low_mode=0, can_emit=1, has_reserved_window=0,
                  readiness=2, now_s=1000, horizon_s=100000, deadline_s=10000,
                  stable_after_s=30, reserved_end_s=900, min_cycle_gap_s=30, store_wh=1.0)
    values.update(overrides)
    for name, typ in Admission._fields_:
        if typ in (U8, U64):
            checked_unsigned(values[name], 8 if typ is U8 else 64, name)
    return Admission(**values)


def fingerprints(paths):
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in paths}


class Harness:
    def __init__(self, lib):
        self.lib = lib
        self.records = []
        self.numeric_comparisons = 0
        self.max_absolute_error = self.max_relative_error = 0.0
        self.max_relative_error_above_abs_tol = 0.0
        signatures = {
            "config": ([ct.POINTER(Config)], INT),
            "protocol": ([ct.POINTER(Protocol)], INT),
            "add": ([U64, U64, ct.POINTER(U64)], INT),
            "budget": ([ct.POINTER(Config), ct.POINTER(Protocol), INT, ct.POINTER(Budget)], INT),
            "account": ([ct.POINTER(Config), INT, D, ct.POINTER(D), D, ct.POINTER(Balance)], INT),
            "power": ([ct.POINTER(Config), ct.POINTER(Power), U8, ct.POINTER(PowerResult)], INT),
            "quota_init": ([ct.POINTER(Quota), U64, D, D], INT),
            "quota_advance": ([ct.POINTER(Quota), U64], INT),
            "wifi_check": ([ct.POINTER(Config), ct.POINTER(Quota), U64, ct.POINTER(Wifi)], INT),
            "wifi_charge": ([ct.POINTER(Config), ct.POINTER(Quota), U64, U64, ct.POINTER(Wifi)], INT),
            "admit": ([ct.POINTER(Config), ct.POINTER(Protocol), ct.POINTER(Admission), ct.POINTER(Quota), ct.POINTER(AdmissionResult)], INT),
            "retry": ([ct.POINTER(Config), U32, U64, ct.POINTER(Retry)], INT),
            "reason": ([INT], ct.c_char_p),
            "layout": ([ct.POINTER(ct.c_size_t), ct.c_size_t], ct.c_size_t),
        }
        for name, (args, result) in signatures.items():
            func = getattr(lib, "energy_" + name)
            func.argtypes, func.restype = args, result
            setattr(self, name, func)

    def compare(self, actual, expected, path):
        if isinstance(expected, dict):
            if actual.keys() != expected.keys():
                raise AssertionError((path, actual.keys(), expected.keys()))
            for key in expected:
                self.compare(actual[key], expected[key], path + "." + key)
        elif isinstance(expected, list):
            if len(actual) != len(expected):
                raise AssertionError((path, "length mismatch"))
            for index, (a, e) in enumerate(zip(actual, expected)):
                self.compare(a, e, f"{path}[{index}]")
        elif isinstance(expected, float):
            if not math.isfinite(actual) or not math.isclose(actual, expected, abs_tol=ABS_TOL, rel_tol=REL_TOL):
                raise AssertionError((path, actual, expected))
            self.numeric_comparisons += 1
            error = abs(actual - expected)
            self.max_absolute_error = max(self.max_absolute_error, error)
            if expected != 0:
                self.max_relative_error = max(self.max_relative_error, error / abs(expected))
            if abs(expected) > ABS_TOL:
                self.max_relative_error_above_abs_tol = max(self.max_relative_error_above_abs_tol, error / abs(expected))
        elif actual != expected:
            raise AssertionError((path, actual, expected))

    def check(self, group, label, actual, expected, inputs=None):
        actual, expected = plain(actual), plain(expected)
        self.compare(actual, expected, group + ":" + label)
        self.records.append(dict(group=group, label=label, input=plain(inputs), actual=actual, expected=expected))

    def error(self, label, expected, call, outputs=(), inputs=None):
        before = [bytes(out) for out in outputs]
        code = call()
        self.check("invalid", label, {"code": code, "unchanged": [bytes(o) == b for o, b in zip(outputs, before)]},
                   {"code": expected, "unchanged": [True] * len(outputs)}, inputs)

    def new_quota(self, now, current=0.0, next_used=0.0):
        q = Quota()
        status = self.quota_init(ct.byref(q), now, current, next_used)
        if status != OK:
            raise AssertionError(("valid quota initialization", status))
        return q

    def budget_case(self, label, e, p, kind):
        c, cp, out = config(e), protocol(p), Budget()
        status = self.budget(ct.byref(c), ct.byref(cp), kind, ct.byref(out))
        parts = measurement_components(e, p) if kind == 0 else {"wifi": e.wifi_w * e.wifi_timeout_s / 3600}
        duration = int(p.total_s) if kind == 0 else e.wifi_timeout_s
        expected = dict(duration_s=duration, components_wh=[parts.get(k, 0.0) for k in CATEGORIES],
                        action_load_wh=sum(parts.values()), background_load_wh=(e.base_w + e.misc_w) * duration / 3600,
                        required_battery_wh=required_battery_wh(e, p, "measurement" if kind == 0 else "wifi"))
        self.check("budget", label, {"status": status, "result": plain(out)}, {"status": OK, "result": expected},
                   {"config": c, "protocol": cp, "kind": kind})

    def account_case(self, label, e, store, requested, solar=0.0, impulse=False):
        c, out = config(e), Balance()
        amounts = (D * 11)(*(requested.get(k, 0.0) for k in CATEGORIES))
        status = self.account(ct.byref(c), int(impulse), store, amounts, solar, ct.byref(out))
        py = reference(e); py.store = store
        py.account(0, 0 if impulse else 1, requested, solar)
        row = py.ledger[0]
        expected = {k: row[k] for k in ("store_start_wh", "solar_battery_wh", "unmet_load_wh",
                                        "conversion_loss_wh", "spill_wh", "store_end_wh")}
        expected.update(requested_load_wh=[requested.get(k, 0.0) for k in CATEGORIES],
                        served_load_wh=[row["served_load_wh"].get(k, 0.0) for k in CATEGORIES],
                        balance_residual_wh=store + solar - sum(row["served_load_wh"].values()) / e.eta - row["spill_wh"] - py.store)
        self.check("account", label, {"status": status, "result": plain(out)}, {"status": OK, "result": expected},
                   {"config": c, "store_wh": store, "requested": list(amounts), "solar_wh": solar, "impulse": impulse})
        self.check("conservation", label, out.balance_residual_wh, 0.0)

    def power_case(self, label, e, powered, mode, store, can_emit):
        c, state, out = config(e), Power(powered, mode, store), PowerResult()
        status = self.power(ct.byref(c), ct.byref(state), can_emit, ct.byref(out))
        py = reference(e); py.powered = bool(powered); py.mode = ("stopped", "low", "normal")[mode]
        py.store = store; py.last_event_wall = 999 if can_emit else 1000
        py.boot_or_stop(1000)
        expected = dict(state=dict(powered=int(py.powered), mode=("stopped", "low", "normal").index(py.mode), store_wh=py.store),
                        booted=int(not powered and py.powered), stopped=int(powered and not py.powered),
                        stop_deferred=int(powered and store <= e.stop_wh + 1e-12 and not can_emit),
                        boot_load_wh=sum(row["served_load_wh"].get("boot", 0.0) for row in py.ledger))
        self.check("power", label, {"status": status, "result": plain(out)}, {"status": OK, "result": expected},
                   {"config": c, "state": state, "can_emit": can_emit})

    def admit_case(self, label, e, p, a, used=(0.0, 0.0)):
        c, cp, out = config(e), protocol(p), AdmissionResult()
        q = self.new_quota(a.now_s, *used)
        status = self.admit(ct.byref(c), ct.byref(cp), ct.byref(a), ct.byref(q), ct.byref(out))
        py = reference(e, p); py.powered = bool(a.powered); py.mode = "low" if a.low_mode else "normal"
        py.store = a.store_wh; py.install_queue = ["queued"] if a.installation_queued else []
        py.machine.installation_id = "install" if a.installed else None; py.machine.batch_id = "batch"
        py.machine.state = ("FAULT", "STABILIZING", "READY", "AWAITING_RECHECK", "SOURCE_PRESENT")[a.readiness]
        py.machine.stable_after_s = a.stable_after_s; py.last_event_wall = a.now_s - 1 if a.can_emit else a.now_s
        py.reserved_end_wall = a.reserved_end_s if a.has_reserved_window else None
        py.config = replace(py.config, policy=replace(py.config.policy, min_cycle_gap_s=a.min_cycle_gap_s))
        py.scenario = replace(py.scenario, duration_s=a.horizon_s)
        day = a.now_s // DAY; py.wifi_used = {day: used[0], day + 1: used[1]}
        request = dict(batch_id="batch" if a.batch_matches else "stale", critical=bool(a.critical), deadline_s=a.deadline_s) if a.has_request else None
        kind = "measurement" if a.kind == 0 else "wifi"
        reason = py.eligibility(a.now_s, kind, request)
        expected = dict(status=OK, reason=reason, complete_at_s=a.now_s + (int(p.total_s) if a.kind == 0 else e.wifi_timeout_s),
                        required_battery_wh=required_battery_wh(e, p, kind))
        self.check("admission", label, dict(status=status, reason=self.reason(out.reason).decode("ascii"),
                   complete_at_s=out.complete_at_s, required_battery_wh=out.required_battery_wh), expected,
                   {"config": c, "protocol": cp, "admission": a, "used_battery_wh": used})

    def wifi_case(self, label, e, start, used, elapsed):
        c, q, out = config(e), self.new_quota(start, *used), Wifi()
        py = reference(e); day = start // DAY; py.wifi_used = {day: used[0], day + 1: used[1]}
        before = plain(q)
        status = self.wifi_check(ct.byref(c), ct.byref(q), start, ct.byref(out))
        expected_allowed = int(py.wifi_budget_ok(start))
        portions = [0.0, 0.0]
        for t in range(start, start + e.wifi_timeout_s):
            portions[t // DAY - day] += e.wifi_w / 3600 / e.eta
        self.check("wifi-check", label, dict(status=status, result=plain(out), ledger=plain(q)),
                   dict(status=OK, result=dict(first_day=day, battery_wh=portions, allowed=expected_allowed), ledger=before),
                   {"config": c, "start_s": start, "used": used})
        # Charge through the real T05 per-second runtime, not a duplicate formula.
        py.active = dict(kind="wifi", id="attempt", network_ok=True, paid_components_wh={})
        paid = Wifi()
        status = self.wifi_charge(ct.byref(c), ct.byref(q), start, elapsed, ct.byref(paid))
        for t in range(start, start + elapsed):
            py.spend_second(t)
        end_day = (start + elapsed) // DAY
        expected_q = dict(last_time_s=start + elapsed, day=end_day,
                          used_battery_wh=[py.wifi_used.get(end_day, 0.0), py.wifi_used.get(end_day + 1, 0.0)], initialized=1)
        actual_cost = [0.0, 0.0]
        for t in range(start, start + elapsed):
            actual_cost[t // DAY - day] += e.wifi_w / 3600 / e.eta
        within = int(all(py.wifi_used.get(d, 0) <= e.wifi_daily_wh + 1e-12 for d in (day, day + 1)))
        self.check("wifi-charge", label, dict(status=status, result=plain(paid), ledger=plain(q)),
                   dict(status=OK, result=dict(first_day=day, battery_wh=actual_cost, allowed=within), ledger=expected_q),
                   {"config": c, "start_s": start, "elapsed_s": elapsed, "used": used})

    def retry_case(self, label, e, attempts, end):
        c, out = config(e), Retry()
        status = self.retry(ct.byref(c), attempts, end, ct.byref(out))
        py = reference(e)
        request = dict(id="r", at_s=0, status="running", attempts=attempts)
        py.pending = {"r": request}
        py.active = dict(kind="wifi", request_id="r", started_s=end - e.wifi_timeout_s, network_ok=False)
        py.finish(end)
        exhausted = request["status"] == "retry_exhausted"
        self.check("retry", label, dict(status=status, result=plain(out)),
                   dict(status=OK, result=dict(exhausted=int(exhausted), next_attempt_s=0 if exhausted else request["next_attempt_s"])),
                   {"config": c, "attempts_done": attempts, "failed_at_s": end})


def legal_cases(h):
    e, p = Energy(), MeasurementProtocol()
    for kind in (0, 1):
        h.budget_case("default-" + str(kind), e, p, kind)
    budget = Budget(); c, cp = config(e), protocol(p)
    h.budget(ct.byref(c), ct.byref(cp), 0, ct.byref(budget))
    h.check("analytic", "full-cycle-joules", budget.action_load_wh * 3600, 4.38)
    h.account_case("empty", e, 0.0, {"background_stop": 0.001})
    h.account_case("full-spill", e, e.capacity_wh, {"background_run": 0.001}, 0.1)
    h.account_case("category-priority", e, 0.02, {k: 0.01 for k in CATEGORIES})
    h.account_case("idle", e, 0.5, {"background_run": e.base_w / 3600, "misc": e.misc_w / 3600})
    h.account_case("zero", e, 0.0, {}, 0.0)
    h.account_case("vent-close", e, 0.5, {"vent_close": e.vent_close_j / 3600}, impulse=True)
    for boundary in (0.0, e.stop_wh, e.restart_wh, e.low_enter_wh, e.low_exit_wh, e.capacity_wh):
        for delta in (-1e-9, 0, 1e-9):
            store = boundary + delta
            if 0 <= store <= e.capacity_wh:
                for powered, mode in ((0, 0), (1, 1), (1, 2)):
                    for slot in (0, 1):
                        h.power_case(f"{boundary}:{delta}:{mode}:{slot}", e, powered, mode, store, slot)
    cases = [dict(), dict(powered=0), dict(installation_queued=1), dict(installed=0), dict(batch_matches=0),
             dict(low_mode=1, critical=0), dict(low_mode=1, critical=0, has_request=0),
             dict(horizon_s=1140), dict(horizon_s=1139), dict(deadline_s=1140), dict(deadline_s=1139),
             dict(readiness=0), dict(readiness=1, stable_after_s=1000), dict(readiness=1, stable_after_s=1001),
             dict(readiness=3), dict(readiness=4), dict(can_emit=0),
             dict(has_reserved_window=1, reserved_end_s=970), dict(has_reserved_window=1, reserved_end_s=971),
             dict(store_wh=e.stop_wh + required_battery_wh(e, p, "measurement")),
             dict(store_wh=e.stop_wh + required_battery_wh(e, p, "measurement") - 1e-9),
             dict(powered=0, installed=0, low_mode=1, critical=0),
             dict(kind=1), dict(kind=1, readiness=0, can_emit=0, has_reserved_window=1, reserved_end_s=9999),
             dict(kind=1, deadline_s=1020), dict(kind=1, deadline_s=1019),
             dict(kind=1, store_wh=e.stop_wh + required_battery_wh(e, p, "wifi")),
             dict(kind=1, store_wh=e.stop_wh + required_battery_wh(e, p, "wifi") - 1e-9)]
    for i, changes in enumerate(cases):
        h.admit_case("boundary-" + str(i), e, p, admission(**changes))
    h.admit_case("no-future-solar-credit", e, p, admission(store_wh=e.stop_wh))
    per = e.wifi_w / 3600 / e.eta
    for start in (0, DAY - 20, DAY - 10, DAY - 1, DAY, 3 * DAY + 9):
        for used in ((0.0, 0.0), (e.wifi_daily_wh, 0.0), (0.0, e.wifi_daily_wh)):
            h.wifi_case(f"boundary-{start}-{used}", e, start, used, e.wifi_success_s)
    amount = sum(per for _ in range(e.wifi_timeout_s))
    for used in (e.wifi_daily_wh - amount, e.wifi_daily_wh - amount + 1e-9):
        h.wifi_case("quota-equality-" + str(used), e, 100, (used, 0.0), e.wifi_timeout_s)
        h.admit_case("quota-reason-" + str(used), e, p, admission(kind=1), (used, 0.0))
    h.wifi_case("full-timeout-failure-cost", e, 1234, (0.0, 0.0), 20)
    h.wifi_case("zero-wifi-existing-overrun", replace(e, wifi_w=0), DAY-1, (0.0, e.wifi_daily_wh+0.01), 6)
    for attempts in range(1, 8):
        h.retry_case("attempt-" + str(attempts), e, attempts, 1000)
    many = replace(e, wifi_attempt_limit=9)
    h.retry_case("backoff-last-slot-repeat", many, 7, 1000)
    # Saved T05 raw one-second or impulse steps only; NEVER replay nonlinear
    # merged intervals as if their harvest and requests were a single step.
    saved = json.loads((ROOT / "research/energy_t05/results/inputs.json").read_text(encoding="utf-8"))
    for index, scenario in enumerate(saved["scenarios"], 1):
        energy = Energy(**scenario["energy"])
        for strategy in ("fixed", "demand"):
            path = ROOT / f"research/energy_t05/results/case-{index:02d}-{strategy}-ledger.jsonl"
            selected = []
            for line in path.open(encoding="utf-8"):
                row = json.loads(line)
                if row["end_s"] - row["start_s"] in (0, 1):
                    selected.append(row)
            for n in sorted(set((0, len(selected)//2, len(selected)-1))):
                row = selected[n]
                h.account_case(f"saved-{index}-{strategy}-{n}", energy, row["store_start_wh"],
                               row["requested_load_wh"], row["solar_battery_wh"], row["end_s"] == row["start_s"])
    rng = random.Random(SEED)
    for i in range(160):
        e = replace(Energy(), capacity_wh=rng.uniform(4, 10), eta=rng.uniform(.55, 1),
                    base_w=rng.uniform(.0001, .05), misc_w=rng.uniform(0, .001),
                    baseline_w=rng.uniform(0, .02), flush_w=rng.uniform(0, .02), recovery_w=rng.uniform(0, .02),
                    fan_w=rng.uniform(0, 1.5), wifi_w=rng.uniform(0, 2), vent_open_j=rng.uniform(0, .2),
                    vent_close_j=rng.uniform(0, .2), wifi_timeout_s=rng.randint(6, 180),
                    wifi_attempt_limit=rng.randint(1, 8), wifi_daily_wh=rng.uniform(.001, .2))
        p = replace(MeasurementProtocol(), baseline_s=rng.randint(10, 60), flush_s=rng.randint(5, 40), recovery_s=rng.randint(30, 120))
        for kind in (0, 1):
            h.budget_case(f"random-{i}-{kind}", e, p, kind)
        requested = {k: rng.uniform(0, 0.2) for k in CATEGORIES}
        h.account_case(f"random-{i}", e, rng.uniform(0, e.capacity_wh), requested, rng.uniform(0, 1))
        mode = rng.randrange(3)
        h.power_case(f"random-{i}", e, int(mode != 0), mode, rng.uniform(0, e.capacity_wh), rng.randrange(2))
        a = admission(kind=rng.randrange(2), powered=rng.randrange(2), installed=rng.randrange(2),
                      installation_queued=int(rng.random() < .1), has_request=rng.randrange(2), batch_matches=rng.randrange(2),
                      critical=rng.randrange(2), low_mode=rng.randrange(2), can_emit=rng.randrange(2),
                      readiness=rng.randrange(5), now_s=1000, horizon_s=rng.randint(1000, 1400),
                      deadline_s=rng.randint(1000, 1400), stable_after_s=rng.randint(990, 1010),
                      has_reserved_window=rng.randrange(2), reserved_end_s=rng.randint(900, 1000), store_wh=rng.uniform(0, e.capacity_wh))
        h.admit_case(f"random-{i}", e, p, a, (rng.uniform(0, .2), rng.uniform(0, .2)))
        start = DAY + rng.randint(-180, 180)
        h.wifi_case(f"random-{i}", e, start, (rng.uniform(0, .2), rng.uniform(0, .2)), rng.randint(1, e.wifi_timeout_s))
        h.retry_case(f"random-{i}", e, rng.randint(1, 10), 10000)


def boundary_and_invalid_cases(h):
    e, c, p = Energy(), config(Energy()), protocol(MeasurementProtocol())
    for bits in (8, 32, 64):
        for value in (-1, 1 << bits, float('nan'), float('inf'), 1.5, True):
            rejected = False
            try:
                checked_unsigned(value, bits, 'transport_time')
            except ValueError:
                rejected = True
            h.check('transport', f'uint{bits}-{value}', rejected, True, {'value': value, 'bits': bits})
    # Calendar state is a C bounded-storage addition. T05 retains an unbounded
    # dictionary; compare admissions to that dictionary while advancing days.
    q = h.new_quota(DAY - 10, .02, .03)
    py = reference(e); py.wifi_used = {0: .02, 1: .03}
    for now in (DAY - 10, DAY, 2 * DAY, 7 * DAY, 7 * DAY + 1):
        out = Wifi(); status = h.wifi_check(ct.byref(c), ct.byref(q), now, ct.byref(out))
        day = now // DAY
        h.check("calendar", str(now), dict(status=status, allowed=out.allowed, ledger=plain(q)),
                dict(status=OK, allowed=int(py.wifi_budget_ok(now)), ledger=dict(last_time_s=now, day=day,
                    used_battery_wh=[py.wifi_used.get(day, 0.0), py.wifi_used.get(day+1, 0.0)], initialized=1)))
    h.error("backward-second", TIME, lambda: h.quota_advance(ct.byref(q), 7*DAY), (q,))
    h.error("old-day-no-resurrection", TIME, lambda: h.quota_advance(ct.byref(q), 0), (q,))
    # Repeated checks at equal time do not double charge; no physical reboot API
    # exists in this module, so preserve q across controller boot transitions.
    q = h.new_quota(100, .08, 0); out = Wifi(); before = bytes(q)
    for n in range(2):
        code = h.wifi_check(ct.byref(c), ct.byref(q), 100, ct.byref(out))
        h.check("calendar", f"repeated-denial-{n}", (code, out.allowed, bytes(q)==before), (OK, 0, True))
    untouched = Quota(); h.error("unrestored-ledger", UNINITIALIZED,
        lambda: h.wifi_check(ct.byref(c), ct.byref(untouched), 100, ct.byref(out)), (untouched, out))
    q = h.new_quota(DAY-2, 0, 0)
    h.wifi_charge(ct.byref(c), ct.byref(q), DAY-2, 6, ct.byref(out))
    h.error("overlapping-charge", TIME, lambda: h.wifi_charge(ct.byref(c), ct.byref(q), DAY, 1, ct.byref(out)), (q,out))
    h.error("time-sum-wrap", OVERFLOW, lambda: h.add(U64_MAX, 1, ct.byref(U64(123))))
    result = U64()
    h.check("integer", "u64-exact-end", (h.add(U64_MAX-140, 140, ct.byref(result)), result.value), (OK,U64_MAX))
    bo = Budget(); bal = Balance(); po = PowerResult(); ar = AdmissionResult(); retry = Retry()
    amounts = (D*11)()
    h.error("null-config", ARGUMENT, lambda: h.config(None))
    h.error("null-protocol", ARGUMENT, lambda: h.protocol(None))
    h.error("null-result", ARGUMENT, lambda: h.budget(ct.byref(c),ct.byref(p),0,None))
    h.error("null-load-array", ARGUMENT, lambda: h.account(ct.byref(c),0,1,None,0,ct.byref(bal)),(bal,))
    for name, _ in Config._fields_[:18]:
        for bad in (float('nan'),float('inf'),-1.0):
            invalid=config(e); setattr(invalid,name,bad)
            h.error(name+'-'+str(bad),CONFIG,lambda: h.config(ct.byref(invalid)),inputs=invalid)
            python_rejected = False
            try:
                replace(e, **{name: bad}).validate(DEFAULT_CONFIG)
            except ValueError:
                python_rejected = True
            h.check('invalid-reference', name+'-'+str(bad), python_rejected, True)
    for name,value in (("eta",0),('eta',1.1),('restart_wh',.01),('low_exit_wh',.2),
                       ('wifi_success_s',0),('wifi_timeout_s',DAY+1),('wifi_attempt_limit',0)):
        invalid=config(e);setattr(invalid,name,value)
        h.error(name+'-invalid',CONFIG,lambda:h.config(ct.byref(invalid)),inputs=invalid)
    invalid=config(e);invalid.backoff_s[3]=0
    h.error('zero-backoff',CONFIG,lambda:h.config(ct.byref(invalid)),inputs=invalid)
    for phase in ('baseline_s','flush_s','recovery_s'):
        invalid=protocol(MeasurementProtocol());setattr(invalid,phase,0)
        h.error('zero-'+phase,CONFIG,lambda:h.protocol(ct.byref(invalid)),inputs=invalid)
    h.error('invalid-kind',RANGE,lambda:h.budget(ct.byref(c),ct.byref(p),2,ct.byref(bo)),(bo,))
    invalid=config(e);invalid.fan_w=1e308
    h.error('budget-multiply-overflow',OVERFLOW,lambda:h.budget(ct.byref(invalid),ct.byref(p),0,ct.byref(bo)),(bo,))
    for bad in (-1.0,float('inf'),float('nan'),e.capacity_wh+1):
        h.error('invalid-store-'+str(bad),RANGE,lambda:h.account(ct.byref(c),0,bad,amounts,0,ct.byref(bal)),(bal,))
    for bad in (-1.0,float('inf'),float('nan')):
        h.error('invalid-solar-'+str(bad),RANGE,lambda:h.account(ct.byref(c),0,1,amounts,bad,ct.byref(bal)),(bal,))
        for index in range(11):
            arr=(D*11)();arr[index]=bad
            h.error(f'invalid-load-{index}-{bad}',RANGE,lambda:h.account(ct.byref(c),0,1,arr,0,ct.byref(bal)),(bal,))
    arr=(D*11)(*([1e308]*11))
    h.error('demand-sum-overflow',OVERFLOW,lambda:h.account(ct.byref(c),0,1,arr,0,ct.byref(bal)),(bal,))
    h.error('impulse-cannot-borrow-solar',RANGE,lambda:h.account(ct.byref(c),1,0,amounts,1,ct.byref(bal)),(bal,))
    h.error('invalid-step-kind',RANGE,lambda:h.account(ct.byref(c),2,1,amounts,0,ct.byref(bal)),(bal,))
    for powered,mode,slot in ((2,2,1),(1,0,1),(0,2,1),(1,2,2)):
        state=Power(powered,mode,1)
        h.error(f'invalid-power-{powered}-{mode}-{slot}',RANGE if powered==2 or slot==2 else STATE,
                lambda:h.power(ct.byref(c),ct.byref(state),slot,ct.byref(po)),(po,))
    # Tiny reserves may be admitted by Python's epsilon comparison without
    # actually funding boot; C explicitly fails instead of claiming paid boot.
    tiny=replace(e,stop_wh=0,restart_wh=2e-14,low_enter_wh=.1,boot_wh=1e-14)
    tc=config(tiny);ts=Power(0,0,0)
    h.error('unpaid-sub-epsilon-boot',STATE,lambda:h.power(ct.byref(tc),ct.byref(ts),1,ct.byref(po)),(po,))
    for field,_ in Admission._fields_:
        if field in ('kind','readiness') or getattr(Admission,field).size==1:
            a=admission();setattr(a,field,99)
            h.error('invalid-admission-'+field,RANGE,lambda:h.admit(ct.byref(c),ct.byref(p),ct.byref(a),None,ct.byref(ar)),(ar,))
    for bad in (float('nan'),float('inf'),-1.0):
        a=admission(store_wh=bad)
        h.error('admission-store-'+str(bad),RANGE,lambda:h.admit(ct.byref(c),ct.byref(p),ct.byref(a),None,ct.byref(ar)),(ar,))
    for label,a,expected in [('past-horizon',admission(horizon_s=999),TIME),
                            ('zero-minimum-gap',admission(min_cycle_gap_s=0),CONFIG),
                            ('complete-overflow',admission(now_s=U64_MAX-1,horizon_s=U64_MAX),OVERFLOW),
                            ('reserved-overflow',admission(has_reserved_window=1,reserved_end_s=U64_MAX),OVERFLOW)]:
        h.error(label,expected,lambda:h.admit(ct.byref(c),ct.byref(p),ct.byref(a),None,ct.byref(ar)),(ar,))
    for callname in ('wifi_check','wifi_charge'):
        q=h.new_quota(U64_MAX-1)
        call = (lambda:h.wifi_check(ct.byref(c),ct.byref(q),U64_MAX-1,ct.byref(out))) if callname=='wifi_check' else (lambda:h.wifi_charge(ct.byref(c),ct.byref(q),U64_MAX-1,6,ct.byref(out)))
        h.error(callname+'-end-overflow',OVERFLOW,call,(q,out))
    q=h.new_quota(1)
    for duration in (0,e.wifi_timeout_s+1,U64_MAX):
        h.error('invalid-charge-length-'+str(duration),RANGE,lambda:h.wifi_charge(ct.byref(c),ct.byref(q),1,duration,ct.byref(out)),(q,out))
    h.error('retry-zero-attempts',RANGE,lambda:h.retry(ct.byref(c),0,100,ct.byref(retry)),(retry,))
    h.error('retry-overflow',OVERFLOW,lambda:h.retry(ct.byref(c),1,U64_MAX,ct.byref(retry)),(retry,))
    h.check('integer','exhausted-at-u64-end',(h.retry(ct.byref(c),U32_MAX,U64_MAX,ct.byref(retry)),plain(retry)),
            (OK,dict(exhausted=1,next_attempt_s=0)))
    h.check('integer','invalid-reason-name',h.reason(99).decode('ascii'),'invalid_reason')
    # Largest supported wifi interval and exact midnight quota boundaries.
    big=replace(e,wifi_timeout_s=DAY,wifi_daily_wh=20.0)
    bc=config(big);q=h.new_quota(DAY-1);out=Wifi();py=reference(big)
    h.check('integer','one-day-timeout',(h.wifi_check(ct.byref(bc),ct.byref(q),DAY-1,ct.byref(out)),out.allowed),
            (OK,int(py.wifi_budget_ok(DAY-1))))


def run_validation(compiler, build_dir):
    """Return JSON-safe evidence after real compilation and all native calls.

    Raises on compile/load/ABI/input freshness/comparison failures. Each run
    gets its own subdirectory to avoid overwriting a DLL loaded by this process.
    """
    compiler_path=Path(compiler)
    if not compiler_path.is_absolute() or not compiler_path.is_file():
        raise ValueError('compiler must be an existing absolute path')
    build_dir=Path(build_dir).resolve()
    allowed=(HERE/'build'/'energy').resolve()
    if not build_dir.is_relative_to(allowed):
        raise ValueError('build_dir must remain within firmware/host/build/energy')
    build_dir.mkdir(parents=True,exist_ok=True)
    run_dir=Path(tempfile.mkdtemp(prefix='energy_run_',dir=build_dir))
    source_hashes,input_hashes=fingerprints(SOURCES),fingerprints(INPUTS)
    accepted=json.loads((ROOT/'research/energy_t05/results/manifest.json').read_text(encoding='utf-8'))
    for base,mapping in ((ROOT/'research/energy_t05',accepted['source_sha256']),
                         (ROOT,accepted['input_sha256'])):
        for name,expected in mapping.items():
            if hashlib.sha256((base/name).read_bytes()).hexdigest()!=expected:
                raise AssertionError(('accepted T05 input/source changed',name))
    for name in REFERENCE_ARTIFACTS:
        if input_hashes['research/energy_t05/results/'+name]!=accepted['output_sha256'][name]:
            raise AssertionError(('accepted T05 artifact changed',name))
    env=dict(os.environ);env['PATH']=str(compiler_path.parent)+os.pathsep+env.get('PATH','')
    commands,logs=[],[]
    def command(args):
        commands.append([str(a) for a in args])
        result=subprocess.run(commands[-1],cwd=run_dir,env=env,text=True,encoding='utf-8',errors='replace',capture_output=True,check=False)
        logs.append(dict(command=commands[-1],returncode=result.returncode,stdout=result.stdout,stderr=result.stderr))
        if result.returncode:
            (run_dir/'energy_build_log.json').write_text(json.dumps(logs,ensure_ascii=False,indent=2),encoding='utf-8')
            raise RuntimeError(f'compiler command failed: {commands[-1]}\n{result.stdout}\n{result.stderr}')
        return result.stdout
    version=command([compiler_path,'--version']).splitlines()[0]
    flags=['-std=c99','-Wall','-Wextra','-Werror','-pedantic','-O2','-fno-fast-math','-ffp-contract=off','-fstack-usage']
    if os.name!='nt':flags.append('-fPIC')
    objects=[]
    for source in ('firmware/core/hanger_energy.c','firmware/host/energy_bridge.c'):
        obj=run_dir/(Path(source).stem+'.o');objects.append(obj)
        # GCC's assembler in this Windows toolchain cannot reliably encode the
        # Chinese absolute output path. A Unicode process cwd plus ASCII
        # relative source/output arguments avoids that code-page round trip.
        command([compiler_path,*flags,'-c',os.path.relpath(ROOT/source,run_dir),'-o',obj.name])
    library=run_dir/('energy_core.dll' if os.name=='nt' else 'energy_core.so')
    command([compiler_path,'-shared',*(p.name for p in objects),'-o',library.name,'-lm'])
    dll_path=os.add_dll_directory(str(compiler_path.parent)) if os.name=='nt' else None
    try:
        h=Harness(ct.CDLL(str(library)))
        sizes=(ct.c_size_t*14)();count=h.layout(sizes,14)
        expected=[ct.sizeof(cls) for cls in STRUCTS]+[ct.sizeof(D),ct.sizeof(U64),ct.sizeof(INT)]
        h.check('abi','native-sizeof',dict(count=count,sizes=list(sizes)),dict(count=14,sizes=expected))
        h.check('abi','binary64-and-u64',expected[-3:],[8,8,4])
        legal_cases(h)
        boundary_and_invalid_cases(h)
    finally:
        if dll_path is not None:dll_path.close()
    if source_hashes!=fingerprints(SOURCES) or input_hashes!=fingerprints(INPUTS):
        raise RuntimeError('source or reference changed during validation')
    stack=[]
    for path in sorted(run_dir.glob('*.su')):
        for line in path.read_text(encoding='utf-8').splitlines():
            location,amount,kind=line.split('\t')
            if kind not in ('static','dynamic,bounded'):
                raise AssertionError(('unbounded compiler stack record',line))
            stack.append(dict(function=location.rsplit(':',1)[-1],bytes=int(amount),kind=kind,file=path.name))
    if not stack:raise AssertionError('compiler produced no stack-usage evidence')
    counts={name:sum(r['group']==name for r in h.records) for name in sorted({r['group'] for r in h.records})}
    vectors=run_dir/'energy_vectors.json'
    vectors.write_text(json.dumps(dict(label='SYNTHETIC_UNCALIBRATED',seed=SEED,checks=h.records),ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (run_dir/'energy_build_log.json').write_text(json.dumps(logs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    result=dict(passed=True,check_count=len(h.records),commands=commands,command_cwd=str(run_dir),
        label='SYNTHETIC_UNCALIBRATED',collection_authorized=False,seed=SEED,compiler=version,
        source_sha256=source_hashes,input_sha256=input_hashes,
        metrics=dict(groups=counts,numeric_comparisons=h.numeric_comparisons,
                     abs_tolerance_wh=ABS_TOL,relative_tolerance=REL_TOL,max_absolute_error=h.max_absolute_error,
                     max_relative_error=h.max_relative_error,sizeof_bytes={cls.__name__:ct.sizeof(cls) for cls in STRUCTS},
                     max_relative_error_above_abs_tol=h.max_relative_error_above_abs_tol,
                     stack_usage=stack,max_single_compiler_frame_bytes=max(s['bytes'] for s in stack),
                     sum_all_compiler_frames_bytes=sum(s['bytes'] for s in stack),
                     stack_limit_note='No recursion or heap. Sum of all listed frames is a conservative core/bridge-only bound, not MCU/libm/interrupt stack proof.',
                     wifi_max_loop_iterations=DAY),
        artifacts=dict(build_dir=str(run_dir),vectors=str(vectors),vectors_sha256=hashlib.sha256(vectors.read_bytes()).hexdigest(),library=str(library)),
        limits=['Primitives only; no full C T05 simulator, request queue, real T04 controller, observation generation or collection authorization.',
                'Caller provides accepted protocol/config identity and trusted clock, installation/batch facts, finish-before-stop ordering and one active action.',
                'Integer seconds; uint32 phase durations, uint64 absolute times with checked sums; wifi timeout <= 86400 seconds and four backoff slots.',
                'C refuses an unpaid sub-epsilon boot instead of reproducing the Python epsilon-only permission edge.',
                'Quota persistence is external; uninitialized ledger blocks wifi. Native host ABI/stack is not a target STM32 memory claim.'])
    receipt=run_dir/'energy_validation.json'
    receipt.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler',default=DEFAULT_COMPILER)
    parser.add_argument('--build-dir',type=Path,default=HERE/'build'/'energy')
    args=parser.parse_args()
    result=run_validation(args.compiler,args.build_dir)
    print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))


if __name__=='__main__':main()

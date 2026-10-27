"""Readable deterministic event fixtures. Scenario labels are never sent to the controller."""

from copy import deepcopy
from dataclasses import replace

from theory.model import DEFAULT_SEED, Parameters, simulate
from research.state_machine.machine import DEFAULT_CONFIG, OBSERVATION_KEYS, VERSION


def observations(kind="weak", config=DEFAULT_CONFIG):
    parameters = replace(Parameters(), noise_rh_pct=0, surface_activity=0.6 if kind == "weak" else 1.0)
    if kind == "leaky":
        parameters = replace(parameters, leak_ml_s=1.5)
    rows = simulate(parameters, seed=DEFAULT_SEED, protocol=config.protocol)
    return [{key: row[key] for key in OBSERVATION_KEYS} for row in rows]


class Sequence:
    """Fixture authoring convenience, not a second state-machine implementation."""
    def __init__(self):
        self.config = DEFAULT_CONFIG
        self.boot_id, self.batch_id, self.installation_id = "boot-a", "batch-a", "install-a"
        self.events = []
        self.now = 0.0
        self.cycle_number = 0
        self.install_number = 0
        self.emit("boot", 0)
        self.emit("install", 1, batch_id=self.batch_id, installation_id=self.installation_id)

    def emit(self, kind, now=None, **fields):
        self.now = self.now + 1 if now is None else now
        event = {"event_version": VERSION, "kind": kind, "event_id": f"event-{len(self.events) + 1}",
                 "boot_id": self.boot_id, "monotonic_s": self.now, "config_sha256": self.config.sha256}
        if kind in ("clip", "recover", "start_cycle", "finish_cycle"):
            event.update(batch_id=self.batch_id, installation_id=self.installation_id)
        event.update(fields)
        self.events.append(event)
        return event

    def begin(self, now=None):
        self.cycle_number += 1
        start = self.now + self.config.policy.stabilization_wait_s if now is None else now
        cycle = f"cycle-{self.cycle_number}"
        self.emit("start_cycle", start, cycle_id=cycle)
        return cycle, start, start + self.config.protocol.total_s

    def finish(self, pending, kind="weak", rows=None, **fields):
        cycle, start, end = pending
        return self.emit("finish_cycle", end, cycle_id=cycle, start_s=start, end_s=end,
                         observations=observations(kind, self.config) if rows is None else rows, **fields)

    def cycle(self, kind="weak", **fields):
        return self.finish(self.begin(), kind, **fields)

    def install(self, batch=None):
        self.install_number += 1
        self.installation_id = f"install-new-{self.install_number}"
        self.batch_id = batch or self.batch_id
        return self.emit("install", batch_id=self.batch_id, installation_id=self.installation_id)

    def configure(self, config):
        self.emit("configure", new_config=config.snapshot())
        self.config = config
        self.install()

    def record(self, name, hypothesis):
        return {"name": name, "hypothesis": hypothesis, "initial_config": DEFAULT_CONFIG.snapshot(), "events": self.events}


def scenarios():
    records = []
    s = Sequence()
    for _ in range(4):
        s.cycle("leaky")
    records.append(s.record("normal_weak", "Wet-like surface with large leak remains unconfirmed after four cycles."))

    s = Sequence()
    for kind in ("weak", "strong", "weak"):
        s.cycle(kind)
    records.append(s.record("strong_resets", "Strong vapor-source evidence clears weak count without attributing source to cloth."))

    for kind in ("missing_value", "missing_tail", "observed_clip_with_invalid"):
        s = Sequence()
        s.cycle()
        rows = observations()
        if kind == "missing_tail":
            rows.pop()
        else:
            rows[60]["local_RH_pct"] = None
            if kind == "observed_clip_with_invalid":
                rows[70]["clip_event"] = True
        s.finish(s.begin(), rows=rows)
        if kind == "observed_clip_with_invalid":
            s.install()
        else:
            s.emit("recover")
        s.cycle()
        records.append(s.record(kind, "Invalid cycle clears evidence; explicit recovery/reinstallation precedes another cycle."))

    s = Sequence()
    s.cycle()
    pending = s.begin()
    s.emit("tick", pending[2] + s.config.policy.completion_grace_s + 1)
    s.emit("finish_cycle", cycle_id=pending[0], start_s=pending[1], end_s=pending[2], observations=observations())
    records.append(s.record("unfinished_timeout", "No finish before deadline; late packet cannot resurrect evidence."))

    s = Sequence()
    completed = s.cycle()
    s.events.append(deepcopy(completed))
    s.events.append({**deepcopy(completed), "event_id": "retry-new-message", "monotonic_s": s.now + 1})
    s.now += 1
    s.cycle()
    records.append(s.record("duplicate_delivery", "Same packet and same completed cycle under a new message ID are not recounted."))

    s = Sequence()
    completed = s.cycle()
    s.emit("start_cycle", completed["start_s"], cycle_id="renamed-cycle")
    records.append(s.record("renamed_old_window", "Renaming a cycle does not make an old start timestamp valid."))

    s = Sequence()
    completed = s.cycle()
    s.install("batch-b")
    s.events.append({**deepcopy(completed), "event_id": "old-batch-delayed"})
    s.cycle()
    s.emit("install", batch_id="batch-a", installation_id="forbidden-old-batch-return")
    records.append(s.record("batch_switch", "Old batch packets and retired batch installation are rejected without importing evidence."))

    s = Sequence()
    s.cycle()
    pending = s.begin()
    old_install = s.installation_id
    s.emit("clip", s.now + 10)
    s.install()
    s.emit("finish_cycle", pending[2], installation_id=old_install, cycle_id=pending[0],
           start_s=pending[1], end_s=pending[2], observations=observations())
    s.cycle()
    records.append(s.record("clip_reinstallation", "Clip cancels active window; late data from the prior installation cannot count."))

    for part in ("gates", "policy", "protocol"):
        s = Sequence()
        completed = s.cycle()
        update = {"gates": replace(s.config.gates, weak_delta_pa=30),
                  "policy": replace(s.config.policy, review_after_weak_cycles=1),
                  "protocol": replace(s.config.protocol, sample_period_s=0.5)}[part]
        s.configure(replace(s.config, **{part: update}))
        s.events.append({**deepcopy(completed), "event_id": "old-config-packet"})
        s.cycle()
        records.append(s.record("change_" + part, "Any component of full configuration identity changes evidence domain and requires reinstall."))

    s = Sequence()
    old_boot_event = deepcopy(s.events[0])
    completed = s.cycle()
    s.boot_id = "boot-b"
    s.emit("boot", 0)
    s.events.append({**deepcopy(completed), "event_id": "previous-boot-packet"})
    s.events.append(old_boot_event)
    s.install()
    s.cycle()
    records.append(s.record("reboot", "New boot resets monotonic domain, installation and evidence; old boot packets remain retired."))

    for kind in ("same_time", "backward_time", "large_jump"):
        s = Sequence()
        s.cycle()
        now = s.now + {"same_time": 0, "backward_time": -1,
                       "large_jump": s.config.policy.max_event_gap_s + 1}[kind]
        s.emit("tick", now)
        s.emit("recover", now + 2)
        s.cycle()
        records.append(s.record(kind, "Current-domain clock anomaly clears evidence and needs explicit recovery with a fresh wait."))

    s = Sequence()
    s.cycle()
    s.emit("tick", s.now + s.config.policy.max_evidence_gap_s + 1)
    s.cycle()
    records.append(s.record("evidence_expiry", "A long idle gap ends the weak chain without inventing additional physical evidence."))

    s = Sequence()
    pending = s.begin()
    s.emit("finish_cycle", pending[2], cycle_id=pending[0], start_s=pending[1], end_s=pending[2],
           state="LOW_SIGNAL_UNCONFIRMED", collection_authorized=True,
           assessment={"state": "LOW_SIGNAL_UNCONFIRMED", "collection_authorized": True})
    records.append(s.record("forged_assessment", "Claimed result without raw observations is ignored and becomes UNKNOWN."))
    return records

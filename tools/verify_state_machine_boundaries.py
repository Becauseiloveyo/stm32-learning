"""Independent public-API T04 acceptance sequences; read-only, no hardware.

Uses single-cycle fixtures from the accepted model, not the worker's scenario
builder or expected transition fixtures. No file writes or evidence restoration.
"""

import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.state_machine.machine import Configuration, StateMachine
from theory.model import Parameters, assess, simulate


class Session:
    def __init__(self, config=None):
        self.config = config or Configuration()
        self.machine = StateMachine(self.config)
        self.events = []
        self.last_event = None

    def send(self, kind, now, **fields):
        state = self.machine.snapshot()
        event = {"event_version": "1.0", "event_id": f"independent-{len(self.events)}",
                 "kind": kind, "monotonic_s": now, "config_sha256": state["config_sha256"],
                 "boot_id": state["boot_id"] or "boot-a", "batch_id": state["batch_id"],
                 "installation_id": state["installation_id"], **fields}
        return self.deliver(event)

    def deliver(self, event):
        self.last_event = copy.deepcopy(event)
        result = self.machine.handle(event)
        self.events.append((event, result))
        assert result["collection_authorized"] is False
        if result["assessment"]:
            assert result["assessment"]["collection_authorized"] is False
        return result

    def install(self):
        assert self.send("boot", 0)["accepted"]
        assert self.send("install", 1, batch_id="batch-a", installation_id="install-a")["accepted"]

    def cycle(self, start, identity, rows):
        assert self.send("start_cycle", start, cycle_id=identity)["accepted"]
        end = start + self.config.protocol.total_s
        return self.send("finish_cycle", end, cycle_id=identity, start_s=start, end_s=end, observations=rows)


def main():
    checks = []
    sessions = []

    def check(label, condition):
        assert condition, label
        checks.append(label)

    config = Configuration()
    fields = ("observation_version", "unit_system", "protocol_sha256", "time_s", "phase",
              "ambient_T_C", "ambient_RH_pct", "local_T_C", "local_RH_pct", "sensor_valid", "clip_event")
    raw = simulate(Parameters(surface_activity=0.6, noise_rh_pct=0), protocol=config.protocol)
    rows = [{key: row[key] for key in fields} for row in raw]
    check("fixture uses actual weak assessment", assess(rows)["state"] == "LOW_SIGNAL_UNCONFIRMED")
    s = Session(config); sessions.append(s); s.install()
    check("early start cannot count", s.send("start_cycle", 2, cycle_id="early")["weak_cycles"] == 0)
    check("first completed cycle counts once", s.cycle(31, "cycle-a", rows)["weak_cycles"] == 1)
    delivered = copy.deepcopy(s.last_event)
    before = s.machine.snapshot()
    duplicate = s.deliver(delivered)
    check("exact redelivery is a no-op", not duplicate["accepted"] and s.machine.snapshot() == before)
    # Claiming a different transport ID cannot make the same cycle count again.
    delivered.update(event_id="different-transport-id", monotonic_s=172)
    replayed = s.deliver(delivered)
    check("new message ID cannot recount cycle", not replayed["accepted"] and s.machine.snapshot() == before)
    # One missing internal sample is permitted by T01; losing the endpoint is not.
    isolated_missing = rows[:60] + rows[61:]
    check("T01 permitted missing sample stays valid", assess(isolated_missing)["state"] == "LOW_SIGNAL_UNCONFIRMED")
    check("legal sparse cycle preserves chain", s.cycle(201, "cycle-b", isolated_missing)["weak_cycles"] == 2)
    bad = s.cycle(371, "cycle-c", rows[:-1])
    check("incomplete cycle clears evidence", not bad["accepted"] and bad["weak_cycles"] == 0)
    check("fault cannot immediately start", not s.send("start_cycle", 512, cycle_id="skip-recovery")["accepted"])
    check("explicit recovery accepted", s.send("recover", 513)["accepted"])
    check("recovery yields a new chain", s.cycle(543, "cycle-d", rows)["weak_cycles"] == 1)
    check("clip clears evidence and installation", s.send("clip", 684)["weak_cycles"] == 0 and s.machine.snapshot()["installation_id"] is None)
    assert s.send("install", 685, batch_id="batch-b", installation_id="install-b")["accepted"]
    before = s.machine.snapshot()
    late = s.send("finish_cycle", 1e9, batch_id="batch-a", installation_id="install-a",
                  cycle_id="late-old-cycle", start_s=31, end_s=171, observations=rows)
    check("old batch cannot advance clock or evidence", not late["accepted"] and s.machine.snapshot() == before)
    check("new garment starts at one", s.cycle(715, "cycle-e", rows)["weak_cycles"] == 1)
    changed = replace(config, gates=replace(config.gates, strong_delta_pa=110))
    old_fingerprint = config.sha256
    result = s.send("configure", 856, new_config=changed.snapshot())
    check("gate-only change changes identity and clears evidence", result["accepted"] and result["weak_cycles"] == 0
          and result["config_sha256"] != old_fingerprint and result["installation_id"] is None)
    s.config = changed
    before = s.machine.snapshot()
    stale = s.send("tick", 1e9, config_sha256=old_fingerprint)
    check("old configuration cannot change current state", not stale["accepted"] and s.machine.snapshot() == before)
    assert s.send("install", 857, batch_id="batch-b", installation_id="install-c")["accepted"]
    check("reconfigured chain starts at one", s.cycle(887, "cycle-f", rows)["weak_cycles"] == 1)
    booted = s.send("boot", 0, boot_id="boot-b")
    check("new boot clears all evidence", booted["accepted"] and booted["weak_cycles"] == 0 and booted["installation_id"] is None)
    before = s.machine.snapshot()
    stale = s.send("finish_cycle", 1e9, boot_id="boot-a", batch_id="batch-b", installation_id="install-c",
                   cycle_id="old-boot-cycle", start_s=887, end_s=1027, observations=rows)
    check("old boot cannot alter new clock domain", not stale["accepted"] and s.machine.snapshot() == before)

    for label, kind, at, extra in (
        ("time reversal", "tick", 170, {}),
        ("time duplicate", "tick", 171, {}),
        ("large time jump", "tick", 5000, {}),
        ("same interval with new cycle identity", "start_cycle", 31, {"cycle_id": "new-id-old-window"}),
    ):
        t = Session(config); sessions.append(t); t.install(); t.cycle(31, "first", rows)
        result = t.send(kind, at, **extra)
        check(label + " cannot preserve/increase weak chain", not result["accepted"] and result["weak_cycles"] == 0)

    t = Session(config); sessions.append(t); t.install()
    assert t.send("start_cycle", 31, cycle_id="unfinished")["accepted"]
    result = t.send("tick", 182)
    check("completion timeout invalidates pending cycle", not result["accepted"] and result["weak_cycles"] == 0 and result["pending_cycle"] is None)
    paths = ("tools/verify_state_machine_boundaries.py", "research/state_machine/machine.py", "theory/model.py", "theory/protocol.py")
    print(json.dumps({"synthetic": "SYNTHETIC_UNCALIBRATED", "checks": len(checks), "labels": checks,
                      "events": sum(len(x.events) for x in sessions), "collection_authorized": False,
                      "source_sha256": {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths}},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

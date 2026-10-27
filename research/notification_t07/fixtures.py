"""Deterministic synthetic RAW observations. Never feeds a claimed assessment."""
from copy import deepcopy

from research.state_machine.scenarios import observations
from theory.model import DEFAULT_SEED

WALL_ORIGIN_S = 10000


class RawFixture:
    def __init__(self, device, *, wall_origin_s=WALL_ORIGIN_S, clock_valid=True, uncertainty_s=0):
        self.device = device
        self.initial_config = device.machine.config.snapshot()
        self.boot_event = dict(event_version="1.0", kind="boot", event_id=f"boot-{device.head.boot_seq}",
                               boot_id=device.boot_id, monotonic_s=0, config_sha256=device.machine.config.sha256)
        self.wall_origin_s = wall_origin_s
        self.clock_valid = clock_valid
        self.uncertainty_s = uncertainty_s
        self.raw_events = []
        self.outputs = []
        self.n = 0
        self.cycle_n = 0
        self.install_n = 0

    @property
    def now(self):
        return self.device.machine.last_monotonic_s

    @property
    def wall(self):
        return self.wall_origin_s + self.now

    def emit(self, kind, now=None, **fields):
        self.n += 1
        raw = dict(event_version="1.0", kind=kind, event_id=f"input-{self.n}", boot_id=self.device.boot_id,
                   monotonic_s=self.now + 1 if now is None else now,
                   config_sha256=self.device.machine.config.sha256)
        if kind in ("clip", "recover", "start_cycle", "finish_cycle"):
            raw.update(batch_id=self.device.machine.batch_id, installation_id=self.device.machine.installation_id)
        raw.update(fields)
        wall = self.wall_origin_s + raw["monotonic_s"] if self.clock_valid else None
        result = self.device.process(raw, wall_s=wall, uncertainty_s=self.uncertainty_s)
        self.raw_events.append(deepcopy(raw))
        self.outputs.append(deepcopy(result["output"]))
        return result

    def install(self, batch="batch-a"):
        self.install_n += 1
        return self.emit("install", batch_id=batch, installation_id=f"install-{self.install_n}")

    def cycle(self, kind="weak", *, delay_s=0):
        self.cycle_n += 1
        machine = self.device.machine
        start = max(self.now + 1, machine.stable_after_s or 0,
                    (machine.last_window_end_s + machine.config.policy.min_cycle_gap_s) if machine.last_window_end_s is not None else 0)
        cycle_id = f"cycle-{self.cycle_n}"
        self.emit("start_cycle", start, cycle_id=cycle_id)
        end = start + machine.config.protocol.total_s
        return self.emit("finish_cycle", end + delay_s, cycle_id=cycle_id, start_s=start, end_s=end,
                         observations=observations(kind, machine.config))

    def review(self):
        if self.device.machine.installation_id is None:
            self.install()
        for _ in range(self.device.machine.config.policy.review_after_weak_cycles):
            self.cycle()
        return self.device.head

    def record(self):
        return dict(initial_config=self.initial_config, boot_event=self.boot_event,
                    raw_events=deepcopy(self.raw_events), expected_outputs=deepcopy(self.outputs))

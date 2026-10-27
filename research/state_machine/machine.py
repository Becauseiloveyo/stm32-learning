"""T04 deterministic, uncalibrated multi-cycle evidence controller.

No I/O, wall clock, sensor model, caller-supplied assessment or collection branch.
The acquisition owner supplies trusted identities and monotonic timestamps.
"""

from dataclasses import asdict, dataclass
import hashlib
import json

from theory.model import assess
from theory.protocol import DEFAULT_GATES, DEFAULT_PROTOCOL, Gates, MeasurementProtocol, finite_number

VERSION = "1.0"
OBSERVATION_KEYS = ("observation_version", "unit_system", "protocol_sha256", "time_s", "phase",
                    "ambient_T_C", "ambient_RH_pct", "local_T_C", "local_RH_pct", "sensor_valid", "clip_event")
EVENT_KINDS = ("boot", "install", "configure", "clip", "tick", "recover", "start_cycle", "finish_cycle")
EVENT_KEYS = ("event_version", "kind", "event_id", "boot_id", "monotonic_s", "config_sha256",
              "batch_id", "installation_id", "cycle_id", "start_s", "end_s", "new_config")


def canonical(value):
    """Canonical configuration numbers; counts 3 and times 3.0 hash consistently."""
    if type(value) in (int, float):
        return float(value)
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [canonical(item) for item in value]
    return value


def fingerprint(value):
    data = json.dumps(canonical(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Policy:
    """Software timing assumptions in seconds; no garment calibration."""
    stabilization_wait_s: float = 30.0
    min_cycle_gap_s: float = 30.0
    max_evidence_gap_s: float = 1800.0
    max_event_gap_s: float = 3600.0
    completion_grace_s: float = 10.0
    timestamp_tolerance_s: float = 1e-6
    review_after_weak_cycles: int = 3

    def validate(self):
        for name, value in asdict(self).items():
            if not finite_number(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if type(self.review_after_weak_cycles) is not int:
            raise ValueError("review_after_weak_cycles must be an integer")
        if self.max_evidence_gap_s < self.min_cycle_gap_s:
            raise ValueError("max_evidence_gap_s must cover min_cycle_gap_s")
        if self.timestamp_tolerance_s >= min(self.stabilization_wait_s, self.min_cycle_gap_s,
                                            self.completion_grace_s, self.max_event_gap_s) / 1000:
            raise ValueError("timestamp_tolerance_s must be small relative to policy times")
        return self


@dataclass(frozen=True)
class Configuration:
    version: str = VERSION
    protocol: MeasurementProtocol = DEFAULT_PROTOCOL
    gates: Gates = DEFAULT_GATES
    policy: Policy = Policy()

    def validate(self):
        if self.version != VERSION:
            raise ValueError("Unsupported state-machine version")
        if not isinstance(self.protocol, MeasurementProtocol) or not isinstance(self.gates, Gates) or not isinstance(self.policy, Policy):
            raise ValueError("Invalid configuration types")
        self.protocol.validate()
        self.gates.validate()
        self.policy.validate()
        if self.policy.timestamp_tolerance_s >= self.protocol.sample_period_s / 1000:
            raise ValueError("timestamp_tolerance_s must be small relative to sampling period")
        return self

    def snapshot(self):
        self.validate()
        return asdict(self)

    @property
    def sha256(self):
        return fingerprint(self.snapshot())

    @classmethod
    def from_snapshot(cls, data):
        try:
            if set(data) != {"version", "protocol", "gates", "policy"}:
                raise ValueError("Configuration snapshot fields mismatch")
            for key, expected in (("protocol", asdict(DEFAULT_PROTOCOL)), ("gates", asdict(DEFAULT_GATES)),
                                  ("policy", asdict(Policy()))):
                if set(data[key]) != set(expected):
                    raise ValueError(f"Incomplete or unknown {key} snapshot fields")
            return cls(data["version"], MeasurementProtocol(**data["protocol"]),
                       Gates(**data["gates"]), Policy(**data["policy"])).validate()
        except (TypeError, KeyError) as exc:
            raise ValueError("Malformed configuration snapshot") from exc


DEFAULT_CONFIG = Configuration().validate()


def valid_id(value):
    return (isinstance(value, str) and 0 < len(value) <= 128 and value == value.strip()
            and all(char.isprintable() for char in value))


def event_signature(event):
    """Hash only consumed fields; diagnostic truth/labels cannot affect decisions."""
    projected = {key: event[key] for key in EVENT_KEYS if key in event}
    if "observations" in event:
        rows = event["observations"]
        projected["observations"] = [
            {key: row[key] for key in OBSERVATION_KEYS if key in row} if isinstance(row, dict) else row
            for row in rows
        ] if isinstance(rows, (list, tuple)) else rows
    # Nonfinite observation values are hashed consistently then rejected by assess.
    encoded = json.dumps(projected, sort_keys=True, separators=(",", ":"), allow_nan=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class StateMachine:
    def __init__(self, config=DEFAULT_CONFIG):
        if not isinstance(config, Configuration):
            raise ValueError("Expected Configuration")
        self.config = config.validate()
        self.boot_id = self.batch_id = self.installation_id = None
        self.state = "AWAITING_INSTALLATION"
        self.last_monotonic_s = self.stable_after_s = self.last_window_end_s = None
        self.last_evidence_end_s = None
        self.pending = None
        self.evidence = []
        self.seen_boots, self.installations, self.retired_batches, self.cycles = set(), set(), set(), set()
        self.messages = {}

    def snapshot(self):
        return {"state_machine_version": VERSION, "state": self.state, "boot_id": self.boot_id,
                "batch_id": self.batch_id, "installation_id": self.installation_id,
                "config_sha256": self.config.sha256, "last_monotonic_s": self.last_monotonic_s,
                "stable_after_s": self.stable_after_s, "last_window_end_s": self.last_window_end_s,
                "pending_cycle": dict(self.pending) if self.pending else None,
                "evidence_cycle_ids": list(self.evidence), "weak_cycles": len(self.evidence),
                "last_evidence_end_s": self.last_evidence_end_s,
                "independent_review_due": len(self.evidence) >= self.config.policy.review_after_weak_cycles,
                "collection_authorized": False}

    def _reply(self, accepted, reason, assessment=None, disposition=None):
        return {"accepted": accepted, "disposition": disposition or ("APPLIED" if accepted else "REJECTED"),
                "reason": reason, "assessment": assessment, **self.snapshot()}

    def _clear_evidence(self):
        self.evidence.clear()
        self.last_evidence_end_s = None

    def _fault(self, reason, assessment=None):
        self._clear_evidence()
        self.pending = None
        self.stable_after_s = None
        self.state = "FAULT"
        return self._reply(False, reason, assessment)

    def _detach(self):
        self._clear_evidence()
        self.pending = None
        self.installation_id = self.stable_after_s = None
        self.state = "AWAITING_INSTALLATION"

    def _stabilize(self, now):
        deadline = now + self.config.policy.stabilization_wait_s
        if not finite_number(deadline) or deadline <= now:
            return self._fault("unrepresentable_deadline")
        self._clear_evidence()
        self.pending = None
        self.stable_after_s = deadline
        self.state = "STABILIZING"
        return None

    def handle(self, event):
        """Apply one event in arrival order. Never sort, repair, or trust a claimed state."""
        if not isinstance(event, dict):
            return self._fault("malformed_event")
        kind = event.get("kind")
        if (event.get("event_version") != VERSION or kind not in EVENT_KINDS
                or not valid_id(event.get("event_id")) or not valid_id(event.get("boot_id"))):
            return self._fault("malformed_event")
        boot = event["boot_id"]
        if kind != "boot" and boot != self.boot_id:
            return self._reply(False, "stale_or_unknown_boot")
        if kind == "boot" and boot in self.seen_boots and boot != self.boot_id:
            return self._reply(False, "retired_boot")
        if event.get("config_sha256") != self.config.sha256:
            return self._reply(False, "configuration_mismatch")
        # Foreign identities cannot advance the current clock or clear current evidence.
        if kind in ("clip", "recover", "start_cycle", "finish_cycle"):
            if event.get("batch_id") != self.batch_id or event.get("installation_id") != self.installation_id:
                return self._reply(False, "batch_or_installation_mismatch")
        try:
            signature = event_signature(event)
        except (TypeError, ValueError, OverflowError):
            return self._fault("malformed_event_payload")
        key = (boot, event["event_id"])
        if key in self.messages:
            if self.messages[key] == signature:
                return self._reply(False, "duplicate_event", disposition="IGNORED_DUPLICATE")
            return self._fault("event_id_conflict")
        self.messages[key] = signature
        if kind == "boot" and boot in self.seen_boots:
            return self._reply(False, "boot_id_reused")
        if kind == "install":
            if not valid_id(event.get("batch_id")) or not valid_id(event.get("installation_id")):
                return self._fault("invalid_installation_identity")
            if event["installation_id"] in self.installations or event["batch_id"] in self.retired_batches:
                return self._reply(False, "retired_installation_or_batch")
        now = event.get("monotonic_s")
        if not finite_number(now) or now < 0:
            return self._fault("invalid_monotonic_time")
        if kind == "boot":
            self.seen_boots.add(boot)
            self.boot_id, self.batch_id = boot, None
            self.installations.clear()
            self.retired_batches.clear()
            self.cycles.clear()
            self.last_window_end_s = None
            self.last_monotonic_s = now
            self._detach()
            return self._reply(True, "boot_requires_installation")
        # Replayed cycle identities cannot reset a healthy chain or increment it.
        cycle_id = event.get("cycle_id")
        if kind in ("start_cycle", "finish_cycle"):
            if not valid_id(cycle_id):
                return self._fault("invalid_cycle_id")
            if cycle_id in self.cycles and (kind == "start_cycle" or not self.pending or self.pending["cycle_id"] != cycle_id):
                return self._reply(False, "cycle_id_reused", disposition="IGNORED_DUPLICATE")
        if now <= self.last_monotonic_s:
            return self._fault("non_increasing_event_time")
        gap = now - self.last_monotonic_s
        self.last_monotonic_s = now
        policy = self.config.policy
        if gap > policy.max_event_gap_s:
            return self._fault("event_time_jump")
        if kind not in ("install", "configure", "clip", "recover") and self.pending:
            if now > self.pending["deadline_s"] + policy.timestamp_tolerance_s:
                return self._fault("cycle_timeout")
        if self.last_evidence_end_s is not None and not self.pending:
            if now - self.last_evidence_end_s > policy.max_evidence_gap_s:
                self._clear_evidence()
                if self.state == "AWAITING_RECHECK":
                    self.state = "READY"
        if kind == "install":
            batch, installation = event.get("batch_id"), event.get("installation_id")
            if self.batch_id is not None and self.batch_id != batch:
                self.retired_batches.add(self.batch_id)
            self.batch_id, self.installation_id = batch, installation
            self.installations.add(installation)
            failure = self._stabilize(now)
            return failure or self._reply(True, "installation_wait")
        if kind == "configure":
            try:
                updated = Configuration.from_snapshot(event.get("new_config"))
            except ValueError:
                return self._fault("invalid_configuration")
            if updated.sha256 == self.config.sha256:
                return self._reply(True, "configuration_unchanged")
            self.config = updated
            self._detach()
            return self._reply(True, "configuration_changed_reinstall")
        if kind == "clip":
            self._detach()
            return self._reply(True, "clip_requires_reinstallation")
        if kind == "recover":
            if self.state != "FAULT" or self.installation_id is None:
                return self._reply(False, "recovery_requires_fault_and_installation")
            failure = self._stabilize(now)
            return failure or self._reply(True, "recovery_wait")
        if self.state == "STABILIZING" and now >= self.stable_after_s:
            self.state = "READY"
            self.stable_after_s = None
        if kind == "tick":
            return self._reply(True, "clock_observed")
        if kind == "start_cycle":
            if self.state == "MEASURING":
                return self._fault("cycle_already_active")
            if self.state not in ("READY", "AWAITING_RECHECK", "SOURCE_PRESENT"):
                return self._reply(False, "not_ready")
            if self.last_window_end_s is not None and now < self.last_window_end_s + policy.min_cycle_gap_s:
                return self._reply(False, "cycle_gap_too_short")
            end = now + self.config.protocol.total_s
            deadline = end + policy.completion_grace_s
            if not finite_number(deadline) or end <= now or deadline <= end:
                return self._fault("unrepresentable_deadline")
            self.cycles.add(cycle_id)
            self.last_window_end_s = end  # reserve even an aborted/invalid interval
            self.pending = {"cycle_id": cycle_id, "start_s": now, "end_s": end, "deadline_s": deadline}
            self.state = "MEASURING"
            return self._reply(True, "cycle_started")
        if kind == "finish_cycle":
            if not self.pending or self.pending["cycle_id"] != cycle_id:
                return self._fault("no_matching_cycle_start")
            start, end = event.get("start_s"), event.get("end_s")
            expected = self.pending
            tol = policy.timestamp_tolerance_s
            if (not finite_number(start) or not finite_number(end) or end <= start
                    or abs(start - expected["start_s"]) > tol or abs(end - expected["end_s"]) > tol
                    or now + tol < expected["end_s"]):
                return self._fault("cycle_interval_mismatch")
            rows = event.get("observations")
            assessment = assess(rows, self.config.gates, protocol=self.config.protocol)
            self.pending = None
            if isinstance(rows, (list, tuple)) and any(isinstance(row, dict) and row.get("clip_event") is True for row in rows):
                self._detach()
                return self._reply(False, "observed_clip_requires_reinstallation", assessment)
            if assessment["state"] == "UNKNOWN":
                return self._fault("invalid_cycle:" + assessment["reasons"], assessment)
            if assessment["state"] == "VAPOR_SOURCE_PRESENT":
                self._clear_evidence()
                self.state = "SOURCE_PRESENT"
                return self._reply(True, "source_observed", assessment)
            self.evidence.append(cycle_id)
            self.last_evidence_end_s = end
            self.state = "AWAITING_RECHECK"
            return self._reply(True, "weak_evidence_unconfirmed", assessment)
        raise AssertionError("Unreachable event kind")


def replay(events, config=DEFAULT_CONFIG):
    machine = StateMachine(config)
    return [machine.handle(event) for event in events]

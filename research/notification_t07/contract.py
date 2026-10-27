"""Research-only transport projection. No simulated or real collection branch."""
from dataclasses import asdict, dataclass
import json
import math
import re

from research.state_machine.machine import Configuration
from research.notification_t07.storage import ContractError, digest, encode

SYNTHETIC = "SYNTHETIC_UNCALIBRATED"
TTL_S = 1800
MAX_SEQUENCE = 2**31 - 1
MAX_WALL_S = 1e12
RETRY_S = (60, 300, 900, 3600)
RESEARCH_TEXT = "研究：需独立复核。弱信号尚未确认，不是收衣建议。"
STATES = {"AWAITING_INSTALLATION", "STABILIZING", "READY", "MEASURING", "AWAITING_RECHECK", "SOURCE_PRESENT", "FAULT"}


def count(value, minimum=0):
    if type(value) is not int or not minimum <= value <= MAX_SEQUENCE:
        raise ContractError("counter outside bounded integer range")


def seconds(value, maximum=MAX_WALL_S):
    if type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= maximum:
        raise ContractError("invalid finite seconds")


def identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,16}", value):
        raise ContractError("invalid transport identity")


def review(snapshot):
    return snapshot["state"] == "AWAITING_RECHECK" and snapshot["independent_review_due"] is True


@dataclass(frozen=True)
class Event:
    device_id: str
    producer_id: str
    binding_epoch: int
    event_seq: int
    boot_seq: int
    episode: int
    upstream: dict
    applied_config: dict
    measured_at_s: float | None
    confirmed_at_s: float | None
    uncertainty_s: float
    clock_valid: bool
    controller_fault: str | None
    ttl_s: int = TTL_S
    schema_version: str = "T07-1"
    synthetic: str = SYNTHETIC
    collection_authorized: bool = False

    @property
    def event_id(self):
        return f"{self.producer_id}:event:{self.event_seq}"

    @property
    def state_version(self):
        # Lifetime version avoids ordering unrelated batch/installation names.
        return self.event_seq

    @property
    def notice_kind(self):
        return "RESEARCH_REVIEW" if review(self.upstream) and self.controller_fault is None else "STATE_UPDATE"

    @property
    def notice_key(self):
        domain = [self.upstream[k] for k in ("boot_id", "batch_id", "installation_id", "config_sha256")]
        return digest([self.device_id, self.producer_id, self.binding_epoch, domain, self.episode])

    @property
    def evidence_key(self):
        if self.upstream["last_evidence_end_s"] is None:
            return None
        return digest([self.device_id, self.producer_id, self.binding_epoch,
                       [self.upstream[k] for k in ("boot_id", "batch_id", "installation_id", "config_sha256")],
                       self.upstream["last_evidence_end_s"], self.upstream["evidence_cycle_ids"][-1]])

    @property
    def expires_at_s(self):
        return None if self.measured_at_s is None else self.measured_at_s + self.ttl_s - self.uncertainty_s

    def validate(self):
        identity(self.device_id)
        identity(self.producer_id)
        for value in (self.binding_epoch, self.event_seq, self.boot_seq):
            count(value, 1)
        count(self.episode)
        count(self.ttl_s, 1)
        if self.ttl_s > TTL_S or self.schema_version != "T07-1" or self.synthetic != SYNTHETIC:
            raise ContractError("unsupported notification contract")
        if self.collection_authorized is not False or type(self.clock_valid) is not bool:
            raise ContractError("collection authorization is permanently disabled")
        try:
            config = Configuration.from_snapshot(self.applied_config)
            state = self.upstream
            if state["collection_authorized"] is not False or state["state"] not in STATES:
                raise ContractError("not an actual research state")
            if state["config_sha256"] != config.sha256 or state["state_machine_version"] != config.version:
                raise ContractError("applied config mismatch")
            if state["boot_id"] != f"{self.producer_id}-b{self.boot_seq}":
                raise ContractError("boot must come from persistent generation")
            count(state["weak_cycles"])
            if state["weak_cycles"] != len(state["evidence_cycle_ids"]):
                raise ContractError("weak evidence summary mismatch")
            if type(state["independent_review_due"]) is not bool or state["independent_review_due"] != (
                    state["weak_cycles"] >= config.policy.review_after_weak_cycles):
                raise ContractError("research review flag mismatch")
            if review(state) and (not state["batch_id"] or not state["installation_id"]
                                  or state["last_evidence_end_s"] is None or self.episode < 1):
                raise ContractError("research review without evidence domain")
            if state["last_monotonic_s"] is not None:
                seconds(state["last_monotonic_s"], 1e9)
            if state["last_evidence_end_s"] is not None:
                seconds(state["last_evidence_end_s"], 1e9)
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError("invalid upstream projection") from exc
        if self.controller_fault not in (None, "OUTBOX_CAPACITY_EXHAUSTED"):
            raise ContractError("unknown controller fault")
        seconds(self.uncertainty_s)
        for value in (self.measured_at_s, self.confirmed_at_s):
            if value is not None:
                seconds(value)
        if self.clock_valid and (self.measured_at_s is None or self.confirmed_at_s is None
                                 or self.measured_at_s > self.confirmed_at_s
                                 or self.measured_at_s < self.uncertainty_s):
            raise ContractError("inconsistent valid wall clock")
        return self

    def payload(self):
        return asdict(self)

    def encode(self):
        return encode(self.payload())

    def digest(self):
        return digest(self.payload())

    @classmethod
    def decode(cls, payload):
        try:
            return cls(**json.loads(payload)).validate()
        except (TypeError, ValueError) as exc:
            raise ContractError("invalid immutable event payload") from exc

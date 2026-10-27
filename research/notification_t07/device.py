"""Single-writer controller calling the accepted T04 state machine on raw events."""
from copy import deepcopy
import json
import sqlite3

from research.state_machine.machine import DEFAULT_CONFIG, StateMachine, Configuration
from research.notification_t07.contract import Event, count, identity, seconds
from research.notification_t07.storage import Store, ContractError, digest, encode, fail_at

SCHEMA = """
CREATE TABLE meta(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL, sha256 TEXT NOT NULL);
CREATE TABLE outbox(event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, sha256 TEXT NOT NULL);
"""


class Device(Store):
    @classmethod
    def provision(cls, path, device_id, producer_id, *, wall_s=None, config=DEFAULT_CONFIG, capacity=32):
        identity(device_id)
        identity(producer_id)
        count(capacity, 1)
        config.validate()
        initial = dict(device_id=device_id, producer_id=producer_id, binding_epoch=1, event_seq=0,
                       boot_seq=0, episode=0, review_active=False, applied_config=config.snapshot(),
                       capacity=capacity, halted=False, head=None, evidence_stamp=None, wall_floor=None,
                       clock_fault=False, discarded_on_rebind=0)
        return cls(path, wall_s=wall_s, _initial=initial)

    def __init__(self, path, *, wall_s=None, _initial=None, fail=None):
        super().__init__(path, create=_initial is not None, schema=SCHEMA)
        try:
            with self.transaction():
                if _initial is not None:
                    self._write(_initial)
                meta = self._meta()
                for row in self.db.execute("SELECT payload,sha256 FROM outbox"):
                    event = Event.decode(row[0])
                    if event.digest() != row[1] or event.producer_id != meta["producer_id"] or event.event_seq > meta["event_seq"]:
                        raise ContractError("outbox identity/counter corruption")
                if self.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] > meta["capacity"]:
                    raise ContractError("outbox beyond declared capacity")
                candidate, event = self._restart(meta, wall_s, fail=fail)
            self.machine = candidate
            self.boot_seq = event.boot_seq
            fail_at(fail, "after_device_commit")
        except BaseException:
            self.close()
            raise

    def _meta(self):
        try:
            row = self.db.execute("SELECT payload,sha256 FROM meta WHERE id=1").fetchone()
            if row is None:
                raise ContractError("missing durable identity")
            meta = json.loads(row[0])
            if digest(meta) != row[1]:
                raise ContractError("metadata checksum mismatch")
            for key in ("event_seq", "boot_seq", "episode"):
                count(meta[key])
            identity(meta["device_id"])
            identity(meta["producer_id"])
            Configuration.from_snapshot(meta["applied_config"])
            if meta["head"]:
                head = Event.decode(meta["head"])
                if head.event_seq != meta["event_seq"] or head.producer_id != meta["producer_id"]:
                    raise ContractError("head/counter corruption")
            return meta
        except (sqlite3.Error, KeyError, TypeError, ValueError) as exc:
            raise ContractError("invalid durable state; explicit reprovisioning required") from exc

    def _write(self, meta):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (1,?,?)", (encode(meta), digest(meta)))

    def _owned_meta(self):
        meta = self._meta()
        if meta["boot_seq"] != self.boot_seq:
            raise ContractError("stale writer: another reopen already advanced boot")
        return meta

    @property
    def boot_id(self):
        return self.machine.boot_id

    @property
    def head(self):
        return Event.decode(self._owned_meta()["head"])

    def _wall(self, meta, wall_s):
        if wall_s is None:
            return None
        seconds(wall_s)
        if meta["wall_floor"] is not None and wall_s < meta["wall_floor"]:
            meta["clock_fault"] = True
        meta["wall_floor"] = max(wall_s, meta["wall_floor"] or 0)
        return None if meta["clock_fault"] else wall_s

    def _restart(self, meta, wall_s, *, fail=None):
        meta["boot_seq"] += 1
        count(meta["boot_seq"], 1)
        meta["clock_fault"] = False  # A reboot may repair time only above persisted wall_floor.
        meta["review_active"] = False
        meta["evidence_stamp"] = None
        candidate = StateMachine(Configuration.from_snapshot(meta["applied_config"]))
        raw = dict(event_version="1.0", kind="boot", event_id=f"boot-{meta['boot_seq']}",
                   boot_id=f"{meta['producer_id']}-b{meta['boot_seq']}", monotonic_s=0,
                   config_sha256=candidate.config.sha256)
        output = candidate.handle(raw)
        event = self._record(meta, candidate, output, raw, wall_s, 0, fail)
        return candidate, event

    def _record(self, meta, candidate, output, raw, wall_s, uncertainty_s, fail):
        seconds(uncertainty_s)
        confirmed = self._wall(meta, wall_s)
        snapshot = candidate.snapshot()
        if snapshot["collection_authorized"] is not False:
            raise ContractError("upstream unexpectedly authorized collection")
        end = snapshot["last_evidence_end_s"]
        stamp = meta["evidence_stamp"]
        if end is None:
            stamp = None
        elif stamp is None or stamp["end_s"] != end or stamp["boot_id"] != snapshot["boot_id"]:
            # Wall clock mapping is captured ONLY at the accepted raw finish.
            if raw["kind"] != "finish_cycle" or output["accepted"] is not True or output["assessment"] is None:
                raise ContractError("new evidence without an accepted raw measurement")
            measured = None if confirmed is None else confirmed - (raw["monotonic_s"] - end)
            if measured is not None and measured < uncertainty_s:
                measured = None
            stamp = dict(boot_id=snapshot["boot_id"], end_s=end, measured_at_s=measured, uncertainty_s=uncertainty_s)
        due = snapshot["independent_review_due"]
        if due and not meta["review_active"]:
            meta["episode"] += 1
        meta["review_active"] = due
        meta["evidence_stamp"] = stamp
        meta["applied_config"] = candidate.config.snapshot()
        meta["event_seq"] += 1
        count(meta["event_seq"], 1)
        full = self.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] >= meta["capacity"]
        meta["halted"] = meta["halted"] or full
        measured = stamp["measured_at_s"] if stamp else None
        event = Event(
            device_id=meta["device_id"], producer_id=meta["producer_id"], binding_epoch=meta["binding_epoch"],
            event_seq=meta["event_seq"], boot_seq=meta["boot_seq"], episode=meta["episode"], upstream=snapshot,
            applied_config=meta["applied_config"], measured_at_s=measured, confirmed_at_s=confirmed,
            uncertainty_s=stamp["uncertainty_s"] if stamp else 0,
            clock_valid=measured is not None and confirmed is not None and measured <= confirmed,
            controller_fault="OUTBOX_CAPACITY_EXHAUSTED" if meta["halted"] else None,
        ).validate()
        meta["head"] = event.encode()
        self._write(meta)
        fail_at(fail, "after_device_counter")
        if not full:
            self.db.execute("INSERT INTO outbox VALUES (?,?,?)", (event.event_id, event.encode(), event.digest()))
        # head is a separately reserved safety slot; overflow MUST replace old eligible head.
        fail_at(fail, "before_device_commit")
        return event

    def process(self, raw, *, wall_s=None, uncertainty_s=0, fail=None):
        if not isinstance(raw, dict) or raw.get("kind") == "boot":
            raise ContractError("raw boot is owned by persistent reopen")
        # T06 time envelope. Invalid raw values within this domain still reach T04.
        if type(raw.get("monotonic_s")) in (float, int) and raw["monotonic_s"] > 1e9:
            raise ContractError("outside declared T06 monotonic time domain")
        with self.transaction():
            meta = self._owned_meta()
            if meta["halted"]:
                raise ContractError("outbox overflow latched; report safety head and reprovision explicitly")
            candidate = deepcopy(self.machine)
            before = candidate.snapshot()
            output = candidate.handle(deepcopy(raw))
            if output["accepted"] is not True and candidate.snapshot() == before:
                event = Event.decode(meta["head"])
            else:
                event = self._record(meta, candidate, output, raw, wall_s, uncertainty_s, fail)
        self.machine = candidate
        fail_at(fail, "after_device_commit")
        return dict(output=output, event=event)

    def rebind(self, epoch, *, wall_s=None):
        count(epoch, 1)
        with self.transaction():
            meta = self._owned_meta()
            if epoch <= meta["binding_epoch"]:
                raise ContractError("binding epoch must increase")
            meta["binding_epoch"] = epoch
            meta["discarded_on_rebind"] += self.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
            self.db.execute("DELETE FROM outbox")  # Revoked owner data is not sent to a new binding.
            candidate, event = self._restart(meta, wall_s)
        self.machine, self.boot_seq = candidate, event.boot_seq
        return event

    def envelope(self):
        with self.transaction():
            meta = self._owned_meta()
            pending = []
            for row in self.db.execute("SELECT payload,sha256 FROM outbox ORDER BY rowid"):
                event = Event.decode(row[0])
                if event.digest() != row[1]:
                    raise ContractError("outbox checksum mismatch")
                pending.append(event)
            return Event.decode(meta["head"]), [e for e in pending if e.binding_epoch == meta["binding_epoch"]]

    def acknowledge(self, ack):
        with self.transaction():
            meta = self._owned_meta()
            if (ack.get("kind") != "SERVER_DURABLY_SAVED" or ack.get("device_id") != meta["device_id"]
                    or type(ack.get("binding_epoch")) is not int or ack["binding_epoch"] > meta["binding_epoch"]
                    or ack["binding_epoch"] < 1):
                raise ContractError("wrong saved acknowledgment scope")
            if ack["binding_epoch"] < meta["binding_epoch"]:
                return  # Delayed ACK from a revoked owner cannot release current events.
            for event_id, sha in ack["saved_hashes"].items():
                row = self.db.execute("SELECT payload FROM outbox WHERE event_id=?", (event_id,)).fetchone()
                if row is None:
                    # Exact current safety/head ACK may not have an outbox row.
                    head = Event.decode(meta["head"])
                    if event_id == head.event_id:
                        if sha != head.digest():
                            raise ContractError("safety/head acknowledgment mismatch")
                    else:
                        prefix = meta["producer_id"] + ":event:"
                        sequence = event_id.removeprefix(prefix) if isinstance(event_id, str) else ""
                        if not sequence.isascii() or not sequence.isdecimal() or not event_id.startswith(prefix):
                            raise ContractError("invalid acknowledgment identity")
                        if not 1 <= int(sequence) < meta["event_seq"]:
                            raise ContractError("acknowledgment for an unissued event")
                        # Issued ID absent from bounded outbox: already removed/revoked.
                        # No digest history is needed; this branch cannot delete anything.
                else:
                    event = Event.decode(row[0])
                    if sha != event.digest() or event.binding_epoch != meta["binding_epoch"]:
                        raise ContractError("acknowledgment content mismatch")
                    self.db.execute("DELETE FROM outbox WHERE event_id=?", (event_id,))

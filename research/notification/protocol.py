"""Offline, synthetic notification contract. No sockets, cloud, or phone integration."""

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3


MAX_TTL_S = 1800  # Frozen baseline's provisional 30 minutes; not calibrated.
RETRY_S = (60, 300, 900, 3600)  # Deterministic server fixture; not radio energy policy.
SIMULATION_AUTH = "EXPLICIT_SIMULATION_ONLY"
ALLOWED = "SIMULATED_COLLECTION_ALLOWED"
STATES = {ALLOWED, "RESEARCH_WET", "LOW_SIGNAL_UNCONFIRMED", "UNKNOWN", "MONITORING_PAUSED"}
MAX_INT = 2**63 - 1


class ContractError(ValueError):
    pass


class InjectedCrash(RuntimeError):
    pass


def fail_at(requested, point):
    if requested == point:
        raise InjectedCrash(point)


def integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= MAX_INT:
        raise ContractError("integer outside contract range")


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ContractError("invalid identifier")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class Event:
    device_id: str
    producer_id: str
    binding_epoch: int
    batch_seq: int
    garment_session_id: str
    event_seq: int
    event_id: str
    state_version: int
    boot_id: int
    monotonic_ms: int
    measured_at_s: int | None
    clock_valid: bool
    clock_uncertainty_s: int
    ttl_s: int
    state: str
    authorization: str
    episode: int
    config_version: int
    schema_version: int = 1
    origin: str = "synthetic_offline_fixture"
    algorithm_version: str = "unintegrated_research_v1"
    quality_reason: str = "synthetic_input_no_garment_calibration"

    def validate(self):
        for value in (self.device_id, self.producer_id):
            identifier(value)
        for value in (self.binding_epoch, self.batch_seq, self.event_seq, self.state_version,
                      self.boot_id, self.config_version):
            integer(value, 1)
        for value in (self.monotonic_ms, self.clock_uncertainty_s, self.episode):
            integer(value)
        integer(self.ttl_s, 1)
        if self.ttl_s > MAX_TTL_S or type(self.clock_valid) is not bool:
            raise ContractError("invalid TTL or clock flag")
        if self.clock_valid:
            integer(self.measured_at_s)
            if self.measured_at_s < self.clock_uncertainty_s:
                raise ContractError("negative earliest measurement")
            integer(self.measured_at_s + self.ttl_s + self.clock_uncertainty_s)
        elif self.measured_at_s is not None or self.clock_uncertainty_s != 0:
            raise ContractError("invalid clock must not invent a wall time")
        if self.event_id != f"{self.producer_id}:event:{self.event_seq}":
            raise ContractError("event ID does not match persistent sequence")
        if self.garment_session_id != f"{self.producer_id}:batch:{self.batch_seq}":
            raise ContractError("batch ID mismatch")
        if (type(self.schema_version) is not int or self.schema_version != 1
                or self.origin != "synthetic_offline_fixture" or self.state not in STATES):
            raise ContractError("unsupported schema, origin, or state")
        if self.algorithm_version != "unintegrated_research_v1" or not isinstance(self.quality_reason, str):
            raise ContractError("not the frozen research interface")
        if self.state == ALLOWED:
            if self.authorization != SIMULATION_AUTH or self.episode < 1:
                raise ContractError("notification requires explicit simulation input")
        elif self.authorization != "NONE":
            raise ContractError("research evidence is not collection authorization")
        return self

    @property
    def order(self):
        return self.batch_seq, self.state_version

    @property
    def notice_key(self):
        return f"{self.device_id}:{self.binding_epoch}:{self.garment_session_id}:{self.episode}"

    @property
    def expires_at_s(self):
        return None if not self.clock_valid else self.measured_at_s + self.ttl_s - self.clock_uncertainty_s

    def encode(self):
        return canonical(asdict(self))

    def digest(self):
        return hashlib.sha256(self.encode().encode("utf-8")).hexdigest()

    @classmethod
    def decode(cls, payload):
        return cls(**json.loads(payload)).validate()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.db = sqlite3.connect(self.path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def close(self):
        self.db.close()

    def inspect(self):
        names = [r[0] for r in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {name: [dict(r) for r in self.db.execute(f'SELECT * FROM "{name}" ORDER BY rowid')]
                for name in names}


class Device(Store):
    """Create explicitly; reopening requires the durable DB to already exist."""

    def __init__(self, path, *, device_id=None, producer_id=None):
        if device_id is None and not Path(path).exists():
            raise ContractError("missing device storage: require explicit reprovisioning")
        super().__init__(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS device_meta (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS outbox (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, saved_ack INTEGER NOT NULL DEFAULT 0);
        """)
        with self.transaction():
            row = self.db.execute("SELECT payload FROM device_meta WHERE id=1").fetchone()
            if row is None:
                identifier(device_id)
                identifier(producer_id)
                meta = dict(device_id=device_id, producer_id=producer_id, binding_epoch=1,
                            batch_seq=0, event_seq=0, state_version=0, boot_id=0,
                            monotonic_ms=0, episode=0, head=None)
            else:
                if device_id is not None:
                    raise ContractError("do not overwrite provisioned device storage")
                meta = json.loads(row[0])
            meta["boot_id"] += 1
            integer(meta["boot_id"], 1)
            meta["monotonic_ms"] = 0
            self._write_meta(meta)

    def _meta(self):
        return json.loads(self.db.execute("SELECT payload FROM device_meta WHERE id=1").fetchone()[0])

    def _write_meta(self, meta):
        self.db.execute("INSERT OR REPLACE INTO device_meta VALUES (1, ?)", (canonical(meta),))

    def rebind(self, epoch):
        integer(epoch, 1)
        with self.transaction():
            meta = self._meta()
            if epoch <= meta["binding_epoch"]:
                raise ContractError("binding epoch must increase")
            meta.update(binding_epoch=epoch, head=None)
            self._write_meta(meta)
        # Old outbox entries remain inspectable, but never cross to the new owner.

    def emit(self, state, measured_at_s, *, monotonic_ms=0, new_batch=False,
             authorization="NONE", ttl_s=MAX_TTL_S, uncertainty_s=0, config_version=1,
             fail=None):
        with self.transaction():
            meta = self._meta()
            integer(monotonic_ms)
            if monotonic_ms < meta["monotonic_ms"]:
                raise ContractError("monotonic time moved backwards within one boot")
            previous = Event.decode(meta["head"]) if meta["head"] else None
            if new_batch or previous is None:
                meta["batch_seq"] += 1
                meta["state_version"] = 0
                meta["episode"] = 0
                previous = None
            if state == ALLOWED and (previous is None or previous.state != ALLOWED):
                meta["episode"] += 1
            meta["event_seq"] += 1
            meta["state_version"] += 1
            meta["monotonic_ms"] = monotonic_ms
            event = Event(
                device_id=meta["device_id"], producer_id=meta["producer_id"],
                binding_epoch=meta["binding_epoch"], batch_seq=meta["batch_seq"],
                garment_session_id=f'{meta["producer_id"]}:batch:{meta["batch_seq"]}',
                event_seq=meta["event_seq"], event_id=f'{meta["producer_id"]}:event:{meta["event_seq"]}',
                state_version=meta["state_version"], boot_id=meta["boot_id"], monotonic_ms=monotonic_ms,
                measured_at_s=measured_at_s, clock_valid=measured_at_s is not None,
                clock_uncertainty_s=uncertainty_s, ttl_s=ttl_s, state=state,
                authorization=authorization, episode=meta["episode"], config_version=config_version,
            ).validate()
            meta["head"] = event.encode()
            self._write_meta(meta)
            fail_at(fail, "after_counter")
            self.db.execute("INSERT INTO outbox(event_id,payload) VALUES (?,?)", (event.event_id, event.encode()))
            fail_at(fail, "before_device_commit")
        return event  # Nothing may be transmitted before the transaction commits.

    def envelope(self):
        with self.transaction():
            meta = self._meta()
            if meta["head"] is None:
                raise ContractError("new binding requires a new state snapshot")
            pending = [Event.decode(r[0]) for r in self.db.execute(
                "SELECT payload FROM outbox WHERE saved_ack=0 ORDER BY rowid")]
            return Event.decode(meta["head"]), [e for e in pending if e.binding_epoch == meta["binding_epoch"]]

    def acknowledge(self, ack):
        with self.transaction():
            meta = self._meta()
            if (ack["kind"] != "SERVER_DURABLY_SAVED" or ack["device_id"] != meta["device_id"]
                    or ack["binding_epoch"] != meta["binding_epoch"]):
                raise ContractError("wrong acknowledgment scope")
            for event_id, digest in ack["saved_hashes"].items():
                row = self.db.execute("SELECT payload FROM outbox WHERE event_id=?", (event_id,)).fetchone()
                event = Event.decode(row[0]) if row else None
                if event is None or event.digest() != digest or event.binding_epoch != meta["binding_epoch"]:
                    raise ContractError("acknowledgment does not match immutable event")
                self.db.execute("UPDATE outbox SET saved_ack=1 WHERE event_id=?", (event_id,))


class Service(Store):
    def __init__(self, path):
        super().__init__(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS service_clock (id INTEGER PRIMARY KEY CHECK(id=1), now_s INTEGER NOT NULL);
            INSERT OR IGNORE INTO service_clock VALUES (1,0);
            CREATE TABLE IF NOT EXISTS bindings (device_id TEXT PRIMARY KEY, epoch INTEGER NOT NULL, producer_id TEXT NOT NULL,
                owner TEXT NOT NULL, token TEXT NOT NULL, config_version INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, device_id TEXT NOT NULL, epoch INTEGER NOT NULL,
                batch_seq INTEGER NOT NULL, state_version INTEGER NOT NULL, payload TEXT NOT NULL, saved_at_s INTEGER NOT NULL,
                time_admitted INTEGER NOT NULL, ingest_reason TEXT NOT NULL,
                UNIQUE(device_id,epoch,batch_seq,state_version));
            CREATE TABLE IF NOT EXISTS heads (device_id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(event_id));
            CREATE TABLE IF NOT EXISTS jobs (event_id TEXT PRIMARY KEY REFERENCES events(event_id), notice_key TEXT NOT NULL,
                status TEXT NOT NULL, reason TEXT NOT NULL, next_attempt_s INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                expires_at_s INTEGER NOT NULL, receiver_saved_at_s INTEGER, user_read_at_s INTEGER);
            CREATE TABLE IF NOT EXISTS push_attempts (id INTEGER PRIMARY KEY, event_id TEXT NOT NULL REFERENCES jobs(event_id),
                attempted_at_s INTEGER NOT NULL, result TEXT NOT NULL);
        """)

    def _tick(self, now_s):
        integer(now_s)
        last = self.db.execute("SELECT now_s FROM service_clock WHERE id=1").fetchone()[0]
        if now_s < last:
            raise ContractError("service clock rollback: fail closed until time is repaired")
        self.db.execute("UPDATE service_clock SET now_s=? WHERE id=1", (now_s,))

    def bind(self, device_id, producer_id, owner, token, now_s, *, config_version=1):
        """Privileged fixture operation, not a production authentication endpoint."""
        for value in (device_id, producer_id, owner, token):
            identifier(value)
        integer(config_version, 1)
        with self.transaction():
            self._tick(now_s)
            old = self.db.execute("SELECT epoch FROM bindings WHERE device_id=?", (device_id,)).fetchone()
            epoch = old[0] + 1 if old else 1
            self.db.execute("INSERT OR REPLACE INTO bindings VALUES (?,?,?,?,?,?)",
                            (device_id, epoch, producer_id, owner, token, config_version))
            self.db.execute("DELETE FROM heads WHERE device_id=?", (device_id,))
            self._sweep(now_s)
        return epoch

    def configure(self, device_id, config_version, now_s):
        integer(config_version, 1)
        with self.transaction():
            self._tick(now_s)
            old = self.db.execute("SELECT config_version FROM bindings WHERE device_id=?", (device_id,)).fetchone()
            if old is None or config_version <= old[0]:
                raise ContractError("configuration version must increase")
            self.db.execute("UPDATE bindings SET config_version=? WHERE device_id=?", (config_version, device_id))
            self._sweep(now_s)

    def _binding(self, event):
        return self.db.execute("SELECT * FROM bindings WHERE device_id=?", (event.device_id,)).fetchone()

    def _head(self, device_id):
        row = self.db.execute("SELECT e.payload FROM heads h JOIN events e USING(event_id) WHERE h.device_id=?",
                              (device_id,)).fetchone()
        return Event.decode(row[0]) if row else None

    def _reason(self, event, now_s):
        binding = self._binding(event)
        if binding is None or binding["epoch"] != event.binding_epoch or binding["producer_id"] != event.producer_id:
            return "binding_revoked"
        head = self._head(event.device_id)
        if head is None or head.event_id != event.event_id:
            return "superseded"
        if event.config_version != binding["config_version"]:
            return "config_mismatch"
        if event.state != ALLOWED or event.authorization != SIMULATION_AUTH:
            return "research_only"
        if not event.clock_valid:
            return "clock_invalid"
        row = self.db.execute("SELECT time_admitted FROM events WHERE event_id=?", (event.event_id,)).fetchone()
        if row is None or not row[0]:
            return "future_or_uncertain_at_ingest"
        if now_s >= event.expires_at_s:
            return "expired"
        return "eligible_simulation"

    def _sweep(self, now_s):
        rows = self.db.execute("SELECT e.payload FROM jobs j JOIN events e USING(event_id) WHERE j.status='pending'").fetchall()
        for row in rows:
            event = Event.decode(row[0])
            reason = self._reason(event, now_s)
            if reason != "eligible_simulation":
                self.db.execute("UPDATE jobs SET status='cancelled',reason=? WHERE event_id=?", (reason, event.event_id))

    def submit(self, snapshot, pending, token, now_s, *, fail=None):
        snapshot.validate()
        events = {snapshot.event_id: snapshot}
        for event in pending:
            event.validate()
            if (event.device_id, event.producer_id, event.binding_epoch) != (
                    snapshot.device_id, snapshot.producer_id, snapshot.binding_epoch):
                raise ContractError("mixed identity/binding in upload")
            if event.order > snapshot.order or event.event_seq > snapshot.event_seq:
                raise ContractError("latest snapshot is behind pending events")
            if event.event_id in events and events[event.event_id] != event:
                raise ContractError("same event ID with different contents")
            events[event.event_id] = event
        with self.transaction():
            self._tick(now_s)
            binding = self._binding(snapshot)
            if (binding is None or binding["epoch"] != snapshot.binding_epoch or binding["token"] != token
                    or binding["producer_id"] != snapshot.producer_id):
                raise ContractError("simulated authorization rejected")
            head = self._head(snapshot.device_id)
            if head and ((snapshot.order > head.order and snapshot.event_seq <= head.event_seq)
                         or (snapshot.order < head.order and snapshot.event_seq >= head.event_seq)):
                raise ContractError("sequence and state order disagree")
            added = []
            for event in events.values():
                known = self.db.execute("SELECT payload FROM events WHERE event_id=?", (event.event_id,)).fetchone()
                if known:
                    if known[0] != event.encode():
                        raise ContractError("immutable event ID collision")
                    continue
                admitted = event.clock_valid and event.measured_at_s + event.clock_uncertainty_s <= now_s
                try:
                    self.db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?)", (
                        event.event_id, event.device_id, event.binding_epoch, event.batch_seq, event.state_version,
                        event.encode(), now_s, int(admitted), "transaction_pending"))
                except sqlite3.IntegrityError as exc:
                    raise ContractError("state version already belongs to another event") from exc
                added.append(event)
            if head is None or snapshot.order > head.order:
                self.db.execute("INSERT OR REPLACE INTO heads VALUES (?,?)", (snapshot.device_id, snapshot.event_id))
            fail_at(fail, "after_event_save")
            self._sweep(now_s)
            for event in added:
                reason = self._reason(event, now_s)
                self.db.execute("UPDATE events SET ingest_reason=? WHERE event_id=?", (reason, event.event_id))
                if reason == "eligible_simulation":
                    self.db.execute("INSERT INTO jobs(event_id,notice_key,status,reason,next_attempt_s,expires_at_s) VALUES (?,?,'pending',?,?,?)",
                                    (event.event_id, event.notice_key, reason, now_s, event.expires_at_s))
            fail_at(fail, "before_service_commit")
        fail_at(fail, "after_service_commit")  # Lost saved ACK, durable event and outbox already exist.
        return dict(kind="SERVER_DURABLY_SAVED", device_id=snapshot.device_id,
                    binding_epoch=snapshot.binding_epoch, acknowledged_at_s=now_s,
                    saved_hashes={event.event_id: event.digest() for event in events.values()})

    def validate_for_receiver(self, event, owner, now_s):
        event.validate()
        with self.transaction():
            self._tick(now_s)
            binding = self._binding(event)
            if binding is None or binding["owner"] != owner:
                return "wrong_recipient"
            row = self.db.execute("SELECT payload FROM events WHERE event_id=?", (event.event_id,)).fetchone()
            if row is None or row[0] != event.encode():
                return "unrecognized_event"
            return self._reason(event, now_s)

    def dispatch(self, receiver, now_s, *, phone_reachable=True, fail=None):
        with self.transaction():
            self._tick(now_s)
            self._sweep(now_s)
            rows = self.db.execute("SELECT e.payload FROM jobs j JOIN events e USING(event_id) "
                                   "WHERE j.status='pending' AND j.next_attempt_s<=? ORDER BY e.rowid", (now_s,)).fetchall()
        for row in rows:
            event = Event.decode(row[0])
            with self.transaction():
                reason = self._reason(event, now_s)
                if reason != "eligible_simulation":
                    continue
                count = self.db.execute("SELECT attempts FROM jobs WHERE event_id=?", (event.event_id,)).fetchone()[0]
                self.db.execute("UPDATE jobs SET attempts=attempts+1,next_attempt_s=? WHERE event_id=?",
                                (now_s + RETRY_S[min(count, len(RETRY_S)-1)], event.event_id))
                self.db.execute("INSERT INTO push_attempts(event_id,attempted_at_s,result) VALUES (?,?,'SIMULATED_PUSH_ACCEPTED')",
                                (event.event_id, now_s))
            fail_at(fail, "after_simulated_push")
            if not phone_reachable:
                continue
            result = receiver.receive(event, self, now_s)
            fail_at(fail, "after_receiver_commit")
            with self.transaction():
                if result in {"SIMULATED_RECEIVER_SAVED", "DUPLICATE_SUPPRESSED"}:
                    self.db.execute("UPDATE jobs SET status='receiver_saved',reason=?,receiver_saved_at_s=? WHERE event_id=?",
                                    (result, now_s, event.event_id))
                else:
                    self.db.execute("UPDATE jobs SET status='cancelled',reason=? WHERE event_id=?", (result, event.event_id))

    def acknowledge_read(self, event, owner, now_s):
        if self.validate_for_receiver(event, owner, now_s) != "eligible_simulation":
            raise ContractError("read acknowledgment belongs to invalidated notification")
        with self.transaction():
            row = self.db.execute("SELECT receiver_saved_at_s FROM jobs WHERE event_id=?", (event.event_id,)).fetchone()
            if row is None or row[0] is None:
                raise ContractError("cannot mark unread/unreceived notification as read")
            self.db.execute("UPDATE jobs SET user_read_at_s=COALESCE(user_read_at_s,?) WHERE event_id=?", (now_s, event.event_id))


class Receiver(Store):
    def __init__(self, path, owner):
        identifier(owner)
        super().__init__(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS receiver_owner (id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS notices (notice_key TEXT PRIMARY KEY, payload TEXT NOT NULL,
                received_at_s INTEGER NOT NULL, simulated_alert_at_s INTEGER NOT NULL,
                user_read_at_s INTEGER, invalidated_at_s INTEGER, invalid_reason TEXT);
        """)
        with self.transaction():
            self.db.execute("INSERT OR IGNORE INTO receiver_owner VALUES (1,?)", (owner,))
            if self.db.execute("SELECT owner FROM receiver_owner").fetchone()[0] != owner:
                raise ContractError("receiver store belongs to another simulated owner")
        self.owner = owner

    def receive(self, event, service, now_s):
        # A data-only hint must be checked against the latest known server state.
        reason = service.validate_for_receiver(event, self.owner, now_s)
        if reason != "eligible_simulation":
            return reason
        with self.transaction():
            row = self.db.execute("SELECT payload FROM notices WHERE notice_key=?", (event.notice_key,)).fetchone()
            if row:
                if Event.decode(row[0]).event_seq < event.event_seq:
                    self.db.execute("UPDATE notices SET payload=?,received_at_s=?,user_read_at_s=NULL,"
                                    "invalidated_at_s=NULL,invalid_reason=NULL WHERE notice_key=?",
                                    (event.encode(), now_s, event.notice_key))
                return "DUPLICATE_SUPPRESSED"  # Refresh card silently, never make a second alert in one episode.
            self.db.execute("INSERT INTO notices VALUES (?,?,?,?,NULL,NULL,NULL)",
                            (event.notice_key, event.encode(), now_s, now_s))
        return "SIMULATED_RECEIVER_SAVED"

    def sync(self, service, now_s):
        """Called when simulated receiver is reachable; cannot recall an offline OS alert."""
        for row in self.db.execute("SELECT notice_key,payload FROM notices WHERE invalidated_at_s IS NULL").fetchall():
            reason = service.validate_for_receiver(Event.decode(row[1]), self.owner, now_s)
            if reason != "eligible_simulation":
                with self.transaction():
                    self.db.execute("UPDATE notices SET invalidated_at_s=?,invalid_reason=? WHERE notice_key=?",
                                    (now_s, reason, row[0]))

    def read(self, notice_key, service, now_s):
        row = self.db.execute("SELECT payload,invalidated_at_s FROM notices WHERE notice_key=?", (notice_key,)).fetchone()
        if row is None or row[1] is not None:
            raise ContractError("no active simulated notification to read")
        event = Event.decode(row[0])
        service.acknowledge_read(event, self.owner, now_s)
        with self.transaction():
            self.db.execute("UPDATE notices SET user_read_at_s=COALESCE(user_read_at_s,?) WHERE notice_key=?", (now_s, notice_key))

"""Single-worker offline service and receiver. No network/provider integration."""
import json

from research.state_machine.machine import Configuration
from research.notification_t07.contract import Event, RETRY_S, RESEARCH_TEXT, identity, seconds
from research.notification_t07.storage import Store, ContractError, encode, fail_at

SERVICE_SCHEMA = """
CREATE TABLE clock(id INTEGER PRIMARY KEY CHECK(id=1), now_s REAL NOT NULL);
INSERT INTO clock VALUES (1,0);
CREATE TABLE bindings(device_id TEXT PRIMARY KEY, producer_id TEXT NOT NULL, epoch INTEGER NOT NULL,
    owner TEXT NOT NULL, token TEXT NOT NULL, required_config_sha256 TEXT NOT NULL, last_contact_s REAL);
CREATE TABLE events(event_id TEXT PRIMARY KEY, device_id TEXT NOT NULL, epoch INTEGER NOT NULL,
    state_version INTEGER NOT NULL, payload TEXT NOT NULL, saved_at_s REAL NOT NULL,
    time_admitted INTEGER NOT NULL, ingest_reason TEXT NOT NULL,
    UNIQUE(device_id,epoch,state_version));
CREATE TABLE heads(device_id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(event_id));
CREATE TABLE evidence_admission(evidence_key TEXT PRIMARY KEY, stamp TEXT NOT NULL, time_admitted INTEGER NOT NULL);
CREATE TABLE jobs(event_id TEXT PRIMARY KEY REFERENCES events(event_id), notice_key TEXT NOT NULL,
    status TEXT NOT NULL, reason TEXT NOT NULL, expires_at_s REAL NOT NULL,
    next_attempt_s REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
    receiver_saved_at_s REAL, user_read_at_s REAL);
CREATE TABLE push_attempts(id INTEGER PRIMARY KEY, event_id TEXT NOT NULL REFERENCES jobs(event_id),
    attempted_at_s REAL NOT NULL, result TEXT NOT NULL);
"""


class Service(Store):
    def __init__(self, path, *, create=False):
        super().__init__(path, create=create, schema=SERVICE_SCHEMA)

    def _tick(self, now_s):
        seconds(now_s)
        if now_s < self.db.execute("SELECT now_s FROM clock WHERE id=1").fetchone()[0]:
            raise ContractError("service clock moved backwards")
        self.db.execute("UPDATE clock SET now_s=? WHERE id=1", (now_s,))

    def bind(self, device_id, producer_id, owner, token, config, now_s):
        """Privileged SIMULATED binding registry, not real authorization."""
        for value in (device_id, producer_id, owner, token):
            identity(value)
        config.validate()
        with self.transaction():
            self._tick(now_s)
            old = self.db.execute("SELECT epoch FROM bindings WHERE device_id=?", (device_id,)).fetchone()
            epoch = old[0] + 1 if old else 1
            self.db.execute("INSERT OR REPLACE INTO bindings VALUES (?,?,?,?,?,?,NULL)",
                            (device_id, producer_id, epoch, owner, token, config.sha256))
            self.db.execute("DELETE FROM heads WHERE device_id=?", (device_id,))
            self._sweep(now_s)
        return epoch

    def require_config(self, device_id, config, now_s):
        if not isinstance(config, Configuration):
            raise ContractError("expected full validated required config")
        config.validate()
        with self.transaction():
            self._tick(now_s)
            changed = self.db.execute("UPDATE bindings SET required_config_sha256=? WHERE device_id=?", (config.sha256, device_id))
            if changed.rowcount != 1:
                raise ContractError("unknown device")
            self._sweep(now_s)

    def _binding(self, event):
        return self.db.execute("SELECT * FROM bindings WHERE device_id=?", (event.device_id,)).fetchone()

    def _head(self, device_id):
        row = self.db.execute("SELECT e.payload FROM heads h JOIN events e USING(event_id) WHERE h.device_id=?", (device_id,)).fetchone()
        return Event.decode(row[0]) if row else None

    def _reason(self, event, now_s):
        binding = self._binding(event)
        if binding is None or binding["epoch"] != event.binding_epoch or binding["producer_id"] != event.producer_id:
            return "binding_revoked"
        head = self._head(event.device_id)
        if head is None or event.event_id != head.event_id:
            return "superseded"
        if event.controller_fault:
            return event.controller_fault
        if binding["required_config_sha256"] != event.upstream["config_sha256"]:
            return "required_config_not_applied"
        if event.notice_kind != "RESEARCH_REVIEW":
            return "state_history_only"
        if not event.clock_valid:
            return "clock_invalid"
        record = self.db.execute("SELECT time_admitted FROM events WHERE event_id=?", (event.event_id,)).fetchone()
        if record is None or not record[0]:
            return "future_or_uncertain_at_ingest"
        if now_s >= event.expires_at_s:
            return "expired"
        return "eligible_research_review"

    def _sweep(self, now_s):
        for row in self.db.execute("SELECT e.payload FROM jobs j JOIN events e USING(event_id) WHERE j.status='pending'").fetchall():
            event = Event.decode(row[0])
            reason = self._reason(event, now_s)
            if reason != "eligible_research_review":
                self.db.execute("UPDATE jobs SET status='cancelled',reason=? WHERE event_id=?", (reason, event.event_id))

    def submit(self, snapshot, pending, token, now_s, *, fail=None):
        snapshot.validate()
        events = {snapshot.event_id: snapshot}
        for event in pending:
            event.validate()
            if (event.device_id, event.producer_id, event.binding_epoch) != (snapshot.device_id, snapshot.producer_id, snapshot.binding_epoch):
                raise ContractError("mixed identity/binding in upload")
            if event.state_version > snapshot.state_version or event.boot_seq > snapshot.boot_seq:
                raise ContractError("snapshot is not latest in upload")
            if event.event_id in events and event != events[event.event_id]:
                raise ContractError("same event ID has conflicting content")
            events[event.event_id] = event
        with self.transaction():
            self._tick(now_s)
            binding = self._binding(snapshot)
            if (binding is None or binding["epoch"] != snapshot.binding_epoch or binding["token"] != token
                    or binding["producer_id"] != snapshot.producer_id):
                raise ContractError("simulated binding authorization rejected")
            head = self._head(snapshot.device_id)
            if head and snapshot.state_version > head.state_version and snapshot.boot_seq < head.boot_seq:
                raise ContractError("new state cannot move boot generation backwards")
            added = []
            for event in events.values():
                known = self.db.execute("SELECT payload FROM events WHERE event_id=?", (event.event_id,)).fetchone()
                if known:
                    if known[0] != event.encode():
                        raise ContractError("immutable event ID collision")
                    continue
                admitted = (event.clock_valid and event.measured_at_s + event.uncertainty_s <= now_s
                            and event.confirmed_at_s <= now_s)
                if event.evidence_key:
                    stamp = encode([event.measured_at_s, event.uncertainty_s, event.ttl_s])
                    proof = self.db.execute("SELECT stamp,time_admitted FROM evidence_admission WHERE evidence_key=?", (event.evidence_key,)).fetchone()
                    if proof is not None:
                        if proof[0] != stamp:
                            raise ContractError("same raw evidence changed measurement time/TTL")
                        admitted = admitted and bool(proof[1])
                    else:
                        self.db.execute("INSERT INTO evidence_admission VALUES (?,?,?)", (event.evidence_key, stamp, int(admitted)))
                self.db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)", (
                    event.event_id, event.device_id, event.binding_epoch, event.state_version,
                    event.encode(), now_s, int(admitted), "transaction_pending"))
                added.append(event)
            if head is None or snapshot.state_version > head.state_version:
                self.db.execute("INSERT OR REPLACE INTO heads VALUES (?,?)", (snapshot.device_id, snapshot.event_id))
            self.db.execute("UPDATE bindings SET last_contact_s=? WHERE device_id=?", (now_s, snapshot.device_id))
            fail_at(fail, "after_service_events")
            self._sweep(now_s)
            for event in added:
                reason = self._reason(event, now_s)
                self.db.execute("UPDATE events SET ingest_reason=? WHERE event_id=?", (reason, event.event_id))
                if reason == "eligible_research_review":
                    self.db.execute("INSERT INTO jobs(event_id,notice_key,status,reason,expires_at_s,next_attempt_s) VALUES (?,?,'pending',?,?,?)",
                                    (event.event_id, event.notice_key, reason, event.expires_at_s, now_s))
            fail_at(fail, "before_service_commit")
        fail_at(fail, "after_service_commit")
        return dict(kind="SERVER_DURABLY_SAVED", device_id=snapshot.device_id, binding_epoch=snapshot.binding_epoch,
                    acknowledged_at_s=now_s, saved_hashes={e.event_id: e.digest() for e in events.values()})

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

    def dispatch(self, receiver, now_s, *, reachable=True, fail=None):
        with self.transaction():
            self._tick(now_s)
            self._sweep(now_s)
            events = [Event.decode(row[0]) for row in self.db.execute(
                "SELECT e.payload FROM jobs j JOIN events e USING(event_id) WHERE j.status='pending' AND j.next_attempt_s<=? ORDER BY e.rowid", (now_s,))]
        for event in events:
            # Serial worker fixture; wrong receiver does not consume/cancel another owner's job.
            if self._binding(event)["owner"] != receiver.owner:
                continue
            with self.transaction():
                if self._reason(event, now_s) != "eligible_research_review":
                    continue
                n = self.db.execute("SELECT attempts FROM jobs WHERE event_id=?", (event.event_id,)).fetchone()[0]
                due = now_s + RETRY_S[min(n, len(RETRY_S)-1)]
                seconds(due)
                self.db.execute("UPDATE jobs SET attempts=attempts+1,next_attempt_s=? WHERE event_id=?", (due, event.event_id))
                self.db.execute("INSERT INTO push_attempts(event_id,attempted_at_s,result) VALUES (?,?,'SIMULATED_PUSH_ACCEPTED')", (event.event_id, now_s))
            fail_at(fail, "after_simulated_push")
            if not reachable:
                continue
            result = receiver.receive(event, self, now_s)
            fail_at(fail, "after_receiver_commit")
            with self.transaction():
                if result in ("SIMULATED_RECEIVER_SAVED", "DUPLICATE_SUPPRESSED"):
                    self.db.execute("UPDATE jobs SET status='receiver_saved',reason=?,receiver_saved_at_s=? WHERE event_id=?", (result, now_s, event.event_id))
                else:
                    self.db.execute("UPDATE jobs SET status='cancelled',reason=? WHERE event_id=?", (result, event.event_id))

    def record_read(self, event, owner, now_s):
        if self.validate_for_receiver(event, owner, now_s) != "eligible_research_review":
            raise ContractError("read targets invalidated notice")
        with self.transaction():
            row = self.db.execute("SELECT receiver_saved_at_s FROM jobs WHERE event_id=?", (event.event_id,)).fetchone()
            if row is None or row[0] is None:
                raise ContractError("no receiver acknowledgment")
            self.db.execute("UPDATE jobs SET user_read_at_s=COALESCE(user_read_at_s,?) WHERE event_id=?", (now_s, event.event_id))

    def app_status(self, device_id, owner, now_s):
        with self.transaction():
            self._tick(now_s)
            binding = self.db.execute("SELECT * FROM bindings WHERE device_id=?", (device_id,)).fetchone()
            if binding is None or binding["owner"] != owner:
                raise ContractError("unknown device or owner")
            head = self._head(device_id)
            return dict(synthetic="SYNTHETIC_UNCALIBRATED", collection_authorized=False,
                        latest=head.payload() if head else None,
                        measured_at_s=head.measured_at_s if head else None,
                        confirmed_at_s=head.confirmed_at_s if head else None,
                        last_contact_s=binding["last_contact_s"], required_config_sha256=binding["required_config_sha256"],
                        applied_config_sha256=head.upstream["config_sha256"] if head else None,
                        eligibility=self._reason(head, now_s) if head else "awaiting_snapshot")


RECEIVER_SCHEMA = """
CREATE TABLE owner(id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL);
CREATE TABLE notices(notice_key TEXT PRIMARY KEY, payload TEXT NOT NULL, research_text TEXT NOT NULL,
    received_at_s REAL NOT NULL, simulated_alert_at_s REAL NOT NULL,
    user_read_at_s REAL, invalidated_at_s REAL, invalid_reason TEXT);
"""


class Receiver(Store):
    def __init__(self, path, owner, *, create=False):
        identity(owner)
        super().__init__(path, create=create, schema=RECEIVER_SCHEMA)
        with self.transaction():
            if create:
                self.db.execute("INSERT INTO owner VALUES (1,?)", (owner,))
            row = self.db.execute("SELECT name FROM owner WHERE id=1").fetchone()
            if row is None or row[0] != owner:
                raise ContractError("receiver belongs to a different owner")
        self.owner = owner

    def receive(self, event, service, now_s):
        reason = service.validate_for_receiver(event, self.owner, now_s)
        if reason != "eligible_research_review":
            return reason
        with self.transaction():
            row = self.db.execute("SELECT payload FROM notices WHERE notice_key=?", (event.notice_key,)).fetchone()
            if row:
                if Event.decode(row[0]).state_version < event.state_version:
                    self.db.execute("UPDATE notices SET payload=?,received_at_s=?,user_read_at_s=NULL,invalidated_at_s=NULL,invalid_reason=NULL WHERE notice_key=?",
                                    (event.encode(), now_s, event.notice_key))
                return "DUPLICATE_SUPPRESSED"
            self.db.execute("INSERT INTO notices VALUES (?,?,?,?,?,NULL,NULL,NULL)",
                            (event.notice_key, event.encode(), RESEARCH_TEXT, now_s, now_s))
        return "SIMULATED_RECEIVER_SAVED"

    def sync(self, service, now_s):
        for row in self.db.execute("SELECT notice_key,payload FROM notices WHERE invalidated_at_s IS NULL").fetchall():
            reason = service.validate_for_receiver(Event.decode(row[1]), self.owner, now_s)
            if reason != "eligible_research_review":
                with self.transaction():
                    self.db.execute("UPDATE notices SET invalidated_at_s=?,invalid_reason=? WHERE notice_key=?", (now_s, reason, row[0]))

    def read(self, notice_key, service, now_s):
        row = self.db.execute("SELECT payload,invalidated_at_s FROM notices WHERE notice_key=?", (notice_key,)).fetchone()
        if row is None or row[1] is not None:
            raise ContractError("no valid notice to read")
        event = Event.decode(row[0])
        service.record_read(event, self.owner, now_s)
        with self.transaction():
            self.db.execute("UPDATE notices SET user_read_at_s=COALESCE(user_read_at_s,?) WHERE notice_key=?", (now_s, notice_key))

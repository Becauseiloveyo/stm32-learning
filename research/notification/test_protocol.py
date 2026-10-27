"""Adversarial offline scenarios; all times are synthetic integer UTC seconds."""

from dataclasses import replace
import itertools
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from protocol import ALLOWED, SIMULATION_AUTH, ContractError, Device, InjectedCrash, Receiver, Service


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="notification-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.device = self.track(Device(self.root / "device.sqlite3", device_id="hanger_1", producer_id="fixture_p1"))
        self.service = self.track(Service(self.root / "service.sqlite3"))
        self.receiver = self.track(Receiver(self.root / "receiver.sqlite3", "owner_a"))
        self.service.bind("hanger_1", "fixture_p1", "owner_a", "fixture_token_a", 1000)

    def track(self, store):
        self.addCleanup(store.close)
        return store

    def allowed(self, at=1000, **kwargs):
        return self.device.emit(ALLOWED, at, authorization=SIMULATION_AUTH, **kwargs)

    def upload(self, at=1000, **kwargs):
        ack = self.service.submit(*self.device.envelope(), "fixture_token_a", at, **kwargs)
        self.device.acknowledge(ack)
        return ack

    def rows(self, table, store=None):
        return (store or self.service).inspect()[table]

    def crash(self, stage):
        result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(Path(__file__).with_name("crash_worker.py")),
                                 str(self.root), stage], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 73, result.stderr)

    def test_saved_push_receiver_and_read_are_distinct(self):
        event = self.allowed()
        ack = self.upload()
        self.assertEqual(ack["kind"], "SERVER_DURABLY_SAVED")
        self.assertEqual(self.rows("push_attempts"), [])
        self.assertIsNone(self.rows("jobs")[0]["receiver_saved_at_s"])
        self.service.dispatch(self.receiver, 1000, phone_reachable=False)
        self.assertEqual(self.rows("push_attempts")[0]["result"], "SIMULATED_PUSH_ACCEPTED")
        self.assertEqual(self.rows("notices", self.receiver), [])
        self.service.dispatch(self.receiver, 1060)
        self.assertEqual(self.rows("jobs")[0]["receiver_saved_at_s"], 1060)
        self.assertIsNone(self.rows("jobs")[0]["user_read_at_s"])
        self.receiver.read(event.notice_key, self.service, 1061)
        self.receiver.read(event.notice_key, self.service, 1062)
        self.assertEqual(self.rows("jobs")[0]["user_read_at_s"], 1061)

    def test_lost_saved_ack_and_duplicate_submission(self):
        self.allowed()
        with self.assertRaises(InjectedCrash):
            self.upload(fail="after_service_commit")
        self.assertEqual(len(self.device.envelope()[1]), 1)
        self.upload()
        for _ in range(5):
            self.upload()
            self.service.dispatch(self.receiver, 1000)
        self.assertEqual(len(self.rows("events")), 1)
        self.assertEqual(len(self.rows("jobs")), 1)
        self.assertEqual(len(self.rows("push_attempts")), 1)
        self.assertEqual(len(self.rows("notices", self.receiver)), 1)

    def test_duplicate_payload_conflict_rolls_back(self):
        event = self.allowed()
        self.upload()
        with self.assertRaises(ContractError):
            self.service.submit(replace(event, ttl_s=100), [], "fixture_token_a", 1001)
        self.assertEqual(len(self.rows("events")), 1)
        self.assertEqual(self.rows("jobs")[0]["expires_at_s"], 2800)

    def test_state_version_collision_rejected(self):
        event = self.allowed()
        self.upload()
        conflicting = replace(event, event_seq=2, event_id="fixture_p1:event:2")
        with self.assertRaises(ContractError):
            self.service.submit(conflicting, [], "fixture_token_a", 1000)

    def test_latest_snapshot_must_cover_all_pending(self):
        old = self.allowed()
        new = self.device.emit("RESEARCH_WET", 1001)
        with self.assertRaises(ContractError):
            self.service.submit(old, [new], "fixture_token_a", 1001)
        self.assertEqual(self.rows("events"), [])

    def test_offline_state_change_keeps_old_allowed_event_as_history(self):
        old = self.allowed()
        new = self.device.emit("RESEARCH_WET", 1001)
        self.upload(1002)
        self.service.dispatch(self.receiver, 1002)
        reasons = {r["event_id"]: r["ingest_reason"] for r in self.rows("events")}
        self.assertEqual(reasons[old.event_id], "superseded")
        self.assertEqual(reasons[new.event_id], "research_only")
        self.assertEqual(self.rows("jobs"), [])
        self.assertEqual(self.rows("notices", self.receiver), [])

    def test_out_of_order_upload_cannot_roll_back_latest_state(self):
        self.allowed()
        old_envelope = self.device.envelope()
        current = self.device.emit("RESEARCH_WET", 1001)
        self.upload(1001)
        self.service.submit(*old_envelope, "fixture_token_a", 1002)
        self.assertEqual(self.rows("heads")[0]["event_id"], current.event_id)
        self.assertEqual(self.rows("jobs"), [])

    def test_queued_and_late_push_invalidated_by_new_state(self):
        event = self.allowed()
        self.upload()
        self.device.emit("RESEARCH_WET", 1001)
        self.upload(1001)
        self.service.dispatch(self.receiver, 1002)
        self.assertEqual(self.rows("jobs")[0]["reason"], "superseded")
        self.assertEqual(self.receiver.receive(event, self.service, 1002), "superseded")
        self.assertEqual(self.rows("notices", self.receiver), [])

    def test_new_batch_fences_old_batch_despite_smaller_state_version(self):
        self.allowed()
        old = self.allowed(1001)
        old_envelope = self.device.envelope()
        self.upload(1001)
        new = self.device.emit("UNKNOWN", 1002, new_batch=True)
        self.assertGreater(old.state_version, new.state_version)
        self.upload(1002)
        self.service.submit(*old_envelope, "fixture_token_a", 1003)
        self.service.dispatch(self.receiver, 1003)
        self.assertEqual(self.rows("heads")[0]["event_id"], new.event_id)
        self.assertEqual(self.rows("notices", self.receiver), [])

    def test_expiry_boundary_and_no_refresh_by_duplicate(self):
        event = self.allowed(ttl_s=10)
        self.upload()
        self.service.dispatch(self.receiver, 1010)
        self.upload(1011)
        self.service.dispatch(self.receiver, 1011)
        self.assertEqual(self.rows("jobs")[0]["reason"], "expired")
        self.assertEqual(self.rows("notices", self.receiver), [])
        self.assertEqual(self.receiver.receive(event, self.service, 1011), "expired")

    def test_already_expired_on_first_upload(self):
        self.allowed(ttl_s=10)
        self.upload(1010)
        self.assertEqual(self.rows("events")[0]["ingest_reason"], "expired")
        self.assertEqual(self.rows("jobs"), [])

    def test_invalid_clock_keeps_history_without_inventing_measurement_time(self):
        event = self.allowed(at=None)
        self.upload()
        self.assertIsNone(event.measured_at_s)
        self.assertIsNone(event.expires_at_s)
        self.assertEqual(self.rows("events")[0]["ingest_reason"], "clock_invalid")
        self.assertEqual(self.rows("jobs"), [])

    def test_future_measurement_cannot_become_valid_by_waiting_and_retrying(self):
        self.allowed(at=1010)
        self.upload(1000)
        self.upload(1010)
        self.assertEqual(self.rows("events")[0]["ingest_reason"], "future_or_uncertain_at_ingest")
        self.assertEqual(self.rows("jobs"), [])
        self.allowed(at=1011)
        self.upload(1011)
        self.service.dispatch(self.receiver, 1011)
        self.assertEqual(len(self.rows("notices", self.receiver)), 1)

    def test_uncertainty_shortens_lifetime(self):
        event = self.allowed(at=1000, ttl_s=20, uncertainty_s=2)
        self.upload(1002)
        self.assertEqual(event.expires_at_s, 1018)
        self.service.dispatch(self.receiver, 1018)
        self.assertEqual(self.rows("jobs")[0]["reason"], "expired")

    def test_weak_signals_never_authorize_collection(self):
        for t in range(1000, 1020):
            self.device.emit("LOW_SIGNAL_UNCONFIRMED", t)
            self.upload(t)
            self.service.dispatch(self.receiver, t)
        self.assertEqual(len(self.rows("events")), 20)
        self.assertEqual(self.rows("jobs"), [])
        with self.assertRaises(ContractError):
            self.device.emit("LOW_SIGNAL_UNCONFIRMED", 1020, authorization=SIMULATION_AUTH)
        with self.assertRaises(ContractError):
            self.device.emit(ALLOWED, 1020)

    def test_malformed_fields_fail_closed(self):
        event = self.allowed()
        for changes in ({"ttl_s": 0}, {"ttl_s": 1801}, {"clock_valid": 1}, {"state_version": True},
                        {"schema_version": 2}, {"measured_at_s": -1}, {"monotonic_ms": -1},
                        {"state": "REAL_COLLECTION_ALLOWED"}, {"authorization": "CALIBRATED"},
                        {"garment_session_id": "forged"}, {"clock_valid": False},
                        {"origin": "real_garment"}, {"event_seq": 2**63}):
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                self.service.submit(replace(event, **changes), [], "fixture_token_a", 1000)
        self.assertEqual(self.rows("events"), [])

    def test_phone_unreachable_retries_only_when_due_then_recovers(self):
        self.allowed()
        self.upload()
        for now in (1000, 1001, 1059):
            self.service.dispatch(self.receiver, now, phone_reachable=False)
        self.assertEqual(len(self.rows("push_attempts")), 1)
        self.assertEqual(self.rows("jobs")[0]["next_attempt_s"], 1060)
        self.service.dispatch(self.receiver, 1060, phone_reachable=False)
        self.service.dispatch(self.receiver, 1360)
        self.assertEqual(len(self.rows("push_attempts")), 3)
        self.assertEqual(len(self.rows("notices", self.receiver)), 1)

    def test_unreachable_phone_does_not_receive_expired_task_on_recovery(self):
        self.allowed(ttl_s=30)
        self.upload()
        self.service.dispatch(self.receiver, 1000, phone_reachable=False)
        self.service.dispatch(self.receiver, 1060)
        self.assertEqual(len(self.rows("push_attempts")), 1)
        self.assertEqual(self.rows("jobs")[0]["reason"], "expired")
        self.assertEqual(self.rows("notices", self.receiver), [])

    def test_rebinding_rejects_old_upload_old_recipient_and_delayed_push(self):
        old = self.allowed()
        envelope = self.device.envelope()
        self.upload()
        epoch = self.service.bind("hanger_1", "fixture_p1", "owner_b", "fixture_token_b", 1001)
        with self.assertRaises(ContractError):
            self.service.submit(*envelope, "fixture_token_a", 1001)
        self.assertEqual(self.receiver.receive(old, self.service, 1001), "wrong_recipient")
        recipient_b = self.track(Receiver(self.root / "receiver-b.sqlite3", "owner_b"))
        self.assertEqual(recipient_b.receive(old, self.service, 1001), "binding_revoked")
        self.device.rebind(epoch)
        with self.assertRaises(ContractError):
            self.device.envelope()
        new = self.allowed(at=1002)
        snapshot, pending = self.device.envelope()
        self.assertEqual([e.event_id for e in pending], [new.event_id])
        self.device.acknowledge(self.service.submit(snapshot, pending, "fixture_token_b", 1002))
        self.service.dispatch(recipient_b, 1002)
        self.assertEqual(len(self.rows("notices", recipient_b)), 1)
        self.assertEqual(self.rows("jobs")[0]["reason"], "binding_revoked")

    def test_wrong_token_and_unknown_producer_rejected(self):
        event = self.allowed()
        with self.assertRaises(ContractError):
            self.service.submit(event, [], "wrong", 1000)
        other = self.track(Device(self.root / "other.sqlite3", device_id="hanger_1", producer_id="unbound_p2"))
        other.emit("UNKNOWN", 1000)
        with self.assertRaises(ContractError):
            self.service.submit(*other.envelope(), "fixture_token_a", 1000)

    def test_config_change_cancels_old_task_until_device_applies_new_version(self):
        old = self.allowed()
        self.upload()
        self.service.configure("hanger_1", 2, 1001)
        self.service.dispatch(self.receiver, 1001)
        self.assertEqual(self.rows("jobs")[0]["reason"], "config_mismatch")
        self.allowed(at=1002, config_version=2)
        self.upload(1002)
        self.service.dispatch(self.receiver, 1002)
        self.assertEqual(len(self.rows("notices", self.receiver)), 1)
        self.assertEqual(self.receiver.receive(old, self.service, 1003), "superseded")

    def test_repeated_confirmation_silent_within_episode_new_episode_may_alert(self):
        first = self.allowed()
        self.upload()
        self.service.dispatch(self.receiver, 1000)
        self.receiver.read(first.notice_key, self.service, 1000)
        second = self.allowed(at=1001)
        self.upload(1001)
        self.service.dispatch(self.receiver, 1001)
        self.assertEqual(first.notice_key, second.notice_key)
        self.assertEqual(len(self.rows("notices", self.receiver)), 1)
        self.assertEqual(self.rows("notices", self.receiver)[0]["simulated_alert_at_s"], 1000)
        self.assertIsNone(self.rows("notices", self.receiver)[0]["user_read_at_s"])
        self.device.emit("RESEARCH_WET", 1002)
        self.upload(1002)
        self.receiver.sync(self.service, 1002)
        third = self.allowed(at=1003)
        self.upload(1003)
        self.service.dispatch(self.receiver, 1003)
        self.assertNotEqual(first.notice_key, third.notice_key)
        self.assertEqual(len(self.rows("notices", self.receiver)), 2)

    def test_already_visible_notice_invalidated_on_sync(self):
        event = self.allowed()
        self.upload()
        self.service.dispatch(self.receiver, 1000)
        self.device.emit("MONITORING_PAUSED", 1001)
        self.upload(1001)
        self.receiver.sync(self.service, 1002)
        self.assertEqual(self.rows("notices", self.receiver)[0]["invalid_reason"], "superseded")
        with self.assertRaises(ContractError):
            self.receiver.read(event.notice_key, self.service, 1002)

    def test_ack_hash_and_scope_must_match(self):
        self.allowed()
        ack = self.service.submit(*self.device.envelope(), "fixture_token_a", 1000)
        for bad in (dict(ack, binding_epoch=2), dict(ack, kind="SIMULATED_PUSH_ACCEPTED"),
                    dict(ack, saved_hashes={"fixture_p1:event:1": "wrong"})):
            with self.assertRaises(ContractError):
                self.device.acknowledge(bad)
        self.assertEqual(len(self.device.envelope()[1]), 1)

    def test_reboot_preserves_event_sequence_batch_and_outbox(self):
        old = self.allowed(monotonic_ms=9000)
        restarted = self.track(Device(self.root / "device.sqlite3"))
        self.assertEqual(restarted.envelope()[0], old)
        new = restarted.emit("RESEARCH_WET", 1001, monotonic_ms=1)
        self.assertGreater(new.boot_id, old.boot_id)
        self.assertEqual(new.batch_seq, old.batch_seq)
        self.assertEqual(new.state_version, old.state_version + 1)
        self.assertEqual(new.event_seq, old.event_seq + 1)
        self.assertNotEqual(new.event_id, old.event_id)
        with self.assertRaises(ContractError):
            restarted.emit("UNKNOWN", 1002, monotonic_ms=0)

    def test_missing_device_storage_requires_reprovisioning(self):
        with self.assertRaises(ContractError):
            Device(self.root / "missing.sqlite3")

    def test_device_process_crash_rolls_back_sequence_and_outbox_together(self):
        old = self.allowed()
        for stage in ("after_counter", "before_device_commit"):
            with self.subTest(stage=stage):
                self.crash(stage)
                restarted = self.track(Device(self.root / "device.sqlite3"))
                self.assertEqual(restarted.envelope()[0], old)
                self.assertEqual(len(restarted.envelope()[1]), 1)
                self.assertEqual(restarted.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        new = self.device.emit("RESEARCH_WET", 1011)
        self.assertEqual(new.event_seq, old.event_seq + 1)

    def test_service_process_crash_rolls_back_events_head_and_jobs_together(self):
        self.allowed()
        for stage in ("after_event_save", "before_service_commit"):
            with self.subTest(stage=stage):
                self.crash(stage)
                restarted = self.track(Service(self.root / "service.sqlite3"))
                self.assertEqual(self.rows("events", restarted), [])
                self.assertEqual(self.rows("heads", restarted), [])
                self.assertEqual(self.rows("jobs", restarted), [])
                self.assertEqual(restarted.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.upload(1011)
        self.assertEqual(len(self.rows("jobs")), 1)

    def test_service_process_crash_after_commit_preserves_job(self):
        self.allowed()
        self.crash("after_service_commit")
        restarted = self.track(Service(self.root / "service.sqlite3"))
        self.assertEqual(len(self.rows("jobs", restarted)), 1)
        self.assertEqual(len(self.device.envelope()[1]), 1)
        self.upload(1011)
        self.assertEqual(len(self.rows("jobs")), 1)

    def test_crash_after_simulated_push_retries_without_assuming_receiver_saved(self):
        self.allowed()
        self.upload()
        self.crash("after_simulated_push")
        self.assertIsNone(self.rows("jobs")[0]["receiver_saved_at_s"])
        self.assertEqual(self.rows("notices", self.receiver), [])
        self.service.dispatch(self.receiver, 1070)
        self.assertEqual(len(self.rows("notices", self.receiver)), 1)

    def test_crash_after_receiver_save_dedupes_across_both_restarts(self):
        self.allowed()
        self.upload()
        self.crash("after_receiver_commit")
        self.assertIsNone(self.rows("jobs")[0]["receiver_saved_at_s"])
        restarted_service = self.track(Service(self.root / "service.sqlite3"))
        restarted_receiver = self.track(Receiver(self.root / "receiver.sqlite3", "owner_a"))
        restarted_service.dispatch(restarted_receiver, 1070)
        self.assertEqual(len(self.rows("notices", restarted_receiver)), 1)
        self.assertEqual(len(self.rows("push_attempts", restarted_service)), 2)
        self.assertEqual(self.rows("jobs", restarted_service)[0]["reason"], "DUPLICATE_SUPPRESSED")

    def test_service_clock_rollback_fails_closed_across_restart(self):
        self.allowed()
        self.upload(1010)
        restarted = self.track(Service(self.root / "service.sqlite3"))
        with self.assertRaises(ContractError):
            restarted.dispatch(self.receiver, 1009)
        self.assertEqual(self.rows("push_attempts"), [])

    def test_all_old_envelope_permutations_leave_latest_head_unchanged(self):
        envelopes = []
        for now, state in enumerate((ALLOWED, "RESEARCH_WET", "LOW_SIGNAL_UNCONFIRMED"), 1000):
            self.device.emit(state, now, authorization=SIMULATION_AUTH if state == ALLOWED else "NONE")
            envelopes.append(self.device.envelope())
        latest = envelopes[-1][0]
        for index, order in enumerate(itertools.permutations(envelopes)):
            service = self.track(Service(self.root / f"permutation-{index}.sqlite3"))
            service.bind("hanger_1", "fixture_p1", "owner_a", "fixture_token_a", 1000)
            service.submit(*envelopes[-1], "fixture_token_a", 1010)
            for envelope in order:
                service.submit(*envelope, "fixture_token_a", 1010)
            self.assertEqual(self.rows("heads", service)[0]["event_id"], latest.event_id)
            self.assertEqual(self.rows("jobs", service), [])

    def test_unsent_device_change_is_explicit_knowledge_limit(self):
        # Intentional limitation: server cannot infer a later event still offline.
        self.allowed()
        self.upload()
        self.device.emit("RESEARCH_WET", 1001)
        self.service.dispatch(self.receiver, 1002)
        self.assertEqual(len(self.rows("notices", self.receiver)), 1)
        self.upload(1003)
        self.receiver.sync(self.service, 1003)
        self.assertEqual(self.rows("notices", self.receiver)[0]["invalid_reason"], "superseded")


if __name__ == "__main__":
    unittest.main(verbosity=2)

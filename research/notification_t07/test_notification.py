"""Non-vacuous raw-state integration, persistence and notification failures."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from research.state_machine.machine import DEFAULT_CONFIG, StateMachine
from research.notification_t07.contract import Event, TTL_S, RESEARCH_TEXT
from research.notification_t07.device import Device
from research.notification_t07.fixtures import RawFixture, WALL_ORIGIN_S
from research.notification_t07.service import Service, Receiver
from research.notification_t07.storage import ContractError, InjectedCrash


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".test-", dir=HERE)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.device = self.track(Device.provision(self.root / "device.sqlite3", "hanger_1", "producer_1", wall_s=WALL_ORIGIN_S))
        self.service = self.track(Service(self.root / "service.sqlite3", create=True))
        self.receiver = self.track(Receiver(self.root / "receiver.sqlite3", "owner_a", create=True))
        self.service.bind("hanger_1", "producer_1", "owner_a", "token_a", DEFAULT_CONFIG, WALL_ORIGIN_S)
        self.raw = RawFixture(self.device)

    def track(self, store):
        self.addCleanup(store.close)
        return store

    def rows(self, table, store=None):
        return (store or self.service).inspect()[table]

    def upload(self, now=None, fail=None):
        now = self.raw.wall if now is None else now
        ack = self.service.submit(*self.device.envelope(), "token_a", now, fail=fail)
        self.device.acknowledge(ack)
        return ack

    def crash(self, stage, now=None):
        snapshot, pending = self.device.envelope()
        request = dict(snapshot=snapshot.encode(), pending=[e.encode() for e in pending], now_s=self.raw.wall if now is None else now)
        (self.root / "crash-request.json").write_text(json.dumps(request), encoding="utf-8")
        run = subprocess.run([sys.executable, "-B", "-X", "utf8", str(HERE / "crash_worker.py"), str(self.root), stage],
                             text=True, encoding="utf-8", capture_output=True)
        self.assertEqual(run.returncode, 73, run.stderr)

    def test_real_raw_state_machine_matches_reference_and_reaches_research_notice(self):
        event = self.raw.review()
        reference = StateMachine(DEFAULT_CONFIG)
        reference.handle(dict(event_version="1.0", kind="boot", event_id="boot-1", boot_id=self.device.boot_id,
                              monotonic_s=0, config_sha256=DEFAULT_CONFIG.sha256))
        expected = [reference.handle(raw) for raw in self.raw.raw_events]
        self.assertEqual(expected, self.raw.outputs)
        self.assertEqual(event.notice_kind, "RESEARCH_REVIEW")
        self.assertTrue(event.upstream["independent_review_due"])
        self.assertFalse(event.collection_authorized)
        self.assertEqual(sum(x["assessment"] is not None for x in expected), 3)
        self.upload()
        self.service.dispatch(self.receiver, self.raw.wall)
        self.assertEqual(len(self.rows("notices", self.receiver)), 1)
        self.assertEqual(self.rows("notices", self.receiver)[0]["research_text"], RESEARCH_TEXT)

    def test_one_weak_cycle_is_history_and_repeated_raw_event_cannot_reach_review(self):
        self.raw.install()
        self.raw.cycle()
        old = self.device.head
        for _ in range(20):
            reply = self.device.process(self.raw.raw_events[-1], wall_s=self.raw.wall)
            self.assertEqual(reply["event"].event_id, old.event_id)
        self.assertEqual(self.device.machine.snapshot()["weak_cycles"], 1)
        self.upload()
        self.assertEqual(self.rows("jobs"), [])

    def test_forged_assessment_cannot_replace_raw_observations(self):
        self.raw.review()
        self.upload()
        start = self.raw.now + DEFAULT_CONFIG.policy.min_cycle_gap_s
        self.raw.emit("start_cycle", start, cycle_id="forged-cycle")
        self.raw.emit("finish_cycle", start + DEFAULT_CONFIG.protocol.total_s, cycle_id="forged-cycle",
                      start_s=start, end_s=start + DEFAULT_CONFIG.protocol.total_s,
                      state="LOW_SIGNAL_UNCONFIRMED", collection_authorized=True,
                      assessment={"state": "LOW_SIGNAL_UNCONFIRMED", "collection_authorized": True})
        self.assertEqual(self.device.head.upstream["state"], "FAULT")
        self.assertFalse(self.device.head.collection_authorized)
        self.upload()
        self.service.dispatch(self.receiver, self.raw.wall)
        self.assertEqual(self.rows("notices", self.receiver), [])
        self.assertEqual(self.rows("jobs")[0]["reason"], "superseded")

    def test_transport_cannot_enable_collection_or_simulation_backdoor(self):
        event = self.raw.review()
        for changed in (replace(event, collection_authorized=True), replace(event, synthetic="REAL"),
                        replace(event, upstream={**event.upstream, "state": "SIMULATED_COLLECTION_ALLOWED"}),
                        replace(event, upstream={**event.upstream, "collection_authorized": True})):
            with self.assertRaises(ContractError):
                self.service.submit(changed, [], "token_a", self.raw.wall)
        self.assertEqual(self.rows("events"), [])

    def test_save_push_receive_and_read_are_separate(self):
        event = self.raw.review()
        self.upload()
        self.assertEqual(self.rows("push_attempts"), [])
        self.assertIsNone(self.rows("jobs")[0]["receiver_saved_at_s"])
        self.service.dispatch(self.receiver, self.raw.wall, reachable=False)
        self.assertEqual(len(self.rows("push_attempts")), 1)
        self.assertEqual(self.rows("notices", self.receiver), [])
        with self.assertRaises(ContractError):
            self.service.record_read(event, "owner_a", self.raw.wall)
        self.service.dispatch(self.receiver, self.raw.wall + 60)
        self.assertIsNone(self.rows("jobs")[0]["user_read_at_s"])
        self.receiver.read(event.notice_key, self.service, self.raw.wall + 61)
        self.receiver.read(event.notice_key, self.service, self.raw.wall + 62)
        self.assertEqual(self.rows("jobs")[0]["user_read_at_s"], self.raw.wall + 61)

    def test_save_ack_lost_and_duplicate_upload_do_not_duplicate_task(self):
        self.raw.review()
        with self.assertRaises(InjectedCrash):
            self.upload(fail="after_service_commit")
        self.assertGreater(len(self.device.envelope()[1]), 0)
        self.upload()
        for _ in range(3):
            self.upload()
            self.service.dispatch(self.receiver, self.raw.wall)
        self.assertEqual(len(self.rows("jobs")), 1)
        self.assertEqual(len(self.rows("push_attempts")), 1)
        self.assertEqual(len(self.rows("notices", self.receiver)), 1)

    def test_changed_content_for_existing_id_rolls_back_whole_upload(self):
        event = self.raw.review()
        self.upload()
        old_count = len(self.rows("events"))
        self.raw.emit("clip")
        latest, pending = self.device.envelope()
        with self.assertRaises(ContractError):
            self.service.submit(latest, pending + [replace(event, confirmed_at_s=event.confirmed_at_s + 1)], "token_a", self.raw.wall)
        self.assertEqual(len(self.rows("events")), old_count)
        self.assertEqual(self.rows("heads")[0]["event_id"], event.event_id)
        self.upload()
        self.assertEqual(self.rows("jobs")[0]["reason"], "superseded")

    def test_reconnect_latest_snapshot_prevents_backlog_review_push(self):
        event = self.raw.review()
        self.raw.emit("clip")
        self.upload()
        self.service.dispatch(self.receiver, self.raw.wall)
        self.assertEqual(self.rows("jobs"), [])
        self.assertIn(event.event_id, [r["event_id"] for r in self.rows("events")])

    def test_stale_snapshot_cannot_accompany_newer_pending(self):
        old = self.raw.review()
        self.raw.emit("clip")
        _, pending = self.device.envelope()
        with self.assertRaises(ContractError):
            self.service.submit(old, pending, "token_a", self.raw.wall)
        self.assertEqual(self.rows("events"), [])

    def test_old_upload_after_newer_snapshot_cannot_roll_back_head(self):
        self.raw.review()
        old = self.device.envelope()
        self.raw.emit("clip")
        self.upload()
        latest = self.device.head
        self.service.submit(*old, "token_a", self.raw.wall + 1)
        self.assertEqual(self.rows("heads")[0]["event_id"], latest.event_id)
        self.assertEqual(self.rows("jobs"), [])

    def test_delayed_push_and_saved_card_cancel_on_new_state(self):
        event = self.raw.review()
        self.upload()
        self.service.dispatch(self.receiver, self.raw.wall)
        self.raw.emit("clip")
        self.upload()
        self.assertEqual(self.receiver.receive(event, self.service, self.raw.wall), "superseded")
        self.receiver.sync(self.service, self.raw.wall)
        self.assertEqual(self.rows("notices", self.receiver)[0]["invalid_reason"], "superseded")
        with self.assertRaises(ContractError):
            self.receiver.read(event.notice_key, self.service, self.raw.wall)

    def test_batch_and_installation_changes_clear_evidence_and_old_jobs(self):
        for new_batch in ("batch-a", "batch-b"):
            self.raw.review()
            self.upload()
            self.raw.install(new_batch)
            self.assertFalse(self.device.head.upstream["independent_review_due"])
            self.upload()
            self.service.dispatch(self.receiver, self.raw.wall)
        self.assertEqual(self.rows("notices", self.receiver), [])
        self.assertTrue(all(j["status"] == "cancelled" for j in self.rows("jobs")))

    def test_tick_and_retransmission_never_refresh_evidence_ttl(self):
        event = self.raw.review()
        end = self.raw.now
        self.upload()
        self.raw.emit("tick", end + TTL_S - 1)
        tick = self.device.head
        self.assertEqual(tick.measured_at_s, event.measured_at_s)
        self.assertEqual(tick.expires_at_s, event.expires_at_s)
        self.assertGreater(tick.confirmed_at_s, event.confirmed_at_s)
        self.upload()
        self.service.dispatch(self.receiver, event.expires_at_s)
        self.assertEqual(self.rows("notices", self.receiver), [])
        self.upload(event.expires_at_s + 1)
        self.assertEqual(self.rows("jobs")[-1]["reason"], "expired")

    def test_finish_delivery_delay_does_not_move_measurement_to_arrival_time(self):
        self.raw.install()
        self.raw.cycle()
        self.raw.cycle()
        self.raw.cycle(delay_s=5)
        event = self.device.head
        self.assertEqual(event.confirmed_at_s - event.measured_at_s, 5)
        self.assertEqual(event.measured_at_s, WALL_ORIGIN_S + event.upstream["last_evidence_end_s"])

    def test_first_expired_upload_is_history_only(self):
        event = self.raw.review()
        self.upload(event.expires_at_s)
        self.assertEqual(self.rows("jobs"), [])
        reason = next(r["ingest_reason"] for r in self.rows("events") if r["event_id"] == event.event_id)
        self.assertEqual(reason, "expired")

    def test_same_evidence_timestamp_cannot_change_in_new_state_version(self):
        event = self.raw.review()
        self.upload()
        self.raw.emit("tick")
        tick = self.device.head
        changed = replace(tick, measured_at_s=event.measured_at_s + 1)
        with self.assertRaises(ContractError):
            self.service.submit(changed, [], "token_a", self.raw.wall)

    def test_unknown_clock_cannot_be_repaired_by_tick(self):
        self.raw.clock_valid = False
        self.raw.review()
        self.upload()
        self.assertEqual(self.rows("jobs"), [])
        self.raw.clock_valid = True
        self.raw.emit("tick")
        self.assertIsNone(self.device.head.measured_at_s)
        self.upload()
        self.assertEqual(self.rows("jobs"), [])

    def test_future_measurement_rejected_across_retry_and_later_tick(self):
        self.raw.review()
        self.upload(WALL_ORIGIN_S)
        self.upload()
        self.raw.emit("tick")
        self.upload()
        self.assertEqual(self.rows("jobs"), [])
        self.raw.cycle()
        self.upload()
        self.service.dispatch(self.receiver, self.raw.wall)
        self.assertEqual(len(self.rows("notices", self.receiver)), 1)

    def test_clock_uncertainty_shortens_deadline(self):
        self.raw.uncertainty_s = 2
        event = self.raw.review()
        self.upload(self.raw.wall + 2)
        self.assertEqual(event.expires_at_s, event.measured_at_s + TTL_S - 2)
        self.service.dispatch(self.receiver, event.expires_at_s)
        self.assertEqual(self.rows("jobs")[0]["reason"], "expired")

    def test_device_wall_clock_rollback_latches_invalidity_and_cancels_old_job(self):
        self.raw.review()
        self.upload()
        self.raw.wall_origin_s -= 100
        self.raw.emit("tick")
        self.assertFalse(self.device.head.clock_valid)
        self.upload(self.raw.wall + 100)
        self.service.dispatch(self.receiver, self.raw.wall + 100)
        self.assertEqual(self.rows("notices", self.receiver), [])

    def test_service_clock_rollback_rejected_after_reopen(self):
        self.raw.review()
        self.upload()
        reopened = self.track(Service(self.root / "service.sqlite3"))
        with self.assertRaises(ContractError):
            reopened.dispatch(self.receiver, self.raw.wall - 1)

    def test_phone_unreachable_retries_then_saves_once(self):
        self.raw.review()
        self.upload()
        now = self.raw.wall
        for step in (0, 1, 59):
            self.service.dispatch(self.receiver, now + step, reachable=False)
        self.assertEqual(len(self.rows("push_attempts")), 1)
        self.service.dispatch(self.receiver, now + 60, reachable=False)
        self.service.dispatch(self.receiver, now + 360)
        self.assertEqual(len(self.rows("push_attempts")), 3)
        self.assertEqual(len(self.rows("notices", self.receiver)), 1)

    def test_unreachable_recovery_after_expiry_never_displays(self):
        event = self.raw.review()
        self.upload()
        self.service.dispatch(self.receiver, self.raw.wall, reachable=False)
        self.service.dispatch(self.receiver, event.expires_at_s)
        self.assertEqual(self.rows("notices", self.receiver), [])
        self.assertEqual(len(self.rows("push_attempts")), 1)

    def test_same_episode_updates_silently_then_reentry_may_alert(self):
        first = self.raw.review()
        self.upload()
        self.service.dispatch(self.receiver, self.raw.wall)
        self.raw.cycle()
        second = self.device.head
        self.assertEqual(first.notice_key, second.notice_key)
        self.assertGreater(second.measured_at_s, first.measured_at_s)
        self.upload()
        self.service.dispatch(self.receiver, self.raw.wall)
        self.assertEqual(len(self.rows("notices", self.receiver)), 1)
        self.raw.cycle("strong")
        self.upload()
        self.receiver.sync(self.service, self.raw.wall)
        third = self.raw.review()
        self.assertNotEqual(first.notice_key, third.notice_key)
        self.upload()
        self.service.dispatch(self.receiver, self.raw.wall)
        self.assertEqual(len(self.rows("notices", self.receiver)), 2)

    def test_required_config_does_not_automatically_claim_applied(self):
        self.raw.review()
        self.upload()
        updated = replace(DEFAULT_CONFIG, policy=replace(DEFAULT_CONFIG.policy, stabilization_wait_s=31))
        self.service.require_config("hanger_1", updated, self.raw.wall)
        status = self.service.app_status("hanger_1", "owner_a", self.raw.wall)
        self.assertNotEqual(status["required_config_sha256"], status["applied_config_sha256"])
        self.assertEqual(status["eligibility"], "required_config_not_applied")
        self.raw.emit("configure", new_config=updated.snapshot())
        self.assertEqual(self.device.machine.config.sha256, updated.sha256)
        self.assertIsNone(self.device.head.upstream["installation_id"])
        self.upload()
        self.service.dispatch(self.receiver, self.raw.wall)
        self.assertEqual(self.rows("notices", self.receiver), [])
        self.raw.review()
        self.upload()
        self.service.dispatch(self.receiver, self.raw.wall)
        self.assertEqual(len(self.rows("notices", self.receiver)), 1)

    def test_rebind_revokes_old_owner_and_requires_new_boot_evidence(self):
        old = self.raw.review()
        envelope = self.device.envelope()
        self.upload()
        now = self.raw.wall
        epoch = self.service.bind("hanger_1", "producer_1", "owner_b", "token_b", DEFAULT_CONFIG, now)
        with self.assertRaises(ContractError):
            self.service.submit(*envelope, "token_a", self.raw.wall)
        self.assertEqual(self.receiver.receive(old, self.service, self.raw.wall), "wrong_recipient")
        new = self.device.rebind(epoch, wall_s=self.raw.wall)
        self.assertNotEqual(old.upstream["boot_id"], new.upstream["boot_id"])
        self.assertFalse(new.upstream["independent_review_due"])
        self.assertEqual(len(self.device.envelope()[1]), 1)
        recipient = self.track(Receiver(self.root / "receiver-b.sqlite3", "owner_b", create=True))
        self.assertEqual(recipient.receive(old, self.service, now), "binding_revoked")

    def test_duplicate_and_late_ack_are_noop_without_freeing_new_events(self):
        self.raw.review()
        ack = self.upload()
        self.device.acknowledge(ack)
        self.raw.emit("tick")
        new_ids = [e.event_id for e in self.device.envelope()[1]]
        self.device.acknowledge(ack)
        self.assertEqual([e.event_id for e in self.device.envelope()[1]], new_ids)
        new_ack = self.service.submit(*self.device.envelope(), "token_a", self.raw.wall)
        corrupted = deepcopy(new_ack)
        corrupted["saved_hashes"][new_ids[0]] = "wrong"
        with self.assertRaises(ContractError):
            self.device.acknowledge(corrupted)
        self.assertEqual([e.event_id for e in self.device.envelope()[1]], new_ids)
        self.device.rebind(2, wall_s=self.raw.wall)
        rebound_ids = [e.event_id for e in self.device.envelope()[1]]
        self.device.acknowledge(ack)
        self.assertEqual([e.event_id for e in self.device.envelope()[1]], rebound_ids)

    def test_mixed_ack_bad_pending_digest_rolls_back_earlier_deletions(self):
        self.raw.review()
        ack = self.service.submit(*self.device.envelope(), "token_a", self.raw.wall)
        old_ids = [e.event_id for e in self.device.envelope()[1]]
        bad = deepcopy(ack)
        bad["saved_hashes"][old_ids[-2]] = "wrong"
        with self.assertRaises(ContractError):
            self.device.acknowledge(bad)
        self.assertEqual([e.event_id for e in self.device.envelope()[1]], old_ids)

    def test_reopen_creates_new_boot_and_cannot_restore_old_evidence(self):
        old = self.raw.review()
        self.upload()
        reopened = self.track(Device(self.root / "device.sqlite3", wall_s=self.raw.wall))
        new = reopened.head
        self.assertGreater(new.event_seq, old.event_seq)
        self.assertNotEqual(new.upstream["boot_id"], old.upstream["boot_id"])
        self.assertEqual(new.upstream["state"], "AWAITING_INSTALLATION")
        self.assertIsNone(new.measured_at_s)
        reopened.acknowledge(self.service.submit(*reopened.envelope(), "token_a", self.raw.wall))
        self.service.dispatch(self.receiver, self.raw.wall)
        self.assertEqual(self.rows("notices", self.receiver), [])
        with self.assertRaises(ContractError):
            self.device.envelope()  # Old live writer is fenced too.
        late = reopened.process(self.raw.raw_events[-1], wall_s=self.raw.wall)
        self.assertEqual(late["output"]["reason"], "stale_or_unknown_boot")
        raw = RawFixture(reopened, wall_origin_s=self.raw.wall)
        raw.install()
        self.assertEqual(reopened.head.upstream["state"], "STABILIZING")

    def test_missing_empty_corrupt_storage_never_initializes(self):
        for name, content in (("empty.sqlite3", b""), ("bad.sqlite3", b"not sqlite")):
            path = self.root / name
            path.write_bytes(content)
            with self.assertRaises(ContractError):
                Device(path)
            self.assertEqual(path.read_bytes(), content)
        missing = self.root / "missing.sqlite3"
        with self.assertRaises(ContractError):
            Device(missing)
        self.assertFalse(missing.exists())

    def test_metadata_checksum_corruption_is_rejected(self):
        self.device.db.execute("UPDATE meta SET payload='{}' WHERE id=1")
        with self.assertRaises(ContractError):
            Device(self.root / "device.sqlite3")

    def test_outbox_checksum_corruption_is_rejected_without_reset(self):
        self.raw.review()
        self.device.db.execute("UPDATE outbox SET sha256='bad'")
        with self.assertRaises(ContractError):
            self.device.envelope()
        with self.assertRaises(ContractError):
            Device(self.root / "device.sqlite3")

    def test_evidence_verifier_rejects_empty_success_receipt(self):
        from research.notification_t07.run_checks import verify
        fake = self.root / "receipt.json"
        fake.write_text(json.dumps(dict(kind="T07_NOTIFICATION_ROLE_EVIDENCE", all_passed=True,
                                        inputs_unchanged_during_run=True, tests_run=0, failures=0,
                                        errors=0, skipped=0, passed_test_ids=[])), encoding="utf-8")
        with self.assertRaises(ValueError):
            verify(fake)

    def test_full_outbox_replaces_eligible_head_with_reserved_safety_snapshot(self):
        small = self.track(Device.provision(self.root / "small.sqlite3", "small", "small_p", wall_s=WALL_ORIGIN_S, capacity=8))
        raw = RawFixture(small)
        eligible = raw.review()
        self.assertEqual(eligible.notice_kind, "RESEARCH_REVIEW")
        self.service.bind("small", "small_p", "owner_a", "small_token", DEFAULT_CONFIG, raw.wall)
        self.service.submit(*small.envelope(), "small_token", raw.wall)  # Deliberately lose save ACK.
        raw.emit("clip")
        safety, pending = small.envelope()
        self.assertEqual(safety.controller_fault, "OUTBOX_CAPACITY_EXHAUSTED")
        self.assertEqual(safety.notice_kind, "STATE_UPDATE")
        self.assertEqual(len(pending), 8)
        self.assertGreater(safety.state_version, eligible.state_version)
        small.acknowledge(self.service.submit(safety, pending, "small_token", raw.wall))
        self.service.dispatch(self.receiver, raw.wall)
        self.assertEqual(self.rows("notices", self.receiver), [])
        with self.assertRaises(ContractError):
            raw.emit("tick")
        restarted = self.track(Device(self.root / "small.sqlite3", wall_s=raw.wall))
        self.assertEqual(restarted.head.controller_fault, "OUTBOX_CAPACITY_EXHAUSTED")

    def test_ack_validation_does_not_discard_unconfirmed_outbox(self):
        self.raw.review()
        ack = self.service.submit(*self.device.envelope(), "token_a", self.raw.wall)
        before = len(self.device.envelope()[1])
        for bad in (dict(ack, binding_epoch=2), dict(ack, kind="SIMULATED_PUSH_ACCEPTED"),
                    dict(ack, saved_hashes={self.device.head.event_id: "wrong"})):
            with self.assertRaises(ContractError):
                self.device.acknowledge(bad)
        self.assertEqual(len(self.device.envelope()[1]), before)

    def test_measurement_confirmation_and_last_communication_are_distinct(self):
        event = self.raw.review()
        self.upload()
        self.raw.emit("tick", self.raw.now + 10)
        self.upload()
        self.upload(self.raw.wall + 20)
        status = self.service.app_status("hanger_1", "owner_a", self.raw.wall + 20)
        self.assertEqual(status["measured_at_s"], event.measured_at_s)
        self.assertEqual(status["confirmed_at_s"], event.confirmed_at_s + 10)
        self.assertEqual(status["last_contact_s"], event.confirmed_at_s + 30)

    def test_device_precommit_crash_keeps_old_transaction_then_reopen_revokes(self):
        old = self.raw.review()
        for stage in ("after_device_counter", "before_device_commit"):
            with self.subTest(stage=stage):
                self.crash(stage)
                self.assertEqual(self.device.head, old)
                self.assertEqual(self.device.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        new = self.track(Device(self.root / "device.sqlite3", wall_s=self.raw.wall)).head
        self.assertEqual(new.event_seq, old.event_seq + 1)
        self.assertFalse(new.upstream["independent_review_due"])

    def test_device_postcommit_crash_keeps_identity_and_next_boot_does_not_reuse(self):
        old = self.raw.review()
        self.crash("after_device_commit")
        new = self.track(Device(self.root / "device.sqlite3", wall_s=self.raw.wall)).head
        self.assertEqual(new.event_seq, old.event_seq + 2)
        self.assertEqual(new.boot_seq, old.boot_seq + 2)
        self.assertIsNone(new.measured_at_s)

    def test_service_precommit_process_crashes_roll_back_events_head_and_jobs(self):
        self.raw.review()
        for stage in ("after_service_events", "before_service_commit"):
            with self.subTest(stage=stage):
                self.crash(stage)
                service = self.track(Service(self.root / "service.sqlite3"))
                self.assertEqual(self.rows("events", service), [])
                self.assertEqual(self.rows("heads", service), [])
                self.assertEqual(self.rows("jobs", service), [])
                self.assertEqual(self.rows("evidence_admission", service), [])
        self.upload()
        self.assertEqual(len(self.rows("jobs")), 1)

    def test_service_postcommit_process_crash_keeps_job_and_lost_ack(self):
        self.raw.review()
        self.crash("after_service_commit")
        service = self.track(Service(self.root / "service.sqlite3"))
        self.assertEqual(len(self.rows("jobs", service)), 1)
        self.assertGreater(len(self.device.envelope()[1]), 0)
        self.upload()
        self.assertEqual(len(self.rows("jobs")), 1)

    def test_receiver_commit_without_ack_dedupes_after_both_restarts(self):
        self.raw.review()
        self.upload()
        self.crash("after_receiver_commit")
        service = self.track(Service(self.root / "service.sqlite3"))
        receiver = self.track(Receiver(self.root / "receiver.sqlite3", "owner_a"))
        self.assertIsNone(self.rows("jobs", service)[0]["receiver_saved_at_s"])
        service.dispatch(receiver, self.raw.wall + 60)
        self.assertEqual(len(self.rows("notices", receiver)), 1)
        self.assertEqual(self.rows("jobs", service)[0]["reason"], "DUPLICATE_SUPPRESSED")
        self.assertEqual(len(self.rows("push_attempts", service)), 2)

    def test_push_accepted_without_receiver_can_retry_after_process_crash(self):
        self.raw.review()
        self.upload()
        self.crash("after_simulated_push")
        self.assertEqual(self.rows("notices", self.receiver), [])
        self.service.dispatch(self.receiver, self.raw.wall + 60)
        self.assertEqual(len(self.rows("notices", self.receiver)), 1)

    def test_unsent_offline_state_change_remains_an_explicit_knowledge_limit(self):
        self.raw.review()
        self.upload()
        self.raw.emit("clip")
        self.service.dispatch(self.receiver, self.raw.wall)
        self.assertEqual(len(self.rows("notices", self.receiver)), 1)
        self.upload()
        self.receiver.sync(self.service, self.raw.wall)
        self.assertEqual(self.rows("notices", self.receiver)[0]["invalid_reason"], "superseded")


if __name__ == "__main__":
    unittest.main(verbosity=2)

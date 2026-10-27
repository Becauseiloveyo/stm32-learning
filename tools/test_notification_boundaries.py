"""Coordinator-authored device edge cases using T04 raw observations, not notice labels."""

from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.notification_t07.device import Device
from research.notification_t07.storage import ContractError
from research.notification_t07.contract import Event
from research.notification_t07.service import Service, Receiver
from research.state_machine.scenarios import Sequence
from research.state_machine.machine import DEFAULT_CONFIG


class DeviceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "device.sqlite3"
        self.device = None
        self.addCleanup(self.close_device)
        self.sequence = Sequence()
        for _ in range(3):
            self.sequence.cycle()

    def close_device(self):
        if self.device is not None:
            self.device.close()
            self.device = None

    def ready(self, capacity=32):
        self.device = Device.provision(self.path, "review-device", "review-prod", wall_s=1000, capacity=capacity)
        events = []
        for raw in self.sequence.events[1:]:
            raw = {**deepcopy(raw), "boot_id": self.device.boot_id}
            result = self.device.process(raw, wall_s=1000 + raw["monotonic_s"])
            self.assertTrue(result["output"]["accepted"])
            events.append(result["event"])
        self.assertEqual(self.device.head.notice_kind, "RESEARCH_REVIEW")
        self.assertFalse(self.device.head.collection_authorized)
        return events

    def apply(self, kind, now, **fields):
        raw = self.sequence.emit(kind, now, **fields)
        raw["boot_id"] = self.device.boot_id
        return self.device.process(raw, wall_s=1000 + now)["event"]

    def endpoints(self):
        service = Service(Path(self.temp.name) / "service.sqlite3", create=True)
        receiver = Receiver(Path(self.temp.name) / "receiver.sqlite3", "owner", create=True)
        self.addCleanup(service.close)
        self.addCleanup(receiver.close)
        service.bind("review-device", "review-prod", "owner", "token", DEFAULT_CONFIG, 1000)
        return service, receiver

    def test_ticks_do_not_renew_measured_time_or_ttl(self):
        self.ready()
        first = self.device.head
        now = self.sequence.now
        for index in range(1, 5):
            current = self.apply("tick", now + index)
            self.assertEqual(current.measured_at_s, first.measured_at_s)
            self.assertEqual(current.expires_at_s, first.expires_at_s)
            self.assertEqual(current.notice_key, first.notice_key)
            self.assertGreater(current.confirmed_at_s, first.confirmed_at_s)

    def test_full_queue_keeps_distinct_safety_head_and_latches(self):
        self.ready(capacity=8)
        old = self.device.head
        current = self.apply("clip", self.sequence.now + 1)
        head, pending = self.device.envelope()
        self.assertEqual(len(pending), 8)
        self.assertEqual(head, current)
        self.assertGreater(head.event_seq, old.event_seq)
        self.assertNotIn(head.event_id, [e.event_id for e in pending])
        self.assertEqual(head.controller_fault, "OUTBOX_CAPACITY_EXHAUSTED")
        self.assertEqual(head.notice_kind, "STATE_UPDATE")
        with self.assertRaises(ContractError):
            self.apply("tick", self.sequence.now + 1)

    def test_reopen_preserves_identity_sequence_but_clears_evidence(self):
        self.ready()
        first = self.device.head
        self.close_device()
        self.device = Device(self.path, wall_s=2000)
        second = self.device.head
        self.assertGreater(second.event_seq, first.event_seq)
        self.assertGreater(second.boot_seq, first.boot_seq)
        self.assertNotEqual(second.event_id, first.event_id)
        self.assertEqual(second.upstream["weak_cycles"], 0)
        self.assertFalse(second.upstream["independent_review_due"])
        self.assertIsNone(second.measured_at_s)
        self.assertEqual(second.notice_kind, "STATE_UPDATE")

    def test_old_process_cannot_write_after_another_reopen(self):
        self.ready()
        another = Device(self.path, wall_s=2000)
        self.addCleanup(another.close)
        with self.assertRaises(ContractError):
            self.device.envelope()
        with self.assertRaises(ContractError):
            self.apply("tick", self.sequence.now + 1)

    def test_clock_regression_cannot_be_fixed_by_retransmission(self):
        self.ready()
        first = self.device.head
        raw = self.sequence.emit("tick", self.sequence.now + 1)
        raw["boot_id"] = self.device.boot_id
        result = self.device.process(raw, wall_s=first.confirmed_at_s - 1)
        self.assertFalse(result["event"].clock_valid)
        self.assertIsNone(result["event"].confirmed_at_s)
        later = self.apply("tick", self.sequence.now + 1)
        self.assertFalse(later.clock_valid)
        self.assertEqual(later.expires_at_s, first.expires_at_s)

    def test_missing_or_corrupt_persistence_does_not_auto_initialize(self):
        with self.assertRaises(ContractError):
            Device(self.path)
        self.assertFalse(self.path.exists())
        self.ready()
        self.device.db.execute("UPDATE meta SET sha256='corrupt'")
        self.close_device()
        with self.assertRaises(ContractError):
            Device(self.path, wall_s=2000)

    def test_serialized_collection_flag_has_no_override(self):
        self.ready()
        payload = self.device.head.payload()
        for value in (True, 1, "true", None):
            changed = deepcopy(payload)
            changed["collection_authorized"] = value
            with self.assertRaises(ContractError):
                Event(**changed).validate()
            changed = deepcopy(payload)
            changed["upstream"]["collection_authorized"] = value
            with self.assertRaises(ContractError):
                Event(**changed).validate()

    def test_safety_head_cancels_an_actually_eligible_queued_reminder(self):
        self.ready(capacity=8)
        service, receiver = self.endpoints()
        old = self.device.head
        service.submit(*self.device.envelope(), "token", old.confirmed_at_s)
        self.assertEqual(service.db.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0], 1)
        current = self.apply("clip", self.sequence.now + 1)
        service.submit(*self.device.envelope(), "token", current.confirmed_at_s)
        service.dispatch(receiver, current.confirmed_at_s)
        self.assertEqual(service.db.execute("SELECT status FROM jobs WHERE event_id=?", (old.event_id,)).fetchone()[0], "cancelled")
        self.assertEqual(receiver.db.execute("SELECT COUNT(*) FROM notices").fetchone()[0], 0)

    def test_repeated_and_late_ack_cannot_remove_newer_messages(self):
        self.ready()
        service, _ = self.endpoints()
        ack = service.submit(*self.device.envelope(), "token", self.device.head.confirmed_at_s)
        self.device.acknowledge(ack)
        self.device.acknowledge(ack)
        newer = self.apply("tick", self.sequence.now + 1)
        self.device.acknowledge(ack)
        self.assertEqual([e.event_id for e in self.device.envelope()[1]], [newer.event_id])
        self.close_device()
        self.device = Device(self.path, wall_s=2000)
        pending_before = [e.event_id for e in self.device.envelope()[1]]
        self.device.acknowledge(ack)
        self.assertEqual([e.event_id for e in self.device.envelope()[1]], pending_before)

    def test_bad_digest_in_mixed_ack_rolls_back_all_queue_deletions(self):
        self.ready()
        service, _ = self.endpoints()
        ack = service.submit(*self.device.envelope(), "token", self.device.head.confirmed_at_s)
        pending_before = [e.event_id for e in self.device.envelope()[1]]
        key = list(ack["saved_hashes"])[-1]
        ack["saved_hashes"][key] = "0" * 64
        with self.assertRaises(ContractError):
            self.device.acknowledge(ack)
        self.assertEqual([e.event_id for e in self.device.envelope()[1]], pending_before)

    def test_old_inflight_upload_cannot_restore_a_revoked_head(self):
        self.ready()
        service, receiver = self.endpoints()
        old_envelope = self.device.envelope()
        current = self.apply("clip", self.sequence.now + 1)
        service.submit(*self.device.envelope(), "token", current.confirmed_at_s)
        service.submit(*old_envelope, "token", current.confirmed_at_s + 1)
        service.dispatch(receiver, current.confirmed_at_s + 1)
        status = service.app_status("review-device", "owner", current.confirmed_at_s + 1)
        self.assertEqual(status["latest"]["event_seq"], current.event_seq)
        self.assertEqual(receiver.db.execute("SELECT COUNT(*) FROM notices").fetchone()[0], 0)

    def test_offline_change_is_only_known_after_sync(self):
        self.ready()
        service, receiver = self.endpoints()
        old = self.device.head
        service.submit(*self.device.envelope(), "token", old.confirmed_at_s)
        current = self.apply("clip", self.sequence.now + 1)
        service.dispatch(receiver, current.confirmed_at_s)
        # This deliberate counterexample prevents claiming perfect offline freshness.
        self.assertEqual(receiver.db.execute("SELECT COUNT(*) FROM notices").fetchone()[0], 1)
        service.submit(*self.device.envelope(), "token", current.confirmed_at_s + 1)
        receiver.sync(service, current.confirmed_at_s + 1)
        row = receiver.db.execute("SELECT invalidated_at_s,invalid_reason FROM notices").fetchone()
        self.assertIsNotNone(row[0])
        self.assertEqual(row[1], "superseded")

    def test_ttl_equality_expires_and_retry_keeps_original_measurement(self):
        self.ready()
        service, receiver = self.endpoints()
        first = self.device.head
        service.submit(*self.device.envelope(), "token", first.confirmed_at_s)
        service.dispatch(receiver, first.confirmed_at_s, reachable=False)
        status = service.app_status("review-device", "owner", first.confirmed_at_s + 1)
        self.assertEqual(status["measured_at_s"], first.measured_at_s)
        service.submit(*self.device.envelope(), "token", first.expires_at_s)
        service.dispatch(receiver, first.expires_at_s)
        status = service.app_status("review-device", "owner", first.expires_at_s)
        self.assertEqual(status["eligibility"], "expired")
        self.assertEqual(status["measured_at_s"], first.measured_at_s)
        self.assertEqual(status["last_contact_s"], first.expires_at_s)
        self.assertEqual(receiver.db.execute("SELECT COUNT(*) FROM notices").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()

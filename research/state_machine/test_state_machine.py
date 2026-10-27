"""Behavioral invariants for T04. No upstream test suite is rerun here."""

from copy import deepcopy
from dataclasses import asdict, replace
import json
import math
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.state_machine import machine as module
from research.state_machine.machine import Configuration, DEFAULT_CONFIG, Policy, StateMachine, replay
from research.state_machine.scenarios import Sequence, observations, scenarios
from research.state_machine import run
from theory.model import assess


class StateMachineChecks(unittest.TestCase):
    def setUp(self):
        self.s = Sequence()
        self.machine = StateMachine()
        self.applied = 0
        self.apply_new()

    def apply_new(self):
        results = [self.machine.handle(event) for event in self.s.events[self.applied:]]
        self.applied = len(self.s.events)
        return results

    def cycle(self, kind="weak"):
        self.s.cycle(kind)
        outputs = self.apply_new()
        self.assertTrue(outputs[-2]["accepted"], outputs)
        self.assertTrue(outputs[-1]["accepted"], outputs)
        return outputs[-1]

    def test_installation_wait_boundary_and_measurement_transition(self):
        self.s.emit("start_cycle", 30.999, cycle_id="too-early")
        self.assertEqual(self.apply_new()[-1]["reason"], "not_ready")
        pending = self.s.begin(31)
        self.assertEqual(self.apply_new()[-1]["state"], "MEASURING")
        self.s.finish(pending)
        self.assertEqual(self.apply_new()[-1]["weak_cycles"], 1)

    def test_repeated_wet_leak_signal_never_authorizes_collection(self):
        for count in range(1, 11):
            result = self.cycle("leaky")
            self.assertEqual(result["state"], "AWAITING_RECHECK")
            self.assertEqual(result["weak_cycles"], count)
            self.assertFalse(result["collection_authorized"])
            self.assertEqual(result["independent_review_due"], count >= 3)
        self.assertEqual(len(set(result["evidence_cycle_ids"])), 10)

    def test_strong_and_unknown_reset_evidence_and_recovery_waits(self):
        self.cycle()
        self.cycle()
        strong = self.cycle("strong")
        self.assertEqual(strong["weak_cycles"], 0)
        self.assertEqual(strong["state"], "SOURCE_PRESENT")
        self.cycle()
        rows = observations()
        rows[50]["local_RH_pct"] = None
        self.s.finish(self.s.begin(), rows=rows)
        invalid = self.apply_new()[-1]
        self.assertEqual(invalid["state"], "FAULT")
        self.assertEqual(invalid["weak_cycles"], 0)
        self.s.emit("recover")
        self.assertEqual(self.apply_new()[-1]["state"], "STABILIZING")
        self.assertEqual(self.cycle()["weak_cycles"], 1)

    def test_claimed_weak_assessment_cannot_replace_raw_observations(self):
        pending = self.s.begin()
        self.s.emit("finish_cycle", pending[2], cycle_id=pending[0], start_s=pending[1], end_s=pending[2],
                    state="LOW_SIGNAL_UNCONFIRMED", collection_authorized=True,
                    assessment={"state": "LOW_SIGNAL_UNCONFIRMED"})
        with patch.object(module, "assess", wraps=assess) as real_assess:
            result = self.apply_new()[-1]
        self.assertEqual(real_assess.call_count, 1)
        self.assertIsNone(real_assess.call_args.args[0])
        self.assertEqual(result["assessment"]["state"], "UNKNOWN")
        self.assertFalse(result["collection_authorized"])
        self.assertEqual(result["weak_cycles"], 0)

    def test_raw_strong_readings_override_forged_weak_claim(self):
        self.cycle()
        self.s.finish(self.s.begin(), kind="strong", assessment={"state": "LOW_SIGNAL_UNCONFIRMED"},
                      collection_authorized=True)
        result = self.apply_new()[-1]
        self.assertEqual(result["assessment"]["state"], "VAPOR_SOURCE_PRESENT")
        self.assertEqual(result["weak_cycles"], 0)

    def test_truth_and_diagnostic_fields_are_never_read_even_for_deduplication(self):
        forbidden = {"synthetic_true_chamber_Pa", "delta_e_Pa", "surface_activity", "case", "leak_ml_s"}

        class GuardedRow(dict):
            def __getitem__(self, key):
                if key in forbidden:
                    raise AssertionError(f"Non-observation read: {key}")
                return super().__getitem__(key)

        rows = [GuardedRow({**r, **{k: object() for k in forbidden}}) for r in observations()]
        event = self.s.finish(self.s.begin(), rows=rows)
        result = self.apply_new()[-1]
        self.assertEqual(result["weak_cycles"], 1)
        event["observations"] = observations()
        self.assertEqual(self.machine.handle(event)["disposition"], "IGNORED_DUPLICATE")

    def test_identical_message_and_cycle_retries_are_idempotent(self):
        self.cycle()
        event = deepcopy(self.s.events[-1])
        before = self.machine.snapshot()
        duplicate = self.machine.handle(event)
        self.assertEqual(duplicate["reason"], "duplicate_event")
        self.assertEqual(self.machine.snapshot(), before)
        event.update(event_id="different-envelope", monotonic_s=100000)
        retry = self.machine.handle(event)
        self.assertEqual(retry["reason"], "cycle_id_reused")
        self.assertEqual(self.machine.snapshot(), before)

    def test_changed_payload_under_same_message_id_faults(self):
        self.cycle()
        event = deepcopy(self.s.events[-1])
        event["observations"][60]["local_RH_pct"] = 70
        result = self.machine.handle(event)
        self.assertEqual(result["reason"], "event_id_conflict")
        self.assertEqual(result["weak_cycles"], 0)

    def test_cycle_relabel_or_finish_without_start_never_recounts(self):
        self.cycle()
        event = deepcopy(self.s.events[-1])
        event.update(event_id="renamed-envelope", cycle_id="renamed-cycle", monotonic_s=self.s.now + 1)
        result = self.machine.handle(event)
        self.assertEqual(result["reason"], "no_matching_cycle_start")
        self.assertEqual(result["weak_cycles"], 0)

    def test_old_time_window_with_new_cycle_id_fails(self):
        self.cycle()
        start = self.s.events[-2]["monotonic_s"]
        self.s.emit("start_cycle", start, cycle_id="new-label-old-window")
        result = self.apply_new()[-1]
        self.assertEqual(result["reason"], "non_increasing_event_time")
        self.assertEqual(result["weak_cycles"], 0)

    def test_cycle_spacing_closed_boundary(self):
        self.cycle()
        end = self.s.now
        gap = self.s.config.policy.min_cycle_gap_s
        self.s.emit("start_cycle", end + gap - 0.001, cycle_id="premature")
        self.assertEqual(self.apply_new()[-1]["reason"], "cycle_gap_too_short")
        pending = self.s.begin(end + gap)
        self.assertTrue(self.apply_new()[-1]["accepted"])
        self.s.finish(pending)
        self.assertEqual(self.apply_new()[-1]["weak_cycles"], 2)

    def test_batch_switch_rejects_old_packets_without_mutating_new_batch(self):
        self.cycle()
        old = deepcopy(self.s.events[-1])
        self.s.install("new-clothes")
        self.apply_new()
        self.assertEqual(self.cycle()["weak_cycles"], 1)
        before = self.machine.snapshot()
        old.update(event_id="delayed-old-batch", monotonic_s=1e8)
        self.assertEqual(self.machine.handle(old)["reason"], "batch_or_installation_mismatch")
        self.assertEqual(self.machine.snapshot(), before)
        self.s.emit("install", batch_id="batch-a", installation_id="new-install-old-clothes")
        self.assertEqual(self.apply_new()[-1]["reason"], "retired_installation_or_batch")
        self.assertEqual(self.machine.snapshot(), before)

    def test_clip_midcycle_cancels_and_old_installation_cannot_return(self):
        self.cycle()
        pending = self.s.begin()
        self.apply_new()
        old_install = self.s.installation_id
        self.s.emit("clip", self.s.now + 10)
        clipped = self.apply_new()[-1]
        self.assertEqual(clipped["state"], "AWAITING_INSTALLATION")
        self.assertEqual(clipped["weak_cycles"], 0)
        self.assertIsNone(clipped["pending_cycle"])
        self.s.install()
        self.apply_new()
        self.s.emit("finish_cycle", pending[2], installation_id=old_install, cycle_id=pending[0],
                    start_s=pending[1], end_s=pending[2], observations=observations())
        self.assertEqual(self.apply_new()[-1]["reason"], "batch_or_installation_mismatch")
        self.assertEqual(self.cycle()["weak_cycles"], 1)

    def test_clip_in_invalid_observations_still_requires_reinstall(self):
        self.cycle()
        rows = observations()
        rows[50]["sensor_valid"] = False
        rows[51]["clip_event"] = True
        self.s.finish(self.s.begin(), rows=rows)
        result = self.apply_new()[-1]
        self.assertEqual(result["reason"], "observed_clip_requires_reinstallation")
        self.assertEqual(result["weak_cycles"], 0)
        self.assertEqual(result["state"], "AWAITING_INSTALLATION")

    def test_all_configuration_components_have_distinct_identity(self):
        config = DEFAULT_CONFIG
        changed = [replace(config, gates=replace(config.gates, weak_delta_pa=30)),
                   replace(config, protocol=replace(config.protocol, sample_period_s=0.5))]
        for name, value in asdict(config.policy).items():
            changed.append(replace(config, policy=replace(config.policy, **{name: value * 2})))
        identities = {config.sha256} | {item.sha256 for item in changed}
        self.assertEqual(len(identities), len(changed) + 1)
        self.assertEqual(Configuration.from_snapshot(config.snapshot()).sha256, config.sha256)
        with self.assertRaises(ValueError):
            replace(config, version="2.0").validate()

    def test_configuration_changes_require_new_installation_and_do_not_inherit(self):
        for name in ("change_gates", "change_policy", "change_protocol"):
            stream = next(s for s in scenarios() if s["name"] == name)
            result = replay(stream["events"])
            changed = next(r for r in result if r["reason"] == "configuration_changed_reinstall")
            self.assertEqual(changed["weak_cycles"], 0)
            self.assertIsNone(changed["installation_id"])
            self.assertEqual(result[-1]["weak_cycles"], 1)
            self.assertFalse(result[-1]["collection_authorized"])

    def test_reboot_retired_boot_and_reused_boot_ids(self):
        self.cycle()
        old_event = deepcopy(self.s.events[-1])
        old_boot = deepcopy(self.s.events[0])
        self.s.boot_id = "next-startup"
        self.s.emit("boot", 0)
        booted = self.apply_new()[-1]
        self.assertEqual(booted["weak_cycles"], 0)
        self.assertIsNone(booted["installation_id"])
        before = self.machine.snapshot()
        self.assertEqual(self.machine.handle(old_event)["reason"], "stale_or_unknown_boot")
        self.assertEqual(self.machine.handle(old_boot)["reason"], "retired_boot")
        self.assertEqual(self.machine.snapshot(), before)
        self.s.emit("boot", 0)
        self.assertEqual(self.apply_new()[-1]["reason"], "boot_id_reused")
        self.s.install()
        self.apply_new()
        self.assertEqual(self.cycle()["weak_cycles"], 1)

    def test_new_controller_has_no_persisted_evidence(self):
        self.cycle()
        self.cycle()
        fresh = StateMachine()
        result = fresh.handle(self.s.events[-1])
        self.assertEqual(result["reason"], "stale_or_unknown_boot")
        self.assertEqual(result["weak_cycles"], 0)
        self.assertFalse(result["collection_authorized"])

    def test_equal_backwards_and_large_jump_times_reset_without_regressing_clock(self):
        for name in ("same_time", "backward_time", "large_jump"):
            stream = next(s for s in scenarios() if s["name"] == name)
            results = replay(stream["events"])
            fault = next(r for r in results if r["state"] == "FAULT")
            self.assertEqual(fault["weak_cycles"], 0)
            self.assertEqual(results[-1]["weak_cycles"], 1)
            times = [r["last_monotonic_s"] for r in results]
            self.assertEqual(times, sorted(times))

    def test_max_event_gap_boundary(self):
        self.cycle()
        self.s.emit("tick", self.s.now + self.s.config.policy.max_event_gap_s)
        self.assertNotEqual(self.apply_new()[-1]["state"], "FAULT")
        self.s.emit("tick", self.s.now + self.s.config.policy.max_event_gap_s + 0.01)
        self.assertEqual(self.apply_new()[-1]["reason"], "event_time_jump")

    def test_idle_evidence_gap_boundary_and_pending_window_are_distinct(self):
        for offset, expected in ((0, 2), (0.01, 1)):
            s, m = Sequence(), StateMachine()
            s.cycle()
            for event in s.events:
                m.handle(event)
            pending = s.begin(s.now + s.config.policy.max_evidence_gap_s + offset)
            started = m.handle(s.events[-1])
            self.assertEqual(started["weak_cycles"], expected - 1)
            s.emit("tick", pending[1] + 1)
            # Total age is now over the idle limit, but a timely started cycle is pending.
            self.assertEqual(m.handle(s.events[-1])["weak_cycles"], expected - 1)
            s.finish(pending)
            self.assertEqual(m.handle(s.events[-1])["weak_cycles"], expected)

    def test_idle_tick_expiry_and_no_background_clock(self):
        self.cycle()
        old = self.machine.snapshot()
        self.assertEqual(self.machine.snapshot(), old)  # no real clock or implicit timer
        self.s.emit("tick", self.s.now + self.s.config.policy.max_evidence_gap_s + 1)
        result = self.apply_new()[-1]
        self.assertEqual(result["weak_cycles"], 0)
        self.assertEqual(result["state"], "READY")

    def test_timeout_boundary_and_late_finish_cannot_resurrect(self):
        for offset, accepted in ((0, True), (0.01, False)):
            s, m = Sequence(), StateMachine()
            pending = s.begin()
            for event in s.events:
                m.handle(event)
            s.emit("finish_cycle", pending[2] + s.config.policy.completion_grace_s + offset,
                   cycle_id=pending[0], start_s=pending[1], end_s=pending[2], observations=observations())
            result = m.handle(s.events[-1])
            self.assertEqual(result["accepted"], accepted)
            self.assertEqual(result["weak_cycles"], int(accepted))
            if not accepted:
                self.assertEqual(result["reason"], "cycle_timeout")
                retry = {**s.events[-1], "event_id": "late-retry", "monotonic_s": s.now + 1}
                self.assertEqual(m.handle(retry)["reason"], "cycle_id_reused")

    def test_incomplete_cycle_envelope_and_raw_window_are_both_required(self):
        for mode in ("short_duration", "premature_delivery", "missing_tail", "missing_stage"):
            s, m = Sequence(), StateMachine()
            pending = s.begin()
            rows = observations()
            if mode == "missing_tail":
                rows.pop()
            elif mode == "missing_stage":
                rows = [r for r in rows if r["phase"] != "flush"]
            s.finish(pending, rows=rows)
            if mode == "short_duration":
                s.events[-1]["end_s"] -= 1
            elif mode == "premature_delivery":
                s.events[-1]["monotonic_s"] -= 1
            result = None
            for event in s.events:
                result = m.handle(event)
            self.assertEqual(result["state"], "FAULT")
            self.assertEqual(result["weak_cycles"], 0)

    def test_second_cycle_cannot_start_while_measuring(self):
        self.s.begin()
        self.apply_new()
        self.s.emit("start_cycle", cycle_id="concurrent-cycle")
        self.assertEqual(self.apply_new()[-1]["reason"], "cycle_already_active")

    def test_malformed_envelopes_and_configuration_are_explicit(self):
        for event in (None, {}, {"event_version": "0.0"}, []):
            self.assertEqual(StateMachine().handle(event)["reason"], "malformed_event")
        for value in (None, math.nan, math.inf, -1, True, "1"):
            event = {**self.s.events[0], "monotonic_s": value}
            self.assertEqual(StateMachine().handle(event)["reason"], "invalid_monotonic_time")
        for value in (None, {}, "bad", [], {"version": "1.0"}):
            self.s.emit("configure", new_config=value)
            self.assertEqual(self.apply_new()[-1]["reason"], "invalid_configuration")
        self.s.emit("install", batch_id={}, installation_id=[])
        self.assertEqual(self.apply_new()[-1]["reason"], "invalid_installation_identity")

    def test_invalid_policy_configuration_rejected_at_construction(self):
        for name in asdict(Policy()):
            for value in (0, -1, math.inf, math.nan, True, None, "30"):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    StateMachine(replace(DEFAULT_CONFIG, policy=replace(Policy(), **{name: value})))
        for change in ({"review_after_weak_cycles": 2.5}, {"max_evidence_gap_s": 1},
                       {"timestamp_tolerance_s": 1}):
            with self.assertRaises(ValueError):
                StateMachine(replace(DEFAULT_CONFIG, policy=replace(Policy(), **change)))

    def test_identity_bounds_reject_empty_whitespace_and_control_characters(self):
        for field in ("boot_id", "event_id"):
            for value in ("", " leading", "trailing ", "line\nbreak", "delete\x7f", "hidden\u200b", "x" * 129, None, []):
                with self.subTest(field=field, value=repr(value)):
                    event = {**self.s.events[0], field: value}
                    result = StateMachine().handle(event)
                    self.assertEqual(result["reason"], "malformed_event")
                    self.assertIsNone(result["boot_id"])
        valid = {**self.s.events[0], "boot_id": "启动域一", "event_id": "e" * 128}
        self.assertTrue(StateMachine().handle(valid)["accepted"])

    def test_scenarios_replay_deterministically_with_exposed_safety_invariants(self):
        fixtures = scenarios()
        self.assertEqual(len({s["name"] for s in fixtures}), len(fixtures))
        for stream in fixtures:
            first = replay(stream["events"])
            second = replay(json.loads(json.dumps(stream["events"])))
            self.assertEqual(first, second, stream["name"])
            previous = 0
            for result in first:
                self.assertFalse(result["collection_authorized"])
                self.assertEqual(result["weak_cycles"], len(set(result["evidence_cycle_ids"])))
                if result["weak_cycles"] > previous:
                    self.assertEqual(result["weak_cycles"], previous + 1)
                    self.assertEqual(result["assessment"]["state"], "LOW_SIGNAL_UNCONFIRMED")
                    self.assertTrue(result["accepted"])
                if result["state"] in ("FAULT", "AWAITING_INSTALLATION", "STABILIZING", "SOURCE_PRESENT"):
                    self.assertEqual(result["weak_cycles"], 0)
                previous = result["weak_cycles"]

    def test_seeded_event_mutations_never_manufacture_evidence(self):
        # Test-only randomness; the persisted reference event fixtures are deterministic.
        rng = random.Random(20261024)
        original = Sequence()
        original.cycle("leaky")
        for _ in range(120):
            events = deepcopy(original.events)
            index = rng.randrange(len(events))
            key, value = rng.choice([
                ("boot_id", "foreign-boot"), ("config_sha256", "wrong-config"),
                ("monotonic_s", -1), ("event_version", "0.0"), ("batch_id", "foreign-clothes"),
                ("installation_id", "old-install"), ("cycle_id", "renamed")])
            events[index][key] = value
            outputs = replay(events)
            self.assertTrue(all(r["collection_authorized"] is False for r in outputs))
            self.assertLessEqual(outputs[-1]["weak_cycles"], 1)

    def test_saved_outputs_replay_and_verify_detects_tampering_without_writing(self):
        inputs = run.input_hashes()
        outputs, metadata = run.build_outputs()
        manifest = run.make_manifest(inputs, outputs, metadata)
        with tempfile.TemporaryDirectory(prefix="test-artifacts-", dir=run.HERE) as temporary:
            folder = Path(temporary)
            for name, data in {**outputs, "manifest.json": run.json_bytes(manifest)}.items():
                (folder / name).write_bytes(data)
            before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir()}
            self.assertEqual(run.verify(folder), metadata["counts"])
            after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir()}
            self.assertEqual(before, after)
            (folder / "summary.csv").write_text("altered", encoding="utf-8")
            with self.assertRaises(ValueError):
                run.verify(folder)


if __name__ == "__main__":
    unittest.main()

"""Mathematical and decision-invariant checks, not real-world validation."""

import csv
from dataclasses import asdict, replace
import json
import math
from pathlib import Path
import statistics
import unittest

from model import EvidenceTracker, Parameters, assess, chamber_and_sensor_step, simulate, vapor_pa
from protocol import DEFAULT_GATES, DEFAULT_PROTOCOL, MeasurementProtocol
from run_simulation import cases, verification_protocols


class ModelChecks(unittest.TestCase):
    def test_same_rh_different_temperature_is_not_same_vapor_pressure(self):
        self.assertGreater(vapor_pa(30, 60), vapor_pa(20, 60))

    def test_isothermal_equilibrium_stays_at_equilibrium(self):
        rows = simulate(replace(Parameters(), surface_activity=0.60, noise_rh_pct=0))
        self.assertLess(max(abs(r["delta_e_Pa"]) for r in rows), 1e-8)

    def test_zero_exchange_and_zero_flow_remain_finite(self):
        rows = simulate(replace(Parameters(), exchange_ml_s=0, leak_ml_s=0,
                                passive_extra_ml_s=0, flush_extra_ml_s=0, noise_rh_pct=0))
        self.assertTrue(all(math.isfinite(r["delta_e_Pa"]) for r in rows))

    def test_equal_time_constants_against_independent_numeric_solution(self):
        exact = chamber_and_sensor_step(1, 0.5, 2, 0.25, 4, 3)
        pc, ps, h = 1.0, 0.5, 0.0001
        for _ in range(30000):
            # Independent midpoint integration checks the degenerate analytic branch.
            dpc, dps = 2 - 0.25 * pc, (pc - ps) / 4
            mid_pc, mid_ps = pc + h * dpc / 2, ps + h * dps / 2
            pc += h * (2 - 0.25 * mid_pc)
            ps += h * (mid_pc - mid_ps) / 4
        self.assertAlmostEqual(exact[0], pc, places=7)
        self.assertAlmostEqual(exact[1], ps, places=7)

    def test_different_physical_parameters_can_be_indistinguishable(self):
        p = replace(Parameters(), noise_rh_pct=0)
        a = simulate(p)
        b = simulate(replace(p, exchange_ml_s=0.04, leak_ml_s=0, surface_activity=0.80))
        self.assertLess(max(abs(x["delta_e_Pa"] - y["delta_e_Pa"]) for x, y in zip(a, b)), 1e-8)

    def test_wet_like_surface_has_recovery_signal(self):
        self.assertEqual(assess(simulate(Parameters()))["state"], "VAPOR_SOURCE_PRESENT")

    def test_wet_leak_counterexample_is_not_called_dry(self):
        result = assess(simulate(replace(Parameters(), leak_ml_s=1.5)))
        self.assertEqual(result["state"], "LOW_SIGNAL_UNCONFIRMED")

    def test_high_humidity_is_rejected_even_with_weak_signal(self):
        result = assess(simulate(replace(Parameters(), ambient_rh_pct=95)))
        self.assertEqual(result["state"], "UNKNOWN")
        self.assertIn("high_ambient_humidity", result["reasons"])

    def test_clip_and_sensor_faults_invalidate_cycle(self):
        for field in ("clip_at_s", "invalid_at_s"):
            with self.subTest(field=field):
                result = assess(simulate(replace(Parameters(), **{field: 85})))
                self.assertEqual(result["state"], "UNKNOWN")

    def test_nan_cannot_become_weak_valid_evidence(self):
        rows = simulate(Parameters())
        rows[80]["local_RH_pct"] = math.nan
        self.assertEqual(assess(rows)["state"], "UNKNOWN")

    def test_missing_samples_and_missing_phase_are_rejected(self):
        rows = simulate(replace(Parameters(), surface_activity=0.60))
        missing_samples = rows[:60] + rows[75:]
        self.assertIn("sample_gap", assess(missing_samples)["reasons"])
        only_recovery = [r for r in rows if r["phase"] == "recovery"]
        self.assertIn("missing_phase", assess(only_recovery)["reasons"])

    def test_repeated_timestamp_is_rejected(self):
        rows = simulate(Parameters())
        rows[60]["time_s"] = rows[59]["time_s"]
        self.assertEqual(assess(rows)["state"], "UNKNOWN")

    def test_repeated_weak_evidence_never_creates_calibration(self):
        tracker = EvidenceTracker()
        weak = assess(simulate(replace(Parameters(), surface_activity=0.60)))
        for _ in range(10):
            self.assertFalse(tracker.update(weak)["collection_authorized"])
        self.assertEqual(tracker.weak_cycles, 10)
        tracker.update({"state": "UNKNOWN"})
        self.assertEqual(tracker.weak_cycles, 0)

    def test_impossible_parameter_is_rejected(self):
        with self.assertRaises(ValueError):
            simulate(replace(Parameters(), volume_ml=-1))


class MeasurementContractChecks(unittest.TestCase):
    def assert_unknown(self, rows, reason, **kwargs):
        result = assess(rows, **kwargs)
        self.assertEqual(result["state"], "UNKNOWN")
        self.assertIn(reason, result["reasons"].split(";"))
        self.assertFalse(result["collection_authorized"])
        return result

    def test_default_regression_matches_frozen_twelve_cases(self):
        baseline = Path(__file__).resolve().parents[1] / "docs/team/baseline-v1/theory/results"
        manifest = json.loads((baseline / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(asdict(DEFAULT_GATES), manifest["gates_are_illustrative"])
        with (baseline / "case-summary.csv").open(encoding="utf-8-sig", newline="") as handle:
            expected = list(csv.DictReader(handle))
        self.assertEqual(len(expected), 12)
        for old in expected:
            with self.subTest(case=old["case"]):
                parameters, _ = cases()[old["case"]]
                frozen = manifest["cases"][old["case"]]["parameters"].copy()
                for key in ("baseline_s", "flush_s", "recovery_s"):
                    self.assertEqual(frozen.pop(key), getattr(DEFAULT_PROTOCOL, key))
                self.assertEqual(asdict(parameters), frozen)
                result = assess(simulate(parameters, seed=manifest["seed"]))
                self.assertEqual(result["state"], old["state"])
                self.assertEqual(result["reasons"], old["reasons"])
                for key in ("recovery_rise_Pa", "tail_delta_Pa"):
                    if old[key]:
                        self.assertAlmostEqual(result[key], float(old[key]), delta=1e-9)
                    else:
                        self.assertIsNone(result[key])
                self.assertFalse(result["collection_authorized"])

    def test_default_regression_matches_frozen_sixty_sweep_cases(self):
        path = Path(__file__).resolve().parents[1] / "docs/team/baseline-v1/theory/results/parameter-sweep.csv"
        with path.open(encoding="utf-8-sig", newline="") as handle:
            expected = list(csv.DictReader(handle))
        self.assertEqual(len(expected), 60)
        for old in expected:
            parameters = replace(Parameters(), noise_rh_pct=0,
                                 **{key: float(old[key]) for key in ("volume_ml", "ambient_rh_pct", "leak_ml_s")})
            result = assess(simulate(parameters))
            self.assertEqual(result["state"], old["state"])
            self.assertEqual(result["reasons"], old["reasons"])
            for key in ("recovery_rise_Pa", "tail_delta_Pa"):
                self.assertAlmostEqual(result[key], float(old[key]), delta=1e-9)

    def test_default_and_two_alternative_protocols_keep_counterexamples(self):
        variants = verification_protocols()
        self.assertEqual(len(variants), 3)
        for name, protocol in variants.items():
            with self.subTest(protocol=name):
                wet = simulate(Parameters(), protocol=protocol)
                dry = simulate(replace(Parameters(), surface_activity=0.6), protocol=protocol)
                leaky = simulate(replace(Parameters(), leak_ml_s=1.5), protocol=protocol)
                self.assertEqual(len(wet), round(protocol.total_s / protocol.sample_period_s) + 1)
                self.assertEqual(assess(wet, protocol=protocol)["state"], "VAPOR_SOURCE_PRESENT")
                self.assertEqual(assess(dry, protocol=protocol)["state"], "LOW_SIGNAL_UNCONFIRMED")
                self.assertEqual(assess(leaky, protocol=protocol)["state"], "LOW_SIGNAL_UNCONFIRMED")
                a = simulate(replace(Parameters(), noise_rh_pct=0), protocol=protocol)
                b = simulate(replace(Parameters(), noise_rh_pct=0, exchange_ml_s=0.04,
                                     leak_ml_s=0, surface_activity=0.8), protocol=protocol)
                self.assertLess(max(abs(x["delta_e_Pa"] - y["delta_e_Pa"]) for x, y in zip(a, b)), 1e-8)
                tracker = EvidenceTracker()
                for _ in range(20):
                    self.assertFalse(tracker.update(assess(leaky, protocol=protocol))["collection_authorized"])

    def test_protocol_binding_detects_wrong_settings_and_mixed_rows(self):
        alternate = verification_protocols()["half_second"]
        rows = simulate(Parameters(), protocol=alternate)
        self.assert_unknown(rows, "protocol_mismatch")
        default_rows = simulate(Parameters())
        default_rows[30] = rows[60]
        self.assert_unknown(default_rows, "protocol_mismatch")

    def test_protocol_fingerprint_roundtrip_and_numeric_normalization(self):
        protocol = DEFAULT_PROTOCOL
        restored = MeasurementProtocol(**json.loads(json.dumps(protocol.snapshot())))
        self.assertEqual(restored.fingerprint, protocol.fingerprint)
        self.assertEqual(replace(protocol, baseline_s=30).fingerprint, protocol.fingerprint)
        self.assertNotEqual(replace(protocol, head_window_s=6).fingerprint, protocol.fingerprint)

    def test_shorter_protocol_does_not_inherit_eighty_second_rule(self):
        protocol = replace(DEFAULT_PROTOCOL, baseline_s=10, flush_s=10, recovery_s=30,
                           head_window_s=2, tail_window_s=4)
        rows = simulate(replace(Parameters(), surface_activity=0.6), protocol=protocol)
        self.assertEqual(assess(rows, protocol=protocol)["state"], "LOW_SIGNAL_UNCONFIRMED")
        # This shows timing consistency, not adequacy of a shorter physical experiment.

    def test_invalid_or_insufficient_protocol_is_rejected_before_simulation(self):
        changes = [
            {"version": "99.0"}, {"baseline_s": 0}, {"recovery_s": -1},
            {"sample_period_s": 0}, {"sample_period_s": 7}, {"sample_period_s": 10},
            {"baseline_s": 1}, {"head_window_s": 0.5}, {"head_window_s": 80},
            {"max_gap_factor": 0.9}, {"min_phase_samples": 1}, {"min_window_samples": 1},
            {"min_window_samples": 2.5}, {"min_phase_samples": True},
            {"time_tolerance_s": 0.001}, {"time_tolerance_s": 0},
            {"baseline_s": 1e308, "flush_s": 1e308}, {"head_window_s": "5"},
        ]
        changes += [{"sample_period_s": value} for value in (math.nan, math.inf, None, True)]
        for change in changes:
            with self.subTest(change=change):
                invalid = replace(DEFAULT_PROTOCOL, **change)
                with self.assertRaises(ValueError):
                    simulate(Parameters(), protocol=invalid)
                with self.assertRaises(ValueError):
                    assess([], protocol=invalid)

    def test_invalid_gates_cannot_silently_relax_decision(self):
        changes = [{"weak_delta_pa": 100}, {"strong_delta_pa": 0},
                   {"high_ambient_rh_pct": 101}, {"saturation_rh_pct": -1},
                   {"ambient_change_rh_pct": 101}, {"temp_mismatch_c": 51},
                   {"weak_delta_pa": math.nan}, {"strong_delta_pa": math.inf},
                   {"temp_mismatch_c": "1"}, {"high_ambient_rh_pct": True}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                assess([], replace(DEFAULT_GATES, **change))

    def test_events_must_align_and_lie_in_cycle(self):
        for field in ("clip_at_s", "invalid_at_s", "ambient_step_at_s"):
            for value in (85.5, DEFAULT_PROTOCOL.total_s + 1):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    simulate(replace(Parameters(), **{field: value}))
        for value in (0, DEFAULT_PROTOCOL.total_s):
            self.assert_unknown(simulate(replace(Parameters(), clip_at_s=value)), "clip_changed")

    def test_fractional_sampling_handles_phase_boundaries(self):
        protocol = replace(DEFAULT_PROTOCOL, sample_period_s=0.1, baseline_s=0.3,
                           flush_s=0.3, recovery_s=1.2, head_window_s=0.2, tail_window_s=0.3)
        rows = simulate(replace(Parameters(), surface_activity=0.6), protocol=protocol)
        self.assertEqual(rows[3]["phase"], "flush")
        self.assertEqual(rows[6]["phase"], "recovery")
        self.assertEqual(assess(rows, protocol=protocol)["state"], "LOW_SIGNAL_UNCONFIRMED")

    def test_negative_event_sentinels_remain_disabled_within_tolerance(self):
        rows = simulate(replace(Parameters(), clip_at_s=-1e-10, invalid_at_s=-1e-10))
        self.assertTrue(all(r["sensor_valid"] and not r["clip_event"] for r in rows))

    def test_quality_gate_equality_and_just_over_boundary(self):
        original = simulate(replace(Parameters(), surface_activity=0.6, noise_rh_pct=0))
        for field, exact, reason in (("ambient_RH_pct", 90.0, "high_ambient_humidity"),
                                     ("local_RH_pct", 98.0, "near_saturation")):
            rows = [dict(r) for r in original]
            rows[80][field] = exact
            self.assert_unknown(rows, reason)
            rows[80][field] = math.nextafter(exact, -math.inf)
            self.assertNotIn(reason, assess(rows)["reasons"].split(";"))
        for field, exact, reason in (("ambient_RH_pct", 63.0, "ambient_changed"),
                                     ("local_T_C", 26.0, "temperature_mismatch")):
            rows = [dict(r) for r in original]
            # Make the reference exact, independent of floating point simulator noise.
            for row in rows:
                row["ambient_RH_pct"] = 60.0
            rows[80][field] = exact
            self.assertNotIn(reason, assess(rows)["reasons"].split(";"))
            rows[80][field] = math.nextafter(exact, math.inf)
            self.assert_unknown(rows, reason)

    def test_missing_container_rows_and_fields_are_explicit(self):
        for missing in ([], None, {}, "", [None]):
            reason = "missing_field" if missing == [None] else "missing_data"
            self.assert_unknown(missing, reason)
        original = simulate(Parameters())
        for key in original[0]:
            if key in ("delta_e_Pa", "synthetic_true_chamber_Pa"):
                continue
            with self.subTest(key=key):
                rows = [dict(r) for r in original]
                del rows[60][key]
                self.assert_unknown(rows, "missing_field")

    def test_malformed_numeric_fields_and_flags_are_explicit(self):
        original = simulate(Parameters())
        for key in ("time_s", "ambient_T_C", "ambient_RH_pct", "local_T_C", "local_RH_pct"):
            for value in (None, math.nan, math.inf, -math.inf, "0", True, {}, 10 ** 1000):
                with self.subTest(key=key, value_type=type(value).__name__):
                    rows = [dict(r) for r in original]
                    rows[60][key] = value
                    self.assert_unknown(rows, "invalid_sensor")
        for key in ("clip_event", "sensor_valid"):
            for value in (None, 0, 1, "False"):
                rows = [dict(r) for r in original]
                rows[60][key] = value
                self.assert_unknown(rows, "invalid_flag")

    def test_incompatible_schema_and_units_are_explicit(self):
        for key, value, reason in (("observation_version", "0.1", "unsupported_observation_version"),
                                   ("unit_system", "ms_K_fraction_kPa", "unsupported_units")):
            rows = simulate(Parameters())
            rows[0][key] = value
            self.assert_unknown(rows, reason)

    def test_physical_reading_bounds_are_closed_and_outside_is_rejected(self):
        for temp in (0, 50):
            for rh in (0, 100):
                rows = simulate(replace(Parameters(), temp_c=temp, ambient_rh_pct=rh,
                                        surface_activity=rh / 100, noise_rh_pct=0))
                result = assess(rows)
                self.assertNotIn("outside_model_domain", result["reasons"])
                self.assertNotIn("invalid_sensor", result["reasons"])
                self.assertFalse(result["collection_authorized"])
        for key, value in (("ambient_T_C", -0.001), ("local_T_C", 50.001),
                           ("ambient_RH_pct", -0.001), ("local_RH_pct", 100.001)):
            rows = simulate(Parameters())
            rows[80][key] = value
            self.assert_unknown(rows, "outside_model_domain")

    def test_removed_stage_or_truncated_cycle_never_shifts_windows(self):
        rows = simulate(Parameters())
        for phase in ("baseline", "flush", "recovery"):
            self.assert_unknown([r for r in rows if r["phase"] != phase], "missing_phase")
        for truncated in (rows[1:], rows[:-1], rows[:-5]):
            result = self.assert_unknown(truncated, "incomplete_cycle")
            self.assertIsNone(result["recovery_rise_Pa"])

    def test_phase_labels_and_timestamp_anomalies_are_rejected(self):
        original = simulate(Parameters())
        for phase in ("baseline", "flush", "unknown", None, []):
            rows = [dict(r) for r in original]
            rows[80]["phase"] = phase
            self.assert_unknown(rows, "phase_time_mismatch")
        for timestamp in (79, 78, -1):
            rows = [dict(r) for r in original]
            rows[80]["time_s"] = timestamp
            self.assert_unknown(rows, "non_monotonic_time")
        for timestamp in (-1, 1e12):
            rows = [dict(r) for r in original]
            rows[0 if timestamp < 0 else -1]["time_s"] = timestamp
            self.assert_unknown(rows, "incomplete_cycle")

    def test_gap_boundary_and_small_isolated_loss(self):
        original = simulate(replace(Parameters(), surface_activity=0.6, noise_rh_pct=0))
        rows = [dict(r) for r in original if r["time_s"] != 61]
        rows[60]["time_s"] = 61.5  # exactly 2.5 s after the previous observation
        self.assertEqual(assess(rows)["state"], "LOW_SIGNAL_UNCONFIRMED")
        rows[60]["time_s"] += 1e-5
        self.assert_unknown(rows, "sample_gap")
        # A single missing interior sample is explicitly permitted by the gap bound.
        self.assertEqual(assess(original[:80] + original[81:])["state"], "LOW_SIGNAL_UNCONFIRMED")

    def test_feature_windows_are_anchored_to_protocol_not_remaining_data(self):
        rows = simulate(Parameters())
        rows = [r for r in rows if r["time_s"] != DEFAULT_PROTOCOL.recovery_start_s]
        result = assess(rows)
        head = [r["delta_e_Pa"] for r in rows if 50 <= r["time_s"] <= 55]
        tail = [r["delta_e_Pa"] for r in rows if 130 <= r["time_s"] <= 140]
        self.assertAlmostEqual(result["recovery_rise_Pa"], statistics.mean(tail) - statistics.mean(head))

    def test_insufficient_phase_or_feature_support_is_rejected(self):
        protocol = replace(DEFAULT_PROTOCOL, baseline_s=2, flush_s=2)
        rows = simulate(Parameters(), protocol=protocol)
        self.assert_unknown([r for r in rows if r["time_s"] != 3], "insufficient_phase_samples", protocol=protocol)
        protocol = replace(DEFAULT_PROTOCOL, min_window_samples=6)
        rows = simulate(Parameters(), protocol=protocol)
        self.assert_unknown([r for r in rows if r["time_s"] != 51], "insufficient_feature_samples", protocol=protocol)

    def test_only_observations_and_protocol_affect_assessment(self):
        forbidden = {"delta_e_Pa", "synthetic_true_chamber_Pa", "case", "surface_activity",
                     "leak_ml_s", "exchange_ml_s", "hypothesis"}

        class GuardedRow(dict):
            def __getitem__(self, key):
                if key in forbidden:
                    raise AssertionError(f"Classifier read non-observation: {key}")
                return super().__getitem__(key)

        for parameters in (Parameters(), replace(Parameters(), leak_ml_s=1.5)):
            rows = simulate(parameters)
            expected = assess(rows)
            guarded = [GuardedRow({**r, **{key: "adversarial truth" for key in forbidden}}) for r in rows]
            self.assertEqual(assess(guarded), expected)
            observations_only = [{key: value for key, value in r.items() if key not in forbidden} for r in rows]
            self.assertEqual(assess(observations_only), expected)

    def test_legacy_independent_timing_arguments_are_not_silently_accepted(self):
        with self.assertRaises(TypeError):
            Parameters(recovery_s=120)
        with self.assertRaises(TypeError):
            simulate(Parameters(), dt=0.5)


if __name__ == "__main__":
    unittest.main()

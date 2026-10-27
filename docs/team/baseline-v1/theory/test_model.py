"""Mathematical and decision-invariant checks, not real-world validation."""

from dataclasses import replace
import math
import unittest

from model import EvidenceTracker, Parameters, assess, chamber_and_sensor_step, simulate, vapor_pa


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


if __name__ == "__main__":
    unittest.main()

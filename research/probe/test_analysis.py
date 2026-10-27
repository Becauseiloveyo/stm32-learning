"""Checks of independent research mathematics; no physical validation."""

from dataclasses import replace
import hashlib
import json
import math
import unittest

from analyze import (BASELINE, OUTPUT, Parameters, baseline_fingerprints,
                     coefficients, common_step_delta, cycle, decaying_wall,
                     features, matched_wall_ug_s, saturation_pa, step,
                     uncertainty_example)


class ResearchChecks(unittest.TestCase):
    def test_frozen_baseline_hashes(self):
        self.assertEqual(len(baseline_fingerprints()["files_checked"]), 10)

    def test_step_matches_independent_rk4_including_equal_time_constants(self):
        # Independent integration of the two differential equations.
        for tau in (4.0, 30.0, 125.0):
            source, rate = 4.2, 0.008
            y, z, dt = 31.0, 22.0, 0.01
            initial = (y, z)
            f = lambda state: (source - rate * state[0], (state[0] - state[1]) / tau)
            for _ in range(2000):
                a = f((y, z))
                b = f((y + dt * a[0] / 2, z + dt * a[1] / 2))
                c = f((y + dt * b[0] / 2, z + dt * b[1] / 2))
                d = f((y + dt * c[0], z + dt * c[1]))
                y += dt * (a[0] + 2 * b[0] + 2 * c[0] + d[0]) / 6
                z += dt * (a[1] + 2 * b[1] + 2 * c[1] + d[1]) / 6
            exact = step(*initial, source, rate, tau, 20)
            self.assertAlmostEqual(y, exact[0], delta=1e-8)
            self.assertAlmostEqual(z, exact[1], delta=1e-8)

    def test_ideal_sensor_and_equilibrium_limits(self):
        y, z = step(10, 2, 4, 0.02, 0, 20)
        self.assertEqual(y, z)
        self.assertEqual(step(200, 200, 4, 0.02, 4, 20), (200, 200))
        rows = cycle(Parameters(surface_activity=0.6))
        self.assertTrue(all(r["synthetic_observed_delta_Pa"] == 0 for r in rows))

    def test_baseline_nonidentifiability_and_wall_mimic(self):
        base = Parameters()
        matches = [replace(base, exchange_ml_s=0.04, leak_ml_s=0, surface_activity=0.8),
                   replace(base, surface_activity=0.6, wall_source_ug_s=matched_wall_ug_s(base))]
        for other in matches:
            for a, b in zip(cycle(base), cycle(other)):
                self.assertAlmostEqual(a["synthetic_observed_delta_Pa"], b["synthetic_observed_delta_Pa"], delta=1e-9)

    def test_leak_and_sensor_finite_window_counterexamples(self):
        base = Parameters()
        nominal = features(base, cycle(base))
        leaky = replace(base, leak_ml_s=1.5)
        slow = replace(base, sensor_tau_s=120)
        self.assertLess(features(leaky, cycle(leaky))["rise_Pa"], nominal["rise_Pa"] / 20)
        self.assertLess(features(slow, cycle(slow))["rise_Pa"], nominal["rise_Pa"] / 2)
        self.assertAlmostEqual(features(slow, cycle(slow))["formal_equilibrium_delta_Pa"], nominal["formal_equilibrium_delta_Pa"])

    def test_common_step_transient_has_zero_true_difference(self):
        peak = math.log(30 / 4) / (1 / 4 - 1 / 30)
        self.assertGreater(common_step_delta(peak), 180)
        self.assertLess(common_step_delta(peak - 0.1), common_step_delta(peak))
        self.assertLess(common_step_delta(peak + 0.1), common_step_delta(peak))
        self.assertEqual(common_step_delta(0), 0)
        self.assertEqual(common_step_delta(9, local_tau_s=4), 0)

    def test_finite_wall_inventory_mass_balance_and_equal_rate_limit(self):
        p = Parameters(surface_activity=0.6, wall_source_ug_s=0.1)
        # Integrate outflow/cloth uptake by trapezoid; compare to inventory release.
        v, rt, d = p.volume_ml * 1e-6, 461.5 * (p.temp_c + 273.15), (p.exchange_ml_s + p.leak_ml_s) * 1e-6
        for wall_tau in (30, 120, 125, 600):
            dt = 0.01
            values = [decaying_wall(i * dt, wall_tau, p)[0] for i in range(18001)]
            out_ug = d / rt * dt * (sum(values) - (values[0] + values[-1]) / 2) * 1e9
            air_ug = v * values[-1] / rt * 1e9
            _, mass = decaying_wall(180, wall_tau, p)
            release = p.wall_source_ug_s * wall_tau - mass
            self.assertAlmostEqual(release, air_ug + out_ug, delta=1e-7)
            self.assertGreaterEqual(mass, 0)
        self.assertEqual(decaying_wall(90, 120, replace(p, wall_source_ug_s=0)), (0, 0))

    def test_sensitivities_match_finite_difference(self):
        p = Parameters()
        h = 1e-6
        equilibrium = lambda x: coefficients(x)[0] / coefficients(x)[1]
        numeric = (equilibrium(replace(p, leak_ml_s=p.leak_ml_s + h)) - equilibrium(replace(p, leak_ml_s=p.leak_ml_s - h))) / (2 * h)
        analytic = -equilibrium(p) / (p.exchange_ml_s + p.leak_ml_s)
        self.assertAlmostEqual(numeric / analytic, 1, delta=1e-8)
        numeric_w = (equilibrium(replace(p, wall_source_ug_s=h)) - equilibrium(p)) / h
        analytic_w = 461.5 * (25 + 273.15) * 1e-3 / (p.exchange_ml_s + p.leak_ml_s)
        self.assertAlmostEqual(numeric_w, analytic_w, delta=1e-6)

    def test_temperature_consistency_and_uncertainty_units(self):
        ambient = 0.6 * saturation_pa(25)
        for local_t in (22, 24, 26, 28):
            local_rh = 100 * ambient / saturation_pa(local_t)
            self.assertAlmostEqual(local_rh / 100 * saturation_pa(local_t), ambient, delta=1e-10)
        result = uncertainty_example()
        self.assertAlmostEqual(result["u_delta_Pa_independent"], 47.49, delta=0.01)
        self.assertAlmostEqual(result["d_e_d_RH_Pa_per_percentage_point"], saturation_pa(25) / 100)

    def test_invalid_domain_is_rejected_not_clipped(self):
        for p in (Parameters(volume_ml=0), Parameters(leak_ml_s=-1), Parameters(temp_c=51),
                  Parameters(surface_activity=1.1), Parameters(sensor_tau_s=float("nan")),
                  Parameters(wall_source_ug_s=100)):
            with self.assertRaises(ValueError):
                cycle(p)

    def test_generated_evidence_fingerprints(self):
        manifest = json.loads((OUTPUT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["data_status"], "SYNTHETIC_UNCALIBRATED")
        self.assertEqual(manifest["sweep_count"], 135)
        for name, digest in manifest["source_sha256"].items():
            self.assertEqual(hashlib.sha256((OUTPUT.parent / name).read_bytes()).hexdigest(), digest)
        for name, digest in manifest["outputs_sha256"].items():
            self.assertEqual(hashlib.sha256((OUTPUT / name).read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main(verbosity=2)

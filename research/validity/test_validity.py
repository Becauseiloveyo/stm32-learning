"""Independent analytic checks and artifact/observation boundaries for T03."""
import sys
sys.dont_write_bytecode = True

import csv
from dataclasses import replace
import io
import json
import math
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run as v


class ValidityTests(unittest.TestCase):
    def setUp(self):
        self.protocol = v.profiles()["default"]
        self.p = replace(v.model.Parameters(), surface_activity=0.6, noise_rh_pct=0)

    def test_protocol_windows_and_fractional_sampling(self):
        expected = {"default": (6, 11, 82.5), "three_second_long": (3, 5, 111)}
        for name, protocol in v.profiles().items():
            head, tail = v.window_indices(protocol)
            self.assertEqual((len(head), len(tail)), expected[name][:2])
            self.assertAlmostEqual(v.feature_values(v.times(protocol), protocol)[0], expected[name][2])
        fractional = replace(self.protocol, sample_period_s=0.5, head_window_s=7.5, tail_window_s=12.5)
        head, tail = v.window_indices(fractional)
        self.assertEqual((len(head), len(tail)), (16, 26))
        self.assertEqual(v.times(fractional)[head[-1]], fractional.recovery_start_s+7.5)
        self.assertAlmostEqual(v.feature_values(v.times(fractional), fractional)[0], 80.0)

    def test_drift_feature_has_analytic_window_centroid_dependence(self):
        for name, protocol in v.profiles().items():
            rows = v.model.simulate(self.p, protocol=protocol)
            for offset in (0, 30):
                biased = v.biased_rows(rows, pressure_offset=offset, drift=0.5)
                result = v.model.assess(biased, protocol=protocol)
                self.assertAlmostEqual(result["recovery_rise_Pa"], 41.25 if name == "default" else 55.5, places=10)
                self.assertAlmostEqual(result["tail_delta_Pa"], offset+0.5*(135 if name == "default" else 156), places=10)
                self.assertFalse(result["collection_authorized"])
            constant = v.biased_rows(rows, pressure_offset=30)
            self.assertAlmostEqual(v.model.assess(constant, protocol=protocol)["recovery_rise_Pa"], 0, places=10)

    def test_temperature_bias_differs_from_real_temperature_at_equal_vapor(self):
        ea = v.model.vapor_pa(25, 60)
        self.assertAlmostEqual(v.model.vapor_pa(25.5, 60)-ea, 57.295763890066, places=8)
        for t in (22, 24.5, 25, 25.5, 28):
            corrected_rh = 100*ea/v.model.saturation_pa(t)
            self.assertAlmostEqual(v.model.vapor_pa(t, corrected_rh)-ea, 0, places=10)

    def test_common_step_equal_response_sign_and_analytic_peak(self):
        peak_t = math.log(30/4)/(1/4-1/30)
        peak = v.common_step_error(peak_t, -300, 30, 4)
        self.assertAlmostEqual(peak_t, 9.299552402, places=8)
        ratio = 4/30
        independent_peak = 300*(1-ratio)*ratio**(ratio/(1-ratio))
        self.assertAlmostEqual(peak, independent_peak, places=10)
        self.assertAlmostEqual(peak, 190.699054, places=6)
        for t in (-1, 0, 4, 30, 120):
            self.assertEqual(v.common_step_error(t, -300, 4, 4), 0)
            self.assertAlmostEqual(v.common_step_error(t, -300, 30, 4), -v.common_step_error(t, -300, 4, 30))
        self.assertGreater(peak, v.common_step_error(peak_t-0.1, -300, 30, 4))
        self.assertGreater(peak, v.common_step_error(peak_t+0.1, -300, 30, 4))

    def test_wall_unequal_rates_against_analytic_solution_and_sensor_convolution(self):
        t, source, wall_tau = 75.0, 0.1, 30.0
        rt, _, _, _ = v.wall_constants(self.p)
        a = (self.p.exchange_ml_s+self.p.leak_ml_s)/self.p.volume_ml
        b, h = 1/wall_tau, 1/self.p.sensor_tau_s
        c = rt*source*1e-9/(self.p.volume_ml*1e-6)
        expected_y = c*(math.exp(-b*t)-math.exp(-a*t))/(a-b)
        response = lambda rate: h*(math.exp(-rate*t)-math.exp(-h*t))/(h-rate)
        expected_z = c/(a-b)*(response(b)-response(a))
        state = v.integrate_wall((0, 0, source*wall_tau, 0, 0), t, self.p, self.p.leak_ml_s, wall_tau)
        self.assertAlmostEqual(state[0], expected_y, places=8)
        self.assertAlmostEqual(state[1], expected_z, places=8)
        self.assertAlmostEqual(state[2], source*wall_tau*math.exp(-t/wall_tau), places=10)

    def test_wall_equal_rates_and_triple_equal_sensor_limit(self):
        p = replace(self.p, sensor_tau_s=125)
        t, source, tau = 125.0, 0.1, 125.0
        rt = v.wall_constants(p)[0]
        a = 1/tau
        c = rt*source*1e-9/(p.volume_ml*1e-6)
        y, z, *_ = v.integrate_wall((0, 0, source*tau, 0, 0), t, p, p.leak_ml_s, tau)
        self.assertAlmostEqual(y, c*t*math.exp(-a*t), places=8)
        self.assertAlmostEqual(z, c*a*t*t/2*math.exp(-a*t), places=8)

    def test_wall_zero_source_recovers_accepted_model_for_both_protocols(self):
        p = replace(self.p, surface_activity=1)
        for protocol in v.profiles().values():
            rows, trace = v.finite_wall_cycle(p, 0, 120, protocol)
            accepted = v.model.simulate(p, protocol=protocol)
            self.assertLess(max(abs(v.measured_delta(a)-v.measured_delta(b)) for a, b in zip(rows, accepted)), 2e-7)
            self.assertLess(max(abs(x["mass_residual_ug"]) for x in trace), 1e-10)
            self.assertTrue(all(x["wall_inventory_ug"] == 0 for x in trace))

    def test_wall_long_lifetime_recovers_constant_source_limit(self):
        rows, _ = v.finite_wall_cycle(self.p, 0.1, 1e8, self.protocol)
        accepted = v.model.simulate(replace(self.p, wall_source_ug_s=0.1), protocol=self.protocol)
        self.assertLess(max(abs(v.measured_delta(a)-v.measured_delta(b)) for a, b in zip(rows, accepted)), 0.001)

    def test_mass_balance_with_independent_simpson_integral(self):
        t, source, tau = 100.0, 0.1, 30.0
        rt = v.wall_constants(self.p)[0]
        a = (self.p.exchange_ml_s+self.p.leak_ml_s)/self.p.volume_ml
        b, c = 1/tau, rt*source*1e-9/(self.p.volume_ml*1e-6)
        analytic_y = lambda s: c*(math.exp(-b*s)-math.exp(-a*s))/(a-b)
        n = 2000
        dt = t/n
        integral = dt/3*(analytic_y(0)+analytic_y(t)+sum((4 if i%2 else 2)*analytic_y(i*dt) for i in range(1, n)))
        state = v.integrate_wall((0, 0, source*tau, 0, 0), t, self.p, self.p.leak_ml_s, tau)
        cloth = -self.p.exchange_ml_s*1e-6/rt*1e9*integral
        outlet = self.p.leak_ml_s*1e-6/rt*1e9*integral
        gas = self.p.volume_ml*1e-6*state[0]/rt*1e9
        self.assertAlmostEqual(state[3], cloth, places=8)
        self.assertAlmostEqual(state[4], outlet, places=8)
        self.assertAlmostEqual(gas, cloth+source*tau*(1-math.exp(-t/tau))-outlet, places=8)

    def test_wall_step_convergence_and_instant_sensor(self):
        p = replace(self.p, sensor_tau_s=0)
        start = (0, 0, 3, 0, 0)
        coarse = v.integrate_wall(start, 30, p, 0.02, 30, max_step_s=1)
        fine = v.integrate_wall(start, 30, p, 0.02, 30, max_step_s=0.5)
        rt = v.wall_constants(p)[0]
        a, b = 0.008, 1/30
        exact = (rt*0.1*1e-9/(p.volume_ml*1e-6))*(math.exp(-b*30)-math.exp(-a*30))/(a-b)
        self.assertLess(abs(fine[0]-exact), abs(coarse[0]-exact)/10)
        self.assertEqual(fine[0], fine[1])

    def test_physical_and_readout_domain_failures_without_clipping(self):
        with self.assertRaises(v.OutsideDomain):
            v.finite_wall_cycle(replace(self.p, volume_ml=2), 1, 600, self.protocol)
        raw = v.model.simulate(self.p, protocol=self.protocol)
        original = json.dumps(raw)
        with self.assertRaises(v.OutsideDomain):
            v.biased_rows(raw, pressure_offset=2000)
        self.assertEqual(json.dumps(raw), original)

    def test_extension_rejects_unsupported_and_duplicate_wall_source(self):
        for p in (replace(self.p, wall_source_ug_s=0.1), replace(self.p, noise_rh_pct=0.1),
                  replace(self.p, ambient_step_at_s=50), replace(self.p, local_temp_bias_c=1)):
            with self.assertRaises(ValueError):
                v.finite_wall_cycle(p, 0.1, 120, self.protocol)
        with self.assertRaises(ValueError):
            v.finite_wall_cycle(self.p, 0.1, 0, self.protocol)

    def test_uncertainty_jacobian_and_cross_probe_covariance(self):
        examples = v.uncertainty_examples()
        c = [x["coefficient"] for x in examples["components"]]
        eps = 0.0001
        numerical_t = (v.model.vapor_pa(25+eps, 60)-v.model.vapor_pa(25-eps, 60))/(2*eps)
        numerical_rh = (v.model.vapor_pa(25, 60+eps)-v.model.vapor_pa(25, 60-eps))/(2*eps)
        self.assertAlmostEqual(c[0], numerical_rh, places=6)
        self.assertAlmostEqual(c[1], numerical_t, places=6)
        uncertainties = [row["residual_standard_uncertainty_Pa"] for row in examples["examples"]]
        self.assertAlmostEqual(uncertainties[1], 47.488661553614435, places=10)
        self.assertGreater(uncertainties[0], uncertainties[1])
        self.assertGreater(uncertainties[1], uncertainties[2])
        self.assertAlmostEqual(uncertainties[3], 0, places=6)
        self.assertIsNone(examples["unknown_model_bias"])
        self.assertIsNone(examples["system_accuracy"])

    def test_budget_rejects_double_count_nonresidual_and_invalid_covariance(self):
        a = {"id": "measurement", "kind": "residual_measurement", "coefficient": 1}
        b = {**a, "id": "other"}
        with self.assertRaises(ValueError):
            v.residual_budget([a, a], [[1, 0], [0, 1]])
        for kind in ("explicit_dynamic", "systematic", "unknown_model"):
            with self.assertRaises(ValueError):
                v.residual_budget([{**a, "kind": kind}], [[1]])
        for cov in ([[1, 2], [2, 1]], [[1, 1], [0, 1]], [[-1, 0], [0, 1]], [[1, 0]], [[0, 1], [1, 1]]):
            with self.assertRaises(ValueError):
                v.residual_budget([a, b], cov)

    def test_classifier_cannot_read_synthetic_truth(self):
        rows = v.legal_rows(v.model.simulate(self.p, protocol=self.protocol))
        self.assertTrue(all(set(row) == set(v.OBS_KEYS) for row in rows))
        expected = v.model.assess(rows, protocol=self.protocol)
        poison = [{**row, "delta_e_Pa": 1e99, "synthetic_true_chamber_Pa": -1e99,
                   "surface_activity": 1, "ready_to_collect": True} for row in rows]
        self.assertEqual(v.model.assess(poison, protocol=self.protocol), expected)
        self.assertFalse(expected["collection_authorized"])


class ArtifactTests(unittest.TestCase):
    def test_saved_artifacts_counts_labels_and_no_error_only_classification(self):
        m = json.loads((v.RESULTS/"manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(m["counts"], {"cycle_cases": 70, "complete_cycles": 66,
                                      "not_applicable": 4, "wall_trace_rows": 980,
                                      "error_only_step_cases": 10, "temperature_rows": 21})
        summary = list(csv.DictReader(io.StringIO((v.RESULTS/"cycle-summary.csv").read_text(encoding="utf-8"))))
        self.assertEqual(sum(row["applicability"] == "NOT_APPLICABLE" for row in summary), 4)
        for row in summary:
            self.assertEqual(row["synthetic"], v.SYNTHETIC)
            self.assertEqual(row["collection_authorized"], "False")
            if row["applicability"] == "NOT_APPLICABLE":
                self.assertEqual(row["state"], "")
                self.assertEqual(row["recovery_rise_Pa"], "")
        for name in ("temperature.csv", "response-summary.csv"):
            for row in csv.DictReader(io.StringIO((v.RESULTS/name).read_text(encoding="utf-8"))):
                self.assertEqual(row["assessment"], "")
                self.assertEqual(row["collection_authorized"], "False")

    def test_full_verify_recomputes_without_writes(self):
        files = [p for p in v.RESULTS.iterdir() if p.is_file()]
        before = {p.name: (v.sha_file(p), p.stat().st_mtime_ns) for p in files}
        self.assertEqual(v.verify()["complete_cycles"], 66)
        self.assertEqual(before, {p.name: (v.sha_file(p), p.stat().st_mtime_ns) for p in files})

    def test_verify_detects_stale_source_hash(self):
        original = v.sha_file
        with patch.object(v, "sha_file", side_effect=lambda p: "0"*64 if p == v.HERE/"run.py" else original(p)):
            with self.assertRaisesRegex(ValueError, "Stale or changed"):
                v.verify()

    def test_verify_rejects_changed_result_even_with_updated_output_hash(self):
        # Temporary owned scope only; verify must not repair or overwrite the copy.
        with tempfile.TemporaryDirectory(dir=v.HERE, prefix="test-copy-") as folder:
            dest = Path(folder)
            for p in v.RESULTS.iterdir():
                if p.is_file():
                    shutil.copyfile(p, dest/p.name)
            changed = dest/"temperature.csv"
            changed.write_bytes(changed.read_bytes()+b"\n")
            manifest_path = dest/"manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["output_sha256"][changed.name] = v.sha_file(changed)
            manifest_path.write_text(v.json_text(manifest), encoding="utf-8")
            before = {p.name: (v.sha_file(p), p.stat().st_mtime_ns) for p in dest.iterdir()}
            with self.assertRaisesRegex(ValueError, "Recalculation mismatch"):
                v.verify(dest)
            self.assertEqual(before, {p.name: (v.sha_file(p), p.stat().st_mtime_ns) for p in dest.iterdir()})


if __name__ == "__main__":
    unittest.main(verbosity=2)

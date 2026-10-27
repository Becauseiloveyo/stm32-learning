"""Owned T02 checks; does not record or modify the main project test receipt."""

from dataclasses import replace
import csv
import math
import unittest
from unittest.mock import patch

from research.probe import t02_replay as replay


class T02Checks(unittest.TestCase):
    def test_default_against_preserved_first_round_results(self):
        with (replay.legacy.OUTPUT / "case-summary.csv").open(encoding="utf-8-sig") as handle:
            old = list(csv.DictReader(handle))
        protocol = replay.protocols()["default"]
        for row in old:
            p = replay.case_parameters()[row["case"]]
            metrics, _, _ = replay.compare(p, protocol)
            self.assertAlmostEqual(metrics["recovery_rise_Pa"], float(row["rise_Pa"]), delta=1e-8)
            self.assertAlmostEqual(metrics["tail_delta_Pa"], float(row["tail_Pa"]), delta=1e-8)

    def test_accepted_default_and_nondefault_cross_model_agreement(self):
        for protocol in replay.protocols().values():
            for p in replay.case_parameters().values():
                metrics, observations, _ = replay.compare(p, protocol)
                self.assertEqual(metrics["domain_status"], "within_short_window_domain")
                self.assertLess(metrics["max_true_gap_Pa"], 1e-8)
                self.assertLess(metrics["max_observed_gap_Pa"], 1e-8)
                self.assertFalse(metrics["collection_authorized"])
                self.assertEqual(observations[-1]["time_s"], protocol.total_s)

    def test_fractional_custom_protocol_and_equal_time_constants(self):
        protocol = replace(replay.protocols()["default"], baseline_s=12.5, flush_s=7.5, recovery_s=45,
                           sample_period_s=0.5, head_window_s=3.5, tail_window_s=7.5)
        p = replace(replay.case_parameters()["wet_like_nominal"], sensor_tau_s=125.0,
                    passive_extra_ml_s=0.21, flush_extra_ml_s=0.63)
        metrics, observations, _ = replay.compare(p, protocol)
        self.assertEqual(len(observations), 131)
        self.assertEqual([sum(row["phase"] == stage for row in observations) for stage in ("baseline", "flush", "recovery")], [25, 15, 91])
        self.assertLess(metrics["max_feature_gap_Pa"], 1e-8)

    def test_phase_boundaries_and_windows_come_from_nondefault_protocol(self):
        protocol = replay.protocols()["three_second_long"]
        mapping = replay.protocol_mapping(protocol)
        self.assertEqual(mapping["head_times_s"], [42, 45, 48])
        self.assertEqual(mapping["tail_times_s"], [150, 153, 156, 159, 162])
        self.assertEqual(mapping["phase_sample_counts"], {"baseline": 8, "flush": 6, "recovery": 41})
        _, obs, _ = replay.compare(replay.case_parameters()["wet_like_nominal"], protocol)
        self.assertEqual(next(r["phase"] for r in obs if r["time_s"] == 24), "flush")
        self.assertEqual(next(r["phase"] for r in obs if r["time_s"] == 42), "recovery")
        result = replay.model.assess(obs, protocol=replay.protocols()["default"])
        self.assertIn("protocol_mismatch", result["reasons"])
        self.assertFalse(result["collection_authorized"])

    def test_window_change_changes_feature_without_changing_physics(self):
        original = replay.protocols()["default"]
        changed = replace(original, head_window_s=2, tail_window_s=3)
        p = replay.case_parameters()["wet_like_nominal"]
        a, _, ta = replay.compare(p, original)
        b, _, tb = replay.compare(p, changed)
        self.assertEqual(ta, tb)
        self.assertNotAlmostEqual(a["recovery_rise_Pa"], b["recovery_rise_Pa"], places=3)

    def test_independent_ideal_sensor_closed_form_limit(self):
        p = replace(replay.case_parameters()["wet_like_nominal"], sensor_tau_s=0)
        protocol = replay.protocols()["three_second_long"]
        rows = replay.research_cycle(p, protocol)
        start = next(r["synthetic_true_delta_Pa"] for r in rows if r["time_s"] == protocol.recovery_start_s)
        sat = replay.legacy.saturation_pa(p.temp_c)
        eq = p.exchange_ml_s * sat * (p.surface_activity - p.ambient_rh_pct / 100) / (p.exchange_ml_s + p.leak_ml_s)
        expected = eq + (start - eq) * math.exp(-(p.exchange_ml_s + p.leak_ml_s) / p.volume_ml * protocol.recovery_s)
        self.assertAlmostEqual(rows[-1]["synthetic_observed_delta_Pa"], expected, delta=1e-9)

    def test_source_and_parameter_nonuniqueness_survive_protocol_change(self):
        cases = replay.case_parameters()
        for protocol in replay.protocols().values():
            _, nominal, _ = replay.compare(cases["wet_like_nominal"], protocol)
            for case in ("equilibrium_with_matched_wall", "same_AB_different_parameters"):
                _, other, _ = replay.compare(cases[case], protocol)
                self.assertLess(max(abs(replay.observed_delta(a) - replay.observed_delta(b)) for a, b in zip(nominal, other)), 1e-8)

    def test_leak_and_small_K_cannot_authorize_dryness(self):
        for protocol in replay.protocols().values():
            for case in ("wet_like_large_leak", "small_K_stress"):
                result, _, _ = replay.compare(replay.case_parameters()[case], protocol)
                self.assertFalse(result["collection_authorized"])
            result, _, _ = replay.compare(replay.case_parameters()["wet_like_large_leak"], protocol)
            self.assertEqual(result["state"], "LOW_SIGNAL_UNCONFIRMED")

    def test_whitelist_strips_truth_parameters_and_diagnostic_delta(self):
        protocol = replay.protocols()["default"]
        raw = replay.model.simulate(replay.case_parameters()["wet_like_nominal"], protocol=protocol)
        for row in raw:
            row.update(synthetic_true_chamber_Pa=object(), delta_e_Pa=object(), K=object(), surface_activity=object())
        clean = replay.legal_observations(raw)
        self.assertTrue(all(set(r) == set(replay.OBSERVATION_FIELDS) for r in clean))
        expected = replay.compare(replay.case_parameters()["wet_like_nominal"], protocol)[0]
        result = replay.model.assess(clean, protocol=protocol)
        self.assertEqual(result["state"], expected["state"])
        self.assertEqual(result["recovery_rise_Pa"], expected["recovery_rise_Pa"])
        self.assertFalse(result["collection_authorized"])

    def test_physical_domain_failure_not_assessed_or_clipped(self):
        p = replace(replay.case_parameters()["wet_like_nominal"], volume_ml=2, leak_ml_s=0.005, wall_source_ug_s=0.5)
        for protocol in replay.protocols().values():
            with patch.object(replay.model, "assess", side_effect=AssertionError("must not assess invalid physics")):
                result, obs, trace = replay.compare(p, protocol)
            self.assertEqual(result["domain_status"], "not_applicable")
            self.assertTrue(result["research_domain_error"])
            self.assertTrue(result["main_domain_error"])
            self.assertEqual((obs, trace), ([], []))
            self.assertIsNone(result["recovery_rise_Pa"])

    def test_unsupported_analytic_inputs_are_not_silently_dropped(self):
        p = replay.case_parameters()["wet_like_nominal"]
        for other in (replace(p, noise_rh_pct=0.1), replace(p, local_temp_bias_c=1), replace(p, clip_at_s=30)):
            with self.assertRaises(ValueError):
                replay.research_cycle(other, replay.protocols()["default"])

    def test_saved_evidence_freshness_and_complete_replay(self):
        manifest = replay.verify()
        self.assertEqual(manifest["counts"]["envelope_points"], 270)
        self.assertEqual(manifest["counts"]["representative_cycles"], 16)
        self.assertFalse(manifest["collection_authorized"])

    def test_source_staleness_is_detected_without_editing_files(self):
        with patch.object(replay, "source_fingerprints", return_value={"t02_replay.py": "changed"}):
            with self.assertRaisesRegex(RuntimeError, "Stale"):
                replay.verify()

    def test_output_tampering_is_detected_without_editing_files(self):
        real_hash = replay.sha256_file
        def tampered_hash(path):
            return "changed" if path == replay.OUTPUT / "case-summary.csv" else real_hash(path)
        with patch.object(replay, "sha256_file", side_effect=tampered_hash):
            with self.assertRaisesRegex(RuntimeError, "Output fingerprint"):
                replay.verify()


if __name__ == "__main__":
    unittest.main(verbosity=2)

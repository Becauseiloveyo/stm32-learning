"""Independent mathematical, causal and boundary checks (no hardware claims)."""

import argparse
from dataclasses import asdict, replace
import math
from pathlib import Path
import random
import types
import unittest

from research.energy.model import Config, Network, Request, Scenario, Solar, from_dict, simulate
from research.energy.scenarios import scenarios, service_requests


def case(minutes=60, config=None, requests=(), solar=(), network=()):
    return Scenario("test", minutes, config or Config(), tuple(requests), tuple(solar), tuple(network))


class EnergyTests(unittest.TestCase):
    def assert_accounting(self, source, result):
        cfg = source.config
        for row in result["ledger"]:
            self.assertGreaterEqual(row["store_end_wh"], 0)
            self.assertLessEqual(row["store_end_wh"], cfg.capacity_wh)
            self.assertAlmostEqual(row["balance_residual_wh"], 0, delta=1e-10)
            self.assertAlmostEqual(row["base_requested_load_wh"] + row["action_requested_load_wh"] + row["boot_load_wh"],
                                   row["served_load_wh"] + row["unmet_requested_load_wh"], delta=1e-10)
            self.assertAlmostEqual(row["served_load_wh"] + row["conversion_loss_wh"],
                                   row["served_load_wh"] / cfg.dc_efficiency, delta=1e-10)
        self.assertAlmostEqual(result["summary"]["balance_residual_wh"], 0, delta=1e-9)
        self.assertEqual(len(result["requests"]), len(source.requests))
        for attempt in result["attempts"]:
            self.assertGreaterEqual(attempt["available_above_stop_wh"] + 1e-12, attempt["reserve_battery_wh"])
            if attempt["outcome"] in ("budget_completed", "network_failed"):
                self.assertAlmostEqual(attempt["load_wh"], attempt["paid_load_wh"], delta=1e-12)
        for request in result["requests"]:
            self.assertFalse(request["collection_authorized"])
            self.assertEqual(request["research_state"], "unconfirmed")
            if request["status"] == "budget_completed":
                evidence = [a for a in result["attempts"] if a["event_id"] == request["event_id"]
                            and a["outcome"] == "budget_completed"]
                self.assertEqual(len(evidence), 1)
                self.assertGreaterEqual(evidence[0]["start_minute"], request["at_minute"])
                self.assertLessEqual(evidence[0]["end_minute"], request["deadline_minute"])

    def test_all_scenarios_balance_and_no_unpaid_completion(self):
        for scenario in scenarios():
            for policy in ("fixed", "demand"):
                with self.subTest(scenario=scenario.name, policy=policy):
                    self.assert_accounting(scenario, simulate(scenario, policy))

    def test_analytical_zero_load_and_overflow(self):
        cfg = replace(Config(), running_base_w=0, stopped_base_w=0, initial_wh=3.8)
        source = case(60, cfg, solar=(Solar(0, 60, 1.0),))
        result = simulate(source, "demand")
        self.assertAlmostEqual(result["summary"]["spill_battery_wh"], 0.915)
        self.assertAlmostEqual(result["summary"]["final_wh"], 3.885)
        self.assert_accounting(source, result)

    def test_analytical_baseload_without_solar(self):
        source = case(120)
        result = simulate(source, "demand")
        expected = source.config.initial_wh - source.config.running_base_w * 2 / source.config.dc_efficiency
        self.assertAlmostEqual(result["summary"]["final_wh"], expected)

    def test_zero_store_exposes_background_gap_and_unserved_request(self):
        source = case(10, replace(Config(), initial_wh=0),
                      (Request("m", 0, 10, "measurement", "test"),))
        result = simulate(source, "demand")
        self.assertEqual(result["requests"][0]["status"], "expired")
        self.assertEqual(result["attempts"], [])
        self.assertAlmostEqual(result["summary"]["unmet_requested_load_wh"], source.config.stopped_base_w * 10 / 60)
        self.assertGreater(result["summary"]["energy_blocked_jobs_reference_load_wh"], 0)
        self.assert_accounting(source, result)

    def test_insufficient_wh_never_partially_completes_an_atomic_action(self):
        cfg = replace(Config(), initial_wh=0.0501)
        result = simulate(case(20, cfg, (Request("m", 0, 20, "measurement", "test"),)), "demand")
        self.assertEqual(result["attempts"], [])
        self.assertEqual(result["requests"][0]["status"], "expired")
        self.assertIn("insufficient_energy", result["requests"][0]["block_reasons"])

    def test_exact_admission_boundary_includes_background(self):
        cfg = Config()
        exact = cfg.stop_wh + (cfg.measurement_load_wh + cfg.running_base_w * cfg.measurement_minutes / 60) / cfg.dc_efficiency
        request = Request("m", 0, 5, "measurement", "test")
        accepted = simulate(case(5, replace(cfg, initial_wh=exact), (request,)), "demand")
        denied = simulate(case(5, replace(cfg, initial_wh=exact - 1e-9), (request,)), "demand")
        self.assertEqual(accepted["requests"][0]["status"], "budget_completed")
        self.assertAlmostEqual(accepted["summary"]["final_wh"], cfg.stop_wh)
        self.assertEqual(denied["attempts"], [])

    def test_sun_cannot_fund_an_action_before_it_arrives(self):
        source = case(60, replace(Config(), initial_wh=0),
                      (Request("early", 0, 20, "measurement", "test"),),
                      (Solar(30, 60, 2.0),))
        result = simulate(source, "demand")
        self.assertEqual(result["requests"][0]["status"], "expired")
        self.assertEqual(result["attempts"], [])
        self.assertGreater(result["summary"]["final_wh"], 0.5)

    def test_deadline_completion_boundary_and_too_short_window(self):
        for deadline, expected in ((5, "budget_completed"), (4, "expired")):
            result = simulate(case(10, requests=(Request("m", 0, deadline, "measurement", "test"),)), "demand")
            self.assertEqual(result["requests"][0]["status"], expected)

    def test_failed_wifi_cost_backoff_and_retry_limit(self):
        source = case(120, requests=(Request("w", 0, 120, "wifi", "test"),),
                      network=(Network(0, 120, False),))
        result = simulate(source, "demand")
        self.assertEqual([a["start_minute"] for a in result["attempts"]], [0, 2, 8, 24, 85])
        self.assertEqual(result["requests"][0]["status"], "retry_exhausted")
        self.assertAlmostEqual(sum(a["paid_load_wh"] for a in result["attempts"]), 5 * source.config.wifi_timeout_load_wh)
        self.assert_accounting(source, result)

    def test_wifi_daily_cap_survives_day_boundary(self):
        cfg = replace(Config(), wifi_daily_battery_limit_wh=0.004)
        source = case(1500, cfg, (Request("w", 1430, 1500, "wifi", "test"),), network=(Network(0, 1500, False),))
        result = simulate(source, "demand")
        self.assertEqual([a["start_minute"] for a in result["attempts"]], [1430, 1440])
        self.assertTrue(all(e <= cfg.wifi_daily_battery_limit_wh + 1e-12 for e in result["wifi_daily_battery_wh"].values()))
        self.assertEqual(result["requests"][0]["status"], "expired")
        self.assertIn("wifi_daily_budget", result["requests"][0]["block_reasons"])

    def test_network_recovers_without_unpaid_ack(self):
        source = case(30, requests=(Request("w", 0, 30, "wifi", "test"),), network=(Network(0, 5, False),))
        result = simulate(source, "demand")
        self.assertEqual([a["outcome"] for a in result["attempts"]], ["network_failed", "network_failed", "budget_completed"])
        self.assertEqual(result["requests"][0]["finished_minute"], 9)
        self.assertAlmostEqual(sum(a["paid_load_wh"] for a in result["attempts"]),
                               2 * source.config.wifi_timeout_load_wh + source.config.wifi_success_load_wh)

    def test_hysteresis_weak_pulses_no_boot_loop(self):
        result = simulate(scenarios()[-1], "demand")
        boots = [t for t in result["transitions"] if t["reason"] == "paid_restart"]
        self.assertEqual(len(boots), 1)
        self.assertGreater(boots[0]["minute"], 240)
        self.assertTrue(all(r["mode_during"] == "stopped" for r in result["ledger"][:240]))
        self.assertAlmostEqual(sum(r["boot_load_wh"] for r in result["ledger"]), Config().boot_load_wh)

    def test_recovery_creates_service_only_after_paid_restart(self):
        result = simulate(scenarios()[3], "demand")
        first_boot = next(t["minute"] for t in result["transitions"] if t["reason"] == "paid_restart")
        self.assertTrue(all(a["start_minute"] >= first_boot for a in result["attempts"]))
        self.assertGreater(result["summary"]["no_budget_result_services"], 0)
        self.assertGreater(result["summary"]["budget_completed_services"], 0)

    def test_future_events_weather_network_cannot_change_prefix(self):
        first = case(100, requests=(Request("m", 3, 33, "measurement", "test"),))
        changed = replace(first, requests=first.requests + (Request("future", 70, 100, "measurement", "test"),),
                          solar=(Solar(60, 100, 1.0),), network=(Network(60, 100, False),))
        for policy in ("fixed", "demand"):
            self.assertEqual(simulate(first, policy)["ledger"][:60], simulate(changed, policy)["ledger"][:60])

    def test_fixed_and_demand_share_identical_external_requests(self):
        source = scenarios()[0]
        a, b = simulate(source, "fixed"), simulate(source, "demand")
        self.assertEqual({r["event_id"] for r in a["requests"]}, {r["event_id"] for r in b["requests"]})
        self.assertEqual(a["summary"]["no_budget_result_services"], 0)
        self.assertEqual(b["summary"]["no_budget_result_services"], 0)
        self.assertGreater(a["summary"]["measurement_cycles"], b["summary"]["measurement_cycles"])
        self.assertGreater(a["summary"]["battery_consumed_wh"], b["summary"]["battery_consumed_wh"])
        self.assertGreater(a["summary"]["successful_wait_p95_min"], b["summary"]["successful_wait_p95_min"])

    def test_low_priority_shed_never_changes_evidence_state(self):
        requests = (Request("m", 0, 10, "measurement", "test"),
                    Request("w", 6, 20, "wifi", "test", False))
        result = simulate(case(20, replace(Config(), initial_wh=0.1), requests), "demand")
        self.assertEqual(result["requests"][0]["status"], "budget_completed")
        self.assertEqual(result["requests"][1]["status"], "expired")
        self.assertIn("low_priority_shed", result["requests"][1]["block_reasons"])
        self.assertTrue(all(not r["collection_authorized"] for r in result["requests"]))

    def test_multiple_sessions_do_not_share_a_measurement_result(self):
        requests = (Request("a", 0, 10, "measurement", "test", session_id="A"),
                    Request("b", 0, 10, "measurement", "test", session_id="B"))
        result = simulate(case(10, requests=requests), "demand")
        self.assertEqual([r["finished_minute"] for r in result["requests"]], [5, 10])
        self.assertEqual([a["event_id"] for a in result["attempts"]], ["a", "b"])
        self.assertEqual(result["summary"]["measurement_budget_completed_requests"], 2)

    def test_minute_of_arrival_cannot_reuse_already_running_cycle(self):
        source = case(20, requests=(Request("m", 1, 20, "measurement", "test"),))
        result = simulate(source, "fixed")
        self.assertEqual(result["requests"][0]["finished_minute"], 15)
        self.assertEqual(result["attempts"][0]["event_id"], "")

    def test_low_mode_recovers_only_at_exit_threshold(self):
        cfg = replace(Config(), initial_wh=0.31, running_base_w=0.06, stopped_base_w=0)
        source = case(60, cfg, solar=(Solar(20, 30, 0.3), Solar(40, 50, 1.2)))
        result = simulate(source, "demand")
        changes = [(r["from"], r["to"], r["minute"]) for r in result["transitions"]]
        self.assertEqual([(a, b) for a, b, _ in changes], [("normal", "low"), ("low", "normal")])
        self.assertTrue(all(r["mode_during"] == "low" for r in result["ledger"][20:40]))

    def test_timeout_reservation_does_not_use_future_success_as_oracle(self):
        cfg = Config()
        enough_for_success_only = cfg.stop_wh + (cfg.wifi_success_load_wh + cfg.running_base_w / 60) / cfg.dc_efficiency
        result = simulate(case(10, replace(cfg, initial_wh=enough_for_success_only),
                               (Request("w", 0, 10, "wifi", "test"),)), "demand")
        self.assertEqual(result["attempts"], [])
        self.assertEqual(result["requests"][0]["status"], "expired")

    def test_reject_invalid_inputs_and_duplicate_ids(self):
        bad_configs = ({"initial_wh": -1}, {"dc_efficiency": 0}, {"dc_efficiency": 1.01},
                       {"running_base_w": math.nan}, {"capacity_wh": math.inf},
                       {"initial_wh": 10}, {"restart_wh": 0.05}, {"measurement_minutes": 0},
                       {"max_wifi_attempts": 1.5}, {"measurement_minutes": 5.0},
                       {"boot_load_wh": 1}, {"retry_minutes": (0,)})
        for change in bad_configs:
            with self.subTest(change=change), self.assertRaises(ValueError):
                simulate(case(config=replace(Config(), **change)), "demand")
        request = Request("same", 0, 10, "measurement", "test")
        for source in (case(requests=(request, request)),
                       case(solar=(Solar(10, 30, 1), Solar(20, 40, 1))),
                       case(solar=(Solar(0, 61, 1),)),
                       case(network=(Network(0, 10, "false"),)),
                       case(requests=(replace(request, kind="authorize_collection"),))):
            with self.assertRaises(ValueError):
                simulate(source, "demand")

    def test_json_roundtrip_deterministic(self):
        source = case(100, requests=service_requests(100))
        self.assertEqual(simulate(source, "demand"), simulate(from_dict(asdict(source)), "demand"))

    def test_frozen_baseline_daily_math_without_margin_rounding(self):
        path = Path(__file__).resolve().parents[2] / "docs/team/baseline-v1/analysis/energy_budget.py"
        # Execute the read-only frozen text without creating a baseline __pycache__.
        module = types.ModuleType("frozen_energy_budget")
        exec(compile(path.read_text(encoding="utf-8-sig"), str(path), "exec"), module.__dict__)
        args = argparse.Namespace(base_mw=1, fan_watts=0.2, fan_seconds=20, vent_joules=0.1,
                                  wifi_watts=0.6, wifi_seconds=6, wifi_sessions=28, misc_wh=0.004,
                                  dc_efficiency=0.85, margin=1.3, round_step_wh=0.05, panel_watts=2,
                                  sun_hours=0.25, harvest_efficiency=0.6, battery_volts=3.7,
                                  battery_mah=1500, usable_fraction=0.7, dark_days=7, recovery_sun_hours=1)
        # Explicit 36 measurements and 28 successful network jobs, all finish that day.
        requests = tuple(Request(f"m{i}", i * 40, i * 40 + 10, "measurement", "test") for i in range(36))
        requests += tuple(Request(f"w{i}", 7 + i * 50, 17 + i * 50, "wifi", "test") for i in range(28))
        for policy, cycles in (("demand", 36), ("fixed", 144)):
            result = simulate(case(1440, requests=requests), policy)
            expected = module.budget(args, cycles)
            self.assertEqual(result["summary"]["no_budget_result_services"], 0)
            self.assertEqual(result["summary"]["measurement_cycles"], cycles)
            self.assertAlmostEqual(result["summary"]["served_load_wh"], expected["load_Wh_per_day"])
            self.assertAlmostEqual(result["summary"]["battery_consumed_wh"], expected["battery_Wh_per_day_before_margin"])

    def test_seeded_boundary_sweep(self):
        rng = random.Random(20261023)
        for _ in range(40):
            cfg = replace(Config(), initial_wh=rng.uniform(0, 3.885), dc_efficiency=rng.uniform(0.3, 1),
                          running_base_w=rng.uniform(0.001, 0.03), measurement_load_wh=rng.uniform(0, 0.05))
            source = case(120, cfg, service_requests(120),
                          (Solar(30, 90, rng.uniform(0, 0.3)),), (Network(0, 50, False),))
            for policy in ("fixed", "demand"):
                self.assert_accounting(source, simulate(source, policy))


if __name__ == "__main__":
    unittest.main()

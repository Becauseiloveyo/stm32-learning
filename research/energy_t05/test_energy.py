"""Mathematical and integration boundary checks; no upstream test receipts written."""
import sys
sys.dont_write_bytecode = True

import argparse
from dataclasses import asdict, replace
import math
from pathlib import Path
import random
import types
import unittest

from research.state_machine.machine import Configuration, DEFAULT_CONFIG, replay
from research.energy_t05.contracts import (Energy, Request, Installation, Solar, Network, ObservationCondition,
                                          Scenario, from_dict, measurement_components, required_battery_wh)
from research.energy_t05.model import Scheduler, simulate
from research.energy_t05.scenarios import scenarios
from theory.model import Parameters, assess


def case(duration=600, **kwargs):
    return Scenario("test", duration, installations=(Installation(1, "A", "I"),), **kwargs)


def request(at=40, deadline=590, kind="measurement", id="r", batch="A"):
    return Request(id, at, deadline, batch, kind)


class IntegratedEnergyTests(unittest.TestCase):
    def check_accounting(self, source, result):
        e, protocol = source.energy, source.validate().protocol
        self.assertAlmostEqual(result["summary"]["balance_residual_wh"], 0, delta=1e-9)
        last_end, last_store = 0, e.initial_wh
        for row in result["ledger"]:
            self.assertEqual(row["start_s"], last_end)
            self.assertAlmostEqual(row["store_start_wh"], last_store, delta=1e-10)
            self.assertGreaterEqual(row["store_end_wh"], 0)
            self.assertLessEqual(row["store_end_wh"], e.capacity_wh)
            self.assertAlmostEqual(row["balance_residual_wh"], 0, delta=1e-9)
            self.assertAlmostEqual(sum(row["requested_load_wh"].values()),
                                   sum(row["served_load_wh"].values()) + row["unmet_load_wh"], delta=1e-10)
            last_end, last_store = row["end_s"], row["store_end_wh"]
        self.assertEqual(last_end, source.duration_s)
        self.assertEqual(len(result["requests"]), len(source.requests))
        paid_actions = sum(sum(a["paid_components_wh"].values()) for a in result["actions"])
        physical_actions = sum(sum(v for k, v in row["served_load_wh"].items()
                                   if k not in ("background_run", "background_stop", "misc", "boot"))
                               for row in result["ledger"])
        self.assertAlmostEqual(paid_actions, physical_actions, delta=1e-10)
        for action in result["actions"]:
            self.assertGreaterEqual(action["available_above_stop_wh"] + 1e-12, action["reserved_battery_wh"])
            if action["outcome"] == "budget_completed":
                self.assertEqual(action["ended_s"] - action["started_s"], protocol.total_s)
                for component, wh in measurement_components(e, protocol).items():
                    self.assertAlmostEqual(action["paid_components_wh"].get(component, 0), wh, delta=1e-12)
            self.assertFalse(action["collection_authorized"])
        self.assertTrue(all(x["configuration_sha256"] == source.validate().sha256 for x in result["actions"]))
        self.assertTrue(all(not x["collection_authorized"] for x in result["requests"]))

    def test_all_builtin_scenarios_conservation_and_paid_actions(self):
        for source in scenarios():
            for policy in ("fixed", "demand"):
                with self.subTest(source=source.name, policy=policy):
                    self.check_accounting(source, simulate(source, policy))

    def test_complete_protocol_and_true_assess(self):
        source = case(requests=(request(),))
        result = simulate(source, "demand")
        finish = next(e for e in result["events"] if e["event"]["kind"] == "finish_cycle")
        config = source.validate()
        raw = finish["event"]["observations"]
        self.assertEqual(raw[-1]["time_s"], config.protocol.total_s)
        self.assertEqual(len(raw), 141)
        self.assertEqual(finish["output"]["assessment"], assess(raw, config.gates, protocol=config.protocol))
        self.assertEqual({r["phase"] for r in raw}, {"baseline", "flush", "recovery"})
        self.check_accounting(source, result)

    def test_real_event_replay_strict_time_and_config(self):
        source = scenarios()[5]
        result = simulate(source, "fixed")
        events = [r["event"] for r in result["events"]]
        self.assertEqual(replay(events, source.validate()), [r["output"] for r in result["events"]])
        last = {}
        for e in events:
            self.assertGreater(e["monotonic_s"], last.get(e["boot_id"], -1))
            last[e["boot_id"]] = e["monotonic_s"]
            self.assertEqual(e["config_sha256"], source.validate().sha256)
        self.assertIn("recover", [e["kind"] for e in events])
        self.assertFalse(any(e["kind"] == "configure" for e in events))

    def test_analytical_idle_and_full_store_spill(self):
        e = replace(Energy(), initial_wh=3.8, base_w=0, misc_w=0, stopped_w=0, boot_wh=0)
        source = case(3600, energy=e, solar=(Solar(0, 3600, 1.0),))
        r = simulate(source, "demand")
        self.assertAlmostEqual(r["summary"]["spill_wh"], 0.915, places=9)
        self.assertEqual(r["summary"]["final_wh"], e.capacity_wh)
        self.check_accounting(source, r)

    def test_analytical_idle_baseload(self):
        source = case(3600)
        r = simulate(source, "demand")
        expected = (source.energy.base_w + source.energy.misc_w + source.energy.boot_wh) / source.energy.eta
        self.assertAlmostEqual(r["summary"]["battery_consumed_wh"], expected, places=12)
        self.assertAlmostEqual(r["summary"]["final_wh"], source.energy.initial_wh - expected, places=9)

    def test_zero_store_gap_no_fabricated_completion(self):
        source = case(600, energy=replace(Energy(), initial_wh=0), requests=(request(),))
        r = simulate(source, "demand")
        self.assertEqual(r["actions"], [])
        self.assertEqual(r["requests"][0]["status"], "missed_offline")
        self.assertAlmostEqual(r["summary"]["actual_unmet_load_wh"], source.energy.stopped_w * 600 / 3600)
        self.check_accounting(source, r)

    def test_admission_equality_and_just_below(self):
        # Test admission after an explicit boot/install, isolating the mathematical boundary.
        source = case(requests=(request(at=31, deadline=171),))
        for offset, expected in ((0, True), (-1e-9, False)):
            runner = Scheduler(source, "demand")
            runner.boot_or_stop(0)
            runner.install(1, source.installations)
            runner.arrive(31, source.requests)
            runner.store = runner.e.stop_wh + required_battery_wh(runner.e, runner.config.protocol, "measurement") + offset
            runner.schedule(31)
            self.assertEqual(runner.active is not None, expected)

    def test_equal_budget_finishes_before_orderly_stop(self):
        # Full initialization plus waiting load; start at 40, finish at 180.
        base = Energy()
        initial = base.stop_wh + required_battery_wh(base, DEFAULT_CONFIG.protocol, "measurement") + base.boot_wh / base.eta + (base.base_w + base.misc_w) * 40 / 3600 / base.eta
        e = replace(base, initial_wh=initial, restart_wh=0.053, low_enter_wh=0.06, low_exit_wh=0.08)
        source = case(300, energy=e, requests=(request(deadline=180),))
        r = simulate(source, "demand")
        self.assertTrue(r["requests"][0]["budget_completed"])
        self.assertEqual(r["requests"][0]["finished_s"], 180)

    def test_finite_deadline_and_horizon_censoring(self):
        source = case(200, requests=(request(at=40, deadline=179, id="expires"),
                                     request(at=180, deadline=500, id="censored")))
        r = simulate(source, "demand")
        self.assertEqual([x["status"] for x in r["requests"]], ["expired", "horizon_censored"])
        self.assertEqual([x["wait_s"] for x in r["requests"]], [139, 20])
        self.assertEqual(r["summary"]["measurement_unfinished_followup_sum_s"], 180)
        self.assertEqual(r["actions"], [])

    def test_install_wait_and_minimum_cycle_gap(self):
        source = case(600, requests=(request(at=2, id="first"), request(at=3, id="second")))
        r = simulate(source, "demand")
        self.assertEqual([a["started_s"] for a in r["actions"]], [31, 201])
        self.assertEqual([a["ended_s"] for a in r["actions"]], [171, 341])

    def test_batch_abort_closes_once_and_preserves_reserved_window(self):
        source = scenarios()[4]
        r = simulate(source, "demand")
        old, new = r["actions"][:2]
        self.assertEqual(old["outcome"], "cancelled_installation")
        self.assertEqual(old["ended_s"], 100)
        self.assertAlmostEqual(old["paid_components_wh"]["vent_open"], .05 / 3600)
        self.assertAlmostEqual(old["paid_components_wh"]["vent_close"], .05 / 3600)
        self.assertGreaterEqual(new["started_s"], old["planned_end_s"] + source.validate().policy.min_cycle_gap_s)
        self.assertEqual(new["batch_id"], "batch-B")
        self.assertEqual(next(x for x in r["requests"] if x["id"] == "old-late")["status"], "unavailable_installation")
        finishes = [e["event"]["cycle_id"] for e in r["events"] if e["event"]["kind"] == "finish_cycle"]
        self.assertNotIn(old["cycle_id"], finishes)
        self.check_accounting(source, r)

    def test_abort_before_open_and_after_close_no_extra_exit_charge(self):
        for at, expected_open, expected_close in ((50, 0, 0), (100, .05, .05), (130, .05, .05)):
            source = replace(case(500, requests=(request(at=40),)),
                             installations=(Installation(1, "A", "I"), Installation(at, "B", "J")))
            r = simulate(source, "demand")
            cost = r["actions"][0]["paid_components_wh"]
            self.assertAlmostEqual(cost.get("vent_open", 0) * 3600, expected_open)
            self.assertAlmostEqual(cost.get("vent_close", 0) * 3600, expected_close)

    def test_install_at_completion_takes_priority_over_old_finish(self):
        source = replace(case(500, requests=(request(at=40),)),
                         installations=(Installation(1, "A", "I"), Installation(180, "B", "J")))
        r = simulate(source, "demand")
        self.assertFalse(r["requests"][0]["budget_completed"])
        self.assertFalse(any(e["event"]["kind"] == "finish_cycle" for e in r["events"]))

    def test_window_reservation_survives_new_boot(self):
        source = case(requests=(request(at=31),))
        runner = Scheduler(source, "demand")
        runner.boot_or_stop(0)
        runner.install(1, source.installations)
        runner.arrive(31, source.requests)
        runner.schedule(31)
        runner.invalidate(40, "test_orderly_stop")
        runner.powered = False
        runner.boot_or_stop(41)
        runner.install(42, (Installation(42, "B", "J"),))
        runner.maintain_clock(72)
        self.assertEqual(runner.eligibility(73, "measurement", None), "reserved_window_or_min_gap")
        self.assertEqual(runner.eligibility(201, "measurement", None), "")

    def test_wifi_failure_paid_backoff_and_attempt_limit(self):
        source = case(6000, requests=(request(at=40, deadline=5900, kind="wifi"),), network=(Network(0, 6000, False),))
        r = simulate(source, "demand")
        self.assertEqual([a["started_s"] for a in r["actions"]], [40, 120, 440, 1360, 4980])
        self.assertEqual(r["requests"][0]["status"], "retry_exhausted")
        self.assertAlmostEqual(r["summary"]["wifi_timeout_load_wh"], .6 * 20 * 5 / 3600)
        self.check_accounting(source, r)

    def test_wifi_recovery_is_not_known_at_admission(self):
        source = case(1200, requests=(request(at=40, deadline=1000, kind="wifi"),), network=(Network(0, 100, False),))
        r = simulate(source, "demand")
        self.assertEqual([a["outcome"] for a in r["actions"]], ["network_timeout", "network_confirmed"])
        self.assertEqual(r["requests"][0]["finished_s"], 126)
        self.assertAlmostEqual(r["summary"]["wifi_success_load_wh"], .6 * 6 / 3600)
        e = replace(Energy(), initial_wh=.16, wifi_timeout_s=1200)
        insufficient = simulate(case(2000, energy=e, requests=(request(at=40, deadline=1900, kind="wifi"),)), "demand")
        self.assertEqual(insufficient["actions"], [])  # Cannot reserve only the lucky 6-second outcome.

    def test_wifi_budget_cross_midnight_uses_both_days(self):
        source = case(87000, energy=replace(Energy(), wifi_daily_wh=0.002),
                      requests=(request(at=86390, deadline=86900, kind="wifi"),), network=(Network(0, 87000, False),))
        r = simulate(source, "demand")
        self.assertEqual(len(r["actions"]), 1)
        self.assertTrue(all(wh <= .002 + 1e-12 for wh in r["wifi_daily_battery_wh"].values()))
        self.assertEqual(set(r["wifi_daily_battery_wh"]), {0, 1})
        self.assertIn("wifi_daily_budget", r["requests"][0]["blocked_reasons"])

    def test_raw_unknown_is_paid_but_cannot_count_weak(self):
        source = scenarios()[5]
        r = simulate(source, "demand")
        first = r["actions"][0]
        self.assertEqual(first["outcome"], "budget_completed")
        self.assertEqual(first["assessment"]["state"], "UNKNOWN")
        self.assertFalse(first["quality_valid"])
        self.assertEqual(first["weak_cycles_after"], 0)
        self.assertEqual(r["requests"][0]["status"], "quality_invalid")

    def test_fixed_demand_preserve_weak_chain_expiry_tradeoff(self):
        source = scenarios()[0]
        fixed, demand = simulate(source, "fixed"), simulate(source, "demand")
        self.assertEqual(fixed["summary"]["measurement_served_requests"], demand["summary"]["measurement_served_requests"])
        self.assertGreater(fixed["summary"]["max_weak_chain"], 3)
        self.assertEqual(demand["summary"]["max_weak_chain"], 1)
        self.assertGreater(demand["summary"]["idle_evidence_expirations"], 0)
        self.assertEqual(demand["summary"]["independent_review_due_finishes"], 0)
        self.assertGreater(fixed["summary"]["independent_review_due_finishes"], 0)
        self.assertEqual(fixed["configuration_sha256"], demand["configuration_sha256"])

    def test_idle_expiry_at_final_boundary_is_not_lost(self):
        for horizon, weak, expired in ((1980, 1, 0), (1981, 0, 1)):
            result = simulate(case(horizon, requests=(request(),)), "demand")
            self.assertEqual(result["final_machine"]["weak_cycles"], weak)
            self.assertEqual(result["summary"]["idle_evidence_expirations"], expired)

    def test_stop_reboot_requires_fresh_observed_installation(self):
        source = scenarios()[3]
        r = simulate(source, "demand")
        self.assertEqual(r["summary"]["paid_boots"], 2)
        self.assertEqual(r["installations"][1]["status"], "missed_offline")
        self.assertEqual(r["installations"][2]["status"], "applied")
        new_boot = [e for e in r["events"] if e["event"]["kind"] == "boot"][-1]
        self.assertEqual(new_boot["output"]["weak_cycles"], 0)
        self.assertTrue(all(a["started_s"] >= 32430 for a in r["actions"] if a["boot_id"] == "boot-2"))
        self.assertFalse(any(e["event"]["kind"] == "boot" and 25200 <= e["wall_s"] < 28800 for e in r["events"]))

    def test_future_environment_requests_observations_do_not_change_prefix(self):
        source = case(1200, requests=(request(at=40),))
        altered = replace(source, requests=source.requests + (request(at=800, deadline=1200, id="future"),),
                          solar=(Solar(700, 1200, .1),), network=(Network(700, 1200, False),),
                          observations=(ObservationCondition(700, 1200, asdict(Parameters(invalid_at_s=60, noise_rh_pct=0))),))
        for policy in ("fixed", "demand"):
            a, b = simulate(source, policy), simulate(altered, policy)
            self.assertEqual([e for e in a["events"] if e["wall_s"] < 700], [e for e in b["events"] if e["wall_s"] < 700])
            keys = ("id", "kind", "started_s", "request_id", "reserved_battery_wh", "available_above_stop_wh")
            self.assertEqual([{k:x[k] for k in keys} for x in a["actions"] if x["started_s"] < 700],
                             [{k:x[k] for k in keys} for x in b["actions"] if x["started_s"] < 700])

    def test_solar_after_deadline_cannot_retroactively_complete(self):
        source = case(1000, energy=replace(Energy(), initial_wh=0), requests=(request(at=40, deadline=300),),
                      solar=(Solar(500, 1000, 2),))
        r = simulate(source, "demand")
        self.assertEqual(r["actions"], [])
        self.assertFalse(r["requests"][0]["budget_completed"])

    def test_initial_same_second_control_has_explicit_one_second_delay(self):
        source = replace(case(100), installations=(Installation(0, "A", "I"),))
        r = simulate(source, "demand")
        self.assertEqual(r["installations"][0]["observed_s"], 0)
        self.assertEqual(r["installations"][0]["applied_s"], 1)
        self.assertEqual(r["events"][0]["wall_s"], 0)
        self.assertEqual(r["events"][1]["wall_s"], 1)

    def test_unsupported_grid_invalid_values_and_duplicates_rejected(self):
        for change in ({"eta": 0}, {"initial_wh": -1}, {"fan_w": math.nan}, {"wifi_w": math.inf},
                       {"restart_wh": .05}, {"wifi_success_s": 21}, {"tick_s": 3600}, {"backoff_s": (0,)}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                simulate(case(energy=replace(Energy(), **change)), "demand")
        for source in (case(requests=(request(), request())), case(solar=(Solar(0, 400, 1), Solar(300, 500, 1))),
                       case(network=(Network(0, 600, "false"),)),
                       replace(case(), configuration=replace(DEFAULT_CONFIG, protocol=replace(DEFAULT_CONFIG.protocol, sample_period_s=.5)).snapshot())):
            with self.assertRaises(ValueError):
                simulate(source, "demand")

    def test_input_roundtrip_preserves_execution(self):
        source = case(requests=(request(),))
        self.assertEqual(simulate(source, "demand"), simulate(from_dict(asdict(source)), "demand"))

    def test_daily_baseline_crosscheck_excludes_explicit_new_overheads(self):
        path = Path(__file__).resolve().parents[2] / "analysis/energy_budget.py"
        baseline = types.ModuleType("read_only_budget")
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), baseline.__dict__)
        args = argparse.Namespace(base_mw=1, fan_watts=.2, fan_seconds=20, vent_joules=.1,
                                  wifi_watts=.6, wifi_seconds=6, wifi_sessions=28, misc_wh=.004,
                                  dc_efficiency=.85, margin=1.3, round_step_wh=.05, panel_watts=2,
                                  sun_hours=.25, harvest_efficiency=.6, battery_volts=3.7,
                                  battery_mah=1500, usable_fraction=.7, dark_days=7, recovery_sun_hours=1)
        requests = tuple(request(at=63+2400*i, deadline=63+2400*i+1800, id=f"m{i}") for i in range(36))
        requests += tuple(request(at=300+3000*i, deadline=300+3000*i+600, id=f"w{i}", kind="wifi") for i in range(28))
        e = replace(Energy(), boot_wh=0, baseline_w=0, flush_w=0, recovery_w=0)
        source = case(86400, energy=e, requests=requests)
        for policy, cycles in (("fixed", 144), ("demand", 36)):
            result = simulate(source, policy)
            expected = baseline.budget(args, cycles)
            self.assertEqual(result["summary"]["measurement_budget_completed"], cycles)
            self.assertEqual(result["summary"]["wifi_confirmed"], 28)
            self.assertAlmostEqual(result["summary"]["battery_consumed_wh"], expected["battery_Wh_per_day_before_margin"], places=10)

    def test_seeded_parameter_boundaries(self):
        rng = random.Random(20261025)
        for i in range(20):
            e = replace(Energy(), initial_wh=rng.uniform(0, 3.885), eta=rng.uniform(.3, 1),
                        fan_w=rng.uniform(0, 2), base_w=rng.uniform(.001, .03))
            source = case(1200, energy=e, requests=(request(), request(at=300, deadline=1100, id="wifi", kind="wifi")),
                          solar=(Solar(400, 900, rng.uniform(0, .3)),), network=(Network(0, 500, False),))
            for policy in ("fixed", "demand"):
                with self.subTest(index=i, policy=policy):
                    self.check_accounting(source, simulate(source, policy))


if __name__ == "__main__":
    unittest.main()

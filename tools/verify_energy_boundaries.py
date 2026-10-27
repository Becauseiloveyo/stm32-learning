"""Coordinator public-API checks; independent inputs and analytic energy oracles.

Read-only, synthetic, no hardware. Does not import the worker's tests/scenarios.
"""
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.energy_t05.contracts import Energy, Scenario, Request, Installation, Solar, Network
from research.energy_t05.model import simulate
from research.state_machine.machine import Configuration, StateMachine
from theory.model import Parameters


def main():
    labels, cases = [], []

    def source_hashes():
        sources = [Path(__file__), ROOT/'research/state_machine/machine.py', ROOT/'theory/model.py', ROOT/'theory/protocol.py']
        sources += list((ROOT/'research/energy_t05').glob('*.py'))
        return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(sources)}

    before_sources = source_hashes()

    def check(label, value):
        assert value, label
        labels.append(label)

    def near(a, b):
        return abs(a - b) <= 1e-10

    def run(scenario, policy="demand"):
        result = simulate(scenario, policy)
        # Independently reconcile all interval and total amounts; replay real events.
        machine = StateMachine(Configuration.from_snapshot(scenario.configuration))
        for row in result["ledger"]:
            paid = sum(row["served_load_wh"].values())
            assert near(sum(row["requested_load_wh"].values()), paid + row["unmet_load_wh"])
            assert near(row["store_start_wh"] + row["solar_battery_wh"],
                        row["store_end_wh"] + paid / scenario.energy.eta + row["spill_wh"])
            assert near(row["conversion_loss_wh"], paid / scenario.energy.eta - paid)
            assert 0 <= row["store_end_wh"] <= scenario.energy.capacity_wh
        for item in result["events"]:
            assert machine.handle(item["event"]) == item["output"]
            assert item["output"]["collection_authorized"] is False
        assert machine.snapshot() == result["final_machine"]
        for a in result["actions"]:
            assert a["reserved_battery_wh"] <= a["available_above_stop_wh"] + 1e-12
            assert a["collection_authorized"] is False
            paid = sum(sum(v for k, v in r["served_load_wh"].items()
                           if k not in ("background_run", "background_stop", "misc"))
                       for r in result["ledger"] if r["action_id"] == a["id"])
            assert near(paid, sum(a["paid_components_wh"].values()))
        s = result["summary"]
        assert near(s["initial_wh"] + s["solar_wh"], s["final_wh"] + s["spill_wh"] + s["battery_consumed_wh"])
        assert all(r["collection_authorized"] is False for r in result["requests"])
        cases.append({"name": scenario.name, "policy": policy, "actions": len(result["actions"]),
                      "events": len(result["events"]), "ledger_intervals": len(result["ledger"])})
        return result

    energy = Energy(capacity_wh=1, initial_wh=1, eta=.5, base_w=.001, misc_w=0, boot_wh=0)
    idle = run(Scenario("analytic_idle", 3600, energy=energy))
    check("one-hour background has analytic battery consumption", near(idle["summary"]["battery_consumed_wh"], .002))
    check("idle final reserve", near(idle["summary"]["final_wh"], .998))
    full = run(Scenario("analytic_spill", 3600, energy=energy, solar=(Solar(0,3600,.1),)))
    check("full reserve spills harvest after supplying load", near(full["summary"]["spill_wh"], .098) and near(full["summary"]["final_wh"], 1))
    empty = run(Scenario("analytic_empty", 3600, energy=replace(energy, initial_wh=0)))
    check("empty reserve exposes unpaid stopped background", near(empty["summary"]["actual_unmet_load_wh"], .00005))
    check("empty reserve does not invent paid actions", not empty["actions"] and near(empty["summary"]["battery_consumed_wh"],0))

    installation = (Installation(1,"b","i"),)
    one = Scenario("one_cycle", 500, installations=installation, requests=(Request("r",40,400,"b"),))
    result = run(one)
    a = result["actions"][0]
    check("full protocol executes 140 seconds", a["ended_s"]-a["started_s"] == 140)
    check("stage and actuator integral is 4.38 joules", near(sum(a["paid_components_wh"].values()), 4.38/3600))
    check("one cycle yields one unconfirmed weak record", a["quality_valid"] and a["weak_cycles_after"]==1 and a["assessment"]["state"]=="LOW_SIGNAL_UNCONFIRMED")
    invalid = run(replace(one, name="quality_invalid", default_parameters=asdict(Parameters(surface_activity=.6, noise_rh_pct=0, invalid_at_s=60))))
    check("paid complete cycle may have no valid result", invalid["summary"]["measurement_budget_completed"]==1 and invalid["summary"]["measurement_quality_valid"]==0)
    check("invalid result is retained in request denominator", invalid["requests"][0]["status"]=="quality_invalid" and invalid["summary"]["measurement_no_result_fraction"]==1)

    no_background = replace(Energy(), base_w=0, misc_w=0, stopped_w=0, boot_wh=0,
                            restart_wh=.05001, low_enter_wh=.1, low_exit_wh=.2)
    # Independent arithmetic, without calling implementation's reservation helper.
    required = (0.002*140 + 0.2*20 + 0.1)/3600/no_background.eta
    boundary = replace(one, name="exact_admission", duration_s=200,
                       energy=replace(no_background, initial_wh=no_background.stop_wh+required),
                       requests=(Request("r",40,180,"b"),))
    exact=run(boundary)
    check("exact energy and deadline finish before stopping", exact["requests"][0]["status"]=="completed")
    below=run(replace(boundary, name="below_admission", energy=replace(boundary.energy, initial_wh=boundary.energy.initial_wh-1e-7)))
    check("slightly insufficient reserve never starts", not below["actions"] and below["requests"][0]["status"]=="expired")
    early=run(replace(one, name="deadline_too_early", requests=(Request("r",40,179,"b"),)))
    check("deadline cannot shorten protocol", not early["actions"] and early["requests"][0]["status"]=="expired")
    later_sun=run(replace(boundary, name="future_solar", energy=replace(boundary.energy, initial_wh=boundary.energy.initial_wh-1e-7), solar=(Solar(50,200,1),)))
    check("future sun cannot fund expired opportunity", not later_sun["actions"] and later_sun["requests"][0]["status"]=="expired")

    changed = run(replace(one, name="batch_during_flush", installations=installation+(Installation(80,"b2","i2"),),
                         requests=one.requests+(Request("r2",81,500,"b2"),)))
    old,new=changed["actions"]
    check("old batch cancelled without completing", old["outcome"]=="cancelled_installation" and not old["quality_valid"])
    check("partial flush pays closure once", near(sum(old["paid_components_wh"].values()),2.18/3600))
    check("new batch respects reserved old window", new["started_s"]>=210 and new["weak_cycles_after"]==1)
    check("old request is not moved to new garment", changed["requests"][0]["status"]=="cancelled_installation" and changed["requests"][1]["status"]=="completed")

    network=replace(one, name="failed_wifi", duration_s=400, energy=replace(Energy(),wifi_attempt_limit=2,backoff_s=(7,)),
                    requests=(Request("net",40,390,"b",kind="wifi"),), network=(Network(0,400,False),))
    failed=run(network)
    check("failed connections consume two full timeout budgets", near(failed["summary"]["load_wifi_wh"],.6*40/3600))
    check("retry limit cannot masquerade as delivery", failed["requests"][0]["status"]=="retry_exhausted" and failed["summary"]["wifi_no_result_fraction"]==1)
    check("retry backoff starts after failure", failed["actions"][1]["started_s"]==failed["actions"][0]["ended_s"]+7)

    requests=tuple(Request(f"r{n}",60+2400*n,600+2400*n,"b") for n in range(3))
    sparse=replace(one,name="sparse_requests",duration_s=5400,requests=requests)
    demand,fixed=run(sparse),run(sparse,"fixed")
    check("comparison uses the same three request denominator", demand["summary"]["measurement_requests"]==fixed["summary"]["measurement_requests"]==3)
    check("sparse demand preserves explicit evidence expiry", demand["summary"]["max_weak_chain"]==1 and demand["summary"]["idle_evidence_expirations"]>=2)
    check("fixed sampling can retain a longer weak chain", fixed["summary"]["max_weak_chain"]>1 and fixed["summary"]["independent_review_due_finishes"]>0)
    check("saving energy is reported alongside evidence difference", demand["summary"]["battery_consumed_wh"]<fixed["summary"]["battery_consumed_wh"])

    # Reboot is paid, but neither old requests nor missed installations reappear.
    offline=run(replace(one,name="offline_recovery",duration_s=400,
                       energy=replace(energy,initial_wh=0),solar=(Solar(0,100,10),),
                       installations=(Installation(6,"old","missed"),Installation(120,"b","new")),
                       requests=(Request("lost",5,350,"old"),Request("new",125,395,"b"))))
    check("offline trigger remains explicitly missed", offline["requests"][0]["status"]=="missed_offline")
    check("recovery needs a new observed installation", offline["installations"][0]["status"]=="missed_offline" and offline["requests"][1]["status"]=="completed")
    check("recovery starts a fresh evidence chain", offline["actions"][0]["weak_cycles_after"]==1)

    assert source_hashes() == before_sources, "sources changed during independent verification"
    print(json.dumps({"synthetic":"SYNTHETIC_UNCALIBRATED","passed":True,"checks":len(labels),"labels":labels,
                      "cases":cases,"sources_stable_during_run":True,"source_sha256":before_sources},indent=2))


if __name__ == "__main__":
    main()

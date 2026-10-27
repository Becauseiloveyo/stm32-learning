"""Deterministic external requests and weather/network assumptions, not dry truth."""

from dataclasses import replace
from research.energy.model import Config, Network, Request, Scenario, Solar


def service_requests(minutes):
    requests = []
    # 36 externally specified measurement requests/day, phase offset from fixed ticks.
    # This is a workload assumption, NOT an online passive trend algorithm.
    for t in range(3, minutes - 30, 40):
        requests.append(Request(f"measure-{t}", t, t + 30, "measurement", "external_recheck_assumption"))
    for t in range(7, minutes - 90, 60):
        requests.append(Request(f"summary-{t}", t, t + 90, "wifi", "hourly_summary", False))
    for t in range(17, minutes - 90, 360):
        requests.append(Request(f"state-{t}", t, t + 90, "wifi", "external_research_state_event"))
    return tuple(sorted(requests, key=lambda r: (r.at_minute, r.event_id)))


def daily_solar(days, wh_per_day, first_day=0):
    # Battery-side energy after harvest losses, spread over 10:00-16:00.
    return tuple(Solar(day * 1440 + 600, day * 1440 + 960, wh_per_day / 6)
                 for day in range(first_day, days))


def scenarios():
    cfg = Config()
    return (
        Scenario("nominal_two_days", 2880, cfg, service_requests(2880), daily_solar(2, 0.30),
                 description="Full candidate store; 0.30 Wh/day battery-side assumed harvest."),
        Scenario("dark_seven_days", 10080, replace(cfg, initial_wh=0.12), service_requests(10080),
                 description="Seven days with zero harvest and deliberately depleted initial store."),
        Scenario("weak_network", 2880, cfg, service_requests(2880), daily_solar(2, 0.30),
                 (Network(0, 2160, False),),
                 "No acknowledgments for first 36 hours; failure costs full timeout energy."),
        Scenario("recharge_after_dark", 7200, replace(cfg, initial_wh=0.04), service_requests(7200),
                 daily_solar(5, 1.20, first_day=2),
                 description="Initially stopped, two dark days, then three recovery harvest days."),
        Scenario("hysteresis_pulses", 360, replace(cfg, initial_wh=0.049), service_requests(360),
                 tuple(Solar(t, t + 10, 0.048) for t in range(0, 120, 20)) + (Solar(240, 260, 0.6),),
                 description="Six 0.008 Wh pulses below restart, then 0.2 Wh; no repeated boots."),
    )

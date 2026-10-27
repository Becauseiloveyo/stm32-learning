"""Workload/observation/environment assumptions shared by both scheduling policies."""
from dataclasses import asdict, replace

from theory.model import Parameters
from research.energy_t05.contracts import Energy, Request, Installation, Solar, Network, ObservationCondition, Scenario


def workload(duration, batch="batch-A"):
    requests = [Request(f"m-{t}", t, t + 1800, batch) for t in range(63, duration, 2400)]
    requests += [Request(f"w-{t}", t, t + 5400, batch, "wifi", False, "hourly_assumed_summary")
                 for t in range(300, duration, 3600)]
    requests += [Request(f"critical-{t}", t, t + 5400, batch, "wifi", True, "external_research_event")
                 for t in range(500, duration, 21600)]
    return tuple(sorted(requests, key=lambda r: (r.at_s, r.id)))


def scenarios():
    initial = (Installation(1, "batch-A", "install-1"),)
    normal = Scenario("normal_and_idle_expiry", 14400, requests=workload(14400), installations=initial,
                      solar=(Solar(3600, 10800, 0.05),),
                      assumption="Four hours, weak synthetic observations, 40-minute external triggers versus 10-minute fixed opportunities. Idle weak-chain expiry is intentional.")
    dark = Scenario("continuous_dark_depleted", 259200, energy=replace(Energy(), initial_wh=0.16),
                    requests=workload(259200), installations=initial,
                    assumption="Three completely dark days from deliberately depleted reserve; this is not a fully charged endurance test.")
    weak = Scenario("weak_network_and_recovery", 108000, requests=workload(108000), installations=initial,
                    network=(Network(0, 93600, False),),
                    solar=(Solar(36000, 57600, 0.05),),
                    assumption="30 hours, connection fails for first 26 hours then recovers; paid timeouts, finite retries and calendar-day budget remain active.")
    recovery = Scenario("stop_recharge_hysteresis", 43200, energy=replace(Energy(), initial_wh=0.16),
                        requests=workload(43200),
                        installations=initial + (Installation(21600, "batch-A", "offline-reinstall"),
                                                 Installation(32400, "batch-A", "trusted-reinstall-after-charge")),
                        solar=tuple(Solar(t, t + 60, 0.048) for t in range(25200, 25560, 120)) +
                              (Solar(28800, 32400, 0.6),),
                        assumption="Higher running load forces both policies to stop. Weak pulses cannot reboot. Later charge permits a new paid boot, but monitoring waits for an explicit trusted reinstallation; offline events are lost.")
    recovery = replace(recovery, energy=replace(recovery.energy, base_w=0.02))
    batch_requests = (Request("old", 63, 1000, "batch-A"), Request("new", 110, 1000, "batch-B"),
                      Request("old-late", 120, 800, "batch-A"), Request("new-later", 2000, 4000, "batch-B"))
    batch = Scenario("batch_switch_during_flush", 7200, requests=batch_requests,
                     installations=initial + (Installation(100, "batch-B", "install-2"),),
                     assumption="Reinstall during flush cancels the old cycle and closes the vent once; old reserved endpoint remains unavailable through the minimum gap.")
    invalid = Scenario("raw_unknown_and_recovery", 14400, requests=workload(14400), installations=initial,
                       observations=(ObservationCondition(0, 3600, asdict(Parameters(surface_activity=0.6, noise_rh_pct=0, invalid_at_s=60))),),
                       assumption="A raw sensor_valid=false sample at cycle second 60 makes early cycles UNKNOWN. Explicit recover events restart stabilization; later conditions are weak, never calibrated dry.")
    return normal, dark, weak, recovery, batch, invalid

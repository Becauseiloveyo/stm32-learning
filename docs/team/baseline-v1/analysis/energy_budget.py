"""Illustrative daily energy budget; all defaults are unmeasured assumptions.

Power is in watts, duration in seconds, energy in watt-hours unless named otherwise.
This is not a cell discharge model: it omits transient droop and intraday timing.
"""

import argparse
import csv
import math
from pathlib import Path


def nonnegative(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be finite and >= 0")
    return number


def positive(value):
    number = nonnegative(value)
    if number == 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return number


def fraction(value):
    number = positive(value)
    if number > 1:
        raise argparse.ArgumentTypeError("must be in (0, 1]")
    return number


def count(value):
    number = nonnegative(value)
    if not number.is_integer():
        raise argparse.ArgumentTypeError("must be an integer")
    return int(number)


def budget(args, cycles):
    # Static consumption is represented as an equivalent load-side average.
    # Do not divide by dc_efficiency again if replacing this with battery-side data.
    loads = {
        "base_Wh": args.base_mw / 1000 * 24,
        "fan_Wh": args.fan_watts * args.fan_seconds * cycles / 3600,
        "vent_Wh": args.vent_joules * cycles / 3600,
        "wifi_Wh": args.wifi_watts * args.wifi_seconds * args.wifi_sessions / 3600,
        "misc_Wh": args.misc_wh,
    }
    load_wh = sum(loads.values())
    battery_wh = load_wh / args.dc_efficiency
    with_margin = battery_wh * args.margin
    planned = math.ceil(with_margin / args.round_step_wh - 1e-12) * args.round_step_wh
    solar_wh = args.panel_watts * args.sun_hours * args.harvest_efficiency
    capacity_wh = args.battery_volts * args.battery_mah / 1000 * args.usable_fraction
    dark_demand = planned * args.dark_days
    # Explicitly retain unmet demand instead of treating an empty battery as success.
    dark_unmet = max(0.0, dark_demand - capacity_wh)
    dark_remaining = max(0.0, capacity_wh - dark_demand)
    refill_deficit = capacity_wh - dark_remaining
    recovery_solar = args.panel_watts * args.recovery_sun_hours * args.harvest_efficiency
    recovery_surplus = recovery_solar - planned
    recovery_days = (
        0 if refill_deficit == 0 else
        math.ceil(refill_deficit / recovery_surplus - 1e-12) if recovery_surplus > 0 else
        None
    )
    return {
        "fan_cycles_per_day": cycles,
        "wifi_sessions_per_day": args.wifi_sessions,
        "wifi_seconds_per_session": args.wifi_seconds,
        **loads,
        "load_Wh_per_day": load_wh,
        "battery_Wh_per_day_before_margin": battery_wh,
        "budget_Wh_per_day_before_rounding": with_margin,
        "planned_Wh_per_day": planned,
        "panel_watts": args.panel_watts,
        "sun_equivalent_hours_per_day": args.sun_hours,
        "harvest_efficiency": args.harvest_efficiency,
        "solar_Wh_per_day": solar_wh,
        "net_Wh_per_day": solar_wh - planned,
        "usable_battery_Wh": capacity_wh,
        "theoretical_dark_days": capacity_wh / planned if planned > 0 else None,
        "dark_days_requested": args.dark_days,
        "remaining_after_dark_Wh": dark_remaining,
        "unmet_during_dark_Wh": dark_unmet,
        "recovery_sun_equivalent_hours_per_day": args.recovery_sun_hours,
        "recovery_net_Wh_per_day": recovery_surplus,
        "ideal_full_days_to_refill": recovery_days,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fan-cycles", type=count, help="one custom scenario; default compares 36 and 144")
    parser.add_argument("--base-mw", type=nonnegative, default=1.0)
    parser.add_argument("--fan-watts", type=nonnegative, default=0.2)
    parser.add_argument("--fan-seconds", type=nonnegative, default=20.0)
    parser.add_argument("--vent-joules", type=nonnegative, default=0.1, help="complete opening AND closing")
    parser.add_argument("--wifi-watts", type=nonnegative, default=0.6)
    parser.add_argument("--wifi-seconds", type=nonnegative, default=6.0, help="complete connection including boot and TLS")
    parser.add_argument("--wifi-sessions", type=count, default=28, help="total connections; include retries if applicable")
    parser.add_argument("--misc-wh", type=nonnegative, default=0.004)
    parser.add_argument("--dc-efficiency", type=fraction, default=0.85)
    parser.add_argument("--margin", type=positive, default=1.3)
    parser.add_argument("--round-step-wh", type=positive, default=0.05)
    parser.add_argument("--panel-watts", type=nonnegative, default=2.0)
    parser.add_argument("--sun-hours", type=nonnegative, default=0.25, help="equivalent full-irradiance hours, not daylight hours")
    parser.add_argument("--harvest-efficiency", type=fraction, default=0.6)
    parser.add_argument("--battery-volts", type=positive, default=3.7)
    parser.add_argument("--battery-mah", type=nonnegative, default=1500.0)
    parser.add_argument("--usable-fraction", type=fraction, default=0.7)
    parser.add_argument("--dark-days", type=count, default=7)
    parser.add_argument("--recovery-sun-hours", type=nonnegative, default=1.0)
    parser.add_argument("--csv", type=Path, help="optional output path; overwrites that file")
    args = parser.parse_args()
    if args.margin < 1:
        parser.error("--margin must be >= 1")

    scenarios = [36, 144] if args.fan_cycles is None else [args.fan_cycles]
    rows = [budget(args, cycles) for cycles in scenarios]
    print("UNMEASURED DESIGN ASSUMPTIONS. Daily average model; not a runtime guarantee.")
    print("Starts with a fully charged usable reserve. Refill ignores charge taper/thermal limits.")
    for row in rows:
        print(f"\nFan cycles/day: {row['fan_cycles_per_day']}")
        for key, value in row.items():
            if key == "fan_cycles_per_day":
                continue
            if value is None:
                formatted = "undefined / no positive recharge surplus"
            elif isinstance(value, float):
                formatted = f"{value:.6f}"
            else:
                formatted = str(value)
            print(f"  {key}: {formatted}")

    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nCSV: {args.csv.resolve()}")


if __name__ == "__main__":
    main()

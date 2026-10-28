"""Recompute T09 public-data summaries and explicit design sizing; standard library.

No downloads, garments, forecasts, hardware, or changes to accepted T01-T07 sources.
Weather is historical grid data; load/PV assumptions are not measurements.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_hours(keys, fmt, expected):
    times = [datetime.strptime(k, fmt) for k in keys]
    assert len(times) == expected and len(set(times)) == expected
    assert all(b - a == timedelta(hours=1) for a, b in zip(times, times[1:]))


def daily_summary(hourly_Wh):
    # UTC dates for both APIs; no partial local-day padding or mixed-year joining.
    days = defaultdict(list)
    for timestamp, value in hourly_Wh:
        assert math.isfinite(value) and value >= 0
        days[timestamp[:8]].append(value)
    assert all(len(values) == 24 for values in days.values())
    totals = {day: math.fsum(values) / 1000 for day, values in sorted(days.items())}
    monthly = defaultdict(list)
    for day, value in totals.items():
        monthly[day[:6]].append(value)
    week = [(day, math.fsum(list(totals.values())[i:i+7]) / 7)
            for i, day in enumerate(totals) if i+7 <= len(totals)]
    return {
        "annual_kWh_m2": math.fsum(totals.values()),
        "mean_daily_kWh_m2": math.fsum(totals.values()) / len(totals),
        "minimum_7day_mean": min(week, key=lambda x: x[1]),
        "monthly_mean_daily_kWh_m2": {
            month: math.fsum(values) / len(values) for month, values in monthly.items()
        },
        "utc_days": len(totals),
    }


def solar_Wh(area_cm2, irradiation_Wh_m2, a):
    return (area_cm2 / 10000 * irradiation_Wh_m2 * a["pv_efficiency"]
            * a["pv_to_battery_derating"] * a["site_exposure_factor"])


def required_area_cm2(demand_Wh, irradiation_Wh_m2, a):
    per_cm2 = solar_Wh(1, irradiation_Wh_m2, a)
    return demand_Wh / per_cm2 if per_cm2 > 0 else None


def main():
    a = read(HERE / "assumptions.json")
    nasa_receipt = read(HERE / "data/weather-source.json")
    nasa_path = ROOT / nasa_receipt["path"]
    assert digest(nasa_path) == nasa_receipt["sha256"]
    nasa = read(nasa_path)
    assert nasa["header"]["time_standard"] == "UTC"
    assert nasa["parameters"]["ALLSKY_SFC_SW_DWN"]["units"] == "Wh/m^2"
    raw = nasa["properties"]["parameter"]
    times = sorted(raw["T2M"])
    validate_hours(times, "%Y%m%d%H", 8784)
    fill = nasa["header"]["fill_value"]
    for name, series in raw.items():
        assert sorted(series) == times
        assert all(math.isfinite(v) and v != fill for v in series.values()), name
    assert all(0 <= v <= 100 for v in raw["RH2M"].values())
    assert all(v >= 0 for key in ("WS2M", "PRECTOTCORR") for v in raw[key].values())
    weather = {
        "label": "HISTORICAL_GRID_DATA_NOT_BALCONY_MEASUREMENTS",
        "year": 2024, "timezone": "UTC", "hours": len(times),
        "temperature_C_min_max": [min(raw["T2M"].values()), max(raw["T2M"].values())],
        "hours_outside_old_model_0_50_C": sum(not 0 <= v <= 50 for v in raw["T2M"].values()),
        "RH_pct_min_max": [min(raw["RH2M"].values()), max(raw["RH2M"].values())],
        "horizontal_solar": daily_summary(sorted(raw["ALLSKY_SFC_SW_DWN"].items())),
        "note": "Precipitation is hourly grid context, not seconds-scale local rain truth. Do not join to PVGIS 2023 as simultaneous weather.",
    }
    orientations = []
    receipts = read(HERE / "data/pvgis-sources.json")
    assert len(receipts) == 6
    for receipt in receipts:
        assert receipt["status"] == "downloaded"
        path = ROOT / receipt["path"]
        assert digest(path) == receipt["sha256"]
        d = read(path)
        assert d["meta"]["outputs"]["hourly"]["timestamp"] == "hourly averages"
        rows = d["outputs"]["hourly"]
        validate_hours([r["time"] for r in rows], "%Y%m%d:%H%M", 8760)
        for column in ("Gb(i)", "Gd(i)", "Gr(i)"):
            assert d["meta"]["outputs"]["hourly"]["variables"][column]["units"] == "W/m2"
        irradiance = []
        for r in rows:
            components = [r[k] for k in ("Gb(i)", "Gd(i)", "Gr(i)")]
            assert all(math.isfinite(v) and v >= 0 for v in components)
            # W/m2 hourly averages times a one-hour interval => Wh/m2.
            irradiance.append((r["time"], math.fsum(components)))
        stats = daily_summary(irradiance)
        orientations.append({"name": receipt["name"], **stats})
    modes = {}
    for name, spec in a["modes"].items():
        loads = {
            "baseline_Wh": a["baseline_mW"] / 1000 * 24,
            "passive_Wh": a["passive_sample_J"] * a["passive_samples_per_day"] / 3600,
            "wifi_Wh": a["wifi_W"] * spec["wifi_attempt_s"] * spec["wifi_attempts"] / 3600,
            "active_Wh": a["active_cycle_J"] * spec["active_cycles"] / 3600,
            "misc_Wh": a["misc_load_Wh_per_day"],
        }
        raw_budget = math.fsum(loads.values()) / a["load_conversion_efficiency"] * a["budget_margin"]
        budget = math.ceil(raw_budget * 100 - 1e-10) / 100
        modes[name] = {
            "load_components": loads, "battery_budget_before_rounding_Wh_day": raw_budget,
            "battery_budget_Wh_day": budget,
            "seven_day_nominal_battery_mAh": budget * a["dark_days"] / (a["battery_nominal_V"] * a["usable_capacity_fraction"]) * 1000,
            "required_active_area_cm2": {str(H): required_area_cm2(budget, H, a) for H in a["irradiation_cases_Wh_m2_day"]},
        }
    panel = [{"active_area_cm2": area, "ideal_STC_W": area/10000*1000*a["pv_efficiency"],
              "harvest_Wh_day": {str(H): solar_Wh(area, H, a) for H in a["irradiation_cases_Wh_m2_day"]}}
             for area in a["panel_active_areas_cm2"]]
    # Independent hand-calculated reference and limiting cases for units/scaling.
    assert math.isclose(solar_Wh(10000, 1000, a), 60)
    assert math.isclose(solar_Wh(50, 500, a), 0.15)
    assert math.isclose(required_area_cm2(0.06, 500, a), 20)
    assert required_area_cm2(0.06, 0, a) is None
    assert solar_Wh(0, 500, a) == 0
    assert modes["passive_reference"]["battery_budget_Wh_day"] == 0.06
    assert math.isclose(modes["passive_reference"]["seven_day_nominal_battery_mAh"], 162.16216216216216)
    assert math.isclose(sum(weather["horizontal_solar"]["monthly_mean_daily_kWh_m2"][f"2024{m:02}"] *
                                ((datetime(2024+(m==12), m%12+1,1)-datetime(2024,m,1)).days)
                                for m in range(1,13)), weather["horizontal_solar"]["annual_kWh_m2"])
    result = {"label": "PUBLIC_DATA_WITH_UNMEASURED_DESIGN_SIZING", "weather": weather,
              "pvgis_year": 2023, "orientations": orientations, "load_modes": modes, "panel_sizing": panel,
              "checks": "Source hashes, hourly coverage/units/fill/ranges, eight arithmetic and limiting checks passed.",
              "limitations": a["limitations"]}
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    (out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    lines = ["# T09 公开数据与设计预算计算", "", "生成结果；假设不等于实测。日期分组采用 UTC。", "",
             "## 武汉 PVGIS-ERA5 2023：仅六个候选比较，无阳台遮挡", "",
             "| 方位/倾角 | 年辐照 kWh/m² | 日均 kWh/m² | 最低连续七天日均 kWh/m² | 七天起日 UTC |",
             "|---|---:|---:|---:|---|"]
    for r in orientations:
        day, low = r["minimum_7day_mean"]
        lines.append(f'| {r["name"]} | {r["annual_kWh_m2"]:.2f} | {r["mean_daily_kWh_m2"]:.3f} | {low:.3f} | {day} |')
    lines += ["", "## 工作量假设的电池侧预算", "", "| 情景 | Wh/日 | 七天无光名义 mAh | 500 Wh/m²/日所需有效面积 cm² | 100 Wh/m²/日所需有效面积 cm² |", "|---|---:|---:|---:|---:|"]
    for name, r in modes.items():
        lines.append(f'| {name} | {r["battery_budget_Wh_day"]:.2f} | {r["seven_day_nominal_battery_mAh"]:.1f} | {r["required_active_area_cm2"]["500"]:.1f} | {r["required_active_area_cm2"]["100"]:.1f} |')
    lines += ["", "面积采用 20% 转换效率、60% 入电池综合折减、50% 现场受光因子；已在工作量预算中加裕量，面积不重复加。不是实际价格、封装尺寸或全年免充电结论。", "",
              "NASA 2024 与 PVGIS 2023 分别保存，不拼接为同年驱动。NASA 的小时太阳量单位为 Wh/m²；PVGIS 为小时平均 W/m²。", "",
              "检查："+result["checks"], ""]
    (out / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    sources = [HERE/"analyze.py", HERE/"assumptions.json", HERE/"data/weather-source.json", nasa_path, HERE/"data/pvgis-sources.json"]
    sources += [ROOT/r["path"] for r in receipts]
    manifest = {"recorded_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                "command": "python -B -X utf8 research/design_extension/analyze.py",
                "sources": {p.relative_to(ROOT).as_posix(): digest(p) for p in sources},
                "artifacts": {p.relative_to(ROOT).as_posix(): digest(p) for p in (out/"summary.json", out/"summary.md")},
                "seed": None, "note": "Deterministic; no garment tests or ETA implementation."}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

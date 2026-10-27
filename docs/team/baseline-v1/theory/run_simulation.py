"""Generate reproducible hypothetical cases, parameter sweeps, and research plots."""

import argparse
import csv
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path

from model import Gates, Parameters, assess, independent_delta_uncertainty, simulate


def cases():
    base = Parameters()
    return {
        "wet_normal": (base, "wet-like surface; no whole-garment mass simulated"),
        "dry_equilibrium": (replace(base, surface_activity=0.60), "surface in equilibrium with ambient"),
        "wet_leaky": (replace(base, leak_ml_s=1.50), "wet-like surface with large unmeasured leak"),
        "wet_high_humidity": (replace(base, ambient_rh_pct=95), "wet-like surface in humid ambient"),
        "ambient_step": (replace(base, ambient_step_at_s=70, ambient_step_rh_pct=15), "ambient changes during recovery"),
        "clip_changed": (replace(base, clip_at_s=85), "observed clip event invalidates cycle"),
        "sensor_failure": (replace(base, invalid_at_s=85), "one failed sensor reading"),
        "temperature_mismatch": (replace(base, local_temp_bias_c=3), "unmatched temperature measurement bias"),
        "dry_wall_source": (replace(base, surface_activity=0.60, wall_source_ug_s=0.10), "dry-like surface plus hypothetical wall desorption"),
        "slow_sensor": (replace(base, sensor_tau_s=30), "slower enclosure response, not known by classifier"),
        "ambiguous_A": (replace(base, noise_rh_pct=0), "K=.02, leak=.02, aw=1"),
        "ambiguous_B": (replace(base, noise_rh_pct=0, exchange_ml_s=0.04, leak_ml_s=0, surface_activity=0.80), "K=.04, leak=0, aw=.8; same observable equation as A"),
    }


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def plot(output, traces, sweep):
    # Optional: numeric simulation and CSV export use only Python's standard library.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties

    font_file = Path("C:/Windows/Fonts/msyh.ttc")
    if font_file.exists():
        plt.rcParams["font.family"] = FontProperties(fname=str(font_file)).get_name()
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(2, 2, figsize=(13, 9), constrained_layout=True)
    palette = ["#176B87", "#3D8361", "#C65D3B", "#7C5DA6"]
    for ax, selected, title in [
        (axes[0, 0], [("wet_normal", "湿表面／小漏风"), ("dry_equilibrium", "平衡干态表面"), ("wet_leaky", "湿表面／大漏风")], "同样弱的信号，可能有不同原因"),
        (axes[0, 1], [("wet_high_humidity", "高湿环境"), ("ambient_step", "环境湿度突变"), ("dry_wall_source", "干态表面＋腔壁放湿")], "环境与腔壁会改变恢复曲线"),
        (axes[1, 0], [("ambiguous_A", "参数组 A"), ("ambiguous_B", "参数组 B（虚线）")], "不同参数可产生重合曲线"),
    ]:
        for i, (key, label) in enumerate(selected):
            rows = traces[key]
            ax.plot([r["time_s"] for r in rows], [r["delta_e_Pa"] for r in rows],
                    label=label, color=palette[i], linewidth=1.8,
                    linestyle="--" if key == "ambiguous_B" else "-")
        ax.axvspan(30, 50, color="#CAD5DE", alpha=0.45)
        ax.axvline(50, color="#777777", linewidth=0.8)
        ax.set(title=title, xlabel="时间（秒）；灰区为换气阶段", ylabel="局部－环境水汽压（Pa）")
        ax.grid(alpha=0.15)
        ax.legend(fontsize=9)
    ax = axes[1, 1]
    leaks, rhs = [0.005, 0.02, 0.1, 0.5, 1.5], [40, 60, 80, 95]
    grid = [[next(r["recovery_rise_Pa"] for r in sweep
                 if r["volume_ml"] == 5 and r["ambient_rh_pct"] == rh and r["leak_ml_s"] == leak)
             for leak in leaks] for rh in rhs]
    im = ax.imshow(grid, origin="lower", aspect="auto", cmap="YlGnBu", vmin=0)
    ax.set_xticks(range(len(leaks)), [str(v) for v in leaks])
    ax.set_yticks(range(len(rhs)), [str(v) for v in rhs])
    ax.set(title="假设湿表面：恢复幅度随漏风和环境湿度变化", xlabel="未受控漏风量（mL/s，假设值）", ylabel="环境相对湿度（%）")
    fig.colorbar(im, ax=ax, label="恢复幅度（Pa）")
    fig.suptitle("衣架测量理论仿真 v0.1｜全部为假设数据，不代表实物准确率", fontsize=16)
    fig.savefig(output / "simulation-overview.png", dpi=160)
    fig.savefig(output / "simulation-overview.svg")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "results")
    parser.add_argument("--plot", action="store_true", help="requires matplotlib; simulation does not")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    traces, manifest, summaries = {}, {}, []
    for name, (parameters, hypothesis) in cases().items():
        rows = simulate(parameters)
        traces[name] = rows
        manifest[name] = {"parameters": asdict(parameters), "hypothesis": hypothesis}
        summaries.append({"case": name, "hypothesis": hypothesis, **assess(rows)})
        write_csv(args.output / f"trace-{name}.csv", rows)
    sweep = []
    for volume in (2, 5, 10):
        for rh in (40, 60, 80, 95):
            for leak in (0.005, 0.02, 0.1, 0.5, 1.5):
                parameters = replace(Parameters(), volume_ml=volume, ambient_rh_pct=rh, leak_ml_s=leak, noise_rh_pct=0)
                sweep.append({"volume_ml": volume, "ambient_rh_pct": rh, "leak_ml_s": leak, **assess(simulate(parameters))})
    write_csv(args.output / "case-summary.csv", summaries)
    write_csv(args.output / "parameter-sweep.csv", sweep)
    max_pair_gap = max(abs(a["delta_e_Pa"] - b["delta_e_Pa"])
                       for a, b in zip(traces["ambiguous_A"], traces["ambiguous_B"]))
    metadata = {
        "status": "SYNTHETIC_UNCALIBRATED", "seed": 20261023,
        "source_sha256": {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                          for name in ("model.py", "run_simulation.py")},
        "scope": "isothermal short-window fixed-surface-potential model; no garment water mass",
        "gates_are_illustrative": asdict(Gates()), "cases": manifest,
        "identical_response_max_gap_Pa": max_pair_gap,
        "example_standard_uncertainty_Pa": independent_delta_uncertainty(25, 60, 1.0, 0.1),
        "uncertainty_note": "Input uRH=1 percentage point and uT=0.1 C are hypothetical standard uncertainties, not SHT45 spec conversions.",
        "sweep_case_count": len(sweep),
    }
    (args.output / "manifest.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.plot:
        plot(args.output, traces, sweep)
    print("SYNTHETIC DATA ONLY; no accuracy or runtime validation.")
    for row in summaries:
        print(row["case"], row["state"], row["reasons"])
    print(f"Parameter sweep: {len(sweep)} cases; identical-pair gap: {max_pair_gap:.3g} Pa")
    print(f"Outputs: {args.output.resolve()}")


if __name__ == "__main__":
    main()

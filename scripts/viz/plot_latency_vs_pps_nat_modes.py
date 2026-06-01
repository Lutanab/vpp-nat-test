#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from viz_common import DEFAULT_RESULTS_ROOT, NAT_MODE_COLORS, NAT_MODES, configure_plot_style
from viz_common import as_float, iter_result_rows, resolve_results_dir


LATENCY_KEYS = {
    "avg": "latency_avg_usec",
    "p95": "latency_p95_usec",
    "p99": "latency_p99_usec",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot latency vs target_pps for one test_name with all available NAT modes.",
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        default=DEFAULT_RESULTS_ROOT,
        help=f"Results root directory (default: {DEFAULT_RESULTS_ROOT}).",
    )
    parser.add_argument(
        "--test-name",
        required=True,
        help="test_name to read from results/test_name_<name>.",
    )
    parser.add_argument(
        "--latency-metric",
        choices=tuple(LATENCY_KEYS),
        default="avg",
        help="Which latency metric to plot (default: avg).",
    )
    parser.add_argument(
        "--x-unit",
        choices=("pps", "mpps"),
        default="mpps",
        help="X-axis units (default: mpps).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output PNG path (default: <results-dir>/latency_vs_pps_<metric>_nat_modes.png).",
    )
    parser.add_argument(
        "--tsv-output",
        type=Path,
        default=None,
        help="Output TSV path (default: output image path with .tsv suffix).",
    )
    return parser.parse_args()


def to_x_value(target_pps: float, x_unit: str) -> float:
    if x_unit == "pps":
        return target_pps
    return target_pps / 1_000_000


def x_label(x_unit: str) -> str:
    return "Target, pps" if x_unit == "pps" else "Target, Mpps"


def default_output_path(results_dir: Path, metric: str) -> Path:
    return results_dir / f"latency_vs_pps_{metric}_nat_modes.png"


def collect_series(
    results_root: Path,
    test_name: str,
    latency_metric_key: str,
) -> dict[str, list[tuple[float, float]]]:
    latest_by_mode_and_pps: dict[str, dict[float, tuple[float, float]]] = {mode: {} for mode in NAT_MODES}

    for row in iter_result_rows(results_root, test_name):
        target_pps = as_float(row.result.get("target_pps"))
        latency_value = as_float(row.result.get(latency_metric_key))
        if target_pps is None or latency_value is None:
            continue
        if row.nat_mode not in latest_by_mode_and_pps:
            continue

        mtime = row.path.stat().st_mtime
        current = latest_by_mode_and_pps[row.nat_mode].get(target_pps)
        if current is None or mtime >= current[0]:
            latest_by_mode_and_pps[row.nat_mode][target_pps] = (mtime, latency_value)

    series: dict[str, list[tuple[float, float]]] = {mode: [] for mode in NAT_MODES}
    for nat_mode, by_pps in latest_by_mode_and_pps.items():
        points = [(target_pps, payload[1]) for target_pps, payload in by_pps.items()]
        series[nat_mode] = sorted(points, key=lambda item: item[0])
    return series


def write_tsv(output: Path, series: dict[str, list[tuple[float, float]]]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    union_target_pps = sorted({pps for points in series.values() for pps, _ in points})
    value_map = {mode: dict(points) for mode, points in series.items()}
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(("target_pps", *(f"{mode}_latency_usec" for mode in NAT_MODES)))
        for target_pps in union_target_pps:
            row = [f"{target_pps:.12g}"]
            for mode in NAT_MODES:
                value = value_map[mode].get(target_pps)
                row.append("" if value is None else f"{value:.12g}")
            writer.writerow(row)


def main() -> int:
    args = parse_args()
    latency_metric_key = LATENCY_KEYS[args.latency_metric]
    results_dir = resolve_results_dir(args.results_root, args.test_name)

    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("error: matplotlib is not installed. Install with: pip install matplotlib")
        return 1

    series = collect_series(args.results_root, args.test_name, latency_metric_key)
    if not any(series.values()):
        print(f"error: no matching latency result.json data found in {results_dir}")
        return 1

    output = args.output or default_output_path(results_dir, args.latency_metric)
    tsv_output = args.tsv_output or output.with_suffix(".tsv")

    configure_plot_style()
    fig, ax = plt.subplots(figsize=(10, 6))
    for nat_mode in NAT_MODES:
        points = series[nat_mode]
        if not points:
            continue
        xs = [to_x_value(target_pps, args.x_unit) for target_pps, _ in points]
        ys = [latency for _, latency in points]
        ax.plot(xs, ys, marker="o", linewidth=2, label=nat_mode, color=NAT_MODE_COLORS[nat_mode])

    ax.set_title(f"Latency vs Target PPS: {args.test_name} ({args.latency_metric})")
    ax.set_xlabel(x_label(args.x_unit))
    ax.set_ylabel("Latency, usec")
    ax.legend(title="nat_mode")
    fig.tight_layout()

    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=150)
    print(f"saved: {output}")

    write_tsv(tsv_output, series)
    print(f"saved: {tsv_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

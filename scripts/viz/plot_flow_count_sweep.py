#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from viz_common import DEFAULT_RESULTS_ROOT, NAT_MODE_COLORS, NAT_MODES, ResultRow, configure_plot_style
from viz_common import format_float, iter_result_rows, metric_label, metric_suffix, pps_to_metric, resolve_results_dir
from viz_common import result_pps


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot flow_count dependency from load-test result.json files.",
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        default=DEFAULT_RESULTS_ROOT,
        help=f"Results root directory (default: {DEFAULT_RESULTS_ROOT}).",
    )
    parser.add_argument(
        "--test-name",
        default="flow_count_sweep",
        help="test_name to read from results/test_name_<name> (default: flow_count_sweep).",
    )
    parser.add_argument(
        "--nat-modes",
        nargs="+",
        choices=NAT_MODES,
        default=list(NAT_MODES),
        help=f"NAT modes to plot (default: {' '.join(NAT_MODES)}).",
    )
    parser.add_argument(
        "--n-workers",
        type=int,
        default=1,
        help="Filter by n_workers (default: 1).",
    )
    parser.add_argument(
        "--packet-size",
        type=int,
        default=64,
        help="Filter by packet_size in bytes (default: 64).",
    )
    parser.add_argument(
        "--flow-counts",
        nargs="+",
        type=int,
        default=None,
        help="Flow counts to include. Default: all flow counts found.",
    )
    parser.add_argument(
        "--metric",
        choices=("mpps", "gbps"),
        default="mpps",
        help="Y-axis metric (default: mpps).",
    )
    parser.add_argument(
        "--xscale",
        choices=("linear", "log2"),
        default="log2",
        help="X-axis scale (default: log2).",
    )
    parser.add_argument(
        "--pps-key",
        choices=("actual_sent_pps", "target_pps"),
        default="actual_sent_pps",
        help="Which PPS metric to use (default: actual_sent_pps).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output PNG path (default: <results-dir>/flow_count_sweep_<metric>.png).",
    )
    parser.add_argument(
        "--tsv-output",
        type=Path,
        default=None,
        help="Output TSV path (default: output image path with .tsv suffix).",
    )
    return parser.parse_args()


def collect_rows(args: argparse.Namespace) -> list[ResultRow]:
    rows = []
    for row in iter_result_rows(args.results_root, args.test_name):
        if row.n_workers != args.n_workers:
            continue
        if row.packet_size != args.packet_size:
            continue
        if row.nat_mode not in args.nat_modes:
            continue
        if args.flow_counts is not None and row.flow_count not in args.flow_counts:
            continue
        if result_pps(row, args.pps_key) is None:
            continue
        rows.append(row)
    return rows


def flow_counts_for_plot(rows: list[ResultRow], requested: list[int] | None) -> list[int]:
    if requested is not None:
        return requested
    return sorted({row.flow_count for row in rows})


def build_series(
    rows: list[ResultRow],
    flow_counts: list[int],
    nat_modes: list[str],
    pps_key: str,
    metric: str,
) -> dict[str, list[tuple[int, float]]]:
    by_key = {(row.flow_count, row.nat_mode): row for row in rows}
    series: dict[str, list[tuple[int, float]]] = {mode: [] for mode in nat_modes}
    for nat_mode in nat_modes:
        for flow_count in flow_counts:
            row = by_key.get((flow_count, nat_mode))
            if row is None:
                continue
            pps = result_pps(row, pps_key)
            if pps is None:
                continue
            series[nat_mode].append((flow_count, pps_to_metric(pps, row.packet_size, metric)))
    return series


def write_tsv(output: Path, rows: list[ResultRow], flow_counts: list[int], nat_modes: list[str], pps_key: str) -> None:
    by_key = {(row.flow_count, row.nat_mode): row for row in rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(("flow_count", "nat_mode", "packet_size", "pps", "mpps", "gbps", "loss_percent"))
        for flow_count in flow_counts:
            for nat_mode in nat_modes:
                row = by_key.get((flow_count, nat_mode))
                pps = result_pps(row, pps_key) if row else None
                loss_percent = row.result.get("loss_percent") if row else None
                packet_size = row.packet_size if row else ""
                writer.writerow(
                    (
                        flow_count,
                        nat_mode,
                        packet_size,
                        format_float(pps),
                        format_float(pps / 1_000_000 if pps is not None else None),
                        format_float(pps_to_metric(pps, row.packet_size, "gbps") if row and pps is not None else None),
                        format_float(float(loss_percent) if isinstance(loss_percent, (int, float)) else None),
                    )
                )


def main() -> int:
    args = parse_args()
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("error: matplotlib is not installed. Install with: pip install matplotlib")
        return 1

    configure_plot_style()
    rows = collect_rows(args)
    results_dir = resolve_results_dir(args.results_root, args.test_name)
    flow_counts = flow_counts_for_plot(rows, args.flow_counts)
    if not rows or not flow_counts:
        print(f"error: no matching result.json data found in {results_dir}")
        return 1

    output = args.output or results_dir / f"flow_count_sweep_{args.metric}.png"
    tsv_output = args.tsv_output or output.with_suffix(".tsv")
    series = build_series(rows, flow_counts, args.nat_modes, args.pps_key, args.metric)

    fig, ax = plt.subplots(figsize=(10, 6))
    for nat_mode in args.nat_modes:
        points = series[nat_mode]
        if not points:
            print(f"warning: no data for mode '{nat_mode}'")
            continue
        xs = [flow_count for flow_count, _ in points]
        ys = [value for _, value in points]
        ax.plot(xs, ys, marker="o", linewidth=2, label=nat_mode, color=NAT_MODE_COLORS[nat_mode])

    ax.set_title(f"Flow count sweep: packet_size={args.packet_size}B, n_workers={args.n_workers}")
    ax.set_xlabel("flow_count")
    ax.set_ylabel(metric_label(args.metric))
    if args.xscale == "log2":
        ax.set_xscale("log", base=2)
    ax.set_xticks(flow_counts)
    ax.set_xticklabels([str(flow_count) for flow_count in flow_counts])
    ax.legend(title="nat_mode")
    fig.tight_layout()

    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=150)
    print(f"saved: {output}")

    write_tsv(tsv_output, rows, flow_counts, args.nat_modes, args.pps_key)
    print(f"saved: {tsv_output}")
    print(f"metric units: {metric_suffix(args.metric)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

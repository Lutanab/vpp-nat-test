#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

from viz_common import DEFAULT_RESULTS_ROOT, NAT_MODE_COLORS, NAT_MODES, ResultRow, configure_plot_style
from viz_common import format_float, iter_result_rows, metric_label, metric_suffix, pps_to_metric, resolve_results_dir
from viz_common import result_pps


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build grouped bar charts for packet-size load-test sweep results.",
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        default=DEFAULT_RESULTS_ROOT,
        help=f"Results root directory (default: {DEFAULT_RESULTS_ROOT}).",
    )
    parser.add_argument(
        "--test-name",
        default="packet_size_sweep",
        help="test_name to read from results/test_name_<name> (default: packet_size_sweep).",
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
        "--flow-count",
        type=int,
        default=1,
        help="Filter by flow_count (default: 1).",
    )
    parser.add_argument(
        "--packet-sizes",
        nargs="+",
        type=int,
        default=None,
        help="Packet sizes to include. Default: all packet sizes found.",
    )
    parser.add_argument(
        "--metric",
        choices=("mpps", "gbps", "both"),
        default="both",
        help="Which metric image to build (default: both).",
    )
    parser.add_argument(
        "--pps-key",
        choices=("actual_sent_pps", "target_pps"),
        default="actual_sent_pps",
        help="Which PPS metric to use (default: actual_sent_pps).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for generated PNG/TSV files (default: selected results directory).",
    )
    parser.add_argument(
        "--mpps-output",
        type=Path,
        default=None,
        help="Output path for Mpps PNG.",
    )
    parser.add_argument(
        "--gbps-output",
        type=Path,
        default=None,
        help="Output path for Gbit/s PNG.",
    )
    parser.add_argument(
        "--tsv-output",
        type=Path,
        default=None,
        help="Output TSV path (default: <output-dir>/packet_size_sweep.tsv).",
    )
    return parser.parse_args()


def collect_rows(args: argparse.Namespace) -> list[ResultRow]:
    rows = []
    for row in iter_result_rows(args.results_root, args.test_name):
        if row.n_workers != args.n_workers:
            continue
        if row.flow_count != args.flow_count:
            continue
        if row.nat_mode not in args.nat_modes:
            continue
        if args.packet_sizes is not None and row.packet_size not in args.packet_sizes:
            continue
        if result_pps(row, args.pps_key) is None:
            continue
        rows.append(row)
    return rows


def packet_sizes_for_plot(rows: list[ResultRow], requested: list[int] | None) -> list[int]:
    if requested is not None:
        return requested
    return sorted({row.packet_size for row in rows})


def build_value_map(rows: list[ResultRow], pps_key: str, metric: str) -> dict[str, dict[int, float]]:
    values: dict[str, dict[int, float]] = {mode: {} for mode in NAT_MODES}
    for row in rows:
        pps = result_pps(row, pps_key)
        if pps is None:
            continue
        values.setdefault(row.nat_mode, {})[row.packet_size] = pps_to_metric(pps, row.packet_size, metric)
    return values


def plot_metric(
    output: Path,
    rows: list[ResultRow],
    packet_sizes: list[int],
    nat_modes: list[str],
    pps_key: str,
    metric: str,
    n_workers: int,
    flow_count: int,
) -> None:
    import matplotlib.pyplot as plt

    values = build_value_map(rows, pps_key, metric)
    x_positions = list(range(len(packet_sizes)))
    mode_count = len(nat_modes)
    group_width = 0.78
    bar_width = group_width / max(1, mode_count)

    fig, ax = plt.subplots(figsize=(10, 6))
    max_value = 0.0
    for mode_index, nat_mode in enumerate(nat_modes):
        offset = -group_width / 2 + bar_width / 2 + mode_index * bar_width
        bar_positions = [x + offset for x in x_positions]
        bar_values = [values.get(nat_mode, {}).get(packet_size, math.nan) for packet_size in packet_sizes]
        finite_values = [value for value in bar_values if math.isfinite(value)]
        if finite_values:
            max_value = max(max_value, max(finite_values))
        bars = ax.bar(
            bar_positions,
            bar_values,
            width=bar_width,
            label=nat_mode,
            color=NAT_MODE_COLORS[nat_mode],
        )
        for bar, value in zip(bars, bar_values, strict=True):
            if not math.isfinite(value):
                continue
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                value,
                f"{value:.2f}",
                ha="center",
                va="bottom",
                fontsize=8,
                rotation=90,
            )

    ax.set_title(f"Packet size sweep: n_workers={n_workers}, flow_count={flow_count}")
    ax.set_xlabel("Packet size, bytes")
    ax.set_ylabel(metric_label(metric))
    ax.set_xticks(x_positions)
    ax.set_xticklabels([str(packet_size) for packet_size in packet_sizes])
    if max_value > 0:
        ax.set_ylim(top=max_value * 1.18)
    ax.legend(title="nat_mode")
    fig.tight_layout()

    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=150)
    plt.close(fig)
    print(f"saved: {output}")


def write_tsv(output: Path, rows: list[ResultRow], packet_sizes: list[int], nat_modes: list[str], pps_key: str) -> None:
    by_key = {(row.packet_size, row.nat_mode): row for row in rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(("packet_size", "nat_mode", "pps", "mpps", "gbps", "loss_percent"))
        for packet_size in packet_sizes:
            for nat_mode in nat_modes:
                row = by_key.get((packet_size, nat_mode))
                pps = result_pps(row, pps_key) if row else None
                loss_percent = row.result.get("loss_percent") if row else None
                writer.writerow(
                    (
                        packet_size,
                        nat_mode,
                        format_float(pps),
                        format_float(pps / 1_000_000 if pps is not None else None),
                        format_float(pps_to_metric(pps, packet_size, "gbps") if pps is not None else None),
                        format_float(float(loss_percent) if isinstance(loss_percent, (int, float)) else None),
                    )
                )


def main() -> int:
    args = parse_args()
    try:
        import matplotlib.pyplot as plt  # noqa: F401
    except ImportError:
        print("error: matplotlib is not installed. Install with: pip install matplotlib")
        return 1

    configure_plot_style()
    rows = collect_rows(args)
    results_dir = resolve_results_dir(args.results_root, args.test_name)
    output_dir = args.output_dir or results_dir
    packet_sizes = packet_sizes_for_plot(rows, args.packet_sizes)
    if not rows or not packet_sizes:
        print(f"error: no matching result.json data found in {results_dir}")
        return 1

    metrics = ("mpps", "gbps") if args.metric == "both" else (args.metric,)
    for metric in metrics:
        if metric == "mpps":
            output = args.mpps_output or output_dir / "packet_size_sweep_mpps.png"
        else:
            output = args.gbps_output or output_dir / "packet_size_sweep_gbps.png"
        plot_metric(
            output=output,
            rows=rows,
            packet_sizes=packet_sizes,
            nat_modes=args.nat_modes,
            pps_key=args.pps_key,
            metric=metric,
            n_workers=args.n_workers,
            flow_count=args.flow_count,
        )

    tsv_output = args.tsv_output or output_dir / "packet_size_sweep.tsv"
    write_tsv(tsv_output, rows, packet_sizes, args.nat_modes, args.pps_key)
    print(f"saved: {tsv_output}")
    print(f"metric units: {', '.join(metric_suffix(metric) for metric in metrics)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

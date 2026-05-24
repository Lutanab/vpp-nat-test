#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
from typing import Any

from viz_common import DEFAULT_RESULTS_ROOT, NAT_MODE_COLORS, NAT_MODES, configure_plot_style, load_json
from viz_common import resolve_results_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot target PPS saturation curves from load-test history.json files.",
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
        "--packet-size",
        type=int,
        default=64,
        help="Filter by packet_size in bytes (default: 64).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output PNG path (default: <results-dir>/saturation_<packet-size>B_flow<flow-count>.png).",
    )
    parser.add_argument(
        "--tsv-output",
        type=Path,
        default=None,
        help="Output TSV path (default: output image path with .tsv suffix).",
    )
    return parser.parse_args()


def collect_series(args: argparse.Namespace) -> tuple[dict[str, list[dict[str, Any]]], float | None]:
    results_dir = resolve_results_dir(args.results_root, args.test_name)
    series: dict[str, list[dict[str, Any]]] = {mode: [] for mode in args.nat_modes}
    target_loss_rate: float | None = None

    for history_path in results_dir.rglob("history.json"):
        payload = load_json(history_path)
        load_params = payload.get("params", {}).get("load_params", {})
        nat_mode = load_params.get("nat_mode")
        if nat_mode not in series:
            continue
        if load_params.get("n_workers") != args.n_workers:
            continue
        if load_params.get("flow_count", 1) != args.flow_count:
            continue
        if load_params.get("packet_size") != args.packet_size:
            continue

        raw_target_loss_rate = load_params.get("target_loss_rate")
        if isinstance(raw_target_loss_rate, (int, float)):
            target_loss_rate = float(raw_target_loss_rate)

        steps = payload.get("result")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            if not valid_step(step):
                continue
            series[nat_mode].append(step)

    for nat_mode in series:
        series[nat_mode].sort(key=lambda step: (step["target_pps"], step["step_index"]))
    return series, target_loss_rate


def valid_step(step: dict[str, Any]) -> bool:
    return (
        isinstance(step.get("step_index"), int)
        and isinstance(step.get("target_pps"), (int, float))
        and isinstance(step.get("actual_sent_pps"), (int, float))
        and isinstance(step.get("loss_percent"), (int, float))
    )


def write_tsv(output: Path, series: dict[str, list[dict[str, Any]]]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(("nat_mode", "step_index", "phase", "target_pps", "actual_sent_pps", "loss_percent", "passed"))
        for nat_mode, steps in series.items():
            for step in steps:
                writer.writerow(
                    (
                        nat_mode,
                        step["step_index"],
                        step.get("phase", ""),
                        f"{float(step['target_pps']):.12g}",
                        f"{float(step['actual_sent_pps']):.12g}",
                        f"{float(step['loss_percent']):.12g}",
                        step.get("passed", ""),
                    )
                )


def main() -> int:
    args = parse_args()
    results_dir = resolve_results_dir(args.results_root, args.test_name)
    output = args.output or results_dir / f"saturation_{args.packet_size}B_flow{args.flow_count}.png"
    tsv_output = args.tsv_output or output.with_suffix(".tsv")

    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("error: matplotlib is not installed. Install with: pip install matplotlib")
        return 1

    configure_plot_style()
    series, target_loss_rate = collect_series(args)
    if not any(series.values()):
        print(f"error: no matching history.json data found in {results_dir}")
        return 1

    fig, (ax_pps, ax_loss) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
    max_target_mpps = 0.0
    max_actual_mpps = 0.0

    for nat_mode in args.nat_modes:
        steps = series[nat_mode]
        if not steps:
            print(f"warning: no data for mode '{nat_mode}'")
            continue
        target_mpps = [float(step["target_pps"]) / 1_000_000 for step in steps]
        actual_mpps = [float(step["actual_sent_pps"]) / 1_000_000 for step in steps]
        loss_percent = [float(step["loss_percent"]) for step in steps]
        max_target_mpps = max(max_target_mpps, max(target_mpps))
        max_actual_mpps = max(max_actual_mpps, max(actual_mpps))

        color = NAT_MODE_COLORS[nat_mode]
        ax_pps.plot(target_mpps, actual_mpps, marker="o", linewidth=2, color=color, label=nat_mode)
        ax_loss.plot(target_mpps, loss_percent, marker="o", linewidth=2, color=color, label=nat_mode)

    diagonal_max = max(max_target_mpps, max_actual_mpps)
    ax_pps.plot([0, diagonal_max], [0, diagonal_max], color="#777777", linestyle=":", linewidth=1.5, label="target=actual")
    ax_pps.set_ylabel("Actual sent, Mpps")
    ax_pps.set_title(
        f"Saturation search: {args.packet_size}B packets, flow_count={args.flow_count}, n_workers={args.n_workers}"
    )
    ax_pps.legend(title="nat_mode")

    if target_loss_rate is not None and math.isfinite(target_loss_rate):
        ax_loss.axhline(target_loss_rate * 100, color="#777777", linestyle=":", linewidth=1.5, label="loss threshold")
    ax_loss.set_xlabel("Target, Mpps")
    ax_loss.set_ylabel("Loss, %")
    ax_loss.legend(title="nat_mode")

    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=150)
    print(f"saved: {output}")

    write_tsv(tsv_output, series)
    print(f"saved: {tsv_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

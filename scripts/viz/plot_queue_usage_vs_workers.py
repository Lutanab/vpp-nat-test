#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from viz_common import DEFAULT_RESULTS_ROOT, NAT_MODES, ResultRow, configure_plot_style
from viz_common import format_float, iter_result_rows, resolve_results_dir


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from build_steps_stats import (  # noqa: E402
    MEMIF_INTERFACES,
    VPP_MEMIF_ROLE,
    load_metrics_rows,
    parse_int,
    queue_indices,
    queue_used_percent,
    tx_rx_directions,
)


@dataclass(frozen=True)
class SelectedStep:
    result_dir: Path
    row: ResultRow
    step_index: int | None
    target_pps: float | None
    actual_sent_pps: float | None
    passed: bool | None
    start_epoch: float
    end_epoch: float
    selection: str


@dataclass(frozen=True)
class QueueUsagePoint:
    n_workers: int
    nat_mode: str
    flow_count: int
    packet_size: int
    direction: str
    interface: str
    queue_id: int
    avg_used_percent: float
    min_used_percent: float
    max_used_percent: float
    samples: int
    target_pps: float | None
    actual_sent_pps: float | None
    passed: bool | None
    step_index: int | None
    selection: str
    result_dir: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build RX/TX memif queue occupancy plots from load-test metrics.tsv files.",
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        default=DEFAULT_RESULTS_ROOT,
        help=f"Results root directory (default: {DEFAULT_RESULTS_ROOT}).",
    )
    parser.add_argument(
        "--test-name",
        default="worker_scaling_workers_1",
        help="test_name to read from results/test_name_<name> (default: worker_scaling_workers_1).",
    )
    parser.add_argument(
        "--nat-modes",
        nargs="+",
        choices=NAT_MODES,
        default=list(NAT_MODES),
        help=f"NAT modes to include (default: {' '.join(NAT_MODES)}).",
    )
    parser.add_argument(
        "--flow-count",
        type=int,
        default=None,
        help="Filter by flow_count (default: all found).",
    )
    parser.add_argument(
        "--packet-size",
        type=int,
        default=None,
        help="Filter by packet_size in bytes (default: all found).",
    )
    parser.add_argument(
        "--include-incomplete",
        action="store_true",
        help=(
            "Include result directories without final result.target_pps by using the last measured "
            "history step. Default: skip incomplete results."
        ),
    )
    parser.add_argument("--skip-incomplete", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output PNG path (default: <results-dir>/queue_usage_vs_workers.png).",
    )
    parser.add_argument(
        "--tsv-output",
        type=Path,
        default=None,
        help="Output TSV path (default: output image path with .tsv suffix).",
    )
    args = parser.parse_args()
    args.skip_incomplete = not args.include_incomplete or args.skip_incomplete
    return args


def as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def load_history_steps(result_dir: Path) -> list[dict[str, Any]]:
    history_path = result_dir / "history.json"
    if not history_path.exists():
        return []

    payload = json.loads(history_path.read_text(encoding="utf-8"))
    steps = payload.get("result", [])
    return steps if isinstance(steps, list) else []


def has_measurement_window(step: dict[str, Any]) -> bool:
    return isinstance(step.get("measurement_start_epoch"), (int, float)) and isinstance(
        step.get("measurement_end_epoch"),
        (int, float),
    )


def selected_step_from_history(row: ResultRow, skip_incomplete: bool) -> SelectedStep | None:
    result_dir = row.path.parent
    steps = [step for step in load_history_steps(result_dir) if has_measurement_window(step)]
    if not steps:
        print(f"warning: no history measurement windows in {result_dir}")
        return None

    target_pps = as_float(row.result.get("target_pps"))
    if target_pps is not None:
        matching_steps = [step for step in steps if as_float(step.get("target_pps")) == target_pps]
        if matching_steps:
            return build_selected_step(result_dir, row, matching_steps[-1], "result_target_pps")

        print(
            "warning: no history step matches "
            f"result.target_pps={target_pps:.0f}; using last history step in {result_dir}"
        )

    if skip_incomplete:
        print(f"warning: skipping incomplete result in {result_dir}")
        return None

    print(f"warning: using last measured history step for incomplete result in {result_dir}")
    return build_selected_step(result_dir, row, steps[-1], "last_history_fallback")


def build_selected_step(result_dir: Path, row: ResultRow, step: dict[str, Any], selection: str) -> SelectedStep:
    return SelectedStep(
        result_dir=result_dir,
        row=row,
        step_index=int(step["step_index"]) if isinstance(step.get("step_index"), int) else None,
        target_pps=as_float(step.get("target_pps")),
        actual_sent_pps=as_float(step.get("actual_sent_pps")),
        passed=step.get("passed") if isinstance(step.get("passed"), bool) else None,
        start_epoch=float(step["measurement_start_epoch"]),
        end_epoch=float(step["measurement_end_epoch"]),
        selection=selection,
    )


def collect_steps(args: argparse.Namespace) -> list[SelectedStep]:
    steps: list[SelectedStep] = []
    for row in iter_result_rows(args.results_root, args.test_name):
        if row.nat_mode not in args.nat_modes:
            continue
        if args.flow_count is not None and row.flow_count != args.flow_count:
            continue
        if args.packet_size is not None and row.packet_size != args.packet_size:
            continue

        selected = selected_step_from_history(row, skip_incomplete=args.skip_incomplete)
        if selected is not None:
            steps.append(selected)

    return sorted(steps, key=lambda item: (item.row.n_workers, item.row.nat_mode, item.row.flow_count, item.row.packet_size))


def collect_usage_points(selected_steps: list[SelectedStep]) -> list[QueueUsagePoint]:
    points: list[QueueUsagePoint] = []
    tx_direction, rx_direction = tx_rx_directions(VPP_MEMIF_ROLE)
    directions = {
        "rx": rx_direction,
        "tx": tx_direction,
    }

    for selected in selected_steps:
        metrics_path = selected.result_dir / "metrics.tsv"
        if not metrics_path.exists():
            print(f"warning: metrics.tsv not found in {selected.result_dir}")
            continue

        metrics_rows = load_metrics_rows(metrics_path)
        step_rows = [
            row
            for row in metrics_rows
            if isinstance(row.get("timestamp_epoch"), float)
            and selected.start_epoch <= row["timestamp_epoch"] <= selected.end_epoch
        ]
        if not step_rows:
            print(f"warning: no metrics rows inside selected measurement window in {selected.result_dir}")
            continue

        for interface in MEMIF_INTERFACES:
            for queue_id in queue_indices(step_rows, f"{interface}/"):
                for direction, ring_direction in directions.items():
                    values = queue_used_values(
                        rows=step_rows,
                        interface_prefix=f"{interface}/",
                        ring_direction=ring_direction,
                        queue_id=queue_id,
                    )
                    if not values:
                        continue
                    points.append(
                        QueueUsagePoint(
                            n_workers=selected.row.n_workers,
                            nat_mode=selected.row.nat_mode,
                            flow_count=selected.row.flow_count,
                            packet_size=selected.row.packet_size,
                            direction=direction,
                            interface=interface,
                            queue_id=queue_id,
                            avg_used_percent=sum(values) / len(values),
                            min_used_percent=min(values),
                            max_used_percent=max(values),
                            samples=len(values),
                            target_pps=selected.target_pps,
                            actual_sent_pps=selected.actual_sent_pps,
                            passed=selected.passed,
                            step_index=selected.step_index,
                            selection=selected.selection,
                            result_dir=selected.result_dir,
                        )
                    )

    return sorted(
        points,
        key=lambda item: (item.n_workers, item.direction, item.interface, item.queue_id, item.nat_mode),
    )


def queue_used_values(
    rows: list[dict[str, Any]],
    interface_prefix: str,
    ring_direction: str,
    queue_id: int,
) -> list[float]:
    values: list[float] = []
    for row in rows:
        if row.get("source") != "memif":
            continue
        if not (row.get("interface") or "").startswith(interface_prefix):
            continue
        if row.get("ring_direction") != ring_direction:
            continue
        if parse_int(row.get("ring_index")) != queue_id:
            continue

        ring_size = parse_int(row.get("ring_size"))
        head = parse_int(row.get("head"))
        tail = parse_int(row.get("tail"))
        if ring_size is None or ring_size <= 0 or head is None or tail is None:
            continue
        values.append(
            queue_used_percent(
                head=head,
                tail=tail,
                ring_size=ring_size,
                ring_direction=ring_direction,
                vpp_role=VPP_MEMIF_ROLE,
            )
        )
    return values


def write_tsv(output: Path, points: list[QueueUsagePoint]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(
            (
                "n_workers",
                "nat_mode",
                "flow_count",
                "packet_size",
                "direction",
                "interface",
                "queue_id",
                "avg_used_percent",
                "min_used_percent",
                "max_used_percent",
                "samples",
                "target_pps",
                "actual_sent_pps",
                "passed",
                "step_index",
                "selection",
                "result_dir",
            )
        )
        for point in points:
            writer.writerow(
                (
                    point.n_workers,
                    point.nat_mode,
                    point.flow_count,
                    point.packet_size,
                    point.direction,
                    point.interface,
                    point.queue_id,
                    format_float(point.avg_used_percent),
                    format_float(point.min_used_percent),
                    format_float(point.max_used_percent),
                    point.samples,
                    format_float(point.target_pps),
                    format_float(point.actual_sent_pps),
                    "" if point.passed is None else str(point.passed).lower(),
                    "" if point.step_index is None else point.step_index,
                    point.selection,
                    point.result_dir,
                )
            )


def queue_jitter(point: QueueUsagePoint) -> float:
    interface_offset = {"memif10": -0.055, "memif20": 0.055}.get(point.interface, 0.0)
    queue_offset = ((point.queue_id % 8) - 3.5) * 0.012
    return interface_offset + queue_offset


def plot_points(output: Path, points: list[QueueUsagePoint], title: str) -> None:
    import matplotlib.pyplot as plt

    configure_plot_style()

    interface_colors = {
        "memif10": "#4e79a7",
        "memif20": "#f28e2b",
    }
    interface_markers = {
        "memif10": "o",
        "memif20": "s",
    }

    fig, axes = plt.subplots(1, 2, figsize=(13, 6), sharey=True)
    x_ticks = sorted({point.n_workers for point in points})
    for ax, direction in zip(axes, ("rx", "tx"), strict=True):
        direction_points = [point for point in points if point.direction == direction]
        for interface in MEMIF_INTERFACES:
            series = [point for point in direction_points if point.interface == interface]
            if not series:
                continue
            xs = [point.n_workers + queue_jitter(point) for point in series]
            ys = [point.avg_used_percent for point in series]
            ax.scatter(
                xs,
                ys,
                s=64,
                marker=interface_markers.get(interface, "o"),
                color=interface_colors.get(interface),
                edgecolors="white",
                linewidths=0.7,
                alpha=0.88,
                label=interface,
            )
            for x_value, y_value, point in zip(xs, ys, series, strict=True):
                ax.text(x_value + 0.012, y_value, f"q{point.queue_id}", fontsize=7, alpha=0.72)

        ax.set_title(direction.upper())
        ax.set_xlabel("n_workers")
        ax.grid(True, linestyle="--", alpha=0.35)
        ax.set_xticks(x_ticks)
        ax.set_xticklabels([str(value) for value in x_ticks])
        ax.set_ylim(0, 105)
        ax.legend(title="interface")

    axes[0].set_ylabel("Average ring occupancy, %")
    fig.suptitle(title)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160)
    plt.close(fig)


def main() -> int:
    args = parse_args()
    results_dir = resolve_results_dir(args.results_root, args.test_name)
    output = args.output or results_dir / "queue_usage_vs_workers.png"
    tsv_output = args.tsv_output or output.with_suffix(".tsv")

    selected_steps = collect_steps(args)
    if not selected_steps:
        print("error: no matching result/history files found")
        return 1

    points = collect_usage_points(selected_steps)
    if not points:
        print("error: no queue usage points found")
        return 1

    write_tsv(tsv_output, points)
    plot_points(output, points, title=f"{args.test_name}: memif RX/TX queue occupancy vs workers")

    print(f"saved: {output}")
    print(f"saved: {tsv_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

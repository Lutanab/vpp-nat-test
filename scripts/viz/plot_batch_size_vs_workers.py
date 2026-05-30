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
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from build_steps_stats import (  # noqa: E402
    all_workers,
    load_metrics_rows,
    memif_interfaces,
    parse_int,
    runtime_deltas_by_worker,
    worker_for_index,
    worker_index_for_interface,
)


def load_cli_defaults() -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "results_root": DEFAULT_RESULTS_ROOT,
        "test_name": "worker_scaling_workers_1",
        "nat_modes": list(NAT_MODES),
        "flow_count": None,
        "packet_size": None,
    }
    try:
        from test_nat.config import load_test_configs

        load_config, _search_config, _load_path, _search_path = load_test_configs(None, None)
        defaults["results_root"] = load_config.results_root
        defaults["test_name"] = load_config.test_name
        defaults["nat_modes"] = [load_config.nat_mode]
        defaults["flow_count"] = load_config.flow_count
        defaults["packet_size"] = load_config.packet_size
    except Exception as exc:
        print(f"warning: failed to load defaults from load config, using script defaults: {exc}")
    return defaults


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
class BatchPoint:
    n_workers: int
    nat_mode: str
    flow_count: int
    packet_size: int
    direction: str
    iface_role: str
    interface: str
    pair_index: int
    queue_id: int
    worker: str
    batch_size: float
    calls: int
    vectors: int
    target_pps: float | None
    actual_sent_pps: float | None
    passed: bool | None
    step_index: int | None
    selection: str
    result_dir: Path


def parse_args() -> argparse.Namespace:
    defaults = load_cli_defaults()
    parser = argparse.ArgumentParser(
        description="Build RX/TX queue batch-size scatter plot from load-test metrics.tsv files.",
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        default=defaults["results_root"],
        help=f"Results root directory (default: {defaults['results_root']}).",
    )
    parser.add_argument(
        "--test-name",
        default=defaults["test_name"],
        help=f"test_name to read from results/test_name_<name> (default: {defaults['test_name']}).",
    )
    parser.add_argument(
        "--nat-modes",
        nargs="+",
        choices=NAT_MODES,
        default=defaults["nat_modes"],
        help=f"NAT modes to include (default: {' '.join(defaults['nat_modes'])}).",
    )
    parser.add_argument(
        "--flow-count",
        type=int,
        default=defaults["flow_count"],
        help=f"Filter by flow_count (default: {defaults['flow_count']}).",
    )
    parser.add_argument(
        "--packet-size",
        type=int,
        default=defaults["packet_size"],
        help=f"Filter by packet_size in bytes (default: {defaults['packet_size']}).",
    )
    parser.add_argument(
        "--skip-incomplete",
        action="store_true",
        help=(
            "Skip result directories without final result.target_pps. "
            "Default: use the last measured history step as a marked fallback."
        ),
    )
    parser.add_argument(
        "--yscale",
        choices=("linear", "log"),
        default="log",
        help="Y-axis scale (default: log).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output PNG path (default: <results-dir>/batch_size_vs_workers.png).",
    )
    parser.add_argument(
        "--tsv-output",
        type=Path,
        default=None,
        help="Output TSV path (default: output image path with .tsv suffix).",
    )
    return parser.parse_args()


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
            step = matching_steps[-1]
            return build_selected_step(result_dir, row, step, "result_target_pps")

        print(
            "warning: no history step matches "
            f"result.target_pps={target_pps:.0f}; using last history step in {result_dir}"
        )

    if skip_incomplete:
        print(f"warning: skipping incomplete result in {result_dir}")
        return None

    step = steps[-1]
    print(f"warning: using last measured history step for incomplete result in {result_dir}")
    return build_selected_step(result_dir, row, step, "last_history_fallback")


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


def collect_batch_points(selected_steps: list[SelectedStep]) -> list[BatchPoint]:
    points: list[BatchPoint] = []

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

        workers = all_workers(step_rows)
        rx_deltas = runtime_deltas_by_worker(step_rows, "memif-input")
        interfaces = memif_interfaces(step_rows)
        tx_deltas_by_interface = {
            interface: runtime_deltas_by_worker(step_rows, f"{interface}-tx") for interface in interfaces
        }

        for interface in interfaces:
            worker_index = worker_index_for_interface(step_rows, interface)
            worker = worker_for_index(workers, worker_index)
            if worker is None:
                continue

            pair_index = pair_index_for_interface(step_rows, interface)
            iface_role = iface_role_for_interface(step_rows, interface)
            for queue_id in queue_ids_for_interface(step_rows, interface):
                add_point(
                    points,
                    selected,
                    "rx",
                    iface_role,
                    interface,
                    pair_index,
                    queue_id,
                    worker,
                    rx_deltas.get(worker),
                )
                add_point(
                    points,
                    selected,
                    "tx",
                    iface_role,
                    interface,
                    pair_index,
                    queue_id,
                    worker,
                    tx_deltas_by_interface[interface].get(worker),
                )

    return sorted(
        points,
        key=lambda item: (
            item.n_workers,
            item.direction,
            item.iface_role,
            item.pair_index,
            item.interface,
            item.queue_id,
            item.nat_mode,
        ),
    )


def add_point(
    points: list[BatchPoint],
    selected: SelectedStep,
    direction: str,
    iface_role: str,
    interface: str,
    pair_index: int,
    queue_id: int,
    worker: str,
    deltas: tuple[int, int] | None,
) -> None:
    if deltas is None:
        return

    calls, vectors = deltas
    if calls <= 0:
        return

    points.append(
        BatchPoint(
            n_workers=selected.row.n_workers,
            nat_mode=selected.row.nat_mode,
            flow_count=selected.row.flow_count,
            packet_size=selected.row.packet_size,
            direction=direction,
            iface_role=iface_role,
            interface=interface,
            pair_index=pair_index,
            queue_id=queue_id,
            worker=worker,
            batch_size=vectors / calls,
            calls=calls,
            vectors=vectors,
            target_pps=selected.target_pps,
            actual_sent_pps=selected.actual_sent_pps,
            passed=selected.passed,
            step_index=selected.step_index,
            selection=selected.selection,
            result_dir=selected.result_dir,
        )
    )


def queue_ids_for_interface(rows: list[dict[str, Any]], interface: str) -> list[int]:
    queue_ids = {
        queue_id
        for row in rows
        if row.get("source") == "memif"
        and (row.get("interface") or "") == interface
        and (queue_id := parse_int(row.get("ring_index"))) is not None
    }
    return sorted(queue_ids)


def pair_index_for_interface(rows: list[dict[str, Any]], interface: str) -> int:
    for row in rows:
        if row.get("source") != "memif":
            continue
        if (row.get("interface") or "") != interface:
            continue
        pair_index = parse_int(row.get("pair_index"))
        if pair_index is not None:
            return pair_index
    return -1


def iface_role_for_interface(rows: list[dict[str, Any]], interface: str) -> str:
    for row in rows:
        if row.get("source") != "memif":
            continue
        if (row.get("interface") or "") != interface:
            continue
        role = (row.get("iface_role") or "").strip().lower()
        if role:
            return role
    return "unknown"


def write_tsv(output: Path, points: list[BatchPoint]) -> None:
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
                "iface_role",
                "interface",
                "pair_index",
                "queue_id",
                "worker",
                "batch_size",
                "calls",
                "vectors",
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
                    point.iface_role,
                    point.interface,
                    point.pair_index,
                    point.queue_id,
                    point.worker,
                    format_float(point.batch_size),
                    point.calls,
                    point.vectors,
                    format_float(point.target_pps),
                    format_float(point.actual_sent_pps),
                    "" if point.passed is None else str(point.passed).lower(),
                    "" if point.step_index is None else point.step_index,
                    point.selection,
                    point.result_dir,
                )
            )


def queue_jitter(point: BatchPoint) -> float:
    role_offset = {"inside": -0.055, "outside": 0.055}.get(point.iface_role, 0.0)
    pair_offset = ((point.pair_index % 8) - 3.5) * 0.012 if point.pair_index >= 0 else 0.0
    queue_offset = ((point.queue_id % 8) - 3.5) * 0.003
    return role_offset + pair_offset + queue_offset


def plot_points(output: Path, points: list[BatchPoint], title: str, yscale: str) -> None:
    import matplotlib.pyplot as plt

    role_colors = {
        "inside": "#4e79a7",
        "outside": "#f28e2b",
        "unknown": "#999999",
    }
    role_markers = {
        "inside": "o",
        "outside": "s",
        "unknown": "x",
    }

    fig, axes = plt.subplots(1, 2, figsize=(13, 6), sharey=True)
    x_ticks = sorted({point.n_workers for point in points})

    for ax, direction in zip(axes, ("rx", "tx"), strict=True):
        direction_points = [point for point in points if point.direction == direction]
        for iface_role in ("inside", "outside", "unknown"):
            series = [point for point in direction_points if point.iface_role == iface_role]
            if not series:
                continue
            xs = [point.n_workers + queue_jitter(point) for point in series]
            ys = [point.batch_size for point in series]
            ax.scatter(
                xs,
                ys,
                s=64,
                marker=role_markers.get(iface_role, "o"),
                color=role_colors.get(iface_role),
                edgecolors="white",
                linewidths=0.7,
                alpha=0.88,
                label=iface_role,
            )
            for x_value, y_value, point in zip(xs, ys, series, strict=True):
                pair_label = f"p{point.pair_index}" if point.pair_index >= 0 else "p?"
                ax.text(x_value + 0.012, y_value, pair_label, fontsize=7, alpha=0.72)

        ax.set_title(direction.upper())
        ax.set_xlabel("n_workers")
        ax.set_xticks(x_ticks)
        ax.set_xticklabels([str(value) for value in x_ticks])
        if yscale == "log":
            ax.set_yscale("log")
        ax.legend(title="interface")

    axes[0].set_ylabel("Average batch size, vectors/call")
    fig.suptitle(title)
    fig.tight_layout()

    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=150)
    plt.close(fig)


def main() -> int:
    args = parse_args()
    try:
        import matplotlib.pyplot as plt  # noqa: F401
    except ImportError:
        print("error: matplotlib is not installed. Install with: pip install matplotlib")
        return 1

    configure_plot_style()
    results_dir = resolve_results_dir(args.results_root, args.test_name)
    output = args.output or results_dir / "batch_size_vs_workers.png"
    tsv_output = args.tsv_output or output.with_suffix(".tsv")

    selected_steps = collect_steps(args)
    if not selected_steps:
        print(f"error: no matching history/result data found in {results_dir}")
        return 1

    points = collect_batch_points(selected_steps)
    if not points:
        print(f"error: no batch-size points found in {results_dir}")
        return 1

    title = f"{args.test_name}: RX/TX queue batch size vs workers"
    if args.yscale == "log" and any(point.batch_size <= 0 for point in points):
        print("warning: non-positive batch sizes found; falling back to linear y-scale")
        args.yscale = "linear"

    plot_points(output, points, title, args.yscale)
    print(f"saved: {output}")

    write_tsv(tsv_output, points)
    print(f"saved: {tsv_output}")
    print(f"points: {len(points)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

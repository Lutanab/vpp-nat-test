#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from viz_common import DEFAULT_RESULTS_ROOT, configure_plot_style


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LATENCY_CONFIG_PATH = REPO_ROOT / "configs" / "loadtest" / "latency" / "load" / "test_config.yaml"


@dataclass(frozen=True)
class LatencyScope:
    test_name: str
    nat_mode: str
    n_workers: int
    flow_count: int
    packet_size: int
    results_dir: Path


@dataclass(frozen=True)
class Point:
    target_pps: float
    avg_latency_usec: float
    loss_percent: float
    source_path: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot latency average and load loss dependency on target_pps for latency-test results.",
    )
    parser.add_argument(
        "--latency-config",
        type=Path,
        default=DEFAULT_LATENCY_CONFIG_PATH,
        help=f"Latency load config path (default: {DEFAULT_LATENCY_CONFIG_PATH}).",
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        default=DEFAULT_RESULTS_ROOT,
        help=f"Results root directory (default: {DEFAULT_RESULTS_ROOT}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output PNG path (default: <results-dir>/latency_loss_vs_pps_<nat_mode>.png).",
    )
    parser.add_argument(
        "--tsv-output",
        type=Path,
        default=None,
        help="Output TSV path (default: output image path with .tsv suffix).",
    )
    parser.add_argument(
        "--x-unit",
        choices=("pps", "mpps"),
        default="mpps",
        help="X-axis units (default: mpps).",
    )
    return parser.parse_args()


def load_scope(latency_config_path: Path, results_root: Path) -> LatencyScope:
    src_root = REPO_ROOT / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))

    try:
        from test_nat.config import load_simple_yaml, slugify_value
    except ModuleNotFoundError as exc:
        raise RuntimeError(f"failed to import test_nat config helpers from {src_root}") from exc

    raw = load_simple_yaml(latency_config_path)

    test_name = raw.get("test_name")
    nat_mode = raw.get("nat_mode")
    n_workers = raw.get("n_workers")
    flow_count = raw.get("flow_count")
    packet_size = raw.get("packet_size")

    if not isinstance(test_name, str) or not test_name.strip():
        raise ValueError(f"{latency_config_path}: missing or invalid test_name")
    if not isinstance(nat_mode, str) or not nat_mode.strip():
        raise ValueError(f"{latency_config_path}: missing or invalid nat_mode")
    if not isinstance(n_workers, int):
        raise ValueError(f"{latency_config_path}: n_workers must be int")
    if not isinstance(flow_count, int):
        raise ValueError(f"{latency_config_path}: flow_count must be int")
    if not isinstance(packet_size, int):
        raise ValueError(f"{latency_config_path}: packet_size must be int")

    resolved_test_name = test_name.strip()
    results_dir = results_root / f"test_name_{slugify_value(resolved_test_name)}"
    return LatencyScope(
        test_name=resolved_test_name,
        nat_mode=nat_mode.strip(),
        n_workers=n_workers,
        flow_count=flow_count,
        packet_size=packet_size,
        results_dir=results_dir,
    )


def as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def matches_scope(load_params: dict[str, Any], scope: LatencyScope) -> bool:
    return (
        load_params.get("nat_mode") == scope.nat_mode
        and load_params.get("n_workers") == scope.n_workers
        and load_params.get("flow_count") == scope.flow_count
        and load_params.get("packet_size") == scope.packet_size
    )


def extract_point(result_path: Path, payload: dict[str, Any]) -> Point | None:
    result = payload.get("result", {})
    if not isinstance(result, dict):
        return None

    target_pps = as_float(result.get("target_pps"))
    avg_latency_usec = as_float(result.get("latency_avg_usec"))
    loss_percent = as_float(result.get("total_load_loss_percent"))
    if target_pps is None or avg_latency_usec is None or loss_percent is None:
        return None
    if not (math.isfinite(target_pps) and math.isfinite(avg_latency_usec) and math.isfinite(loss_percent)):
        return None

    return Point(
        target_pps=target_pps,
        avg_latency_usec=avg_latency_usec,
        loss_percent=loss_percent,
        source_path=result_path,
    )


def collect_points(scope: LatencyScope) -> list[Point]:
    by_target_pps: dict[float, Point] = {}
    by_target_pps_mtime: dict[float, float] = {}

    for result_path in scope.results_dir.rglob("result.json"):
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        load_params = payload.get("params", {}).get("load_params", {})
        if not isinstance(load_params, dict):
            continue
        if not matches_scope(load_params, scope):
            continue

        point = extract_point(result_path, payload)
        if point is None:
            continue

        mtime = result_path.stat().st_mtime
        current_mtime = by_target_pps_mtime.get(point.target_pps)
        if current_mtime is None or mtime >= current_mtime:
            by_target_pps[point.target_pps] = point
            by_target_pps_mtime[point.target_pps] = mtime

    return [by_target_pps[key] for key in sorted(by_target_pps)]


def format_float(value: float) -> str:
    return f"{value:.12g}"


def write_tsv(output: Path, points: list[Point]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(("target_pps", "avg_latency_usec", "loss_percent", "source_result_json"))
        for point in points:
            writer.writerow(
                (
                    format_float(point.target_pps),
                    format_float(point.avg_latency_usec),
                    format_float(point.loss_percent),
                    str(point.source_path),
                )
            )


def to_x_values(points: list[Point], x_unit: str) -> list[float]:
    if x_unit == "pps":
        return [point.target_pps for point in points]
    return [point.target_pps / 1_000_000 for point in points]


def x_label(x_unit: str) -> str:
    return "Target, pps" if x_unit == "pps" else "Target, Mpps"


def default_output_path(scope: LatencyScope) -> Path:
    return scope.results_dir / f"latency_loss_vs_pps_{scope.nat_mode}.png"


def main() -> int:
    args = parse_args()

    try:
        scope = load_scope(args.latency_config, args.results_root)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: failed to resolve latency scope from {args.latency_config}: {exc}")
        return 1

    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("error: matplotlib is not installed. Install with: pip install matplotlib")
        return 1

    points = collect_points(scope)
    if not points:
        print(
            "error: no matching latency result.json files found for scope "
            f"(test_name={scope.test_name}, nat_mode={scope.nat_mode}, n_workers={scope.n_workers}, "
            f"flow_count={scope.flow_count}, packet_size={scope.packet_size}) in {scope.results_dir}"
        )
        return 1

    output = args.output or default_output_path(scope)
    tsv_output = args.tsv_output or output.with_suffix(".tsv")

    configure_plot_style()
    xs = to_x_values(points, args.x_unit)
    ys_latency = [point.avg_latency_usec for point in points]
    ys_loss = [point.loss_percent for point in points]

    fig, ax_latency = plt.subplots(figsize=(10, 6))
    ax_loss = ax_latency.twinx()

    latency_line = ax_latency.plot(
        xs,
        ys_latency,
        marker="o",
        linewidth=2,
        color="#1f77b4",
        label="avg latency, usec",
    )[0]
    loss_line = ax_loss.plot(
        xs,
        ys_loss,
        marker="s",
        linewidth=2,
        color="#d62728",
        label="loss, %",
    )[0]

    ax_latency.set_title(
        f"Latency test: {scope.test_name} | mode={scope.nat_mode}, workers={scope.n_workers}, "
        f"flow={scope.flow_count}, size={scope.packet_size}B"
    )
    ax_latency.set_xlabel(x_label(args.x_unit))
    ax_latency.set_ylabel("Average latency, usec", color=latency_line.get_color())
    ax_loss.set_ylabel("Loss, %", color=loss_line.get_color())
    ax_latency.tick_params(axis="y", labelcolor=latency_line.get_color())
    ax_loss.tick_params(axis="y", labelcolor=loss_line.get_color())

    lines = [latency_line, loss_line]
    labels = [line.get_label() for line in lines]
    ax_latency.legend(lines, labels, loc="best")

    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=150)
    print(f"saved: {output}")

    write_tsv(tsv_output, points)
    print(f"saved: {tsv_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

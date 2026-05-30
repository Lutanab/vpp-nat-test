#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_ROOT = REPO_ROOT / "results"
DEFAULT_LOAD_CONFIG_PATH = REPO_ROOT / "configs" / "loadtest" / "load" / "test_config.yaml"
NAT_MODES = ("none", "nat44", "nat_fo")
NAT_MODE_COLORS = {
    "none": "#1f77b4",
    "nat44": "#ff7f0e",
    "nat_fo": "#2ca02c",
}


@dataclass(frozen=True)
class PlotScope:
    """Describes which result subset should be used for plotting."""

    test_name: str
    results_dir: Path
    flow_count: int
    packet_size: int
    target_loss_rate: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build pps vs workers plot from load-test result.json files.",
    )
    parser.add_argument(
        "--load-config",
        type=Path,
        default=DEFAULT_LOAD_CONFIG_PATH,
        help=f"Load config path used for auto-discovery/filtering (default: {DEFAULT_LOAD_CONFIG_PATH}).",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=None,
        help=(
            "Directory with load test results. "
            "Default is inferred from --load-config: results/test_name_<test_name>."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output image path (default: <resolved results-dir>/pps_vs_workers.png).",
    )
    parser.add_argument(
        "--tsv-output",
        type=Path,
        default=None,
        help="Output TSV path (default: output image path with .tsv suffix).",
    )
    parser.add_argument(
        "--pps-key",
        choices=["actual_sent_pps", "target_pps"],
        default="actual_sent_pps",
        help="Which pps metric to plot (default: actual_sent_pps).",
    )
    parser.add_argument(
        "--no-config-filter",
        action="store_true",
        help=(
            "Disable filtering by flow_count/packet_size/target_loss_rate from --load-config. "
            "By default, only matching result.json files are used."
        ),
    )
    return parser.parse_args()


def load_plot_scope(load_config_path: Path) -> PlotScope:
    """Loads test_name + filter fields from load config."""
    src_root = REPO_ROOT / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))

    try:
        from test_nat.config import load_simple_yaml, slugify_value
    except ModuleNotFoundError as exc:
        raise RuntimeError(f"failed to import test_nat config helpers from {src_root}") from exc

    raw = load_simple_yaml(load_config_path)
    test_name_raw = raw.get("test_name")
    if not isinstance(test_name_raw, str) or not test_name_raw.strip():
        raise ValueError(f"{load_config_path}: missing or invalid test_name")

    flow_count = raw.get("flow_count")
    packet_size = raw.get("packet_size")
    target_loss_rate = raw.get("target_loss_rate")
    if not isinstance(flow_count, int):
        raise ValueError(f"{load_config_path}: flow_count must be int")
    if not isinstance(packet_size, int):
        raise ValueError(f"{load_config_path}: packet_size must be int")
    if not isinstance(target_loss_rate, (int, float)):
        raise ValueError(f"{load_config_path}: target_loss_rate must be numeric")

    test_name = test_name_raw.strip()
    results_dir = RESULTS_ROOT / f"test_name_{slugify_value(test_name)}"
    return PlotScope(
        test_name=test_name,
        results_dir=results_dir,
        flow_count=flow_count,
        packet_size=packet_size,
        target_loss_rate=float(target_loss_rate),
    )


def matches_scope(load_params: dict, scope: PlotScope) -> bool:
    """Checks that result.json belongs to the same config family."""
    flow_count = load_params.get("flow_count")
    packet_size = load_params.get("packet_size")
    target_loss_rate = load_params.get("target_loss_rate")
    if not isinstance(flow_count, int):
        return False
    if not isinstance(packet_size, int):
        return False
    if not isinstance(target_loss_rate, (int, float)):
        return False
    if flow_count != scope.flow_count:
        return False
    if packet_size != scope.packet_size:
        return False
    return math.isclose(float(target_loss_rate), scope.target_loss_rate, rel_tol=1e-12, abs_tol=1e-12)


def collect_points(
    results_dir: Path,
    pps_key: str,
    scope: PlotScope | None,
) -> tuple[dict[str, list[tuple[int, float]]], int, int]:
    series: dict[str, dict[int, float]] = {mode: {} for mode in NAT_MODES}
    total_files = 0
    matched_files = 0

    for result_path in results_dir.rglob("result.json"):
        total_files += 1
        payload = json.loads(result_path.read_text(encoding="utf-8"))

        load_params = payload.get("params", {}).get("load_params", {})
        if scope is not None and not matches_scope(load_params, scope):
            continue
        matched_files += 1

        result = payload.get("result", {})

        nat_mode = load_params.get("nat_mode")
        n_workers = load_params.get("n_workers")
        pps = result.get(pps_key)

        if nat_mode not in series:
            continue
        if not isinstance(n_workers, int):
            continue
        if not isinstance(pps, (int, float)):
            continue

        series[nat_mode][n_workers] = float(pps)

    return {mode: sorted(points.items()) for mode, points in series.items()}, total_files, matched_files


def write_tsv(output: Path, points: dict[str, list[tuple[int, float]]]) -> None:
    workers = sorted({n_workers for xy in points.values() for n_workers, _ in xy})
    values_by_mode = {mode: dict(xy) for mode, xy in points.items()}

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(("n_workers", *NAT_MODES))
        for n_workers in workers:
            writer.writerow(
                (
                    n_workers,
                    *(format_pps(values_by_mode[mode].get(n_workers)) for mode in NAT_MODES),
                ),
            )


def format_pps(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.15g}"


def infer_test_name(results_dir: Path) -> str:
    """Infer displayed test_name from a result directory path."""
    for part in reversed(results_dir.parts):
        if part.startswith("test_name_"):
            return part.removeprefix("test_name_")
    return results_dir.name


def main() -> int:
    args = parse_args()

    try:
        scope = load_plot_scope(args.load_config)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: failed to resolve plot scope from {args.load_config}: {exc}")
        return 1

    resolved_results_dir = args.results_dir or scope.results_dir
    resolved_output = args.output or (resolved_results_dir / "pps_vs_workers.png")
    tsv_output = args.tsv_output or resolved_output.with_suffix(".tsv")
    active_scope = None if args.no_config_filter else scope

    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("error: matplotlib is not installed. Install with: pip install matplotlib")
        return 1

    points, total_files, matched_files = collect_points(resolved_results_dir, args.pps_key, active_scope)

    if not any(points.values()):
        if active_scope is None:
            print(f"error: no valid result.json files found in {resolved_results_dir}")
        else:
            print(
                "error: no valid result.json files found for current load-config scope "
                f"in {resolved_results_dir} (scanned={total_files}, matched_scope={matched_files})"
            )
        return 1

    plt.figure(figsize=(10, 6))

    for mode in NAT_MODES:
        xy = points[mode]
        if not xy:
            print(f"warning: no data for mode '{mode}'")
            continue

        xs = [x for x, _ in xy]
        ys = [y for _, y in xy]

        plt.plot(xs, ys, marker="o", linewidth=2, label=mode, color=NAT_MODE_COLORS[mode])

    title_name = scope.test_name if args.results_dir is None else infer_test_name(resolved_results_dir)
    plt.title(f"{title_name}: PPS vs Number of Workers ({args.pps_key})")
    plt.xlabel("n_workers")
    plt.ylabel("pps")
    plt.grid(True, linestyle="--", alpha=0.5)
    plt.legend(title="nat_mode")
    plt.tight_layout()

    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(resolved_output, dpi=150)
    print(f"saved: {resolved_output}")

    write_tsv(tsv_output, points)
    print(f"saved: {tsv_output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

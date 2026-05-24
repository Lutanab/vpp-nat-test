#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_NAME = "multiflow_64"
RESULTS_ROOT = REPO_ROOT / "results"
DEFAULT_RESULTS_DIR = RESULTS_ROOT / f"test_name_{TEST_NAME}"
DEFAULT_OUTPUT = DEFAULT_RESULTS_DIR / "pps_vs_workers.png"
NAT_MODES = ("none", "nat44", "nat_fo")
NAT_MODE_COLORS = {
    "none": "#1f77b4",
    "nat44": "#ff7f0e",
    "nat_fo": "#2ca02c",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build pps vs workers plot from load-test result.json files.",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=DEFAULT_RESULTS_DIR,
        help=f"Directory with load test results (default: {DEFAULT_RESULTS_DIR}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output image path (default: {DEFAULT_OUTPUT}).",
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
    return parser.parse_args()


def collect_points(results_dir: Path, pps_key: str) -> dict[str, list[tuple[int, float]]]:
    series: dict[str, dict[int, float]] = {mode: {} for mode in NAT_MODES}

    for result_path in results_dir.rglob("result.json"):
        payload = json.loads(result_path.read_text(encoding="utf-8"))

        load_params = payload.get("params", {}).get("load_params", {})
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

    return {mode: sorted(points.items()) for mode, points in series.items()}


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


def main() -> int:
    args = parse_args()
    tsv_output = args.tsv_output or args.output.with_suffix(".tsv")

    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("error: matplotlib is not installed. Install with: pip install matplotlib")
        return 1

    points = collect_points(args.results_dir, args.pps_key)

    if not any(points.values()):
        print(f"error: no valid result.json files found in {args.results_dir}")
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

    plt.title(f"{TEST_NAME}: PPS vs Number of Workers ({args.pps_key})")
    plt.xlabel("n_workers")
    plt.ylabel("pps")
    plt.grid(True, linestyle="--", alpha=0.5)
    plt.legend(title="nat_mode")
    plt.tight_layout()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(args.output, dpi=150)
    print(f"saved: {args.output}")

    write_tsv(tsv_output, points)
    print(f"saved: {tsv_output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

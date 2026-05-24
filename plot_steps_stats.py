#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def load_results_dir(n_workers: int | None) -> Path:
    repo_root = Path(__file__).resolve().parent
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    from test_nat.config import build_results_dir, load_test_configs

    config, _search, _load_path, _search_path = load_test_configs(None, None)
    if n_workers is not None:
        config = replace(config, n_workers=n_workers)
    return build_results_dir(config)


def parse_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def load_steps(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return [
            {str(key).strip(): str(value).strip() for key, value in row.items() if key is not None}
            for row in reader
        ]


def boundary_pps(results_dir: Path, steps: list[dict[str, str]]) -> float | None:
    result_path = results_dir / "result.json"
    if result_path.exists():
        data = json.loads(result_path.read_text(encoding="utf-8"))
        value = data.get("result", {}).get("target_pps")
        if isinstance(value, (int, float)):
            return float(value)

    passing_targets = [
        target
        for row in steps
        if row.get("verdict") == "PASS"
        if (target := parse_float(row.get("target_pps"))) is not None
    ]
    return max(passing_targets) if passing_targets else None


def plot_steps(results_dir: Path, output_path: Path) -> None:
    steps_path = results_dir / "steps_stats.tsv"
    if not steps_path.exists():
        raise FileNotFoundError(f"steps_stats.tsv not found: {steps_path}")

    steps = load_steps(steps_path)
    if not steps:
        raise RuntimeError(f"steps_stats.tsv is empty: {steps_path}")

    rx_columns = [column for column in steps[0] if column.endswith("_rx_usage_perc")]
    if not rx_columns:
        raise RuntimeError("steps_stats.tsv has no *_rx_usage_perc columns")

    points: list[dict[str, Any]] = []
    for row in steps:
        target_pps = parse_float(row.get("target_pps"))
        loss_percent = parse_float(row.get("loss_percent"))
        if target_pps is None or loss_percent is None:
            continue
        points.append({"target_pps": target_pps, "loss_percent": loss_percent, "row": row})

    if not points:
        raise RuntimeError("steps_stats.tsv has no plottable rows")

    points.sort(key=lambda item: item["target_pps"])
    x_values = [point["target_pps"] for point in points]

    plt.figure(figsize=(14, 8))
    plt.plot(
        x_values,
        [point["loss_percent"] for point in points],
        marker="o",
        linewidth=2.5,
        color="black",
        label="loss_percent",
    )

    for column in rx_columns:
        y_values = [parse_float(point["row"].get(column)) for point in points]
        plt.plot(x_values, y_values, marker=".", linewidth=1.3, label=column)

    pps_boundary = boundary_pps(results_dir, steps)
    if pps_boundary is not None:
        plt.axvline(pps_boundary, color="red", linestyle="--", linewidth=2)
        plt.text(
            pps_boundary,
            102,
            f"boundary {pps_boundary:.0f} pps",
            color="red",
            rotation=90,
            va="top",
            ha="right",
        )

    plt.title(results_dir.name)
    plt.xlabel("target_pps")
    plt.ylabel("percent")
    plt.ylim(0, 105)
    plt.grid(True, alpha=0.3)
    plt.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), fontsize="small")
    plt.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=160)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-workers", type=int, default=None, help="Override n_workers from load config")
    parser.add_argument("--output", type=Path, default=None, help="Output PNG path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    results_dir = load_results_dir(args.n_workers)
    output_path = args.output or (results_dir / "steps_stats_plot.png")
    plot_steps(results_dir, output_path)
    print(f"written: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from math import sqrt
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = REPO_ROOT / "virtual_machines/host_mounts/external_vm/ha-test/results"
ARRIVALS_FILE = "arrivals.csv"
STATS_FILE = "arrival_gap_stats.csv"
TOP_GAP_COUNT = 5


@dataclass(frozen=True)
class Gap:
    rank: int
    after_packet: int
    before_seconds: float
    after_seconds: float
    gap_ms: float


@dataclass(frozen=True)
class GapStats:
    total_gap_count: int
    excluded_top_gap_count: int
    included_gap_count: int
    mean_gap_ms: float | None
    stddev_population_gap_ms: float | None


def result_dirs() -> list[Path]:
    if not RESULTS_ROOT.exists():
        return []
    return sorted(
        (path for path in RESULTS_ROOT.iterdir() if (path / ARRIVALS_FILE).is_file()),
        key=lambda path: path.name,
    )


def choose_result_dir(paths: list[Path]) -> Path:
    print("Available UDP receiver measurements:\n")
    for index, path in enumerate(paths, start=1):
        print(f"{index:2d}. {path.name}")
    while True:
        raw = input("\nSelect measurement number: ").strip()
        try:
            selected = int(raw)
        except ValueError:
            print("Please enter a number from the list.")
            continue
        if 1 <= selected <= len(paths):
            return paths[selected - 1]
        print("Number is outside the list.")


def read_arrivals(path: Path) -> list[int]:
    arrivals: list[int] = []
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        if "monotonic_ns" not in (reader.fieldnames or []):
            raise ValueError(f"{path} has no monotonic_ns column")
        for row in reader:
            value = row.get("monotonic_ns", "").strip()
            if value:
                arrivals.append(int(value))
    return arrivals


def arrival_gaps(arrivals: list[int]) -> list[Gap]:
    first = arrivals[0]
    gaps: list[Gap] = []
    for index, (before, after) in enumerate(zip(arrivals, arrivals[1:]), start=1):
        gaps.append(
            Gap(
                rank=0,
                after_packet=index,
                before_seconds=(before - first) / 1_000_000_000,
                after_seconds=(after - first) / 1_000_000_000,
                gap_ms=(after - before) / 1_000_000,
            )
        )
    return gaps


def top_gaps(gaps: list[Gap], limit: int = TOP_GAP_COUNT) -> list[Gap]:
    return [
        replace(gap, rank=rank)
        for rank, gap in enumerate(sorted(gaps, key=lambda gap: gap.gap_ms, reverse=True)[:limit], start=1)
    ]


def gap_stats_excluding_top(gaps: list[Gap], top_count: int = TOP_GAP_COUNT) -> GapStats:
    sorted_gaps = sorted(gaps, key=lambda gap: gap.gap_ms, reverse=True)
    included_gaps = sorted_gaps[min(top_count, len(sorted_gaps)) :]
    values = [gap.gap_ms for gap in included_gaps]

    mean_gap_ms: float | None = None
    stddev_population_gap_ms: float | None = None
    if values:
        mean_gap_ms = sum(values) / len(values)
        variance = sum((value - mean_gap_ms) ** 2 for value in values) / len(values)
        stddev_population_gap_ms = sqrt(variance)

    return GapStats(
        total_gap_count=len(gaps),
        excluded_top_gap_count=min(top_count, len(gaps)),
        included_gap_count=len(values),
        mean_gap_ms=mean_gap_ms,
        stddev_population_gap_ms=stddev_population_gap_ms,
    )


def print_gap_table(gaps: list[Gap]) -> None:
    print(f"\nTop {TOP_GAP_COUNT} timestamp gaps:")
    print("rank  after_packet  before_s    after_s     gap_ms")
    print("----  ------------  ----------  ----------  ----------")
    for gap in gaps:
        print(
            f"{gap.rank:>4}  {gap.after_packet:>12}  "
            f"{gap.before_seconds:>10.6f}  {gap.after_seconds:>10.6f}  {gap.gap_ms:>10.3f}"
        )


def print_gap_stats(stats: GapStats) -> None:
    print(f"\nGap stats excluding top {stats.excluded_top_gap_count} gaps:")
    print(f"total gaps:              {stats.total_gap_count}")
    print(f"included gaps:           {stats.included_gap_count}")
    if stats.mean_gap_ms is None or stats.stddev_population_gap_ms is None:
        print("mean gap ms:             n/a")
        print("stddev population ms:    n/a")
        return
    print(f"mean gap ms:             {stats.mean_gap_ms:.6f}")
    print(f"stddev population ms:    {stats.stddev_population_gap_ms:.6f}")


def save_gap_stats(run_dir: Path, stats: GapStats) -> Path:
    output_path = run_dir / STATS_FILE
    mean_gap_ms = "" if stats.mean_gap_ms is None else f"{stats.mean_gap_ms:.9f}"
    stddev_population_gap_ms = (
        "" if stats.stddev_population_gap_ms is None else f"{stats.stddev_population_gap_ms:.9f}"
    )
    with output_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "total_gap_count",
                "excluded_top_gap_count",
                "included_gap_count",
                "mean_gap_ms",
                "stddev_population_gap_ms",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "total_gap_count": stats.total_gap_count,
                "excluded_top_gap_count": stats.excluded_top_gap_count,
                "included_gap_count": stats.included_gap_count,
                "mean_gap_ms": mean_gap_ms,
                "stddev_population_gap_ms": stddev_population_gap_ms,
            }
        )
    return output_path


def plot_arrivals(run_dir: Path, gaps: list[Gap]) -> Path:
    try:
        import matplotlib.pyplot as plt
    except ModuleNotFoundError as exc:
        raise SystemExit("matplotlib is required. Run this script via `uv run python tools/plot_udp_downtime.py`.") from exc

    times_s = [gap.after_seconds for gap in gaps]
    gaps_ms = [gap.gap_ms for gap in gaps]

    fig, ax = plt.subplots(figsize=(14, 6))
    ax.plot(times_s, gaps_ms, linewidth=0.8)
    ax.scatter(times_s, gaps_ms, s=6)
    ax.set_title(f"UDP inter-arrival gaps: {run_dir.name}")
    ax.set_xlabel("Time since first received packet, seconds")
    ax.set_ylabel("Gap between received packets, ms")
    ax.grid(True, alpha=0.35)
    fig.tight_layout()

    output_path = run_dir / "arrival_gaps.png"
    fig.savefig(output_path, dpi=150)
    plt.show()
    return output_path


def main() -> None:
    paths = result_dirs()
    if not paths:
        raise SystemExit(f"No measurements with {ARRIVALS_FILE} found in {RESULTS_ROOT}")

    run_dir = choose_result_dir(paths)
    arrivals_path = run_dir / ARRIVALS_FILE
    arrivals = read_arrivals(arrivals_path)
    if len(arrivals) < 2:
        raise SystemExit(f"{arrivals_path} has fewer than two timestamps")

    print(f"\nSelected: {run_dir.name}")
    print(f"Packets received: {len(arrivals)}")
    gaps = arrival_gaps(arrivals)
    print_gap_table(top_gaps(gaps))
    stats = gap_stats_excluding_top(gaps)
    print_gap_stats(stats)
    stats_path = save_gap_stats(run_dir, stats)
    output_path = plot_arrivals(run_dir, gaps)
    print(f"\nSaved stats: {stats_path}")
    print(f"Saved plot: {output_path}")


if __name__ == "__main__":
    main()

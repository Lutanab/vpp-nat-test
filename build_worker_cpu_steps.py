#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build per-step worker CPU time TSV from history.json + raw worker CPU samples.")
    parser.add_argument("--results-dir", type=Path, default=None, help="Use explicit results directory")
    parser.add_argument("--n-workers", type=int, default=None, help="Override n_workers")
    parser.add_argument("--load-config", type=Path, default=None, help="Optional load config path")
    parser.add_argument("--search-config", type=Path, default=None, help="Optional search config path")
    parser.add_argument("--history", type=Path, default=None, help="Path to history.json")
    parser.add_argument("--cpu-raw", type=Path, default=None, help="Path to raw worker CPU TSV")
    parser.add_argument("--output", type=Path, default=None, help="Output TSV path")
    return parser.parse_args()


def ensure_src_import_path() -> None:
    repo_root = Path(__file__).resolve().parent
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


def resolve_default_results_dir(
    n_workers_override: int | None,
    load_config_path: Path | None,
    search_config_path: Path | None,
) -> Path:
    ensure_src_import_path()
    from test_nat.config import build_results_dir, load_test_configs

    config, _search, _resolved_load, _resolved_search = load_test_configs(load_config_path, search_config_path)
    if n_workers_override is not None:
        config = replace(config, n_workers=n_workers_override)
    return build_results_dir(config)


def parse_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def load_raw_cpu_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            timestamp_epoch = parse_float(row.get("timestamp_epoch"))
            worker_index = parse_int(row.get("worker_index"))
            cpu_time_sec = parse_float(row.get("cpu_time_sec"))
            if timestamp_epoch is None or worker_index is None or cpu_time_sec is None:
                continue
            rows.append(
                {
                    "timestamp_epoch": timestamp_epoch,
                    "worker_index": worker_index,
                    "cpu_time_sec": cpu_time_sec,
                    "thread": row.get("thread") or "",
                }
            )
    return rows


def build_series_by_worker(rows: list[dict[str, Any]]) -> dict[int, list[tuple[float, float]]]:
    by_worker: dict[int, list[tuple[float, float]]] = {}
    for row in rows:
        worker_index = row["worker_index"]
        by_worker.setdefault(worker_index, []).append((row["timestamp_epoch"], row["cpu_time_sec"]))

    for worker_index, samples in by_worker.items():
        samples.sort(key=lambda item: item[0])
        deduped: list[tuple[float, float]] = []
        for timestamp, cpu_time in samples:
            if deduped and math.isclose(timestamp, deduped[-1][0], rel_tol=0.0, abs_tol=1e-12):
                deduped[-1] = (timestamp, cpu_time)
            else:
                deduped.append((timestamp, cpu_time))
        by_worker[worker_index] = deduped

    return by_worker


def interpolate_cpu_time(samples: list[tuple[float, float]], target_timestamp: float) -> float:
    if not samples:
        raise ValueError("cannot interpolate empty sample list")

    if target_timestamp <= samples[0][0]:
        if target_timestamp < samples[0][0]:
            raise RuntimeError(
                "window starts before first available CPU sample: "
                f"window_ts={target_timestamp:.9f}, first_sample_ts={samples[0][0]:.9f}"
            )
        return samples[0][1]

    if target_timestamp >= samples[-1][0]:
        if target_timestamp > samples[-1][0]:
            raise RuntimeError(
                "window ends after last available CPU sample: "
                f"window_ts={target_timestamp:.9f}, last_sample_ts={samples[-1][0]:.9f}"
            )
        return samples[-1][1]

    left = 0
    right = len(samples) - 1
    while left + 1 < right:
        mid = (left + right) // 2
        mid_ts = samples[mid][0]
        if mid_ts <= target_timestamp:
            left = mid
        else:
            right = mid

    left_ts, left_cpu = samples[left]
    right_ts, right_cpu = samples[right]

    if math.isclose(target_timestamp, left_ts, rel_tol=0.0, abs_tol=1e-12):
        return left_cpu
    if math.isclose(target_timestamp, right_ts, rel_tol=0.0, abs_tol=1e-12):
        return right_cpu

    span = right_ts - left_ts
    if span <= 0:
        return right_cpu

    ratio = (target_timestamp - left_ts) / span
    return left_cpu + (right_cpu - left_cpu) * ratio


def pick_worker_count(
    args_n_workers: int | None,
    history_payload: dict[str, Any],
    load_config_n_workers: int | None,
) -> int:
    if args_n_workers is not None:
        return args_n_workers

    history_workers = (
        history_payload.get("params", {})
        .get("load_params", {})
        .get("n_workers")
    )
    if isinstance(history_workers, int):
        return history_workers

    if load_config_n_workers is not None:
        return load_config_n_workers

    raise RuntimeError("Could not determine n_workers from args/history/config")


def resolve_config_n_workers(load_config_path: Path | None, search_config_path: Path | None) -> int | None:
    ensure_src_import_path()
    from test_nat.config import load_test_configs

    config, _search, _resolved_load, _resolved_search = load_test_configs(load_config_path, search_config_path)
    return config.n_workers


def main() -> int:
    args = parse_args()

    try:
        if args.results_dir is not None:
            results_dir = args.results_dir
        else:
            results_dir = resolve_default_results_dir(
                n_workers_override=args.n_workers,
                load_config_path=args.load_config,
                search_config_path=args.search_config,
            )

        history_path = args.history or (results_dir / "history.json")
        raw_cpu_path = args.cpu_raw or (results_dir / "worker_cpu_raw.tsv")
        output_path = args.output or (results_dir / "worker_cpu_steps.tsv")

        if not history_path.exists():
            raise FileNotFoundError(f"history.json not found: {history_path}")
        if not raw_cpu_path.exists():
            raise FileNotFoundError(f"worker cpu raw TSV not found: {raw_cpu_path}")

        history_payload = json.loads(history_path.read_text(encoding="utf-8"))
        steps = history_payload.get("result", [])
        if not isinstance(steps, list):
            raise RuntimeError("history.json has invalid format: result must be a list")

        config_workers: int | None = None
        history_workers = (
            history_payload.get("params", {})
            .get("load_params", {})
            .get("n_workers")
        )
        if args.n_workers is None and not isinstance(history_workers, int):
            config_workers = resolve_config_n_workers(args.load_config, args.search_config)
        n_workers = pick_worker_count(args.n_workers, history_payload, config_workers)
        if n_workers <= 0:
            raise RuntimeError(f"n_workers must be > 0, got {n_workers}")

        raw_rows = load_raw_cpu_rows(raw_cpu_path)
        if not raw_rows:
            raise RuntimeError(f"No parsable rows in raw CPU TSV: {raw_cpu_path}")

        series_by_worker = build_series_by_worker(raw_rows)
        missing_workers = [index for index in range(n_workers) if index not in series_by_worker]
        if missing_workers:
            rendered = ", ".join(str(index) for index in missing_workers)
            raise RuntimeError(f"Raw CPU TSV has no samples for required workers: {rendered}")

        fieldnames = [
            "step_index",
            "phase",
            "target_pps",
            "passed",
            "wall_time_sec",
            "cpu_time_sec",
            "avg_used_cores",
            "avg_worker_utilization_percent",
        ]
        for worker_index in range(n_workers):
            fieldnames.append(f"worker_{worker_index}_cpu_time_sec")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
            writer.writeheader()

            for step in steps:
                start = step.get("measurement_start_epoch")
                end = step.get("measurement_end_epoch")
                if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                    raise RuntimeError(
                        "history.json step has no measurement window; "
                        "run load test with measurement_start_epoch/measurement_end_epoch"
                    )
                if end < start:
                    raise RuntimeError(
                        "history.json step has invalid window: "
                        f"step_index={step.get('step_index')}, start={start}, end={end}"
                    )

                wall_time_sec = float(end - start)
                row: dict[str, Any] = {
                    "step_index": step.get("step_index"),
                    "phase": step.get("phase"),
                    "target_pps": step.get("target_pps"),
                    "passed": step.get("passed"),
                    "wall_time_sec": f"{wall_time_sec:.9f}",
                }

                total_cpu_time = 0.0
                for worker_index in range(n_workers):
                    samples = series_by_worker[worker_index]
                    start_cpu = interpolate_cpu_time(samples, float(start))
                    end_cpu = interpolate_cpu_time(samples, float(end))
                    delta = max(0.0, end_cpu - start_cpu)
                    row[f"worker_{worker_index}_cpu_time_sec"] = f"{delta:.9f}"
                    total_cpu_time += delta

                avg_used_cores = (total_cpu_time / wall_time_sec) if wall_time_sec > 0 else 0.0
                avg_worker_utilization_percent = (avg_used_cores / n_workers * 100.0) if n_workers > 0 else 0.0

                row["cpu_time_sec"] = f"{total_cpu_time:.9f}"
                row["avg_used_cores"] = f"{avg_used_cores:.9f}"
                row["avg_worker_utilization_percent"] = f"{avg_worker_utilization_percent:.9f}"
                writer.writerow(row)

        print(f"written: {output_path}")
        return 0
    except Exception as exc:
        print(f"build-worker-cpu-steps: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

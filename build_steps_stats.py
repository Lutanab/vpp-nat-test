#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

VPP_MEMIF_ROLE = "master"


def load_results_dir() -> Path:
    repo_root = Path(__file__).resolve().parent
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    from test_nat.config import build_results_dir, load_test_configs

    config, _search, _load_path, _search_path = load_test_configs(None, None)
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


def load_metrics_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            timestamp = parse_float(row.get("timestamp_epoch"))
            if timestamp is None:
                continue
            row["timestamp_epoch"] = timestamp
            rows.append(row)
    return rows


def queue_delta(head: int, tail: int, ring_size: int) -> int:
    delta = (head - tail) & 0xFFFF
    if delta > ring_size:
        delta = ring_size
    return delta


def queue_used_percent(head: int, tail: int, ring_size: int, ring_direction: str, vpp_role: str) -> float:
    delta = queue_delta(head=head, tail=tail, ring_size=ring_size)
    if vpp_role == "master":
        used = ring_size - delta if ring_direction == "master-to-slave" else delta
    else:
        used = delta if ring_direction == "master-to-slave" else ring_size - delta
    if used < 0:
        used = 0
    if used > ring_size:
        used = ring_size
    return (used * 100.0) / ring_size


def tx_rx_directions(vpp_role: str) -> tuple[str, str]:
    if vpp_role == "master":
        return ("master-to-slave", "slave-to-master")
    return ("slave-to-master", "master-to-slave")


def average_queue_used_percent(
    rows: list[dict[str, Any]],
    prefix: str,
    ring_direction: str,
    vpp_role: str,
    ring_index: int | None = None,
) -> float | None:
    values: list[float] = []
    for row in rows:
        if row.get("source") != "memif":
            continue
        interface = row.get("interface") or ""
        if not interface.startswith(prefix):
            continue
        if row.get("ring_direction") != ring_direction:
            continue
        current_ring_index = parse_int(row.get("ring_index"))
        if ring_index is not None and current_ring_index != ring_index:
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
                vpp_role=vpp_role,
            )
        )
    if not values:
        return None
    return sum(values) / len(values)


def worker_packets_from_runtime(rows: list[dict[str, Any]]) -> dict[str, int]:
    per_worker_samples: dict[str, list[tuple[float, int]]] = {}
    for row in rows:
        if row.get("source") != "runtime":
            continue
        if row.get("node") != "memif-input":
            continue
        worker = row.get("thread") or ""
        if not worker:
            continue
        vectors = parse_int(row.get("vectors"))
        timestamp = row.get("timestamp_epoch")
        if vectors is None or not isinstance(timestamp, float):
            continue
        per_worker_samples.setdefault(worker, []).append((timestamp, vectors))

    packets: dict[str, int] = {}
    for worker, samples in per_worker_samples.items():
        if not samples:
            packets[worker] = 0
            continue
        samples.sort(key=lambda item: item[0])
        total = 0
        previous = samples[0][1]
        for _, current in samples[1:]:
            diff = current - previous
            if diff >= 0:
                total += diff
            previous = current
        packets[worker] = total
    return packets


def all_workers(metrics_rows: list[dict[str, Any]]) -> list[str]:
    workers = {
        row.get("thread") or ""
        for row in metrics_rows
        if row.get("source") == "runtime" and row.get("node") == "memif-input" and (row.get("thread") or "")
    }
    return sorted(workers)


def queue_indices(metrics_rows: list[dict[str, Any]], prefix: str) -> list[int]:
    indices = {
        ring_index
        for row in metrics_rows
        if row.get("source") == "memif"
        and (row.get("interface") or "").startswith(prefix)
        and (ring_index := parse_int(row.get("ring_index"))) is not None
    }
    return sorted(indices)


def main() -> int:
    results_dir = load_results_dir()
    history_path = results_dir / "history.json"
    metrics_path = results_dir / "metrics.tsv"
    output_path = results_dir / "steps_stats.tsv"

    if not history_path.exists():
        raise FileNotFoundError(f"history.json not found: {history_path}")
    if not metrics_path.exists():
        raise FileNotFoundError(f"metrics.tsv not found: {metrics_path}")

    history = json.loads(history_path.read_text(encoding="utf-8"))
    steps = history.get("result", [])
    if not isinstance(steps, list):
        raise RuntimeError("history.json has invalid format: result must be a list")

    metrics_rows = load_metrics_rows(metrics_path)
    tx_direction, rx_direction = tx_rx_directions(VPP_MEMIF_ROLE)
    workers = all_workers(metrics_rows)
    memif10_queues = queue_indices(metrics_rows, "memif10/")
    memif20_queues = queue_indices(metrics_rows, "memif20/")

    fieldnames = [
        "step_index",
        "target_pps",
        "verdict",
        "loss_percent",
    ]
    for queue_id in memif10_queues:
        fieldnames.append(f"memif10_q{queue_id}_tx_usage_perc")
        fieldnames.append(f"memif10_q{queue_id}_rx_usage_perc")
    for queue_id in memif20_queues:
        fieldnames.append(f"memif20_q{queue_id}_tx_usage_perc")
        fieldnames.append(f"memif20_q{queue_id}_rx_usage_perc")
    fieldnames.extend(f"worker_packets_{worker}" for worker in workers)

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

            step_rows = [
                row
                for row in metrics_rows
                if isinstance(row.get("timestamp_epoch"), float) and start <= row["timestamp_epoch"] <= end
            ]

            worker_packets = worker_packets_from_runtime(step_rows)
            row: dict[str, Any] = {
                "step_index": step.get("step_index"),
                "target_pps": step.get("target_pps"),
                "verdict": "PASS" if step.get("passed") else "FAILED",
                "loss_percent": step.get("loss_percent"),
            }

            for queue_id in memif10_queues:
                tx_avg = average_queue_used_percent(
                    step_rows,
                    "memif10/",
                    tx_direction,
                    vpp_role=VPP_MEMIF_ROLE,
                    ring_index=queue_id,
                )
                rx_avg = average_queue_used_percent(
                    step_rows,
                    "memif10/",
                    rx_direction,
                    vpp_role=VPP_MEMIF_ROLE,
                    ring_index=queue_id,
                )
                row[f"memif10_q{queue_id}_tx_usage_perc"] = "" if tx_avg is None else f"{tx_avg:.6f}"
                row[f"memif10_q{queue_id}_rx_usage_perc"] = "" if rx_avg is None else f"{rx_avg:.6f}"

            for queue_id in memif20_queues:
                tx_avg = average_queue_used_percent(
                    step_rows,
                    "memif20/",
                    tx_direction,
                    vpp_role=VPP_MEMIF_ROLE,
                    ring_index=queue_id,
                )
                rx_avg = average_queue_used_percent(
                    step_rows,
                    "memif20/",
                    rx_direction,
                    vpp_role=VPP_MEMIF_ROLE,
                    ring_index=queue_id,
                )
                row[f"memif20_q{queue_id}_tx_usage_perc"] = "" if tx_avg is None else f"{tx_avg:.6f}"
                row[f"memif20_q{queue_id}_rx_usage_perc"] = "" if rx_avg is None else f"{rx_avg:.6f}"

            for worker in workers:
                row[f"worker_packets_{worker}"] = worker_packets.get(worker, 0)

            writer.writerow(row)

    print(f"written: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

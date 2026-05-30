#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

VPP_MEMIF_ROLE = "master"
MEMIF_INTERFACES = ("memif10", "memif20")
WORKER_INDEX_RE = re.compile(r"(\d+)$")
MEMIF_IFACE_RE = re.compile(r"^memif(?P<socket_id>\d+)/(?P<interface_id>\d+)$")


def load_results_dir(n_workers: int | None = None) -> Path:
    repo_root = Path(__file__).resolve().parent
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    from test_nat.config import build_results_dir, load_test_configs

    config, _search, _load_path, _search_path = load_test_configs(None, None)
    if n_workers is not None:
        config = replace(config, n_workers=n_workers)
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


def runtime_deltas_by_worker(rows: list[dict[str, Any]], node: str) -> dict[str, tuple[int, int]]:
    per_worker_samples: dict[str, list[tuple[float, int, int]]] = {}
    for row in rows:
        if row.get("source") != "runtime":
            continue
        if row.get("node") != node:
            continue
        worker = row.get("thread") or ""
        if not worker:
            continue
        calls = parse_int(row.get("calls"))
        vectors = parse_int(row.get("vectors"))
        timestamp = row.get("timestamp_epoch")
        if calls is None or vectors is None or not isinstance(timestamp, float):
            continue
        per_worker_samples.setdefault(worker, []).append((timestamp, calls, vectors))

    deltas: dict[str, tuple[int, int]] = {}
    for worker, samples in per_worker_samples.items():
        samples.sort(key=lambda item: item[0])
        total_calls = 0
        total_vectors = 0
        previous_calls = samples[0][1] if samples else 0
        previous_vectors = samples[0][2] if samples else 0
        for _, current_calls, current_vectors in samples[1:]:
            calls_diff = current_calls - previous_calls
            vectors_diff = current_vectors - previous_vectors
            if calls_diff >= 0 and vectors_diff >= 0:
                total_calls += calls_diff
                total_vectors += vectors_diff
            previous_calls = current_calls
            previous_vectors = current_vectors
        deltas[worker] = (total_calls, total_vectors)
    return deltas


def runtime_batch_sizes_by_worker(rows: list[dict[str, Any]], node: str) -> dict[str, float]:
    batch_sizes: dict[str, float] = {}
    for worker, (calls, vectors) in runtime_deltas_by_worker(rows, node).items():
        if calls > 0:
            batch_sizes[worker] = vectors / calls
    return batch_sizes


def all_workers(metrics_rows: list[dict[str, Any]]) -> list[str]:
    workers = {
        row.get("thread") or ""
        for row in metrics_rows
        if row.get("source") == "runtime" and row.get("node") == "memif-input" and (row.get("thread") or "")
    }
    return sorted(workers)


def worker_for_queue(workers: list[str], queue_id: int) -> str | None:
    return worker_for_index(workers, queue_id)


def worker_for_index(workers: list[str], worker_index: int | None) -> str | None:
    if worker_index is None or worker_index < 0:
        return None
    by_index: dict[int, str] = {}
    for worker in workers:
        match = WORKER_INDEX_RE.search(worker)
        if match is None:
            continue
        by_index[int(match.group(1))] = worker
    if worker_index in by_index:
        return by_index[worker_index]
    if worker_index >= len(workers):
        return None
    return workers[worker_index]


def memif_interfaces(metrics_rows: list[dict[str, Any]]) -> list[str]:
    interfaces = {
        (row.get("interface") or "")
        for row in metrics_rows
        if row.get("source") == "memif" and (row.get("interface") or "")
    }

    def sort_key(interface: str) -> tuple[int, int]:
        match = MEMIF_IFACE_RE.match(interface)
        if match is None:
            return (1_000_000, 1_000_000)
        return (int(match.group("socket_id")), int(match.group("interface_id")))

    return sorted(interfaces, key=sort_key)


def worker_index_for_interface(rows: list[dict[str, Any]], interface: str) -> int | None:
    worker_indices = [
        parse_int(row.get("worker_index"))
        for row in rows
        if row.get("source") == "memif" and (row.get("interface") or "") == interface
    ]
    for worker_index in worker_indices:
        if worker_index is not None:
            return worker_index

    match = MEMIF_IFACE_RE.match(interface)
    if match is None:
        return None
    socket_id = int(match.group("socket_id"))
    if 10 <= socket_id <= 19:
        return socket_id - 10
    if 20 <= socket_id <= 29:
        return socket_id - 20
    return None


def interface_column_prefix(interface: str) -> str:
    return interface.replace("/", "_")


def queue_indices(metrics_rows: list[dict[str, Any]], prefix: str) -> list[int]:
    indices = {
        ring_index
        for row in metrics_rows
        if row.get("source") == "memif"
        and (row.get("interface") or "").startswith(prefix)
        and (ring_index := parse_int(row.get("ring_index"))) is not None
    }
    return sorted(indices)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-workers", type=int, default=None, help="Override n_workers from load config")
    parser.add_argument("--results-dir", type=Path, default=None, help="Use explicit results directory")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    results_dir = args.results_dir or load_results_dir(args.n_workers)
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
    interfaces = memif_interfaces(metrics_rows)
    interface_queues = {interface: queue_indices(metrics_rows, interface) for interface in interfaces}

    fieldnames = [
        "step_index",
        "target_pps",
        "verdict",
        "loss_percent",
    ]
    for interface in interfaces:
        prefix = interface_column_prefix(interface)
        for queue_id in interface_queues[interface]:
            fieldnames.append(f"{prefix}_q{queue_id}_tx_usage_perc")
            fieldnames.append(f"{prefix}_q{queue_id}_tx_batch_size")
            fieldnames.append(f"{prefix}_q{queue_id}_rx_usage_perc")
            fieldnames.append(f"{prefix}_q{queue_id}_rx_batch_size")
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
            rx_batch_sizes = runtime_batch_sizes_by_worker(step_rows, "memif-input")
            tx_batch_sizes_by_interface = {
                interface: runtime_batch_sizes_by_worker(step_rows, f"{interface}-tx")
                for interface in interfaces
            }
            row: dict[str, Any] = {
                "step_index": step.get("step_index"),
                "target_pps": step.get("target_pps"),
                "verdict": "PASS" if step.get("passed") else "FAILED",
                "loss_percent": step.get("loss_percent"),
            }

            for interface in interfaces:
                prefix = interface_column_prefix(interface)
                worker_index = worker_index_for_interface(step_rows, interface)
                worker = worker_for_index(workers, worker_index)
                tx_batch = None if worker is None else tx_batch_sizes_by_interface.get(interface, {}).get(worker)
                rx_batch = None if worker is None else rx_batch_sizes.get(worker)

                for queue_id in interface_queues[interface]:
                    tx_avg = average_queue_used_percent(
                        step_rows,
                        interface,
                        tx_direction,
                        vpp_role=VPP_MEMIF_ROLE,
                        ring_index=queue_id,
                    )
                    rx_avg = average_queue_used_percent(
                        step_rows,
                        interface,
                        rx_direction,
                        vpp_role=VPP_MEMIF_ROLE,
                        ring_index=queue_id,
                    )
                    row[f"{prefix}_q{queue_id}_tx_usage_perc"] = "" if tx_avg is None else f"{tx_avg:.6f}"
                    row[f"{prefix}_q{queue_id}_tx_batch_size"] = "" if tx_batch is None else f"{tx_batch:.6f}"
                    row[f"{prefix}_q{queue_id}_rx_usage_perc"] = "" if rx_avg is None else f"{rx_avg:.6f}"
                    row[f"{prefix}_q{queue_id}_rx_batch_size"] = "" if rx_batch is None else f"{rx_batch:.6f}"

            for worker in workers:
                row[f"worker_packets_{worker}"] = worker_packets.get(worker, 0)

            writer.writerow(row)

    print(f"written: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

POLL_INTERVAL_SEC = 0.035
WORKER_NAME_RE = re.compile(r"^vpp_wk_(\d+)$")

# Indices in /proc/<pid>/task/<tid>/stat after stripping leading "pid (comm) ".
UTIME_INDEX = 11
STIME_INDEX = 12
PROCESSOR_INDEX = 36

TSV_COLUMNS = (
    "timestamp_iso",
    "timestamp_epoch",
    "pid",
    "tid",
    "thread",
    "worker_index",
    "expected_core",
    "allowed_cpus",
    "last_cpu",
    "utime_ticks",
    "stime_ticks",
    "total_ticks",
    "cpu_time_sec",
)


@dataclass(frozen=True)
class WorkerThread:
    worker_index: int
    thread_name: str
    tid: int
    expected_core: int
    allowed_cpus: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Poll per-worker VPP CPU time counters into TSV.")
    parser.add_argument("--output", type=Path, default=None, help="Output TSV path")
    parser.add_argument("--results-dir", type=Path, default=None, help="Use explicit results directory")
    parser.add_argument("--n-workers", type=int, default=None, help="Override n_workers from load config")
    parser.add_argument("--interval-sec", type=float, default=POLL_INTERVAL_SEC, help=f"Polling interval, default {POLL_INTERVAL_SEC}s")
    parser.add_argument("--load-config", type=Path, default=None, help="Optional load config path")
    parser.add_argument("--search-config", type=Path, default=None, help="Optional search config path")
    parser.add_argument("--vpp-pid", type=int, default=None, help="Use explicit VPP PID")
    return parser.parse_args()


def ensure_src_import_path() -> None:
    repo_root = Path(__file__).resolve().parent
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


def resolve_run_context(
    n_workers_override: int | None,
    results_dir_override: Path | None,
    load_config_path: Path | None,
    search_config_path: Path | None,
) -> tuple[int, Path, int]:
    ensure_src_import_path()
    from manage_nat.config import VPP_CPU_MAIN_CORE
    from test_nat.config import build_results_dir, load_test_configs

    config, _search, _resolved_load, _resolved_search = load_test_configs(load_config_path, search_config_path)
    if n_workers_override is not None:
        config = replace(config, n_workers=n_workers_override)

    if config.n_workers <= 0:
        raise ValueError(
            "n_workers must be > 0 for worker CPU polling. "
            f"Current n_workers={config.n_workers}."
        )

    results_dir = results_dir_override or build_results_dir(config)
    return config.n_workers, results_dir, VPP_CPU_MAIN_CORE


def resolve_vpp_pid(explicit_pid: int | None) -> int:
    if explicit_pid is not None:
        proc_dir = Path("/proc") / str(explicit_pid)
        if not proc_dir.exists():
            raise RuntimeError(f"Provided --vpp-pid does not exist: {explicit_pid}")
        return explicit_pid

    # Preferred source: systemd service MainPID.
    systemd_result = subprocess.run(
        ["systemctl", "show", "vpp.service", "--property=MainPID", "--value"],
        text=True,
        capture_output=True,
        check=False,
    )
    if systemd_result.returncode == 0:
        raw_value = systemd_result.stdout.strip()
        if raw_value.isdigit():
            main_pid = int(raw_value)
            if main_pid > 0 and (Path("/proc") / str(main_pid)).exists():
                return main_pid

    # Fallback: process name can be "vpp" or "vpp_main" depending on build/package.
    candidate_pids: list[int] = []
    for process_name in ("vpp", "vpp_main"):
        result = subprocess.run(["pgrep", "-x", process_name], text=True, capture_output=True, check=False)
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            text_pid = line.strip()
            if not text_pid.isdigit():
                continue
            pid = int(text_pid)
            if pid not in candidate_pids:
                candidate_pids.append(pid)

    if not candidate_pids:
        raise RuntimeError(
            "Could not find running VPP process. "
            "Checked systemd MainPID (vpp.service) and pgrep -x for process names: vpp, vpp_main."
        )
    if len(candidate_pids) > 1:
        rendered = ", ".join(str(pid) for pid in candidate_pids)
        raise RuntimeError(
            "Multiple VPP processes found. Use --vpp-pid to disambiguate. "
            f"PIDs: {rendered}"
        )
    return candidate_pids[0]


def read_thread_name(pid: int, tid: int) -> str:
    return (Path("/proc") / str(pid) / "task" / str(tid) / "comm").read_text(encoding="utf-8").strip()


def read_thread_allowed_cpus(pid: int, tid: int) -> str:
    status_path = Path("/proc") / str(pid) / "task" / str(tid) / "status"
    for line in status_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("Cpus_allowed_list:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError(f"Cpus_allowed_list is missing in {status_path}")


def parse_cpu_list(cpu_list: str) -> set[int]:
    values: set[int] = set()
    for raw_chunk in cpu_list.split(","):
        chunk = raw_chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start_text, end_text = chunk.split("-", 1)
            if not start_text.isdigit() or not end_text.isdigit():
                raise ValueError(f"Invalid CPU range token: {chunk!r}")
            start = int(start_text)
            end = int(end_text)
            if end < start:
                raise ValueError(f"Invalid descending CPU range: {chunk!r}")
            for cpu in range(start, end + 1):
                values.add(cpu)
        else:
            if not chunk.isdigit():
                raise ValueError(f"Invalid CPU token: {chunk!r}")
            values.add(int(chunk))
    return values


def discover_workers(pid: int, n_workers: int, main_core: int) -> list[WorkerThread]:
    task_dir = Path("/proc") / str(pid) / "task"
    if not task_dir.exists():
        raise RuntimeError(f"Task directory not found: {task_dir}")

    by_index: dict[int, WorkerThread] = {}
    for task_path in task_dir.iterdir():
        if not task_path.name.isdigit():
            continue
        tid = int(task_path.name)
        try:
            thread_name = read_thread_name(pid, tid)
        except FileNotFoundError:
            continue

        match = WORKER_NAME_RE.match(thread_name)
        if match is None:
            continue

        worker_index = int(match.group(1))
        expected_core = main_core + 1 + worker_index
        allowed_cpus = read_thread_allowed_cpus(pid, tid)

        if worker_index in by_index:
            existing = by_index[worker_index]
            raise RuntimeError(
                "Duplicate worker index discovered in VPP threads: "
                f"worker {worker_index} -> TIDs {existing.tid} and {tid}"
            )

        by_index[worker_index] = WorkerThread(
            worker_index=worker_index,
            thread_name=thread_name,
            tid=tid,
            expected_core=expected_core,
            allowed_cpus=allowed_cpus,
        )

    expected = set(range(n_workers))
    actual = set(by_index)
    missing = sorted(expected - actual)
    if missing:
        rendered = ", ".join(f"vpp_wk_{index}" for index in missing)
        raise RuntimeError(
            "Not all required VPP workers are present. "
            f"Expected {n_workers} workers (vpp_wk_0..vpp_wk_{n_workers - 1}); missing: {rendered}"
        )

    workers = [by_index[index] for index in sorted(expected)]

    for worker in workers:
        allowed_set = parse_cpu_list(worker.allowed_cpus)
        if worker.expected_core not in allowed_set:
            raise RuntimeError(
                "Worker CPU affinity does not include expected core from manage_nat/config.py: "
                f"{worker.thread_name} tid={worker.tid}, expected_core={worker.expected_core}, "
                f"allowed={worker.allowed_cpus}"
            )

    return workers


def read_thread_cpu_ticks(pid: int, tid: int) -> tuple[int, int, int]:
    stat_path = Path("/proc") / str(pid) / "task" / str(tid) / "stat"
    raw = stat_path.read_text(encoding="utf-8").strip()
    try:
        stat_tail = raw.rsplit(") ", 1)[1]
    except IndexError as exc:
        raise RuntimeError(f"Unexpected stat format: {stat_path}") from exc

    fields = stat_tail.split()
    if len(fields) <= PROCESSOR_INDEX:
        raise RuntimeError(f"Unexpected stat field count in {stat_path}: {len(fields)}")

    utime_ticks = int(fields[UTIME_INDEX])
    stime_ticks = int(fields[STIME_INDEX])
    last_cpu = int(fields[PROCESSOR_INDEX])
    return utime_ticks, stime_ticks, last_cpu


def sample_worker_rows(
    pid: int,
    workers: list[WorkerThread],
    timestamp_iso: str,
    timestamp_epoch: float,
    clock_ticks: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for worker in workers:
        utime_ticks, stime_ticks, last_cpu = read_thread_cpu_ticks(pid, worker.tid)
        total_ticks = utime_ticks + stime_ticks
        cpu_time_sec = total_ticks / clock_ticks
        rows.append(
            {
                "timestamp_iso": timestamp_iso,
                "timestamp_epoch": f"{timestamp_epoch:.9f}",
                "pid": pid,
                "tid": worker.tid,
                "thread": worker.thread_name,
                "worker_index": worker.worker_index,
                "expected_core": worker.expected_core,
                "allowed_cpus": worker.allowed_cpus,
                "last_cpu": last_cpu,
                "utime_ticks": utime_ticks,
                "stime_ticks": stime_ticks,
                "total_ticks": total_ticks,
                "cpu_time_sec": f"{cpu_time_sec:.9f}",
            }
        )
    return rows


def run_polling(
    output_path: Path,
    pid: int,
    workers: list[WorkerThread],
    interval_sec: float,
) -> None:
    if interval_sec <= 0:
        raise ValueError(f"interval-sec must be positive, got {interval_sec}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    clock_ticks = os.sysconf(os.sysconf_names["SC_CLK_TCK"])

    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TSV_COLUMNS, delimiter="\t")
        writer.writeheader()
        handle.flush()

        next_sample_time = time.monotonic()
        while True:
            now_epoch = time.time()
            now_iso = utc_now_iso()
            rows = sample_worker_rows(
                pid=pid,
                workers=workers,
                timestamp_iso=now_iso,
                timestamp_epoch=now_epoch,
                clock_ticks=clock_ticks,
            )
            for row in rows:
                writer.writerow(row)
            handle.flush()

            next_sample_time += interval_sec
            sleep_for = next_sample_time - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)


def main() -> int:
    args = parse_args()

    try:
        n_workers, default_results_dir, main_core = resolve_run_context(
            n_workers_override=args.n_workers,
            results_dir_override=args.results_dir,
            load_config_path=args.load_config,
            search_config_path=args.search_config,
        )
        output_path = args.output or (default_results_dir / "worker_cpu_raw.tsv")

        pid = resolve_vpp_pid(args.vpp_pid)
        workers = discover_workers(pid=pid, n_workers=n_workers, main_core=main_core)

        print(
            "record-worker-cpu: "
            f"pid={pid}, n_workers={n_workers}, interval_sec={args.interval_sec:g}, "
            f"output={output_path}",
            flush=True,
        )
        for worker in workers:
            print(
                "  "
                f"{worker.thread_name} tid={worker.tid} expected_core={worker.expected_core} "
                f"allowed={worker.allowed_cpus}",
                flush=True,
            )

        run_polling(output_path=output_path, pid=pid, workers=workers, interval_sec=args.interval_sec)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"record-worker-cpu: error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

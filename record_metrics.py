#!/usr/bin/env python3
from __future__ import annotations

import csv
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

POLL_INTERVAL = 0.5

RUNTIME_COMMAND = ["sudo", "vppctl", "show", "runtime"]
MEMIF_SHOW_COMMANDS = (
    ["sudo", "vppctl", "memif", "show"],
    ["sudo", "vppctl", "show", "memif"],
)

THREAD_HEADER_RE = re.compile(r"^Thread\s+\d+\s+(?P<thread>\S+)")
RUNTIME_ROW_RE = re.compile(r"^(?P<node>\S+)\s+\S+\s+(?P<calls>\d+)\s+(?P<vectors>\d+)\b")
MEMIF_TX_NODE_RE = re.compile(r"^memif(?:1\d|2\d)/\d+-tx$")

INTERFACE_RE = re.compile(r"^interface\s+(?P<ifname>\S+)$")
RING_RE = re.compile(r"^(?P<direction>master-to-slave|slave-to-master)\s+ring\s+(?P<ring>\d+):$")
RING_SIZE_RE = re.compile(r"\bring-size\s+(?P<value>\d+)\b")
HEAD_TAIL_RE = re.compile(r"\bhead\s+(?P<head>\d+)\s+tail\s+(?P<tail>\d+)\b")
TARGET_IFACE_RE = re.compile(r"^memif(?:1\d|2\d)/\d+$")
MEMIF_IFACE_RE = re.compile(r"^memif(?P<socket_id>\d+)/(?P<interface_id>\d+)$")

TSV_COLUMNS = (
    "timestamp_iso",
    "timestamp_epoch",
    "source",
    "thread",
    "node",
    "calls",
    "vectors",
    "interface",
    "iface_role",
    "pair_index",
    "worker_index",
    "socket_id",
    "interface_id",
    "ring_direction",
    "ring_index",
    "ring_size",
    "head",
    "tail",
    "error",
)


def interface_meta(interface: str | None) -> dict[str, Any]:
    if interface is None:
        return {
            "iface_role": "",
            "pair_index": "",
            "worker_index": "",
            "socket_id": "",
            "interface_id": "",
        }

    match = MEMIF_IFACE_RE.match(interface)
    if match is None:
        return {
            "iface_role": "",
            "pair_index": "",
            "worker_index": "",
            "socket_id": "",
            "interface_id": "",
        }

    socket_id = int(match.group("socket_id"))
    interface_id = int(match.group("interface_id"))
    iface_role = ""
    pair_index: int | str = ""

    if 10 <= socket_id <= 19:
        iface_role = "inside"
        pair_index = socket_id - 10
    elif 20 <= socket_id <= 29:
        iface_role = "outside"
        pair_index = socket_id - 20

    worker_index: int | str = pair_index if isinstance(pair_index, int) else ""
    return {
        "iface_role": iface_role,
        "pair_index": pair_index,
        "worker_index": worker_index,
        "socket_id": socket_id,
        "interface_id": interface_id,
    }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def command_error(command: list[str], result: subprocess.CompletedProcess[str]) -> str:
    details = " ".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    if not details:
        details = f"exit code {result.returncode}"
    return f"{' '.join(command)}: {details}"


def read_runtime_output() -> str:
    result = run_command(RUNTIME_COMMAND)
    if result.returncode != 0:
        raise RuntimeError(command_error(RUNTIME_COMMAND, result))
    return result.stdout


def read_memif_output() -> str:
    last_error = "unable to run memif show"
    for command in MEMIF_SHOW_COMMANDS:
        result = run_command(command)
        text = result.stdout or ""
        unknown_input = "unknown input" in text.lower()
        if result.returncode == 0 and not unknown_input:
            return text
        last_error = command_error(command, result)
    raise RuntimeError(last_error)


def parse_runtime_rows(output: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current_thread: str | None = None

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        thread_match = THREAD_HEADER_RE.match(line)
        if thread_match:
            thread_name = thread_match.group("thread")
            current_thread = None if thread_name == "vpp_main" else thread_name
            continue

        if current_thread is None:
            continue

        row_match = RUNTIME_ROW_RE.match(line)
        if row_match is None:
            continue

        node = row_match.group("node")
        if node != "memif-input" and MEMIF_TX_NODE_RE.match(node) is None:
            continue

        interface = node.removesuffix("-tx") if node.endswith("-tx") else None
        meta = interface_meta(interface)
        rows.append(
            {
                "thread": current_thread,
                "node": node,
                "calls": int(row_match.group("calls")),
                "vectors": int(row_match.group("vectors")),
                "interface": interface or "",
                **meta,
            }
        )

    return rows


def parse_memif_rows(output: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current_interface: str | None = None
    current_row: dict[str, Any] | None = None

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        interface_match = INTERFACE_RE.match(line)
        if interface_match:
            name = interface_match.group("ifname")
            current_interface = name if TARGET_IFACE_RE.match(name) else None
            current_row = None
            continue

        if current_interface is None:
            continue

        ring_match = RING_RE.match(line)
        if ring_match:
            meta = interface_meta(current_interface)
            current_row = {
                "interface": current_interface,
                **meta,
                "ring_direction": ring_match.group("direction"),
                "ring_index": int(ring_match.group("ring")),
                "ring_size": None,
                "head": None,
                "tail": None,
            }
            rows.append(current_row)
            continue

        if current_row is None:
            continue

        ring_size_match = RING_SIZE_RE.search(line)
        if ring_size_match is not None:
            current_row["ring_size"] = int(ring_size_match.group("value"))

        head_tail_match = HEAD_TAIL_RE.search(line)
        if head_tail_match is not None:
            current_row["head"] = int(head_tail_match.group("head"))
            current_row["tail"] = int(head_tail_match.group("tail"))

    return rows


def load_results_dir() -> Path:
    repo_root = Path(__file__).resolve().parent
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    from test_nat.config import build_results_dir, load_test_configs

    config, _search, _load_path, _search_path = load_test_configs(None, None)
    return build_results_dir(config)


def write_sample(writer: csv.DictWriter, timestamp_iso: str, timestamp_epoch: float) -> tuple[int, list[str]]:
    errors: list[str] = []
    row_count = 0

    try:
        runtime_rows = parse_runtime_rows(read_runtime_output())
    except Exception as exc:
        runtime_rows = []
        errors.append(f"runtime: {exc}")

    try:
        memif_rows = parse_memif_rows(read_memif_output())
    except Exception as exc:
        memif_rows = []
        errors.append(f"memif: {exc}")

    for row in runtime_rows:
        writer.writerow(
            {
                "timestamp_iso": timestamp_iso,
                "timestamp_epoch": f"{timestamp_epoch:.6f}",
                "source": "runtime",
                "thread": row["thread"],
                "node": row["node"],
                "calls": row["calls"],
                "vectors": row["vectors"],
                "interface": row["interface"],
                "iface_role": row["iface_role"],
                "pair_index": row["pair_index"],
                "worker_index": row["worker_index"],
                "socket_id": row["socket_id"],
                "interface_id": row["interface_id"],
            }
        )
        row_count += 1

    for row in memif_rows:
        writer.writerow(
            {
                "timestamp_iso": timestamp_iso,
                "timestamp_epoch": f"{timestamp_epoch:.6f}",
                "source": "memif",
                "interface": row["interface"],
                "iface_role": row["iface_role"],
                "pair_index": row["pair_index"],
                "worker_index": row["worker_index"],
                "socket_id": row["socket_id"],
                "interface_id": row["interface_id"],
                "ring_direction": row["ring_direction"],
                "ring_index": row["ring_index"],
                "ring_size": row["ring_size"],
                "head": row["head"],
                "tail": row["tail"],
            }
        )
        row_count += 1

    for error in errors:
        writer.writerow(
            {
                "timestamp_iso": timestamp_iso,
                "timestamp_epoch": f"{timestamp_epoch:.6f}",
                "source": "error",
                "error": error,
            }
        )
        row_count += 1

    return row_count, errors


def main() -> int:
    results_dir = load_results_dir()
    results_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = results_dir / "metrics.tsv"

    print(f"metrics file: {metrics_path}", flush=True)
    print(f"poll interval: {POLL_INTERVAL} sec", flush=True)

    with metrics_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TSV_COLUMNS, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        handle.flush()

        try:
            while True:
                started_at = time.time()
                timestamp_iso = utc_now_iso()
                timestamp_epoch = time.time()
                row_count, errors = write_sample(writer, timestamp_iso, timestamp_epoch)
                handle.flush()

                if errors:
                    print(f"{timestamp_iso} rows={row_count} errors={len(errors)}", flush=True)
                else:
                    print(f"{timestamp_iso} rows={row_count}", flush=True)

                elapsed = time.time() - started_at
                sleep_sec = max(0.0, POLL_INTERVAL - elapsed)
                time.sleep(sleep_sec)
        except KeyboardInterrupt:
            print("stopped by Ctrl+C", flush=True)
            return 0


if __name__ == "__main__":
    raise SystemExit(main())

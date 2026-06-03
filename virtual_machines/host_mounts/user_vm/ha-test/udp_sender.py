#!/usr/bin/env python3
from __future__ import annotations

import json
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

TARGET_IP = "10.8.0.2"
TARGET_PORT = 5005
POLL_INTERVAL_SECONDS = 0.001
PAYLOAD = b"nat_fo_udp_gap_v1"
RESULTS_DIR = Path("results")
STATUS_INTERVAL_SECONDS = 0.5


def run_dir() -> Path:
    started_at = datetime.now(timezone.utc)
    path = RESULTS_DIR / started_at.strftime("%Y-%m-%d_%H-%M-%S_utc")
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    out_dir = run_dir()
    start_monotonic_ns = time.monotonic_ns()
    start_unix_ns = time.time_ns()
    sent = 0

    write_json(
        out_dir / "sender_start.json",
        {
            "target_ip": TARGET_IP,
            "target_port": TARGET_PORT,
            "poll_interval_seconds": POLL_INTERVAL_SECONDS,
            "payload_hex": PAYLOAD.hex(),
            "start_monotonic_ns": start_monotonic_ns,
            "start_unix_ns": start_unix_ns,
        },
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    target = (TARGET_IP, TARGET_PORT)
    next_send = time.monotonic()
    next_status = next_send + STATUS_INTERVAL_SECONDS

    print(f"udp sender -> {TARGET_IP}:{TARGET_PORT}")
    print(f"result dir: {out_dir}")
    print(f"interval: {POLL_INTERVAL_SECONDS:g}s")
    print("press Ctrl-C to stop")

    try:
        while True:
            sock.sendto(PAYLOAD, target)
            sent += 1
            next_send += POLL_INTERVAL_SECONDS
            now = time.monotonic()
            if now >= next_status:
                elapsed = (time.monotonic_ns() - start_monotonic_ns) / 1_000_000_000
                print(f"alive sender: elapsed={elapsed:.1f}s sent={sent}", flush=True)
                next_status = now + STATUS_INTERVAL_SECONDS
            delay = next_send - time.monotonic()
            if delay > 0:
                time.sleep(delay)
    except KeyboardInterrupt:
        print("\nstopped by user")
    finally:
        end_monotonic_ns = time.monotonic_ns()
        end_unix_ns = time.time_ns()
        write_json(
            out_dir / "sender_summary.json",
            {
                "sent_packets": sent,
                "end_monotonic_ns": end_monotonic_ns,
                "end_unix_ns": end_unix_ns,
                "elapsed_seconds": (end_monotonic_ns - start_monotonic_ns) / 1_000_000_000,
            },
        )
        sock.close()
        print(f"sent packets: {sent}")


if __name__ == "__main__":
    main()

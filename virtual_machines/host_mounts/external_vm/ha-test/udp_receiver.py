#!/usr/bin/env python3
from __future__ import annotations

import json
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

BIND_IP = "10.8.0.2"
BIND_PORT = 5005
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
    arrivals_path = out_dir / "arrivals.csv"
    start_monotonic_ns = time.monotonic_ns()
    start_unix_ns = time.time_ns()
    received = 0
    malformed = 0

    write_json(
        out_dir / "receiver_start.json",
        {
            "bind_ip": BIND_IP,
            "bind_port": BIND_PORT,
            "payload_hex": PAYLOAD.hex(),
            "arrival_file": str(arrivals_path),
            "start_monotonic_ns": start_monotonic_ns,
            "start_unix_ns": start_unix_ns,
        },
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((BIND_IP, BIND_PORT))

    print(f"udp receiver <- {BIND_IP}:{BIND_PORT}")
    print(f"result dir: {out_dir}")
    print(f"arrival file: {arrivals_path}")
    print("press Ctrl-C to stop")

    try:
        with arrivals_path.open("w", encoding="utf-8") as arrivals:
            arrivals.write("monotonic_ns,unix_ns\n")
            next_status = time.monotonic() + STATUS_INTERVAL_SECONDS
            while True:
                data, _addr = sock.recvfrom(2048)
                now_monotonic_ns = time.monotonic_ns()
                now_unix_ns = time.time_ns()
                if data != PAYLOAD:
                    malformed += 1
                else:
                    received += 1
                    arrivals.write(f"{now_monotonic_ns},{now_unix_ns}\n")
                    if received % 100 == 0:
                        arrivals.flush()
                now = time.monotonic()
                if now >= next_status:
                    elapsed = (now_monotonic_ns - start_monotonic_ns) / 1_000_000_000
                    print(
                        f"alive receiver: elapsed={elapsed:.1f}s "
                        f"received={received} malformed={malformed}",
                        flush=True,
                    )
                    next_status = now + STATUS_INTERVAL_SECONDS
    except KeyboardInterrupt:
        print("\nstopped by user")
    finally:
        end_monotonic_ns = time.monotonic_ns()
        end_unix_ns = time.time_ns()
        write_json(
            out_dir / "receiver_summary.json",
            {
                "received_packets": received,
                "malformed_packets": malformed,
                "end_monotonic_ns": end_monotonic_ns,
                "end_unix_ns": end_unix_ns,
                "elapsed_seconds": (end_monotonic_ns - start_monotonic_ns) / 1_000_000_000,
            },
        )
        sock.close()
        print(f"received packets: {received}")
        print(f"malformed packets: {malformed}")


if __name__ == "__main__":
    main()

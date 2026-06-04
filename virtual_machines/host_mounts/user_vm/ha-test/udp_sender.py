#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
BURST_SESSIONS_PER_SECOND = 1000.0
BURST_SOURCE_PORT_MIN = 1024
BURST_SOURCE_PORT_MAX = 65535


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send UDP downtime probes, optionally pre-creating NAT sessions."
    )
    parser.add_argument(
        "--burst-sessions",
        type=int,
        default=0,
        help="number of UDP NAT sessions to create before the main load",
    )
    parser.add_argument(
        "--burst-sessions-per-second",
        type=float,
        default=BURST_SESSIONS_PER_SECOND,
        help="target NAT session creation rate during burst",
    )
    parser.add_argument(
        "--burst-source-port-min",
        type=int,
        default=BURST_SOURCE_PORT_MIN,
        help="first local UDP source port to try for burst sockets",
    )
    parser.add_argument(
        "--burst-source-port-max",
        type=int,
        default=BURST_SOURCE_PORT_MAX,
        help="last local UDP source port to try for burst sockets",
    )
    return parser.parse_args()


def run_dir() -> Path:
    started_at = datetime.now(timezone.utc)
    path = RESULTS_DIR / started_at.strftime("%Y-%m-%d_%H-%M-%S_utc")
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def validate_args(args: argparse.Namespace) -> None:
    if args.burst_sessions < 0:
        raise SystemExit("--burst-sessions must be >= 0")
    if args.burst_sessions_per_second <= 0:
        raise SystemExit("--burst-sessions-per-second must be > 0")
    if not (0 < args.burst_source_port_min <= 65535):
        raise SystemExit("--burst-source-port-min must be in range 1..65535")
    if not (0 < args.burst_source_port_max <= 65535):
        raise SystemExit("--burst-source-port-max must be in range 1..65535")
    if args.burst_source_port_min > args.burst_source_port_max:
        raise SystemExit("--burst-source-port-min must be <= --burst-source-port-max")
    available_ports = args.burst_source_port_max - args.burst_source_port_min + 1
    if args.burst_sessions > available_ports:
        raise SystemExit(
            f"--burst-sessions={args.burst_sessions} does not fit into "
            f"{available_ports} source ports"
        )


def run_burst(args: argparse.Namespace, target: tuple[str, int]) -> dict:
    if args.burst_sessions == 0:
        return {
            "burst_sent_packets": 0,
            "burst_elapsed_seconds": 0.0,
            "burst_failed_ports": 0,
        }

    burst_start_ns = time.monotonic_ns()
    interval = 1.0 / args.burst_sessions_per_second
    next_send = time.monotonic()
    sent = 0
    failed_ports = 0

    print(
        "burst: "
        f"sessions={args.burst_sessions} "
        f"rate={args.burst_sessions_per_second:g}/s "
        f"ports={args.burst_source_port_min}-{args.burst_source_port_max}"
    )

    for source_port in range(args.burst_source_port_min, args.burst_source_port_max + 1):
        if sent >= args.burst_sessions:
            break

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as burst_sock:
            try:
                burst_sock.bind(("", source_port))
                burst_sock.sendto(PAYLOAD, target)
            except OSError as exc:
                failed_ports += 1
                print(f"burst: skip source_port={source_port}: {exc}", flush=True)
                continue

        sent += 1
        next_send += interval
        delay = next_send - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    if sent != args.burst_sessions:
        raise RuntimeError(
            f"burst created only {sent} sessions out of {args.burst_sessions}; "
            f"failed_ports={failed_ports}"
        )

    burst_end_ns = time.monotonic_ns()
    elapsed = (burst_end_ns - burst_start_ns) / 1_000_000_000
    print(f"burst done: sent={sent} elapsed={elapsed:.3f}s")
    return {
        "burst_sent_packets": sent,
        "burst_elapsed_seconds": elapsed,
        "burst_failed_ports": failed_ports,
    }


def main() -> None:
    args = parse_args()
    validate_args(args)

    out_dir = run_dir()
    start_monotonic_ns = time.monotonic_ns()
    start_unix_ns = time.time_ns()
    sent = 0
    target = (TARGET_IP, TARGET_PORT)

    write_json(
        out_dir / "sender_start.json",
        {
            "target_ip": TARGET_IP,
            "target_port": TARGET_PORT,
            "poll_interval_seconds": POLL_INTERVAL_SECONDS,
            "payload_hex": PAYLOAD.hex(),
            "burst_sessions": args.burst_sessions,
            "burst_sessions_per_second": args.burst_sessions_per_second,
            "burst_source_port_min": args.burst_source_port_min,
            "burst_source_port_max": args.burst_source_port_max,
            "start_monotonic_ns": start_monotonic_ns,
            "start_unix_ns": start_unix_ns,
        },
    )

    print(f"udp sender -> {TARGET_IP}:{TARGET_PORT}")
    print(f"result dir: {out_dir}")
    print(f"interval: {POLL_INTERVAL_SECONDS:g}s")

    burst_summary = run_burst(args, target)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    next_send = time.monotonic()
    next_status = next_send + STATUS_INTERVAL_SECONDS

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
                **burst_summary,
                "end_monotonic_ns": end_monotonic_ns,
                "end_unix_ns": end_unix_ns,
                "elapsed_seconds": (end_monotonic_ns - start_monotonic_ns) / 1_000_000_000,
            },
        )
        sock.close()
        print(f"sent packets: {sent}")


if __name__ == "__main__":
    main()

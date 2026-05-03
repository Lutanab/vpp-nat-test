from __future__ import annotations

import signal
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .packets import parse_test_packet
from .utils import utc_now_iso, write_json

VALID_SERVER_MODES = ("sink", "echo")


@dataclass(slots=True)
class ServerRuntime:
    bind: str
    port: int
    mode: str
    received_packets: int = 0
    received_bytes: int = 0
    replied_packets: int = 0
    malformed_packets: int = 0
    first_packet_time: str | None = None
    last_packet_time: str | None = None

    def to_dict(self, unique_flows_seen: int) -> dict[str, Any]:
        return {
            "bind": self.bind,
            "port": self.port,
            "mode": self.mode,
            "received_packets": self.received_packets,
            "received_bytes": self.received_bytes,
            "replied_packets": self.replied_packets if self.mode == "echo" else None,
            "unique_flows_seen": unique_flows_seen,
            "malformed_packets": self.malformed_packets,
            "first_packet_time": self.first_packet_time,
            "last_packet_time": self.last_packet_time,
        }


def run_l34_udp_server(bind: str, port: int, mode: str, output_path: Path | None = None) -> int:
    if mode not in VALID_SERVER_MODES:
        raise ValueError(f"unsupported server mode '{mode}'")

    state = ServerRuntime(bind=bind, port=port, mode=mode)
    unique_flows: set[int] = set()
    stop_requested = False

    def handle_signal(_signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((bind, port))
    sock.settimeout(0.5)

    print(f"L34 UDP server listening on {bind}:{port} in {mode} mode")

    try:
        while not stop_requested:
            try:
                payload, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue

            timestamp = utc_now_iso()
            if state.first_packet_time is None:
                state.first_packet_time = timestamp
            state.last_packet_time = timestamp
            state.received_packets += 1
            state.received_bytes += len(payload)

            try:
                header = parse_test_packet(payload)
            except ValueError:
                state.malformed_packets += 1
                header = None

            if header is not None:
                unique_flows.add(header.flow_id)

            if mode == "echo":
                sock.sendto(payload, addr)
                state.replied_packets += 1
    finally:
        sock.close()

    payload = state.to_dict(unique_flows_seen=len(unique_flows))
    if output_path is not None:
        write_json(output_path, payload)

    print("L34 UDP server stopped")
    print(payload)
    return 0

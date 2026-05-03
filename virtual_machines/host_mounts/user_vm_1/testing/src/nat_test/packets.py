from __future__ import annotations

import struct
from dataclasses import dataclass

HEADER_STRUCT = struct.Struct("!QQQII")
HEADER_SIZE = HEADER_STRUCT.size
FLAG_MEASUREMENT = 1 << 0


@dataclass(slots=True)
class TestPacketHeader:
    flow_id: int
    seq: int
    send_ts_ns: int
    payload_size: int
    flags: int


def build_test_packet(
    flow_id: int,
    seq: int,
    send_ts_ns: int,
    packet_size_bytes: int,
    flags: int,
) -> bytes:
    if packet_size_bytes < HEADER_SIZE:
        raise ValueError(
            f"packet_size_bytes={packet_size_bytes} is smaller than header size {HEADER_SIZE}"
        )
    padding_size = packet_size_bytes - HEADER_SIZE
    return HEADER_STRUCT.pack(flow_id, seq, send_ts_ns, packet_size_bytes, flags) + (b"\0" * padding_size)


def parse_test_packet(payload: bytes) -> TestPacketHeader:
    if len(payload) < HEADER_SIZE:
        raise ValueError(f"packet payload is too short: {len(payload)} < {HEADER_SIZE}")
    flow_id, seq, send_ts_ns, packet_size, flags = HEADER_STRUCT.unpack_from(payload)
    return TestPacketHeader(
        flow_id=flow_id,
        seq=seq,
        send_ts_ns=send_ts_ns,
        payload_size=packet_size,
        flags=flags,
    )

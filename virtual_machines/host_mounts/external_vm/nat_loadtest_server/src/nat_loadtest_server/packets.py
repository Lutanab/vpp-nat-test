from __future__ import annotations

import struct
from dataclasses import dataclass

HEADER_STRUCT = struct.Struct("!QQQII")
HEADER_SIZE = HEADER_STRUCT.size


@dataclass(slots=True)
class TestPacketHeader:
    flow_id: int
    seq: int
    send_ts_ns: int
    payload_size: int
    flags: int


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

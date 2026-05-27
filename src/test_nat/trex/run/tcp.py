from __future__ import annotations

import math
from typing import Any

from ...config import UDP_SPORT_RANGE_END, UDP_SPORT_RANGE_START
from ..setup import TREX_INSIDE_A_IP, TREX_OUTSIDE_IP
from .simple import TCP_DST_PORT, TCP_PACKET_SIZE_BYTES, TCP_SRC_PORT
from .udp import extract_total_counter, split_evenly

TCP_PG_ID = 100
TCP_REVERSE_PG_ID = 200


def resolve_tcp_sport_max(flow_count: int) -> int:
    """Возвращает верхнюю границу TCP source port для заданного числа flow."""
    if flow_count <= 0:
        raise ValueError("flow_count must be positive")
    sport_max = UDP_SPORT_RANGE_START + flow_count - 1
    if sport_max > UDP_SPORT_RANGE_END:
        max_supported = UDP_SPORT_RANGE_END - UDP_SPORT_RANGE_START + 1
        raise ValueError(
            "flow_count is too high for configured TCP sport range "
            f"{UDP_SPORT_RANGE_START}-{UDP_SPORT_RANGE_END}; max supported flow_count is {max_supported}"
        )
    return sport_max


def build_tcp_stream(
    api: Any,
    pps: int,
    packet_size: int = TCP_PACKET_SIZE_BYTES,
    flow_count: int = 1,
    tcp_sport_start: int | None = None,
    pg_id: int = TCP_PG_ID,
    core_id: int = -1,
    name: str = "tcp_inside_a_to_outside",
) -> Any:
    """Собирает continuous TCP SYN stream `inside-a -> outside` с flow-stat PGID."""
    return api.STLStream(
        name=name,
        packet=build_tcp_packet_builder(
            api=api,
            packet_size=packet_size,
            flow_count=flow_count,
            tcp_sport_start=tcp_sport_start,
        ),
        mode=api.STLTXCont(pps=pps),
        flow_stats=api.STLFlowStats(pg_id=pg_id),
        core_id=core_id,
    )


def build_tcp_packet_builder(
    api: Any,
    packet_size: int = TCP_PACKET_SIZE_BYTES,
    flow_count: int = 1,
    tcp_sport_start: int | None = None,
) -> Any:
    """Собирает packet builder с optional VM для варьирования TCP sport."""
    default_sport = TCP_SRC_PORT if flow_count == 1 else UDP_SPORT_RANGE_START
    sport_min = default_sport if tcp_sport_start is None else tcp_sport_start
    sport_max = sport_min + flow_count - 1
    if flow_count > 1 and sport_max > UDP_SPORT_RANGE_END:
        raise ValueError(f"TCP source port range exceeds {UDP_SPORT_RANGE_END}")

    base_packet = (
        api.Ether()
        / api.IP(src=TREX_INSIDE_A_IP, dst=TREX_OUTSIDE_IP)
        / api.TCP(sport=sport_min, dport=TCP_DST_PORT, flags="S", seq=1)
    )
    padding_size = max(0, packet_size - len(base_packet))
    packet = base_packet / (b"x" * padding_size)

    vm = None
    if flow_count > 1:
        vm = [
            api.STLVmFlowVar(
                name="tcp_sport",
                min_value=sport_min,
                max_value=sport_max,
                size=2,
                op="inc",
                split_to_cores=False,
            ),
            api.STLVmWrFlowVar(fv_name="tcp_sport", pkt_offset="TCP.sport"),
            api.STLVmFixChecksumHw(
                l3_offset="IP",
                l4_offset="TCP",
                l4_type=api.CTRexVmInsFixHwCs.L4_TYPE_TCP,
            ),
        ]

    return api.STLPktBuilder(pkt=packet, vm=vm)


def build_tcp_streams(
    api: Any,
    flow_count: int,
    packet_size: int = TCP_PACKET_SIZE_BYTES,
    trex_data_cores: int = 1,
) -> tuple[list[Any], tuple[int, ...]]:
    """Собирает TCP SYN stream-ы для прогрева заданного числа 5-tuple flow."""
    if flow_count <= 0:
        raise ValueError("flow_count must be positive")
    resolve_tcp_sport_max(flow_count)

    stream_count = 1 if flow_count == 1 else min(trex_data_cores, flow_count)
    flows_by_stream = [1] * stream_count if flow_count == 1 else split_evenly(flow_count, stream_count)
    pps_by_stream = flows_by_stream

    streams: list[Any] = []
    pg_ids: list[int] = []
    next_sport = UDP_SPORT_RANGE_START
    for stream_index, (stream_pps, stream_flow_count) in enumerate(
        zip(pps_by_stream, flows_by_stream, strict=True)
    ):
        pg_id = TCP_PG_ID + stream_index
        sport_start = None if flow_count == 1 else next_sport
        streams.append(
            build_tcp_stream(
                api=api,
                pps=stream_pps,
                packet_size=packet_size,
                flow_count=stream_flow_count,
                tcp_sport_start=sport_start,
                pg_id=pg_id,
                core_id=stream_index,
                name=f"tcp_inside_a_to_outside_{stream_index}",
            )
        )
        pg_ids.append(pg_id)
        next_sport += 0 if flow_count == 1 else stream_flow_count
    return streams, tuple(pg_ids)


def build_tcp_burst_stream(
    api: Any,
    total_pkts: int,
    pps: int,
    packet_size: int = TCP_PACKET_SIZE_BYTES,
    flow_count: int = 1,
    tcp_sport_start: int | None = None,
    pg_id: int = TCP_PG_ID,
    core_id: int = -1,
    name: str = "tcp_burst_inside_a_to_outside",
) -> Any:
    """Собирает TCP SYN burst, где каждый пакет соответствует одному NAT flow."""
    if total_pkts <= 0:
        raise ValueError("total_pkts must be positive")
    return api.STLStream(
        name=name,
        packet=build_tcp_packet_builder(
            api=api,
            packet_size=packet_size,
            flow_count=flow_count,
            tcp_sport_start=tcp_sport_start,
        ),
        mode=api.STLTXSingleBurst(total_pkts=total_pkts, pps=pps),
        flow_stats=api.STLFlowStats(pg_id=pg_id),
        core_id=core_id,
    )


def build_tcp_burst_streams(
    api: Any,
    flow_count: int,
    packet_size: int = TCP_PACKET_SIZE_BYTES,
    trex_data_cores: int = 1,
    duration_sec: float = 1,
) -> tuple[list[Any], tuple[int, ...]]:
    """Собирает burst stream-ы: один TCP SYN packet на каждый source-port flow."""
    if flow_count <= 0:
        raise ValueError("flow_count must be positive")
    resolve_tcp_sport_max(flow_count)

    stream_count = 1 if flow_count == 1 else min(trex_data_cores, flow_count)
    flows_by_stream = [1] * stream_count if flow_count == 1 else split_evenly(flow_count, stream_count)

    streams: list[Any] = []
    pg_ids: list[int] = []
    next_sport = UDP_SPORT_RANGE_START
    for stream_index, stream_flow_count in enumerate(flows_by_stream):
        pg_id = TCP_PG_ID + stream_index
        sport_start = None if flow_count == 1 else next_sport
        pps = burst_pps(stream_flow_count, duration_sec)
        streams.append(
            build_tcp_burst_stream(
                api=api,
                total_pkts=stream_flow_count,
                pps=pps,
                packet_size=packet_size,
                flow_count=stream_flow_count,
                tcp_sport_start=sport_start,
                pg_id=pg_id,
                core_id=stream_index,
                name=f"tcp_burst_inside_a_to_outside_{stream_index}",
            )
        )
        pg_ids.append(pg_id)
        next_sport += 0 if flow_count == 1 else stream_flow_count
    return streams, tuple(pg_ids)


def build_tcp_reverse_packet_builder(
    api: Any,
    public_ip: str,
    public_dport_start: int,
    packet_size: int = TCP_PACKET_SIZE_BYTES,
    flow_count: int = 1,
) -> Any:
    """Собирает packet builder для outside -> public NAT mapping проверки."""
    public_dport_max = public_dport_start + flow_count - 1
    if public_dport_max > UDP_SPORT_RANGE_END:
        raise ValueError(f"TCP public destination port range exceeds {UDP_SPORT_RANGE_END}")

    base_packet = (
        api.Ether()
        / api.IP(src=TREX_OUTSIDE_IP, dst=public_ip)
        / api.TCP(sport=TCP_DST_PORT, dport=public_dport_start, flags="A", seq=2, ack=2)
    )
    padding_size = max(0, packet_size - len(base_packet))
    packet = base_packet / (b"x" * padding_size)

    vm = None
    if flow_count > 1:
        vm = [
            api.STLVmFlowVar(
                name="tcp_public_dport",
                min_value=public_dport_start,
                max_value=public_dport_max,
                size=2,
                op="inc",
                split_to_cores=False,
            ),
            api.STLVmWrFlowVar(fv_name="tcp_public_dport", pkt_offset="TCP.dport"),
            api.STLVmFixChecksumHw(
                l3_offset="IP",
                l4_offset="TCP",
                l4_type=api.CTRexVmInsFixHwCs.L4_TYPE_TCP,
            ),
        ]

    return api.STLPktBuilder(pkt=packet, vm=vm)


def build_tcp_reverse_burst_stream(
    api: Any,
    public_ip: str,
    public_dport_start: int,
    total_pkts: int,
    pps: int,
    packet_size: int = TCP_PACKET_SIZE_BYTES,
    flow_count: int = 1,
    pg_id: int = TCP_REVERSE_PG_ID,
    core_id: int = -1,
    name: str = "tcp_burst_outside_to_public_mapping",
) -> Any:
    """Собирает burst, который проверяет существующие outside->inside NAT mappings."""
    if total_pkts <= 0:
        raise ValueError("total_pkts must be positive")
    return api.STLStream(
        name=name,
        packet=build_tcp_reverse_packet_builder(
            api=api,
            public_ip=public_ip,
            public_dport_start=public_dport_start,
            packet_size=packet_size,
            flow_count=flow_count,
        ),
        mode=api.STLTXSingleBurst(total_pkts=total_pkts, pps=pps),
        flow_stats=api.STLFlowStats(pg_id=pg_id),
        core_id=core_id,
    )


def build_tcp_reverse_burst_streams(
    api: Any,
    public_ip: str,
    public_dport_start: int,
    flow_count: int,
    packet_size: int = TCP_PACKET_SIZE_BYTES,
    trex_data_cores: int = 1,
    duration_sec: float = 1,
) -> tuple[list[Any], tuple[int, ...]]:
    """Собирает outside->public burst stream-ы для проверки сохраненных NAT mappings."""
    if flow_count <= 0:
        raise ValueError("flow_count must be positive")
    resolve_tcp_sport_max(flow_count)

    stream_count = 1 if flow_count == 1 else min(trex_data_cores, flow_count)
    flows_by_stream = [1] * stream_count if flow_count == 1 else split_evenly(flow_count, stream_count)

    streams: list[Any] = []
    pg_ids: list[int] = []
    next_dport = public_dport_start
    for stream_index, stream_flow_count in enumerate(flows_by_stream):
        pg_id = TCP_REVERSE_PG_ID + stream_index
        pps = burst_pps(stream_flow_count, duration_sec)
        streams.append(
            build_tcp_reverse_burst_stream(
                api=api,
                public_ip=public_ip,
                public_dport_start=next_dport,
                total_pkts=stream_flow_count,
                pps=pps,
                packet_size=packet_size,
                flow_count=stream_flow_count,
                pg_id=pg_id,
                core_id=stream_index,
                name=f"tcp_burst_outside_to_public_mapping_{stream_index}",
            )
        )
        pg_ids.append(pg_id)
        next_dport += stream_flow_count
    return streams, tuple(pg_ids)


def burst_pps(packet_count: int, duration_sec: float) -> int:
    """Подбирает pps так, чтобы burst примерно уложился в duration_sec."""
    if packet_count <= 0:
        raise ValueError("packet_count must be positive")
    if duration_sec <= 0:
        return packet_count
    return max(1, math.ceil(packet_count / duration_sec))


def read_tcp_counters(client: Any, pg_ids: tuple[int, ...] = (TCP_PG_ID,)) -> tuple[int, int]:
    """Считывает tx/rx counters по PGID-статистике TCP stream."""
    pgid_stats = client.get_pgid_stats(pgid_list=list(pg_ids))
    flow_stats_by_id = pgid_stats.get("flow_stats", {})

    tx_pkts = 0
    rx_pkts = 0
    for pg_id in pg_ids:
        flow_stats = flow_stats_by_id.get(pg_id) or flow_stats_by_id.get(str(pg_id))
        if flow_stats is None:
            raise RuntimeError(f"TRex did not return flow stats for PG ID {pg_id}")
        tx_pkts += extract_total_counter(flow_stats, "tx_pkts")
        rx_pkts += extract_total_counter(flow_stats, "rx_pkts")
    return tx_pkts, rx_pkts

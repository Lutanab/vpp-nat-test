from __future__ import annotations

import importlib
import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from manage_nat.config import TREX_INSTALL_BASE_DIR
from manage_nat.nat_mode import parse_configured_workers

from ...config import UDP_SPORT_RANGE_END, UDP_SPORT_RANGE_START
from ..setup import TREX_INSIDE_A_IP, TREX_OUTSIDE_IP

CLIENT_PORT = 0
SERVER_PORT = 1
UDP_PG_ID = 10
UDP_PACKET_SIZE_BYTES = 64
UDP_SRC_PORT = 12345
UDP_DST_PORT = 5001
TREX_SERVER_HOST = "127.0.0.1"


@dataclass(frozen=True)
class UdpRunResult:
    """Результат UDP-прогона через TRex."""

    target_pps: int
    duration_sec: float
    measurement_start_epoch: float
    measurement_end_epoch: float
    packet_size: int
    expected_packets: int
    received_packets: int
    tx_packets: int
    rx_packets: int
    lost_packets: int
    loss_rate: float
    loss_percent: float
    actual_sent_pps: float


def load_trex_stl_api() -> Any:
    """Загружает TRex STL Python API из установленного release."""
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=SyntaxWarning)
        try:
            return importlib.import_module("trex_stl_lib.api")
        except ModuleNotFoundError:
            api_dir = find_trex_api_dir()
            if api_dir is None:
                raise RuntimeError(
                    "TRex Python API not found. Run `manage-nat prepare` and check "
                    f"{TREX_INSTALL_BASE_DIR}/v*/automation/trex_control_plane/interactive."
                )
            sys.path.insert(0, str(api_dir))
            return importlib.import_module("trex_stl_lib.api")


def find_trex_api_dir() -> Path | None:
    """Ищет каталог, который нужно добавить в `sys.path` для `trex_stl_lib`."""
    if not TREX_INSTALL_BASE_DIR.exists():
        return None
    candidates = sorted(TREX_INSTALL_BASE_DIR.glob("v*/automation/trex_control_plane/interactive"))
    for path in reversed(candidates):
        if (path / "trex_stl_lib" / "api.py").is_file():
            return path
    return None


def resolve_udp_sport_max(flow_count: int) -> int:
    """Возвращает верхнюю границу UDP source port для заданного числа flow."""
    if flow_count <= 0:
        raise ValueError("flow_count must be positive")
    sport_max = UDP_SPORT_RANGE_START + flow_count - 1
    if sport_max > UDP_SPORT_RANGE_END:
        max_supported = UDP_SPORT_RANGE_END - UDP_SPORT_RANGE_START + 1
        raise ValueError(
            "flow_count is too high for configured UDP sport range "
            f"{UDP_SPORT_RANGE_START}-{UDP_SPORT_RANGE_END}; max supported flow_count is {max_supported}"
        )
    return sport_max


def resolve_trex_data_cores() -> int:
    """Возвращает число TRex data cores, с которым запускается server."""
    return max(1, parse_configured_workers() or 0)


def split_evenly(total: int, parts: int) -> list[int]:
    """Делит целое число на части с сохранением точной суммы."""
    if total <= 0:
        raise ValueError("total must be positive")
    if parts <= 0:
        raise ValueError("parts must be positive")
    base, remainder = divmod(total, parts)
    return [base + (1 if index < remainder else 0) for index in range(parts)]


def build_udp_stream(
    api: Any,
    target_pps: int,
    packet_size: int = UDP_PACKET_SIZE_BYTES,
    flow_count: int = 1,
    udp_sport_start: int | None = None,
    pg_id: int = UDP_PG_ID,
    core_id: int = -1,
    name: str = "udp_inside_a_to_outside",
) -> Any:
    """Собирает continuous UDP stream `inside-a -> outside` с flow-stat PGID."""
    default_sport = UDP_SRC_PORT if flow_count == 1 else UDP_SPORT_RANGE_START
    sport_min = default_sport if udp_sport_start is None else udp_sport_start
    sport_max = sport_min + flow_count - 1
    if flow_count > 1 and sport_max > UDP_SPORT_RANGE_END:
        raise ValueError(f"UDP source port range exceeds {UDP_SPORT_RANGE_END}")
    base_packet = (
        api.Ether()
        / api.IP(src=TREX_INSIDE_A_IP, dst=TREX_OUTSIDE_IP)
        / api.UDP(sport=sport_min, dport=UDP_DST_PORT, chksum=0)
    )
    padding_size = max(0, packet_size - len(base_packet))
    packet = base_packet / (b"x" * padding_size)

    vm = None
    if flow_count > 1:
        vm = [
            api.STLVmFlowVar(
                name="udp_sport",
                min_value=sport_min,
                max_value=sport_max,
                size=2,
                op="inc",
                split_to_cores=False,
            ),
            api.STLVmWrFlowVar(fv_name="udp_sport", pkt_offset="UDP.sport"),
        ]

    return api.STLStream(
        name=name,
        packet=api.STLPktBuilder(pkt=packet, vm=vm),
        mode=api.STLTXCont(pps=target_pps),
        flow_stats=api.STLFlowStats(pg_id=pg_id),
        core_id=core_id,
    )


def build_udp_streams(
    api: Any,
    target_pps: int,
    packet_size: int = UDP_PACKET_SIZE_BYTES,
    flow_count: int = 1,
    trex_data_cores: int = 1,
) -> tuple[list[Any], tuple[int, ...]]:
    """Собирает pinned stream-ы так, чтобы target_pps оставался суммарным."""
    if flow_count <= 0:
        raise ValueError("flow_count must be positive")
    resolve_udp_sport_max(flow_count)

    stream_count = trex_data_cores if flow_count == 1 else min(trex_data_cores, flow_count)
    pps_by_stream = split_evenly(target_pps, stream_count)
    flows_by_stream = [1] * stream_count if flow_count == 1 else split_evenly(flow_count, stream_count)

    streams: list[Any] = []
    pg_ids: list[int] = []
    next_sport = UDP_SPORT_RANGE_START
    for stream_index, (stream_pps, stream_flow_count) in enumerate(zip(pps_by_stream, flows_by_stream, strict=True)):
        pg_id = UDP_PG_ID + stream_index
        sport_start = None if flow_count == 1 else next_sport
        streams.append(
            build_udp_stream(
                api=api,
                target_pps=stream_pps,
                packet_size=packet_size,
                flow_count=stream_flow_count,
                udp_sport_start=sport_start,
                pg_id=pg_id,
                core_id=stream_index,
                name=f"udp_inside_a_to_outside_{stream_index}",
            )
        )
        pg_ids.append(pg_id)
        next_sport += 0 if flow_count == 1 else stream_flow_count
    return streams, tuple(pg_ids)


def extract_total_counter(flow_stats: dict[str, Any], counter_name: str) -> int:
    """Достает total-счетчик TRex PGID stats."""
    counter = flow_stats.get(counter_name, {})
    if "total" in counter:
        return int(counter["total"])
    return sum(int(value) for value in counter.values() if isinstance(value, (int, float)))


def read_udp_counters(client: Any, pg_ids: tuple[int, ...] = (UDP_PG_ID,)) -> tuple[int, int]:
    """Считывает tx/rx counters по PGID-статистике UDP stream."""
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


def calculate_loss_percent(client: Any, pg_ids: tuple[int, ...] = (UDP_PG_ID,)) -> float:
    """Считает процент потерь по PGID-статистике UDP stream."""
    tx_pkts, rx_pkts = read_udp_counters(client, pg_ids=pg_ids)
    if tx_pkts <= 0:
        raise RuntimeError("TRex reported zero transmitted UDP packets")

    lost_pkts = max(0, tx_pkts - rx_pkts)
    return (lost_pkts / tx_pkts) * 100


def calculate_expected_packets(target_pps: int, duration: float, tx_packets: int) -> int:
    """Считает expected packets для unified loss с учетом TRex overshoot."""
    target_packets = max(1, int(target_pps * duration))
    return max(tx_packets, target_packets)


def configure_l3_mode(client: Any) -> None:
    """Настраивает L3/ARP для отправляющего и принимающего TRex-портов."""
    ports = [CLIENT_PORT, SERVER_PORT]
    client.set_service_mode(ports=ports, enabled=True)
    try:
        client.set_l3_mode(port=CLIENT_PORT, src_ipv4=TREX_INSIDE_A_IP, dst_ipv4="10.8.1.1")
        client.set_l3_mode(port=SERVER_PORT, src_ipv4=TREX_OUTSIDE_IP, dst_ipv4="10.8.0.1")
    finally:
        client.set_service_mode(ports=ports, enabled=False)


def run_udp_measurement(
    target_pps: int,
    duration: float,
    packet_size: int = UDP_PACKET_SIZE_BYTES,
    flow_count: int = 1,
    warmup_sec: float = 0,
) -> UdpRunResult:
    """Запускает UDP TRex-тест фиксированной длительности и возвращает counters/процент потерь."""
    if target_pps <= 0:
        raise ValueError("target_pps must be positive")
    if duration <= 0:
        raise ValueError("duration must be positive")
    if packet_size <= 0:
        raise ValueError("packet_size must be positive")
    if flow_count <= 0:
        raise ValueError("flow_count must be positive")
    if warmup_sec < 0:
        raise ValueError("warmup_sec must be non-negative")

    api = load_trex_stl_api()
    client = api.STLClient(server=TREX_SERVER_HOST)
    ports = [CLIENT_PORT, SERVER_PORT]
    trex_data_cores = resolve_trex_data_cores()
    streams, pg_ids = build_udp_streams(
        api=api,
        target_pps=target_pps,
        packet_size=packet_size,
        flow_count=flow_count,
        trex_data_cores=trex_data_cores,
    )

    try:
        client.connect()
        client.reset(ports=ports)
        configure_l3_mode(client)
        client.remove_all_streams(ports=[CLIENT_PORT])
        client.add_streams(streams, ports=[CLIENT_PORT])
        client.clear_stats(ports=ports)
        client.start(ports=[CLIENT_PORT])
        if warmup_sec > 0:
            time.sleep(warmup_sec)
            client.clear_stats(ports=ports)
        measurement_start_epoch = time.time()
        time.sleep(duration)
        client.stop(ports=[CLIENT_PORT])
        measurement_end_epoch = time.time()
        tx_packets, rx_packets = read_udp_counters(client, pg_ids=pg_ids)
        expected_packets = calculate_expected_packets(target_pps, duration, tx_packets)
        received_packets = rx_packets
        lost_packets = max(0, expected_packets - received_packets)
        loss_rate = lost_packets / expected_packets
        return UdpRunResult(
            target_pps=target_pps,
            duration_sec=duration,
            measurement_start_epoch=measurement_start_epoch,
            measurement_end_epoch=measurement_end_epoch,
            packet_size=packet_size,
            expected_packets=expected_packets,
            received_packets=received_packets,
            tx_packets=tx_packets,
            rx_packets=rx_packets,
            lost_packets=lost_packets,
            loss_rate=loss_rate,
            loss_percent=loss_rate * 100,
            actual_sent_pps=tx_packets / duration,
        )
    finally:
        try:
            client.stop(ports=[CLIENT_PORT])
        except Exception:
            pass
        client.disconnect()


def run_udp_test(target_pps: int, duration: float, flow_count: int = 1, warmup_sec: float = 0) -> float:
    """Запускает UDP TRex-тест и возвращает процент потерь."""
    return run_udp_measurement(
        target_pps=target_pps,
        duration=duration,
        flow_count=flow_count,
        warmup_sec=warmup_sec,
    ).loss_percent

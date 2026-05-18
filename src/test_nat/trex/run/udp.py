from __future__ import annotations

import importlib
import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from manage_nat.config import TREX_INSTALL_BASE_DIR

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


def build_udp_stream(api: Any, target_pps: int, packet_size: int = UDP_PACKET_SIZE_BYTES) -> Any:
    """Собирает continuous UDP stream `inside-a -> outside` с flow-stat PGID."""
    base_packet = (
        api.Ether()
        / api.IP(src=TREX_INSIDE_A_IP, dst=TREX_OUTSIDE_IP)
        / api.UDP(sport=UDP_SRC_PORT, dport=UDP_DST_PORT)
    )
    padding_size = max(0, packet_size - len(base_packet))
    packet = base_packet / (b"x" * padding_size)

    return api.STLStream(
        name="udp_inside_a_to_outside",
        packet=api.STLPktBuilder(pkt=packet),
        mode=api.STLTXCont(pps=target_pps),
        flow_stats=api.STLFlowStats(pg_id=UDP_PG_ID),
    )


def extract_total_counter(flow_stats: dict[str, Any], counter_name: str) -> int:
    """Достает total-счетчик TRex PGID stats."""
    counter = flow_stats.get(counter_name, {})
    if "total" in counter:
        return int(counter["total"])
    return sum(int(value) for value in counter.values() if isinstance(value, (int, float)))


def read_udp_counters(client: Any) -> tuple[int, int]:
    """Считывает tx/rx counters по PGID-статистике UDP stream."""
    pgid_stats = client.get_pgid_stats(pgid_list=[UDP_PG_ID])
    flow_stats_by_id = pgid_stats.get("flow_stats", {})
    flow_stats = flow_stats_by_id.get(UDP_PG_ID) or flow_stats_by_id.get(str(UDP_PG_ID))
    if flow_stats is None:
        raise RuntimeError(f"TRex did not return flow stats for PG ID {UDP_PG_ID}")

    tx_pkts = extract_total_counter(flow_stats, "tx_pkts")
    rx_pkts = extract_total_counter(flow_stats, "rx_pkts")
    return tx_pkts, rx_pkts


def calculate_loss_percent(client: Any) -> float:
    """Считает процент потерь по PGID-статистике UDP stream."""
    tx_pkts, rx_pkts = read_udp_counters(client)
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
) -> UdpRunResult:
    """Запускает UDP TRex-тест фиксированной длительности и возвращает counters/процент потерь."""
    if target_pps <= 0:
        raise ValueError("target_pps must be positive")
    if duration <= 0:
        raise ValueError("duration must be positive")
    if packet_size <= 0:
        raise ValueError("packet_size must be positive")

    api = load_trex_stl_api()
    client = api.STLClient(server=TREX_SERVER_HOST)
    ports = [CLIENT_PORT, SERVER_PORT]

    try:
        client.connect()
        client.reset(ports=ports)
        configure_l3_mode(client)
        client.remove_all_streams(ports=[CLIENT_PORT])
        client.add_streams(build_udp_stream(api, target_pps, packet_size), ports=[CLIENT_PORT])
        client.clear_stats(ports=ports)
        client.start(ports=[CLIENT_PORT])
        time.sleep(duration)
        client.stop(ports=[CLIENT_PORT])
        tx_packets, rx_packets = read_udp_counters(client)
        expected_packets = calculate_expected_packets(target_pps, duration, tx_packets)
        received_packets = rx_packets
        lost_packets = max(0, expected_packets - received_packets)
        loss_rate = lost_packets / expected_packets
        return UdpRunResult(
            target_pps=target_pps,
            duration_sec=duration,
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


def run_udp_test(target_pps: int, duration: float) -> float:
    """Запускает UDP TRex-тест и возвращает процент потерь."""
    return run_udp_measurement(target_pps=target_pps, duration=duration).loss_percent

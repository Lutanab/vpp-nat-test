from __future__ import annotations

import time
from dataclasses import dataclass
from ipaddress import ip_address, ip_interface
from typing import Any

from manage_nat.network.setup import NAT_INSIDE_BVI_IP_CIDR, NAT_OUTSIDE_IP_CIDR

from ..setup import TREX_INSIDE_A_IP, TREX_OUTSIDE_IP
from .udp import CLIENT_PORT, SERVER_PORT, TREX_SERVER_HOST, configure_l3_mode, load_trex_stl_api

TCP_PACKET_SIZE_BYTES = 64
TCP_SRC_PORT = 12345
TCP_DST_PORT = 80
TCP_CAPTURE_LIMIT = 20
TCP_CAPTURE_DRAIN_SECONDS = 0.5
TCP_CAPTURE_FILTER = "tcp or (vlan and tcp)"


@dataclass(frozen=True)
class CapturedTcpPacket:
    """Описывает TCP-пакет, захваченный TRex capture."""

    index: int | None
    origin: str
    port: int | None
    timestamp: float | None
    length: int
    wire_length: int | None
    summary: str
    details: str
    eth_src: str | None
    eth_dst: str | None
    eth_type: str | None
    ip_src: str | None
    ip_dst: str | None
    ip_proto: int | None
    ip_ttl: int | None
    tcp_src_port: int | None
    tcp_dst_port: int | None
    tcp_flags: str | None
    tcp_seq: int | None
    tcp_ack: int | None


@dataclass(frozen=True)
class SimpleTcpTestResult:
    """Результат минимальной TCP-проверки NAT через TRex."""

    nat_applied: bool | None
    verdict: str
    expected_nat_source_ip: str
    inside_network: str
    outside_network: str
    outside_packet: CapturedTcpPacket | None
    captured_packets: tuple[CapturedTcpPacket, ...]


def build_tcp_syn_stream(api: Any) -> Any:
    """Собирает одиночный TCP SYN stream `inside-a -> outside`."""
    base_packet = (
        api.Ether()
        / api.IP(src=TREX_INSIDE_A_IP, dst=TREX_OUTSIDE_IP)
        / api.TCP(sport=TCP_SRC_PORT, dport=TCP_DST_PORT, flags="S", seq=1)
    )
    padding_size = max(0, TCP_PACKET_SIZE_BYTES - len(base_packet))
    packet = base_packet / (b"x" * padding_size)

    return api.STLStream(
        name="simple_tcp_syn_inside_a_to_outside",
        packet=api.STLPktBuilder(pkt=packet),
        mode=api.STLTXSingleBurst(total_pkts=1, pps=1),
    )


def parse_captured_packet(api: Any, raw_packet: dict[str, Any]) -> CapturedTcpPacket:
    """Парсит один capture-record TRex в компактное описание заголовков."""
    packet = api.Ether(raw_packet["binary"])
    ip = packet["IP"] if "IP" in packet else None
    tcp = packet["TCP"] if "TCP" in packet else None

    return CapturedTcpPacket(
        index=raw_packet.get("index"),
        origin=str(raw_packet.get("origin", "")),
        port=raw_packet.get("port"),
        timestamp=raw_packet.get("ts"),
        length=len(raw_packet["binary"]),
        wire_length=raw_packet.get("wirelen"),
        summary=packet.summary(),
        details=packet.show(dump=True),
        eth_src=getattr(packet, "src", None),
        eth_dst=getattr(packet, "dst", None),
        eth_type=hex(packet.type) if getattr(packet, "type", None) is not None else None,
        ip_src=getattr(ip, "src", None),
        ip_dst=getattr(ip, "dst", None),
        ip_proto=getattr(ip, "proto", None),
        ip_ttl=getattr(ip, "ttl", None),
        tcp_src_port=getattr(tcp, "sport", None),
        tcp_dst_port=getattr(tcp, "dport", None),
        tcp_flags=str(tcp.flags) if tcp is not None else None,
        tcp_seq=getattr(tcp, "seq", None),
        tcp_ack=getattr(tcp, "ack", None),
    )


def find_outside_packet(packets: tuple[CapturedTcpPacket, ...]) -> CapturedTcpPacket | None:
    """Находит первый TCP/IP пакет, пришедший на outside-порт TRex."""
    for packet in packets:
        if packet.port == SERVER_PORT and packet.ip_src and packet.ip_dst:
            return packet
    return None


def build_verdict(outside_packet: CapturedTcpPacket | None) -> tuple[bool | None, str]:
    """Выносит verdict только по source IP пакета на outside-порту."""
    inside_network = ip_interface(NAT_INSIDE_BVI_IP_CIDR).network
    outside_network = ip_interface(NAT_OUTSIDE_IP_CIDR).network
    expected_nat_source_ip = ip_interface(NAT_OUTSIDE_IP_CIDR).ip

    if outside_packet is None:
        return None, "Не удалось захватить TCP/IP пакет на outside-порту TRex."

    source_ip = ip_address(outside_packet.ip_src)
    if source_ip in inside_network:
        return False, f"NAT не сработал: source IP остался inside-адресом {source_ip}."
    if source_ip in outside_network:
        if source_ip == expected_nat_source_ip:
            return True, f"NAT сработал: source IP переписан в {source_ip}."
        return True, f"NAT похож на рабочий: source IP {source_ip} находится во outside-сети."

    return None, f"Непонятный source IP {source_ip}: он не относится ни к inside, ни к outside сети."


def run_simple_tcp_test() -> SimpleTcpTestResult:
    """Запускает минимальную TCP-проверку NAT и возвращает данные capture."""
    api = load_trex_stl_api()
    client = api.STLClient(server=TREX_SERVER_HOST)
    ports = [CLIENT_PORT, SERVER_PORT]
    capture_id: int | None = None
    service_mode_enabled = False

    try:
        client.connect()
        client.reset(ports=ports)
        configure_l3_mode(client)
        client.remove_all_streams(ports=[CLIENT_PORT])
        client.add_streams(build_tcp_syn_stream(api), ports=[CLIENT_PORT])
        client.clear_stats(ports=ports)
        client.set_service_mode(ports=ports, enabled=True)
        service_mode_enabled = True

        capture = client.start_capture(
            tx_ports=[CLIENT_PORT],
            rx_ports=[SERVER_PORT],
            limit=TCP_CAPTURE_LIMIT,
            bpf_filter=TCP_CAPTURE_FILTER,
        )
        capture_id = capture["id"]

        client.start(ports=[CLIENT_PORT], force=True)
        client.wait_on_traffic(ports=[CLIENT_PORT])
        time.sleep(TCP_CAPTURE_DRAIN_SECONDS)

        raw_packets: list[dict[str, Any]] = []
        client.stop_capture(capture_id=capture_id, output=raw_packets)
        capture_id = None

        packets = tuple(parse_captured_packet(api, raw_packet) for raw_packet in raw_packets)
        outside_packet = find_outside_packet(packets)
        nat_applied, verdict = build_verdict(outside_packet)
        inside_network = ip_interface(NAT_INSIDE_BVI_IP_CIDR).network
        outside_network = ip_interface(NAT_OUTSIDE_IP_CIDR).network
        expected_nat_source_ip = ip_interface(NAT_OUTSIDE_IP_CIDR).ip

        return SimpleTcpTestResult(
            nat_applied=nat_applied,
            verdict=verdict,
            expected_nat_source_ip=str(expected_nat_source_ip),
            inside_network=str(inside_network),
            outside_network=str(outside_network),
            outside_packet=outside_packet,
            captured_packets=packets,
        )
    finally:
        if capture_id is not None:
            try:
                client.stop_capture(capture_id=capture_id)
            except Exception:
                pass
        try:
            client.stop(ports=[CLIENT_PORT])
        except Exception:
            pass
        if service_mode_enabled:
            try:
                client.set_service_mode(ports=ports, enabled=False)
            except Exception:
                pass
        client.disconnect()

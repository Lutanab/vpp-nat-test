from __future__ import annotations

import time
from typing import Any

import rich_click as click
from manage_nat.network.setup import restart_vpp_service

from ..trex.run.udp import (
    CLIENT_PORT,
    SERVER_PORT,
    TREX_SERVER_HOST,
    build_udp_burst_streams,
    build_udp_reverse_burst_stream,
    configure_l3_mode,
    load_trex_stl_api,
    read_udp_counters,
)
from .base import (
    FailoverConfig,
    FailoverTimeConfig,
    ObservedUdpMapping,
    UDP_CAPTURE_DRAIN_SEC,
    UDP_CAPTURE_FILTER,
    UdpMappingRange,
    quiet_stdout,
)


def run_failover_udp_mapping_check(config: FailoverConfig, time_config: FailoverTimeConfig) -> None:
    """Проверяет восстановление dataplane mappings через UDP capture."""
    api = load_trex_stl_api()
    client = api.STLClient(server=TREX_SERVER_HOST)
    ports = [CLIENT_PORT, SERVER_PORT]
    capture_id: int | None = None
    service_mode_enabled = False

    try:
        client.connect()
        client.reset(ports=ports)
        configure_l3_mode(client)
        streams, pg_ids = build_udp_burst_streams(
            api=api,
            flow_count=config.flow_count,
            packet_size=config.packet_size,
            trex_data_cores=1,
            duration_sec=time_config.warmup_sec,
        )
        client.remove_all_streams(ports=[CLIENT_PORT])
        client.add_streams(streams, ports=[CLIENT_PORT])
        click.echo("warmup: sending inside->outside burst")

        client.set_service_mode(ports=ports, enabled=True)
        service_mode_enabled = True
        capture = client.start_capture(
            rx_ports=[SERVER_PORT],
            limit=config.flow_count,
            bpf_filter=UDP_CAPTURE_FILTER,
        )
        capture_id = capture["id"]
        client.clear_stats(ports=ports)
        client.start(ports=[CLIENT_PORT], force=True)
        client.wait_on_traffic(ports=[CLIENT_PORT])
        time.sleep(UDP_CAPTURE_DRAIN_SEC)

        raw_packets: list[dict[str, Any]] = []
        client.stop_capture(capture_id=capture_id, output=raw_packets)
        capture_id = None
        pre_tx, pre_rx = read_udp_counters(client, pg_ids=pg_ids)
        mappings = parse_observed_udp_mappings(api, raw_packets, config.flow_count)
        ranges = group_udp_mappings(mappings)
        reverse_streams, reverse_pg_ids = build_reverse_udp_streams(api, ranges, config, time_config)
        click.echo(f"warmup: tx={pre_tx}, rx={pre_rx}, mappings={len(mappings)}")

        click.echo("restart: vpp")
        with quiet_stdout():
            restart_vpp_service()
        time.sleep(time_config.waiting_sec)

        click.echo("verify: sending outside->observed mappings")
        configure_l3_mode(client)
        client.remove_all_streams(ports=[SERVER_PORT])
        client.add_streams(reverse_streams, ports=[SERVER_PORT])
        client.clear_stats(ports=ports)
        client.start(ports=[SERVER_PORT], force=True)
        client.wait_on_traffic(ports=[SERVER_PORT])
        post_tx, post_rx = read_udp_counters(client, pg_ids=reverse_pg_ids)
        survived_flows = min(post_rx, config.flow_count)
        survival_percent = (survived_flows / config.flow_count) * 100

        click.echo(f"verify: tx={post_tx}, rx={post_rx}")
        click.echo(
            "result: "
            f"created={config.flow_count}, survived={survived_flows}, "
            f"survival={survival_percent:.6f}%"
        )
    finally:
        if capture_id is not None:
            try:
                client.stop_capture(capture_id=capture_id)
            except Exception:
                pass
        try:
            client.stop(ports=ports)
        except Exception:
            pass
        if service_mode_enabled:
            try:
                client.set_service_mode(ports=ports, enabled=False)
            except Exception:
                pass
        try:
            client.disconnect()
        except Exception:
            pass


def parse_observed_udp_mappings(api: Any, raw_packets: list[dict[str, Any]], expected_count: int) -> tuple[ObservedUdpMapping, ...]:
    """Извлекает уникальные `(src_ip, src_port)` UDP mappings из server-side capture."""
    mappings: set[ObservedUdpMapping] = set()
    for raw_packet in raw_packets:
        packet = api.Ether(raw_packet["binary"])
        if "IP" not in packet or "UDP" not in packet:
            continue
        mappings.add(ObservedUdpMapping(ip=packet["IP"].src, port=int(packet["UDP"].sport)))

    if len(mappings) != expected_count:
        raise RuntimeError(f"Captured {len(mappings)} unique UDP mappings, expected {expected_count}")
    return tuple(sorted(mappings))


def group_udp_mappings(mappings: tuple[ObservedUdpMapping, ...]) -> tuple[UdpMappingRange, ...]:
    """Группирует mappings в диапазоны последовательных портов для компактного reverse-burst."""
    ranges: list[UdpMappingRange] = []
    current_ip = mappings[0].ip
    start_port = mappings[0].port
    previous_port = start_port
    count = 1

    for mapping in mappings[1:]:
        if mapping.ip == current_ip and mapping.port == previous_port + 1:
            previous_port = mapping.port
            count += 1
            continue
        ranges.append(UdpMappingRange(ip=current_ip, port_start=start_port, count=count))
        current_ip = mapping.ip
        start_port = previous_port = mapping.port
        count = 1

    ranges.append(UdpMappingRange(ip=current_ip, port_start=start_port, count=count))
    return tuple(ranges)


def build_reverse_udp_streams(
    api: Any,
    ranges: tuple[UdpMappingRange, ...],
    config: FailoverConfig,
    time_config: FailoverTimeConfig,
) -> tuple[list[Any], tuple[int, ...]]:
    """Строит reverse UDP burst stream-ы по observed mappings."""
    streams: list[Any] = []
    pg_ids: list[int] = []
    for index, mapping_range in enumerate(ranges):
        pg_id = 20 + index
        streams.append(
            build_udp_reverse_burst_stream(
                api=api,
                dst_ip=mapping_range.ip,
                dst_port_start=mapping_range.port_start,
                flow_count=mapping_range.count,
                packet_size=config.packet_size,
                duration_sec=time_config.warmup_sec,
                pg_id=pg_id,
                core_id=-1,
                name=f"udp_burst_outside_to_observed_mapping_{index}",
            )
        )
        pg_ids.append(pg_id)
    return streams, tuple(pg_ids)

from __future__ import annotations

import importlib
import json
import math
import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from manage_nat.config import TREX_INSTALL_BASE_DIR
from manage_nat.nat_mode import parse_configured_workers

from ...config import UDP_SPORT_RANGE_END, UDP_SPORT_RANGE_START
from ..setup import (
    TREX_INSIDE_A_IP,
    TREX_OUTSIDE_IP,
    TrexPortPair,
    build_active_trex_port_pairs,
    resolve_trex_data_cores as resolve_trex_setup_data_cores,
)

CLIENT_PORT = 0
SERVER_PORT = 1
UDP_PG_ID = 10
UDP_REVERSE_PG_ID = 20
UDP_LATENCY_PG_ID_BASE = 1000
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
    trex_queue_counters: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class UdpLatencyPairResult:
    pair_index: int
    inside_port_id: int
    outside_port_id: int
    inside_ip: str
    outside_ip: str
    flow_count: int
    load_target_pps: int
    latency_target_pps: int
    load_pg_id: int
    latency_pg_id: int
    load_expected_packets: int
    load_tx_packets: int
    load_rx_packets: int
    load_lost_packets: int
    load_loss_rate: float
    load_actual_sent_pps: float
    latency_tx_packets: int
    latency_rx_packets: int
    latency_sample_count: int
    latency_average_usec: float | None
    latency_jitter_usec: float | None
    latency_total_min_usec: int | None
    latency_total_max_usec: int | None
    latency_last_max_usec: int | None
    latency_p50_usec: int | None
    latency_p95_usec: int | None
    latency_p99_usec: int | None
    latency_percentile_source: str
    latency_histogram: dict[int, int]
    latency_err_cntrs: dict[str, int]


@dataclass(frozen=True)
class UdpLatencySummary:
    sample_count: int
    average_usec: float | None
    jitter_usec: float | None
    total_min_usec: int | None
    total_max_usec: int | None
    last_max_usec: int | None
    p50_usec: int | None
    p95_usec: int | None
    p99_usec: int | None
    percentile_source: str
    histogram: dict[int, int]
    err_cntrs: dict[str, int]


@dataclass(frozen=True)
class UdpLatencyRunResult:
    target_pps: int
    duration_sec: float
    warmup_sec: float
    packet_size: int
    flow_count: int
    pair_count: int
    measurement_start_epoch: float
    measurement_end_epoch: float
    total_load_expected_packets: int
    total_load_tx_packets: int
    total_load_rx_packets: int
    total_load_lost_packets: int
    total_load_loss_rate: float
    total_load_loss_percent: float
    total_load_actual_sent_pps: float
    total_latency_tx_packets: int
    total_latency_rx_packets: int
    total_latency_samples: int
    summary: UdpLatencySummary
    pairs: tuple[UdpLatencyPairResult, ...]


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
    return resolve_trex_setup_data_cores()


def resolve_active_port_pairs() -> tuple[TrexPortPair, ...]:
    """Возвращает активные inside/outside пары TRex-портов для текущей worker-конфигурации."""
    return build_active_trex_port_pairs(parse_configured_workers() or 0)


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
    src_ip: str = TREX_INSIDE_A_IP,
    dst_ip: str = TREX_OUTSIDE_IP,
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
        / api.IP(src=src_ip, dst=dst_ip)
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


def build_udp_latency_stream(
    api: Any,
    target_pps: int,
    packet_size: int = UDP_PACKET_SIZE_BYTES,
    flow_count: int = 1,
    src_ip: str = TREX_INSIDE_A_IP,
    dst_ip: str = TREX_OUTSIDE_IP,
    udp_sport_start: int | None = None,
    pg_id: int = UDP_LATENCY_PG_ID_BASE,
    name: str = "udp_latency_inside_a_to_outside",
) -> Any:
    """Собирает continuous UDP stream с latency статистикой."""
    default_sport = UDP_SRC_PORT if flow_count == 1 else UDP_SPORT_RANGE_START
    sport_min = default_sport if udp_sport_start is None else udp_sport_start
    sport_max = sport_min + flow_count - 1
    if flow_count > 1 and sport_max > UDP_SPORT_RANGE_END:
        raise ValueError(f"UDP source port range exceeds {UDP_SPORT_RANGE_END}")

    base_packet = (
        api.Ether()
        / api.IP(src=src_ip, dst=dst_ip)
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
        flow_stats=api.STLFlowLatencyStats(pg_id=pg_id),
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


def build_udp_burst_stream(
    api: Any,
    total_pkts: int,
    pps: int,
    packet_size: int = UDP_PACKET_SIZE_BYTES,
    flow_count: int = 1,
    src_ip: str = TREX_INSIDE_A_IP,
    dst_ip: str = TREX_OUTSIDE_IP,
    udp_sport_start: int | None = None,
    pg_id: int = UDP_PG_ID,
    core_id: int = -1,
    name: str = "udp_burst_inside_a_to_outside",
) -> Any:
    """Собирает UDP burst, где каждый packet соответствует одному source-port flow."""
    if total_pkts <= 0:
        raise ValueError("total_pkts must be positive")
    default_sport = UDP_SRC_PORT if flow_count == 1 else UDP_SPORT_RANGE_START
    sport_min = default_sport if udp_sport_start is None else udp_sport_start
    sport_max = sport_min + flow_count - 1
    if flow_count > 1 and sport_max > UDP_SPORT_RANGE_END:
        raise ValueError(f"UDP source port range exceeds {UDP_SPORT_RANGE_END}")

    base_packet = (
        api.Ether()
        / api.IP(src=src_ip, dst=dst_ip)
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
        mode=api.STLTXSingleBurst(total_pkts=total_pkts, pps=pps),
        flow_stats=api.STLFlowStats(pg_id=pg_id),
        core_id=core_id,
    )


def build_udp_burst_streams(
    api: Any,
    flow_count: int,
    packet_size: int = UDP_PACKET_SIZE_BYTES,
    trex_data_cores: int = 1,
    duration_sec: float = 1,
) -> tuple[list[Any], tuple[int, ...]]:
    """Собирает inside->outside UDP burst stream-ы для заданного числа flows."""
    if flow_count <= 0:
        raise ValueError("flow_count must be positive")
    resolve_udp_sport_max(flow_count)

    stream_count = 1 if flow_count == 1 else min(trex_data_cores, flow_count)
    flows_by_stream = [1] * stream_count if flow_count == 1 else split_evenly(flow_count, stream_count)

    streams: list[Any] = []
    pg_ids: list[int] = []
    next_sport = UDP_SPORT_RANGE_START
    for stream_index, stream_flow_count in enumerate(flows_by_stream):
        pg_id = UDP_PG_ID + stream_index
        sport_start = None if flow_count == 1 else next_sport
        pps = burst_pps(stream_flow_count, duration_sec)
        streams.append(
            build_udp_burst_stream(
                api=api,
                total_pkts=stream_flow_count,
                pps=pps,
                packet_size=packet_size,
                flow_count=stream_flow_count,
                udp_sport_start=sport_start,
                pg_id=pg_id,
                core_id=stream_index,
                name=f"udp_burst_inside_a_to_outside_{stream_index}",
            )
        )
        pg_ids.append(pg_id)
        next_sport += 0 if flow_count == 1 else stream_flow_count
    return streams, tuple(pg_ids)


def build_udp_reverse_burst_stream(
    api: Any,
    dst_ip: str,
    dst_port_start: int,
    flow_count: int,
    packet_size: int = UDP_PACKET_SIZE_BYTES,
    duration_sec: float = 1,
    pg_id: int = UDP_REVERSE_PG_ID,
    core_id: int = -1,
    name: str = "udp_burst_outside_to_observed_mapping",
) -> Any:
    """Собирает outside->observed UDP burst для проверки dataplane mapping после restart."""
    if flow_count <= 0:
        raise ValueError("flow_count must be positive")

    base_packet = (
        api.Ether()
        / api.IP(src=TREX_OUTSIDE_IP, dst=dst_ip)
        / api.UDP(sport=UDP_DST_PORT, dport=dst_port_start, chksum=0)
    )
    padding_size = max(0, packet_size - len(base_packet))
    packet = base_packet / (b"x" * padding_size)

    vm = None
    if flow_count > 1:
        vm = [
            api.STLVmFlowVar(
                name="udp_dport",
                min_value=dst_port_start,
                max_value=dst_port_start + flow_count - 1,
                size=2,
                op="inc",
                split_to_cores=False,
            ),
            api.STLVmWrFlowVar(fv_name="udp_dport", pkt_offset="UDP.dport"),
        ]

    return api.STLStream(
        name=name,
        packet=api.STLPktBuilder(pkt=packet, vm=vm),
        mode=api.STLTXSingleBurst(total_pkts=flow_count, pps=burst_pps(flow_count, duration_sec)),
        flow_stats=api.STLFlowStats(pg_id=pg_id),
        core_id=core_id,
    )


def burst_pps(packet_count: int, duration_sec: float) -> int:
    """Подбирает pps так, чтобы burst примерно уложился в duration_sec."""
    if packet_count <= 0:
        raise ValueError("packet_count must be positive")
    if duration_sec <= 0:
        return packet_count
    return max(1, math.ceil(packet_count / duration_sec))


def extract_total_counter(flow_stats: dict[str, Any], counter_name: str) -> int:
    """Достает total-счетчик TRex PGID stats."""
    counter = flow_stats.get(counter_name, {})
    if "total" in counter:
        return int(counter["total"])
    return sum(int(value) for value in counter.values() if isinstance(value, (int, float)))


def normalize_latency_histogram(raw_histogram: Any) -> dict[int, int]:
    """Нормализует latency histogram в формат `{bucket_upper_usec: packet_count}`."""
    if raw_histogram is None:
        return {}
    if isinstance(raw_histogram, dict):
        normalized: dict[int, int] = {}
        for raw_key, raw_value in raw_histogram.items():
            try:
                bucket = int(raw_key)
                count = int(raw_value)
            except (TypeError, ValueError):
                continue
            if count > 0:
                normalized[bucket] = normalized.get(bucket, 0) + count
        return normalized
    if isinstance(raw_histogram, list):
        normalized = {}
        for item in raw_histogram:
            if not isinstance(item, dict):
                continue
            try:
                bucket = int(item.get("key"))
                count = int(item.get("val", 0))
            except (TypeError, ValueError):
                continue
            if count > 0:
                normalized[bucket] = normalized.get(bucket, 0) + count
        return normalized
    return {}


def merge_latency_histograms(histograms: list[dict[int, int]]) -> dict[int, int]:
    """Объединяет несколько latency histogram в один."""
    merged: dict[int, int] = {}
    for histogram in histograms:
        for bucket_upper_usec, count in histogram.items():
            merged[bucket_upper_usec] = merged.get(bucket_upper_usec, 0) + count
    return dict(sorted(merged.items()))


def latency_bucket_upper_usec(bucket_start_usec: int) -> int:
    """Возвращает верхнюю границу bucket-а latency histogram (usec)."""
    if bucket_start_usec <= 0:
        return 10
    return bucket_start_usec + int(pow(10, len(str(bucket_start_usec)) - 1))


def calculate_histogram_percentile(histogram: dict[int, int], percentile: float) -> int | None:
    """Вычисляет percentile по latency histogram (верхняя граница bucket-а)."""
    if not histogram:
        return None
    if percentile <= 0 or percentile > 100:
        raise ValueError("percentile must be in range (0, 100]")

    total_samples = sum(histogram.values())
    if total_samples <= 0:
        return None
    threshold = math.ceil((percentile / 100) * total_samples)
    running = 0
    for bucket_start_usec in sorted(histogram):
        running += histogram[bucket_start_usec]
        if running >= threshold:
            return latency_bucket_upper_usec(bucket_start_usec)
    return latency_bucket_upper_usec(max(histogram))


def normalize_hdrh_payload(raw_hdrh: Any) -> Any:
    """Нормализует hdrh payload в dict/list, если это возможно."""
    if raw_hdrh is None:
        return None
    if isinstance(raw_hdrh, (dict, list)):
        return raw_hdrh
    if isinstance(raw_hdrh, str):
        text = raw_hdrh.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
    return None


def extract_hdrh_percentiles_usec(raw_hdrh: Any) -> dict[int, int]:
    """Пытается извлечь p50/p95/p99 (usec) из hdrh payload."""
    payload = normalize_hdrh_payload(raw_hdrh)
    if payload is None:
        return {}

    parsed = _extract_percentile_value_pairs(payload)
    if not parsed:
        return {}

    result: dict[int, int] = {}
    for percentile in (50, 95, 99):
        value = _pick_percentile(parsed, percentile)
        if value is not None:
            result[percentile] = int(round(value))
    return result


def _extract_percentile_value_pairs(payload: Any) -> dict[float, float]:
    """Возвращает map `{percentile: latency_usec}` из произвольного hdrh payload."""
    result: dict[float, float] = {}
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            p = _as_float_or_none(item.get("percentile"))
            if p is None:
                p = _as_float_or_none(item.get("perc"))
            if p is None:
                p = _as_float_or_none(item.get("key"))
            value = _as_float_or_none(item.get("value"))
            if value is None:
                value = _as_float_or_none(item.get("val"))
            if p is None or value is None:
                continue
            normalized_p = _normalize_percentile_key(p)
            if normalized_p is not None:
                result[normalized_p] = value
        return result

    if not isinstance(payload, dict):
        return result

    for key in ("percentiles", "values", "data", "stats"):
        nested = payload.get(key)
        if nested is not None:
            result.update(_extract_percentile_value_pairs(nested))

    for key in ("p50", "p95", "p99"):
        value = _as_float_or_none(payload.get(key))
        if value is not None:
            normalized_p = float(key[1:])
            result[normalized_p] = value

    numeric_map_found = False
    for raw_p, raw_value in payload.items():
        p = _as_float_or_none(raw_p)
        value = _as_float_or_none(raw_value)
        if p is None or value is None:
            continue
        normalized_p = _normalize_percentile_key(p)
        if normalized_p is None:
            continue
        result[normalized_p] = value
        numeric_map_found = True

    if numeric_map_found:
        return result
    return result


def _normalize_percentile_key(raw_percentile: float) -> float | None:
    if raw_percentile <= 0:
        return None
    if raw_percentile <= 1:
        return raw_percentile * 100
    if raw_percentile <= 100:
        return raw_percentile
    return None


def _pick_percentile(percentile_map: dict[float, float], target: float) -> float | None:
    if not percentile_map:
        return None
    for key, value in percentile_map.items():
        if abs(key - target) < 1e-9:
            return value
    candidate_keys = sorted(percentile_map)
    lower = [key for key in candidate_keys if key <= target]
    upper = [key for key in candidate_keys if key >= target]
    if lower and upper:
        lo = lower[-1]
        hi = upper[0]
        if lo == hi:
            return percentile_map[lo]
        lo_val = percentile_map[lo]
        hi_val = percentile_map[hi]
        ratio = (target - lo) / (hi - lo)
        return lo_val + (hi_val - lo_val) * ratio
    if lower:
        return percentile_map[lower[-1]]
    if upper:
        return percentile_map[upper[0]]
    return None


def extract_latency_stats_by_pg_id(client: Any, pg_ids: tuple[int, ...]) -> dict[int, dict[str, Any]]:
    """Считывает latency stats по каждому latency PG ID."""
    pgid_stats = client.get_pgid_stats(pgid_list=list(pg_ids))
    latency_by_id = pgid_stats.get("latency", {})
    flow_stats_by_id = pgid_stats.get("flow_stats", {})

    stats_by_pg_id: dict[int, dict[str, Any]] = {}
    for pg_id in pg_ids:
        latency_entry = latency_by_id.get(pg_id) or latency_by_id.get(str(pg_id))
        if latency_entry is None:
            raise RuntimeError(f"TRex did not return latency stats for PG ID {pg_id}")
        latency = latency_entry.get("latency", {})
        histogram = normalize_latency_histogram(latency.get("histogram"))
        hdrh_percentiles_usec = extract_hdrh_percentiles_usec(latency.get("hdrh"))
        err_cntrs_raw = latency_entry.get("err_cntrs", {})
        err_cntrs = {
            "dropped": int(err_cntrs_raw.get("dropped", 0)),
            "dup": int(err_cntrs_raw.get("dup", 0)),
            "out_of_order": int(err_cntrs_raw.get("out_of_order", 0)),
            "seq_too_high": int(err_cntrs_raw.get("seq_too_high", 0)),
            "seq_too_low": int(err_cntrs_raw.get("seq_too_low", 0)),
        }

        flow_entry = flow_stats_by_id.get(pg_id) or flow_stats_by_id.get(str(pg_id)) or {}
        tx_pkts = extract_total_counter(flow_entry, "tx_pkts")
        rx_pkts = extract_total_counter(flow_entry, "rx_pkts")

        stats_by_pg_id[pg_id] = {
            "average_usec": _as_float_or_none(latency.get("average")),
            "jitter_usec": _as_float_or_none(latency.get("jitter")),
            "total_min_usec": _as_int_or_none(latency.get("total_min")),
            "total_max_usec": _as_int_or_none(latency.get("total_max")),
            "last_max_usec": _as_int_or_none(latency.get("last_max")),
            "histogram": histogram,
            "hdrh_percentiles_usec": hdrh_percentiles_usec,
            "sample_count": sum(histogram.values()),
            "err_cntrs": err_cntrs,
            "tx_packets": tx_pkts,
            "rx_packets": rx_pkts,
        }
    return stats_by_pg_id


def _as_float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def read_udp_counters_by_pg_id(client: Any, pg_ids: tuple[int, ...]) -> dict[int, tuple[int, int]]:
    """Считывает tx/rx counters по каждому PG ID отдельно."""
    pgid_stats = client.get_pgid_stats(pgid_list=list(pg_ids))
    flow_stats_by_id = pgid_stats.get("flow_stats", {})

    counters: dict[int, tuple[int, int]] = {}
    for pg_id in pg_ids:
        flow_stats = flow_stats_by_id.get(pg_id) or flow_stats_by_id.get(str(pg_id))
        if flow_stats is None:
            raise RuntimeError(f"TRex did not return flow stats for PG ID {pg_id}")
        tx_pkts = extract_total_counter(flow_stats, "tx_pkts")
        rx_pkts = extract_total_counter(flow_stats, "rx_pkts")
        counters[pg_id] = (tx_pkts, rx_pkts)
    return counters


def read_udp_counters(client: Any, pg_ids: tuple[int, ...] = (UDP_PG_ID,)) -> tuple[int, int]:
    """Считывает tx/rx counters по PGID-статистике UDP stream."""
    counters_by_pg_id = read_udp_counters_by_pg_id(client, pg_ids=pg_ids)
    tx_pkts = sum(tx_pkts for tx_pkts, _rx_pkts in counters_by_pg_id.values())
    rx_pkts = sum(rx_pkts for _tx_pkts, rx_pkts in counters_by_pg_id.values())
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


def configure_l3_mode(client: Any, port_pairs: tuple[TrexPortPair, ...] | None = None) -> None:
    """Настраивает L3/ARP для отправляющего и принимающего TRex-портов."""
    active_pairs = port_pairs if port_pairs is not None else resolve_active_port_pairs()[:1]
    ports: list[int] = []
    for pair in active_pairs:
        ports.extend((pair.inside.port_id, pair.outside.port_id))
    client.set_service_mode(ports=ports, enabled=True)
    try:
        for pair in active_pairs:
            client.set_l3_mode(
                port=pair.inside.port_id,
                src_ipv4=pair.inside.ip,
                dst_ipv4=pair.inside.default_gw,
            )
            client.set_l3_mode(
                port=pair.outside.port_id,
                src_ipv4=pair.outside.ip,
                dst_ipv4=pair.outside.default_gw,
            )
    finally:
        client.set_service_mode(ports=ports, enabled=False)


def select_load_port_pairs(port_pairs: tuple[TrexPortPair, ...], flow_count: int) -> tuple[TrexPortPair, ...]:
    """Выбирает количество memif-пар, которые будут задействованы в конкретном прогоне."""
    if not port_pairs:
        raise RuntimeError("No active TRex port pairs found")
    if flow_count <= 0:
        raise ValueError("flow_count must be positive")
    if flow_count == 1:
        return port_pairs[:1]
    return port_pairs[: min(len(port_pairs), flow_count)]


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
    all_port_pairs = resolve_active_port_pairs()
    active_pairs = select_load_port_pairs(all_port_pairs, flow_count=flow_count)
    ports: list[int] = []
    inside_ports: list[int] = []
    for pair in active_pairs:
        inside_ports.append(pair.inside.port_id)
        ports.extend((pair.inside.port_id, pair.outside.port_id))
    trex_data_cores = resolve_trex_data_cores()
    pair_count = len(active_pairs)
    pps_by_pair = split_evenly(target_pps, pair_count)
    flows_by_pair = [1] * pair_count if flow_count == 1 else split_evenly(flow_count, pair_count)
    streams_by_port: dict[int, list[Any]] = {port: [] for port in inside_ports}
    pg_ids: list[int] = []
    pair_run_plan: list[tuple[TrexPortPair, int, int, int]] = []
    next_sport = UDP_SPORT_RANGE_START
    for pair_slot, (pair, pair_pps, pair_flows) in enumerate(zip(active_pairs, pps_by_pair, flows_by_pair, strict=True)):
        pg_id = UDP_PG_ID + pair_slot
        pg_ids.append(pg_id)
        pair_run_plan.append((pair, pg_id, pair_pps, pair_flows))
        sport_start = None if flow_count == 1 else next_sport
        streams_by_port[pair.inside.port_id].append(
            build_udp_stream(
                api=api,
                target_pps=pair_pps,
                packet_size=packet_size,
                flow_count=pair_flows,
                src_ip=pair.inside.ip,
                dst_ip=pair.outside.ip,
                udp_sport_start=sport_start,
                pg_id=pg_id,
                core_id=pair_slot % trex_data_cores,
                name=f"udp_inside_to_outside_pair_{pair.pair_index}",
            )
        )
        if flow_count > 1:
            next_sport += pair_flows

    try:
        client.connect()
        client.reset(ports=ports)
        configure_l3_mode(client, port_pairs=active_pairs)
        client.remove_all_streams(ports=inside_ports)
        for port_id in inside_ports:
            client.add_streams(streams_by_port[port_id], ports=[port_id])
        client.clear_stats(ports=ports)
        client.start(ports=inside_ports)
        if warmup_sec > 0:
            time.sleep(warmup_sec)
            client.clear_stats(ports=ports)
        measurement_start_epoch = time.time()
        time.sleep(duration)
        client.stop(ports=inside_ports)
        measurement_end_epoch = time.time()
        counters_by_pg_id = read_udp_counters_by_pg_id(client, pg_ids=tuple(pg_ids))
        tx_packets = sum(tx_pkts for tx_pkts, _rx_pkts in counters_by_pg_id.values())
        rx_packets = sum(rx_pkts for _tx_pkts, rx_pkts in counters_by_pg_id.values())
        expected_packets = calculate_expected_packets(target_pps, duration, tx_packets)
        received_packets = rx_packets
        lost_packets = max(0, expected_packets - received_packets)
        loss_rate = lost_packets / expected_packets
        trex_queue_counters: list[dict[str, Any]] = []
        for pair, pg_id, pair_target_pps, pair_flows in pair_run_plan:
            pair_tx_packets, pair_rx_packets = counters_by_pg_id[pg_id]
            pair_expected_packets = calculate_expected_packets(pair_target_pps, duration, pair_tx_packets)
            pair_lost_packets = max(0, pair_expected_packets - pair_rx_packets)
            pair_loss_rate = pair_lost_packets / pair_expected_packets
            trex_queue_counters.append(
                {
                    "pair_index": pair.pair_index,
                    "queue_id": 0,
                    "pg_id": pg_id,
                    "inside_port_id": pair.inside.port_id,
                    "outside_port_id": pair.outside.port_id,
                    "inside_ip": pair.inside.ip,
                    "outside_ip": pair.outside.ip,
                    "target_pps": pair_target_pps,
                    "flow_count": pair_flows,
                    "expected_packets": pair_expected_packets,
                    "tx_packets": pair_tx_packets,
                    "rx_packets": pair_rx_packets,
                    "lost_packets": pair_lost_packets,
                    "loss_rate": pair_loss_rate,
                    "loss_percent": pair_loss_rate * 100,
                    "actual_sent_pps": pair_tx_packets / duration,
                }
            )
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
            trex_queue_counters=tuple(trex_queue_counters),
        )
    finally:
        try:
            client.stop(ports=inside_ports)
        except Exception:
            pass
        client.disconnect()


def run_udp_latency_measurement(
    target_pps: int,
    duration: float,
    packet_size: int = UDP_PACKET_SIZE_BYTES,
    flow_count: int = 1,
    warmup_sec: float = 0,
) -> UdpLatencyRunResult:
    """Запускает combined load+latency UDP тест и возвращает latency/loss артефакт."""
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
    all_port_pairs = resolve_active_port_pairs()
    active_pairs = select_load_port_pairs(all_port_pairs, flow_count=flow_count)
    ports: list[int] = []
    inside_ports: list[int] = []
    for pair in active_pairs:
        inside_ports.append(pair.inside.port_id)
        ports.extend((pair.inside.port_id, pair.outside.port_id))

    pair_count = len(active_pairs)
    trex_data_cores = resolve_trex_data_cores()
    pps_by_pair = split_evenly(target_pps, pair_count)
    flows_by_pair = [1] * pair_count if flow_count == 1 else split_evenly(flow_count, pair_count)

    streams_by_port: dict[int, list[Any]] = {port: [] for port in inside_ports}
    pair_run_plan: list[dict[str, Any]] = []
    load_pg_ids: list[int] = []
    latency_pg_ids: list[int] = []
    next_sport = UDP_SPORT_RANGE_START
    for pair_slot, (pair, pair_pps, pair_flows) in enumerate(zip(active_pairs, pps_by_pair, flows_by_pair, strict=True)):
        load_pg_id = UDP_PG_ID + pair_slot
        latency_pg_id = UDP_LATENCY_PG_ID_BASE + pair_slot
        load_pg_ids.append(load_pg_id)
        latency_pg_ids.append(latency_pg_id)
        sport_start = None if flow_count == 1 else next_sport
        core_id = pair_slot % trex_data_cores

        streams_by_port[pair.inside.port_id].append(
            build_udp_stream(
                api=api,
                target_pps=pair_pps,
                packet_size=packet_size,
                flow_count=pair_flows,
                src_ip=pair.inside.ip,
                dst_ip=pair.outside.ip,
                udp_sport_start=sport_start,
                pg_id=load_pg_id,
                core_id=core_id,
                name=f"udp_load_inside_to_outside_pair_{pair.pair_index}",
            )
        )
        streams_by_port[pair.inside.port_id].append(
            build_udp_latency_stream(
                api=api,
                target_pps=pair_pps,
                packet_size=packet_size,
                flow_count=pair_flows,
                src_ip=pair.inside.ip,
                dst_ip=pair.outside.ip,
                udp_sport_start=sport_start,
                pg_id=latency_pg_id,
                name=f"udp_latency_inside_to_outside_pair_{pair.pair_index}",
            )
        )

        pair_run_plan.append(
            {
                "pair": pair,
                "load_pg_id": load_pg_id,
                "latency_pg_id": latency_pg_id,
                "target_pps": pair_pps,
                "flow_count": pair_flows,
            }
        )
        if flow_count > 1:
            next_sport += pair_flows

    try:
        client.connect()
        client.reset(ports=ports)
        configure_l3_mode(client, port_pairs=active_pairs)
        client.remove_all_streams(ports=inside_ports)
        for port_id in inside_ports:
            client.add_streams(streams_by_port[port_id], ports=[port_id])
        client.clear_stats(ports=ports)
        client.start(ports=inside_ports)
        if warmup_sec > 0:
            time.sleep(warmup_sec)
            client.clear_stats(ports=ports)

        measurement_start_epoch = time.time()
        time.sleep(duration)
        client.stop(ports=inside_ports)
        measurement_end_epoch = time.time()

        load_counters_by_pg_id = read_udp_counters_by_pg_id(client, pg_ids=tuple(load_pg_ids))
        latency_stats_by_pg_id = extract_latency_stats_by_pg_id(client, pg_ids=tuple(latency_pg_ids))

        pair_results: list[UdpLatencyPairResult] = []
        all_histograms: list[dict[int, int]] = []
        summary_err_cntrs = {
            "dropped": 0,
            "dup": 0,
            "out_of_order": 0,
            "seq_too_high": 0,
            "seq_too_low": 0,
        }
        total_load_tx_packets = 0
        total_load_rx_packets = 0
        total_load_expected_packets = 0
        total_latency_tx_packets = 0
        total_latency_rx_packets = 0
        total_latency_samples = 0
        weighted_average_sum = 0.0
        average_weight = 0
        weighted_jitter_sum = 0.0
        jitter_weight = 0
        min_candidates: list[int] = []
        max_candidates: list[int] = []
        last_max_candidates: list[int] = []
        pair_percentile_sources: list[str] = []

        for pair_data in pair_run_plan:
            pair = pair_data["pair"]
            load_pg_id = pair_data["load_pg_id"]
            latency_pg_id = pair_data["latency_pg_id"]
            pair_target_pps = pair_data["target_pps"]
            pair_flows = pair_data["flow_count"]

            load_tx_packets, load_rx_packets = load_counters_by_pg_id[load_pg_id]
            load_expected_packets = calculate_expected_packets(pair_target_pps, duration, load_tx_packets)
            load_lost_packets = max(0, load_expected_packets - load_rx_packets)
            load_loss_rate = load_lost_packets / load_expected_packets

            latency_stats = latency_stats_by_pg_id[latency_pg_id]
            latency_histogram = latency_stats["histogram"]
            latency_sample_count = latency_stats["sample_count"]
            latency_average_usec = latency_stats["average_usec"]
            latency_jitter_usec = latency_stats["jitter_usec"]
            latency_total_min_usec = latency_stats["total_min_usec"]
            latency_total_max_usec = latency_stats["total_max_usec"]
            latency_last_max_usec = latency_stats["last_max_usec"]
            latency_err_cntrs = latency_stats["err_cntrs"]
            latency_tx_packets = latency_stats["tx_packets"]
            latency_rx_packets = latency_stats["rx_packets"]
            hdrh_percentiles_usec = latency_stats.get("hdrh_percentiles_usec", {})

            p50_usec = _as_int_or_none(hdrh_percentiles_usec.get(50))
            p95_usec = _as_int_or_none(hdrh_percentiles_usec.get(95))
            p99_usec = _as_int_or_none(hdrh_percentiles_usec.get(99))
            percentile_source = "hdrh"
            if p50_usec is None or p95_usec is None or p99_usec is None:
                p50_usec = calculate_histogram_percentile(latency_histogram, 50)
                p95_usec = calculate_histogram_percentile(latency_histogram, 95)
                p99_usec = calculate_histogram_percentile(latency_histogram, 99)
                percentile_source = "histogram"

            pair_results.append(
                UdpLatencyPairResult(
                    pair_index=pair.pair_index,
                    inside_port_id=pair.inside.port_id,
                    outside_port_id=pair.outside.port_id,
                    inside_ip=pair.inside.ip,
                    outside_ip=pair.outside.ip,
                    flow_count=pair_flows,
                    load_target_pps=pair_target_pps,
                    latency_target_pps=pair_target_pps,
                    load_pg_id=load_pg_id,
                    latency_pg_id=latency_pg_id,
                    load_expected_packets=load_expected_packets,
                    load_tx_packets=load_tx_packets,
                    load_rx_packets=load_rx_packets,
                    load_lost_packets=load_lost_packets,
                    load_loss_rate=load_loss_rate,
                    load_actual_sent_pps=load_tx_packets / duration,
                    latency_tx_packets=latency_tx_packets,
                    latency_rx_packets=latency_rx_packets,
                    latency_sample_count=latency_sample_count,
                    latency_average_usec=latency_average_usec,
                    latency_jitter_usec=latency_jitter_usec,
                    latency_total_min_usec=latency_total_min_usec,
                    latency_total_max_usec=latency_total_max_usec,
                    latency_last_max_usec=latency_last_max_usec,
                    latency_p50_usec=p50_usec,
                    latency_p95_usec=p95_usec,
                    latency_p99_usec=p99_usec,
                    latency_percentile_source=percentile_source,
                    latency_histogram=latency_histogram,
                    latency_err_cntrs=latency_err_cntrs,
                )
            )
            pair_percentile_sources.append(percentile_source)

            all_histograms.append(latency_histogram)
            for key in summary_err_cntrs:
                summary_err_cntrs[key] += int(latency_err_cntrs.get(key, 0))
            total_load_tx_packets += load_tx_packets
            total_load_rx_packets += load_rx_packets
            total_load_expected_packets += load_expected_packets
            total_latency_tx_packets += latency_tx_packets
            total_latency_rx_packets += latency_rx_packets
            total_latency_samples += latency_sample_count
            if latency_average_usec is not None and latency_sample_count > 0:
                weighted_average_sum += latency_average_usec * latency_sample_count
                average_weight += latency_sample_count
            if latency_jitter_usec is not None and latency_sample_count > 0:
                weighted_jitter_sum += latency_jitter_usec * latency_sample_count
                jitter_weight += latency_sample_count
            if latency_total_min_usec is not None:
                min_candidates.append(latency_total_min_usec)
            if latency_total_max_usec is not None:
                max_candidates.append(latency_total_max_usec)
            if latency_last_max_usec is not None:
                last_max_candidates.append(latency_last_max_usec)

        total_load_lost_packets = max(0, total_load_expected_packets - total_load_rx_packets)
        total_load_loss_rate = total_load_lost_packets / total_load_expected_packets
        merged_histogram = merge_latency_histograms(all_histograms)
        summary_percentile_source = "hdrh" if pair_percentile_sources and all(
            source == "hdrh" for source in pair_percentile_sources
        ) else "histogram"

        summary = UdpLatencySummary(
            sample_count=total_latency_samples,
            average_usec=(
                weighted_average_sum / average_weight
                if average_weight > 0
                else None
            ),
            jitter_usec=(weighted_jitter_sum / jitter_weight if jitter_weight > 0 else None),
            total_min_usec=min(min_candidates) if min_candidates else None,
            total_max_usec=max(max_candidates) if max_candidates else None,
            last_max_usec=max(last_max_candidates) if last_max_candidates else None,
            p50_usec=calculate_histogram_percentile(merged_histogram, 50),
            p95_usec=calculate_histogram_percentile(merged_histogram, 95),
            p99_usec=calculate_histogram_percentile(merged_histogram, 99),
            percentile_source=summary_percentile_source,
            histogram=merged_histogram,
            err_cntrs=summary_err_cntrs,
        )

        return UdpLatencyRunResult(
            target_pps=target_pps,
            duration_sec=duration,
            warmup_sec=warmup_sec,
            packet_size=packet_size,
            flow_count=flow_count,
            pair_count=pair_count,
            measurement_start_epoch=measurement_start_epoch,
            measurement_end_epoch=measurement_end_epoch,
            total_load_expected_packets=total_load_expected_packets,
            total_load_tx_packets=total_load_tx_packets,
            total_load_rx_packets=total_load_rx_packets,
            total_load_lost_packets=total_load_lost_packets,
            total_load_loss_rate=total_load_loss_rate,
            total_load_loss_percent=total_load_loss_rate * 100,
            total_load_actual_sent_pps=total_load_tx_packets / duration,
            total_latency_tx_packets=total_latency_tx_packets,
            total_latency_rx_packets=total_latency_rx_packets,
            total_latency_samples=total_latency_samples,
            summary=summary,
            pairs=tuple(pair_results),
        )
    finally:
        try:
            client.stop(ports=inside_ports)
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

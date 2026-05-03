from __future__ import annotations

import selectors
import resource
import socket
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .config import TrafficTestConfig, machine_metadata_dict, utc_now_iso, validate_traffic_config
from .metrics import ReservoirSampler, ResourceMonitor, summarize_latencies_ns
from .packets import FLAG_MEASUREMENT, build_test_packet, parse_test_packet
from .results import write_json


@dataclass(slots=True)
class StepResult:
    mode: str
    test_name: str
    packet_size_bytes: int
    flow_count: int
    target_pps: int
    actual_sent_pps: float
    actual_gbps: float
    loss_threshold: float
    passed: bool
    sent_packets: int
    sent_bytes: int
    received_replies: int | None
    lost_packets: int | None
    loss_rate: float | None
    latency_ms: dict[str, float | None]
    latency_sampling: dict[str, int]
    resources: dict[str, Any]
    machine_metadata: dict[str, Any]
    timestamps: dict[str, str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["configured_pps"] = self.target_pps
        return payload


def run_udp_client_once(
    config: TrafficTestConfig,
    configured_pps: int,
    nat_pid: int | None = None,
    output_path: Path | None = None,
    progress: Callable[[str], None] | None = None,
    progress_interval_sec: int = 5,
) -> StepResult:
    validate_traffic_config(config)
    if configured_pps <= 0:
        raise ValueError("configured_pps must be positive")

    total_duration_ns = (config.warmup_duration_sec + config.measurement_duration_sec) * 1_000_000_000
    warmup_duration_ns = config.warmup_duration_sec * 1_000_000_000
    measurement_duration_ns = config.measurement_duration_sec * 1_000_000_000
    start_wall = utc_now_iso()
    start_ns = time.monotonic_ns()
    measurement_start_ns = start_ns + warmup_duration_ns
    send_end_ns = start_ns + total_duration_ns
    measurement_end_ns = measurement_start_ns + measurement_duration_ns
    total_expected_sec = config.warmup_duration_sec + config.measurement_duration_sec + config.drain_duration_sec

    if progress is not None:
        destination = f"{config.server_ip}:{config.server_port}"
        progress(
            "Ступенька запущена: "
            f"target_pps={configured_pps}, "
            f"server={destination}, "
            f"mode={config.server_mode}, "
            f"flow_count={config.flow_count}, "
            f"packet_size_bytes={config.packet_size_bytes}, "
            f"warmup={config.warmup_duration_sec}s, "
            f"measurement={config.measurement_duration_sec}s, "
            f"drain={config.drain_duration_sec}s, "
            f"ожидаемое_время~{total_expected_sec}s"
        )
        if output_path is not None:
            progress(f"Файл результата ступеньки: {output_path}")

    sockets = _open_flow_sockets(config)
    receiver = None
    collector: ReplyCollector | None = None
    warnings: list[str] = []

    if config.server_mode == "echo":
        collector = ReplyCollector(sample_size=config.latency_sample_size)
        receiver = ReplyReceiver(sockets=sockets, collector=collector)
        receiver.start()

    resource_monitor = ResourceMonitor(pid=nat_pid)
    resource_monitor.start()

    sent_packets = 0
    sent_bytes = 0
    measurement_sent_packets = 0
    measurement_sent_bytes = 0
    seq = 0
    max_burst_packets = max(64, min(4096, configured_pps // 100 if configured_pps >= 100 else 64))
    destination = (config.server_ip, config.server_port)
    next_progress_ns = start_ns + max(progress_interval_sec, 1) * 1_000_000_000

    try:
        while True:
            now_ns = time.monotonic_ns()
            if now_ns >= send_end_ns:
                break

            # Periodic heartbeat so long attempts do not look stuck.
            if progress is not None and now_ns >= next_progress_ns:
                progress(_format_progress_message(now_ns, start_ns, measurement_start_ns, measurement_end_ns, send_end_ns, sent_packets))
                next_progress_ns += max(progress_interval_sec, 1) * 1_000_000_000

            elapsed_ns = now_ns - start_ns
            target_sent_packets = (elapsed_ns * configured_pps) // 1_000_000_000
            budget = int(target_sent_packets - sent_packets)
            if budget <= 0:
                time.sleep(0.0005)
                continue

            budget = min(budget, max_burst_packets)
            for _ in range(budget):
                packet_send_ns = time.monotonic_ns()
                if packet_send_ns >= send_end_ns:
                    break

                flow_id = seq % config.flow_count
                flags = FLAG_MEASUREMENT if measurement_start_ns <= packet_send_ns < measurement_end_ns else 0
                packet = build_test_packet(
                    flow_id=flow_id,
                    seq=seq,
                    send_ts_ns=packet_send_ns,
                    packet_size_bytes=config.packet_size_bytes,
                    flags=flags,
                )
                sock = sockets[flow_id]
                try:
                    sock.sendto(packet, destination)
                except BlockingIOError:
                    warnings.append("socket send buffer was full; packet send pacing may be affected")
                    time.sleep(0.0005)
                    continue

                sent_packets += 1
                sent_bytes += config.packet_size_bytes
                if flags & FLAG_MEASUREMENT:
                    measurement_sent_packets += 1
                    measurement_sent_bytes += config.packet_size_bytes
                seq += 1
    finally:
        if receiver is not None:
            if progress is not None and config.drain_duration_sec > 0:
                progress(f"Фаза отправки завершена, дожидаемся ответов еще {config.drain_duration_sec}s")
            receiver.stop_after_drain(config.drain_duration_sec)
            receiver.join()
        resource_monitor.stop()
        for sock in sockets:
            sock.close()

    finished_at = utc_now_iso()

    actual_sent_pps = measurement_sent_packets / config.measurement_duration_sec
    if configured_pps > 0:
        relative_diff = abs(actual_sent_pps - configured_pps) / configured_pps
        if relative_diff > config.actual_pps_tolerance:
            warnings.append(
                "actual_sent_pps differs from configured_pps by more than "
                f"{int(config.actual_pps_tolerance * 100)}%"
            )

    resources = resource_monitor.summary()
    warnings.extend(resource_monitor.warnings)
    actual_gbps = actual_sent_pps * config.packet_size_bytes * 8 / 1_000_000_000

    if collector is None:
        received_replies = None
        lost_packets = None
        loss_rate = None
        latency = summarize_latencies_ns(ReservoirSampler(1)).to_dict()
        latency["avg"] = None
        latency["p50"] = None
        latency["p95"] = None
        latency["p99"] = None
        latency["sample_count"] = 0
        latency["observed_count"] = 0
    else:
        received_replies = collector.measurement_replies
        lost_packets = max(measurement_sent_packets - received_replies, 0)
        loss_rate = (lost_packets / measurement_sent_packets) if measurement_sent_packets else None
        latency = summarize_latencies_ns(collector.latency_samples).to_dict()
        warnings.extend(collector.warnings)

    passed = bool(loss_rate is not None and loss_rate <= config.loss_threshold)
    if config.server_mode == "sink":
        passed = False
        warnings.append("режим sink не возвращает ответы, поэтому packet loss и pass/fail корректно оценить нельзя")

    result = StepResult(
        mode=config.nat_mode,
        test_name=config.test_name,
        packet_size_bytes=config.packet_size_bytes,
        flow_count=config.flow_count,
        target_pps=configured_pps,
        actual_sent_pps=round(actual_sent_pps, 4),
        actual_gbps=round(actual_gbps, 9),
        loss_threshold=config.loss_threshold,
        passed=passed,
        sent_packets=measurement_sent_packets,
        sent_bytes=measurement_sent_bytes,
        received_replies=received_replies,
        lost_packets=lost_packets,
        loss_rate=round(loss_rate, 9) if loss_rate is not None else None,
        latency_ms={
            "avg": latency["avg"],
            "p50": latency["p50"],
            "p95": latency["p95"],
            "p99": latency["p99"],
        },
        latency_sampling={
            "sample_count": latency["sample_count"],
            "observed_count": latency["observed_count"],
        },
        resources=resources,
        machine_metadata=machine_metadata_dict(),
        timestamps={
            "started_at": start_wall,
            "finished_at": finished_at,
        },
        warnings=deduplicate_preserve_order(warnings),
    )

    if output_path is not None:
        write_json(output_path, result.to_dict())
    if progress is not None:
        progress(
            "Ступенька завершена: "
            f"target_pps={configured_pps}, "
            f"passed={result.passed}, "
            f"actual_sent_pps={result.actual_sent_pps}, "
            f"loss_rate={result.loss_rate}, "
            f"latency_p95_ms={result.latency_ms['p95']}"
        )
    return result


class ReplyCollector:
    def __init__(self, sample_size: int) -> None:
        self.measurement_replies = 0
        self.latency_samples = ReservoirSampler(sample_size)
        self.warnings: list[str] = []

    def handle_payload(self, payload: bytes, receive_time_ns: int) -> None:
        try:
            packet = parse_test_packet(payload)
        except ValueError as exc:
            self.warnings.append(f"received malformed reply: {exc}")
            return

        if packet.flags & FLAG_MEASUREMENT:
            self.measurement_replies += 1
            latency_ns = max(receive_time_ns - packet.send_ts_ns, 0)
            self.latency_samples.add(latency_ns)


class ReplyReceiver:
    def __init__(self, sockets: list[socket.socket], collector: ReplyCollector) -> None:
        self.sockets = sockets
        self.collector = collector
        self._drain_deadline_ns: int | None = None
        self._thread = threading.Thread(target=self._run, name="udp-reply-receiver", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop_after_drain(self, drain_duration_sec: int) -> None:
        self._drain_deadline_ns = time.monotonic_ns() + int(drain_duration_sec * 1_000_000_000)

    def join(self) -> None:
        self._thread.join()

    def _run(self) -> None:
        selector = selectors.DefaultSelector()
        for sock in self.sockets:
            selector.register(sock, selectors.EVENT_READ)

        try:
            while True:
                now_ns = time.monotonic_ns()
                if self._drain_deadline_ns is not None and now_ns >= self._drain_deadline_ns:
                    break

                events = selector.select(timeout=0.2)
                for key, _ in events:
                    sock = key.fileobj
                    while True:
                        try:
                            payload, _addr = sock.recvfrom(65535)
                        except BlockingIOError:
                            break
                        except OSError as exc:
                            self.collector.warnings.append(f"reply recv failed: {exc}")
                            break
                        self.collector.handle_payload(payload, time.monotonic_ns())
        finally:
            selector.close()


def _open_flow_sockets(config: TrafficTestConfig) -> list[socket.socket]:
    soft_limit, _hard_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
    required_descriptors = config.flow_count + 32
    if soft_limit != resource.RLIM_INFINITY and soft_limit < required_descriptors:
        raise RuntimeError(
            f"flow_count={config.flow_count} requires at least {required_descriptors} file descriptors, "
            f"but RLIMIT_NOFILE soft limit is {soft_limit}. Increase ulimit -n or lower flow_count."
        )

    sockets: list[socket.socket] = []
    try:
        for flow_id in range(config.flow_count):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, config.socket_send_buffer_bytes)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, config.socket_receive_buffer_bytes)
            sock.bind((config.bind_ip, config.base_src_port + flow_id))
            sockets.append(sock)
    except Exception:
        for sock in sockets:
            sock.close()
        raise
    return sockets


def deduplicate_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _format_progress_message(
    now_ns: int,
    start_ns: int,
    measurement_start_ns: int,
    measurement_end_ns: int,
    send_end_ns: int,
    sent_packets: int,
) -> str:
    elapsed_sec = max((now_ns - start_ns) / 1_000_000_000, 0.0)
    send_total_sec = max((send_end_ns - start_ns) / 1_000_000_000, 0.0)
    if now_ns < measurement_start_ns:
        phase = "прогрев"
    elif now_ns < measurement_end_ns:
        phase = "замер"
    else:
        phase = "хвост_отправки"
    return (
        "Прогресс ступеньки: "
        f"phase={phase}, "
        f"elapsed={elapsed_sec:.1f}s/{send_total_sec:.1f}s, "
        f"sent_packets_total={sent_packets}"
    )

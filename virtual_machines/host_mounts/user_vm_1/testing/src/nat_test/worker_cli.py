from __future__ import annotations

import json
import runpy
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import rich_click as click

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
SIMPLE_TEST_PATH = PACKAGE_ROOT / "simple_test.py"
DEFAULT_SERVER_IP = "10.8.0.2"
DEFAULT_SERVER_PORT_BASE = 5001


@dataclass(slots=True)
class FlowRun:
    flow_index: int
    server_ip: str
    server_port: int
    target_mps: int
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    parsed: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("stdout")
        payload.pop("stderr")
        return payload


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def app() -> None:
    """Worker commands executed inside user_vm_1."""


@app.command("simple-test")
def simple_test_command() -> None:
    """Run the existing simple_test.py connectivity check."""
    try:
        runpy.run_path(str(SIMPLE_TEST_PATH), run_name="__main__")
    except SystemExit as exc:
        raise click.exceptions.Exit(exc.code if isinstance(exc.code, int) else 1) from exc


@app.command("run-step")
@click.option("--target-pps", type=int, required=True, help="Total target sockperf messages per second.")
@click.option("--packet-size", type=int, required=True, help="sockperf --msg-size value.")
@click.option("--n-flows", type=int, required=True, help="Number of sockperf client flows/processes.")
@click.option("--warmup-sec", type=int, required=True, help="Warmup duration before measurement.")
@click.option("--measurement-sec", type=int, required=True, help="Measurement duration.")
@click.option("--server-ip", default=DEFAULT_SERVER_IP, show_default=True, help="External VM sockperf server IP.")
@click.option("--server-port-base", type=int, default=DEFAULT_SERVER_PORT_BASE, show_default=True)
@click.option("--reply-every", type=int, default=100, show_default=True, help="sockperf --reply-every value.")
@click.option("--sockperf-bin", default="sockperf", show_default=True, help="sockperf executable.")
def run_step_command(
    target_pps: int,
    packet_size: int,
    n_flows: int,
    warmup_sec: int,
    measurement_sec: int,
    server_ip: str,
    server_port_base: int,
    reply_every: int,
    sockperf_bin: str,
) -> None:
    """Run one benchmark step via sockperf under-load."""
    result = run_step(
        target_pps=target_pps,
        packet_size=packet_size,
        n_flows=n_flows,
        warmup_sec=warmup_sec,
        measurement_sec=measurement_sec,
        server_ip=server_ip,
        server_port_base=server_port_base,
        reply_every=reply_every,
        sockperf_bin=sockperf_bin,
    )
    click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    if result["status"] != "ok":
        raise click.exceptions.Exit(2)


def run_step(
    target_pps: int,
    packet_size: int,
    n_flows: int,
    warmup_sec: int,
    measurement_sec: int,
    server_ip: str,
    server_port_base: int,
    reply_every: int,
    sockperf_bin: str,
) -> dict[str, Any]:
    validate_run_step_args(
        target_pps=target_pps,
        packet_size=packet_size,
        n_flows=n_flows,
        warmup_sec=warmup_sec,
        measurement_sec=measurement_sec,
        server_port_base=server_port_base,
        reply_every=reply_every,
    )
    resolved_sockperf = require_sockperf(sockperf_bin)
    log_lines: list[str] = []

    def log(message: str) -> None:
        line = f"{utc_now_iso()} {message}"
        log_lines.append(line)
        click.echo(f"[run-step] {message}", err=True)

    run_started_at = utc_now_iso()
    warmup_started_at = run_started_at
    log(
        "Старт run_step: "
        f"target_pps={target_pps}, packet_size={packet_size}, n_flows={n_flows}, "
        f"warmup_sec={warmup_sec}, measurement_sec={measurement_sec}, server={server_ip}:{server_port_base}"
    )

    flow_mps = split_rate(target_pps, n_flows)
    warmup_runs: list[FlowRun] = []
    if warmup_sec > 0:
        log("Запуск warmup-фазы")
        warmup_runs = run_sockperf_phase(
            phase="warmup",
            duration_sec=warmup_sec,
            flow_mps=flow_mps,
            packet_size=packet_size,
            server_ip=server_ip,
            server_port_base=server_port_base,
            reply_every=reply_every,
            sockperf_bin=resolved_sockperf,
            log=log,
        )
    else:
        log("Warmup-фаза пропущена: warmup_sec=0")

    measurement_started_at = utc_now_iso()
    log("Запуск measurement-фазы")
    measurement_runs = run_sockperf_phase(
        phase="measurement",
        duration_sec=measurement_sec,
        flow_mps=flow_mps,
        packet_size=packet_size,
        server_ip=server_ip,
        server_port_base=server_port_base,
        reply_every=reply_every,
        sockperf_bin=resolved_sockperf,
        log=log,
    )
    measurement_finished_at = utc_now_iso()

    failed_flows = [flow for flow in measurement_runs if flow.returncode != 0]
    aggregate = aggregate_measurement(measurement_runs, target_pps=target_pps, duration_sec=measurement_sec)
    status = "ok" if not failed_flows else "sockperf_failed"
    result = {
        "status": status,
        "target_pps": target_pps,
        "packet_size": packet_size,
        "n_flows": n_flows,
        "warmup_sec": warmup_sec,
        "measurement_sec": measurement_sec,
        "server_ip": server_ip,
        "server_port_base": server_port_base,
        "reply_every": reply_every,
        "sockperf_bin": resolved_sockperf,
        "timestamps": {
            "run_started_at": run_started_at,
            "warmup_started_at": warmup_started_at,
            "measurement_started_at": measurement_started_at,
            "measurement_finished_at": measurement_finished_at,
            "run_finished_at": utc_now_iso(),
        },
        "aggregate": aggregate,
        "measurement_flows": [flow.to_dict() for flow in measurement_runs],
        "warmup_flows": [flow.to_dict() for flow in warmup_runs],
        "warnings": build_warnings(measurement_runs, aggregate, target_pps),
    }

    log(
        "Measurement завершен: "
        f"status={status}, sent_packets={aggregate['sent_packets']}, "
        f"received_packets={aggregate['received_packets']}, loss_rate={aggregate['loss_rate']}"
    )
    return result


def validate_run_step_args(
    target_pps: int,
    packet_size: int,
    n_flows: int,
    warmup_sec: int,
    measurement_sec: int,
    server_port_base: int,
    reply_every: int,
) -> None:
    if target_pps <= 0:
        raise ValueError("--target-pps must be positive")
    if packet_size <= 0:
        raise ValueError("--packet-size must be positive")
    if n_flows <= 0:
        raise ValueError("--n-flows must be positive")
    if target_pps < n_flows:
        raise ValueError("--target-pps must be at least --n-flows so every flow gets >=1 mps")
    if warmup_sec < 0:
        raise ValueError("--warmup-sec must be non-negative")
    if measurement_sec <= 0:
        raise ValueError("--measurement-sec must be positive")
    if server_port_base <= 0 or server_port_base + n_flows - 1 > 65535:
        raise ValueError("server port range is outside 1..65535")
    if reply_every <= 0:
        raise ValueError("--reply-every must be positive")


def require_sockperf(sockperf_bin: str) -> str:
    resolved = shutil.which(sockperf_bin)
    if resolved is None:
        raise RuntimeError(f"sockperf executable not found: {sockperf_bin}")
    return resolved


def split_rate(total_mps: int, n_flows: int) -> list[int]:
    base = total_mps // n_flows
    remainder = total_mps % n_flows
    return [base + (1 if index < remainder else 0) for index in range(n_flows)]


def run_sockperf_phase(
    phase: str,
    duration_sec: int,
    flow_mps: list[int],
    packet_size: int,
    server_ip: str,
    server_port_base: int,
    reply_every: int,
    sockperf_bin: str,
    log: Any,
) -> list[FlowRun]:
    processes: list[tuple[int, int, int, list[str], subprocess.Popen[str]]] = []
    for flow_index, target_mps in enumerate(flow_mps):
        server_port = server_port_base + flow_index
        command = [
            sockperf_bin,
            "under-load",
            "-i",
            server_ip,
            "-p",
            str(server_port),
            "--msg-size",
            str(packet_size),
            "--time",
            str(duration_sec),
            "--mps",
            str(target_mps),
            "--reply-every",
            str(reply_every),
        ]
        log(f"{phase}: flow={flow_index}, port={server_port}, mps={target_mps}")
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        processes.append((flow_index, server_port, target_mps, command, process))

    results: list[FlowRun] = []
    for flow_index, server_port, target_mps, command, process in processes:
        stdout, stderr = process.communicate()
        parsed = parse_sockperf_output(stdout + "\n" + stderr)
        flow = FlowRun(
            flow_index=flow_index,
            server_ip=server_ip,
            server_port=server_port,
            target_mps=target_mps,
            command=command,
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            parsed=parsed,
        )
        results.append(flow)
        log(f"{phase}: flow={flow_index} завершен с кодом {process.returncode}")
    return results


def parse_sockperf_output(output: str) -> dict[str, Any]:
    import re

    parsed: dict[str, Any] = {}
    patterns = {
        "sent_packets": [
            r"(\d+)\s+(?:messages|packets)\s+sent",
            r"(?:sent|messages sent|total sent)[^0-9\n]*(\d+)",
        ],
        "received_packets": [
            r"(\d+)\s+(?:messages|packets)\s+received",
            r"(?:received|messages received|total received)[^0-9\n]*(\d+)",
        ],
        "dropped_packets": [
            r"(\d+)\s+(?:messages|packets)\s+(?:dropped|lost)",
            r"(?:dropped|lost|messages lost|total dropped)[^0-9\n]*(\d+)",
        ],
        "duplicated_packets": [
            r"(?:duplicate|duplicated)[^0-9\n]*(\d+)",
        ],
        "out_of_order_packets": [
            r"(?:out.of.order|out of order)[^0-9\n]*(\d+)",
        ],
        "latency_avg": [
            r"(?:avg|average)[^0-9\n]*(\d+(?:\.\d+)?)",
        ],
        "latency_p50": [
            r"(?:percentile\s*50(?:\.0+)?|p50)[^0-9\n]*(\d+(?:\.\d+)?)",
        ],
        "latency_p95": [
            r"(?:percentile\s*95(?:\.0+)?|p95)[^0-9\n]*(\d+(?:\.\d+)?)",
        ],
        "latency_p99": [
            r"(?:percentile\s*99(?:\.0+)?|p99)[^0-9\n]*(\d+(?:\.\d+)?)",
        ],
    }
    for field, field_patterns in patterns.items():
        for pattern in field_patterns:
            match = re.search(pattern, output, flags=re.IGNORECASE)
            if match is None:
                continue
            value = match.group(1)
            parsed[field] = float(value) if "." in value else int(value)
            break
    return parsed


def aggregate_measurement(flows: list[FlowRun], target_pps: int, duration_sec: int) -> dict[str, Any]:
    expected_sent = target_pps * duration_sec
    sent_values = [flow.parsed.get("sent_packets") for flow in flows]
    received_values = [flow.parsed.get("received_packets") for flow in flows]
    dropped_values = [flow.parsed.get("dropped_packets") for flow in flows]

    sent_packets = sum_ints(sent_values)
    if sent_packets is None:
        sent_packets = expected_sent
    received_packets = sum_ints(received_values)
    dropped_packets = sum_ints(dropped_values)
    if dropped_packets is None and sent_packets is not None and received_packets is not None:
        dropped_packets = max(sent_packets - received_packets, 0)

    loss_rate = None
    if sent_packets and dropped_packets is not None:
        loss_rate = dropped_packets / sent_packets

    latency = aggregate_latency(flows)
    actual_sent_pps = sent_packets / duration_sec if sent_packets is not None else None
    return {
        "target_pps": target_pps,
        "actual_sent_pps": round(actual_sent_pps, 4) if actual_sent_pps is not None else None,
        "sent_packets": sent_packets,
        "received_packets": received_packets,
        "dropped_packets": dropped_packets,
        "loss_rate": round(loss_rate, 9) if loss_rate is not None else None,
        "duplicated_packets": sum_ints(flow.parsed.get("duplicated_packets") for flow in flows),
        "out_of_order_packets": sum_ints(flow.parsed.get("out_of_order_packets") for flow in flows),
        "latency_rtt": latency,
    }


def aggregate_latency(flows: list[FlowRun]) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for source, target in (
        ("latency_avg", "avg"),
        ("latency_p50", "p50"),
        ("latency_p95", "p95"),
        ("latency_p99", "p99"),
    ):
        values = [flow.parsed[source] for flow in flows if source in flow.parsed]
        result[target] = round(mean(values), 6) if values else None
    return result


def build_warnings(flows: list[FlowRun], aggregate: dict[str, Any], target_pps: int) -> list[str]:
    warnings: list[str] = []
    failed = [flow.flow_index for flow in flows if flow.returncode != 0]
    if failed:
        warnings.append(f"sockperf failed for flows: {failed}")
    if aggregate["received_packets"] is None and aggregate["dropped_packets"] is None:
        warnings.append("sockperf output parser did not find received/dropped counters")
    actual_sent_pps = aggregate["actual_sent_pps"]
    if actual_sent_pps is not None and abs(actual_sent_pps - target_pps) / target_pps > 0.05:
        warnings.append("actual_sent_pps differs from target_pps by more than 5%")
    return warnings


def sum_ints(values: Any) -> int | None:
    total = 0
    seen = False
    for value in values:
        if value is None:
            continue
        total += int(value)
        seen = True
    return total if seen else None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    try:
        app.main(standalone_mode=False)
        return 0
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except (OSError, RuntimeError, ValueError) as exc:
        click.echo(f"error: {exc}", err=True)
        return 1
    except click.Abort:
        click.echo("Aborted", err=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

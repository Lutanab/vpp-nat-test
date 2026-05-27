from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import rich_click as click

from manage_nat.helpers import with_privileges
from manage_nat.network.setup import VPP_SERVICE_NAME, wait_for_vpp_ready

from ..trex.run.udp import (
    CLIENT_PORT,
    SERVER_PORT,
    TREX_SERVER_HOST,
    build_udp_streams,
    configure_l3_mode,
    load_trex_stl_api,
    read_udp_counters,
)
from .base import (
    FAILOVER_PROFILE_PLOT_FILE_NAME,
    FAILOVER_PROFILE_RESULT_FILE_NAME,
    FailoverConfig,
    FailoverTimeConfig,
    ProfileSample,
    build_failover_profile_results_dir,
    quiet_stdout,
)


def run_failover_udp_profile(config: FailoverConfig, time_config: FailoverTimeConfig) -> None:
    """Профилирует rx-динамику UDP трафика во время асинхронного рестарта VPP."""
    api = load_trex_stl_api()
    client = api.STLClient(server=TREX_SERVER_HOST)
    ports = [CLIENT_PORT, SERVER_PORT]
    poll_period_sec = time_config.poll_interval_ms / 1000.0
    restart_process: subprocess.Popen[bytes] | None = None

    try:
        client.connect()
        client.reset(ports=ports)
        configure_l3_mode(client)
        streams, pg_ids = build_udp_streams(
            api=api,
            target_pps=config.target_pps,
            packet_size=config.packet_size,
            flow_count=config.flow_count,
            trex_data_cores=1,
        )
        client.remove_all_streams(ports=[CLIENT_PORT])
        client.add_streams(streams, ports=[CLIENT_PORT])

        click.echo("warmup: continuous inside->outside traffic")
        client.clear_stats(ports=ports)
        client.start(ports=[CLIENT_PORT], force=True)
        if time_config.warmup_sec > 0:
            time.sleep(time_config.warmup_sec)
        client.clear_stats(ports=ports)

        click.echo("measure: polling counters + async restart")
        measurement_start = time.monotonic()
        restart_process = subprocess.Popen(
            with_privileges(["systemctl", "restart", VPP_SERVICE_NAME]),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        samples: list[ProfileSample] = []
        last_tx_pkts = 0
        last_rx_pkts = 0
        poll_errors = 0
        while True:
            now = time.monotonic()
            elapsed = now - measurement_start
            if elapsed > time_config.waiting_sec:
                break
            tx_pkts, rx_pkts, ok = try_read_udp_counters(
                client,
                pg_ids=pg_ids,
                fallback=(last_tx_pkts, last_rx_pkts),
            )
            if not ok:
                poll_errors += 1
            last_tx_pkts, last_rx_pkts = tx_pkts, rx_pkts
            samples.append(ProfileSample(elapsed_sec=elapsed, tx_pkts=tx_pkts, rx_pkts=rx_pkts))
            next_tick = now + poll_period_sec
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)

        if samples:
            tx_pkts, rx_pkts, _ = try_read_udp_counters(
                client,
                pg_ids=pg_ids,
                fallback=(last_tx_pkts, last_rx_pkts),
            )
            samples.append(
                ProfileSample(
                    elapsed_sec=min(time_config.waiting_sec, time.monotonic() - measurement_start),
                    tx_pkts=tx_pkts,
                    rx_pkts=rx_pkts,
                )
            )

        if restart_process.wait(timeout=60) != 0:
            raise RuntimeError("VPP restart process failed during profile measurement")
        with quiet_stdout():
            wait_for_vpp_ready(timeout_seconds=25)

        points = build_profile_points(samples)
        result_path = write_profile_tsv(points, config)
        plot_path = write_profile_plot(points, config)
        if not points:
            raise RuntimeError("No profile samples were collected")

        peak_rx_pps = max(point["rx_pps"] for point in points)
        min_rx_pps = min(point["rx_pps"] for point in points)
        final_rx_pps = points[-1]["rx_pps"]
        click.echo(
            "result: "
            f"samples={len(points)}, min_rx_pps={min_rx_pps:.2f}, "
            f"peak_rx_pps={peak_rx_pps:.2f}, final_rx_pps={final_rx_pps:.2f}, poll_errors={poll_errors}"
        )
        click.echo(f"series: {result_path}")
        click.echo(f"plot: {plot_path}")
    finally:
        if restart_process is not None and restart_process.poll() is None:
            restart_process.terminate()
        try:
            client.stop(ports=[CLIENT_PORT])
        except Exception:
            pass
        try:
            client.disconnect()
        except Exception:
            pass


def build_profile_points(samples: list[ProfileSample]) -> list[dict[str, float]]:
    """Строит временные точки с оценкой tx/rx pps по дельте соседних сэмплов."""
    points: list[dict[str, float]] = []
    previous: ProfileSample | None = None
    for sample in samples:
        if previous is None:
            previous = sample
            continue
        dt = sample.elapsed_sec - previous.elapsed_sec
        if dt <= 0:
            previous = sample
            continue
        tx_pps = max(0.0, (sample.tx_pkts - previous.tx_pkts) / dt)
        rx_pps = max(0.0, (sample.rx_pkts - previous.rx_pkts) / dt)
        points.append(
            {
                "interval_start_sec": previous.elapsed_sec,
                "interval_end_sec": sample.elapsed_sec,
                "dt_sec": dt,
                "tx_pkts": float(sample.tx_pkts),
                "rx_pkts": float(sample.rx_pkts),
                "tx_pps": tx_pps,
                "rx_pps": rx_pps,
            }
        )
        previous = sample
    return points


def try_read_udp_counters(
    client: Any,
    pg_ids: tuple[int, ...],
    fallback: tuple[int, int],
) -> tuple[int, int, bool]:
    """Читает UDP счетчики; при временном сбое возвращает fallback-значения."""
    try:
        tx_pkts, rx_pkts = read_udp_counters(client, pg_ids=pg_ids)
        return tx_pkts, rx_pkts, True
    except Exception:
        return fallback[0], fallback[1], False


def write_profile_tsv(points: list[dict[str, float]], config: FailoverConfig) -> Path:
    """Сохраняет временной ряд профиля в TSV-файл и возвращает путь."""
    output_dir = build_failover_profile_results_dir(config)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / FAILOVER_PROFILE_RESULT_FILE_NAME
    lines = ["interval_start_sec\tinterval_end_sec\tdt_sec\ttx_pkts\trx_pkts\ttx_pps\trx_pps"]
    for point in points:
        lines.append(
            f"{point['interval_start_sec']:.6f}\t{point['interval_end_sec']:.6f}\t"
            f"{point['dt_sec']:.6f}\t{int(point['tx_pkts'])}\t{int(point['rx_pkts'])}\t"
            f"{point['tx_pps']:.6f}\t{point['rx_pps']:.6f}"
        )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def write_profile_plot(points: list[dict[str, float]], config: FailoverConfig) -> Path:
    """Сохраняет график зависимости rx pps от времени."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output_dir = build_failover_profile_results_dir(config)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / FAILOVER_PROFILE_PLOT_FILE_NAME

    times = [point["interval_end_sec"] for point in points]
    rx_pps = [point["rx_pps"] for point in points]

    fig, ax = plt.subplots(figsize=(12, 6), dpi=140)
    ax.plot(times, rx_pps, color="#2563eb", linewidth=1.4, label="rx_pps")
    ax.axhline(config.target_pps, color="red", alpha=0.35, linewidth=1.6, label="target_pps")
    ax.set_title("Failover profile")
    ax.set_xlabel("time, sec")
    ax.set_ylabel("pps")
    ax.grid(True, alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)
    return output_path

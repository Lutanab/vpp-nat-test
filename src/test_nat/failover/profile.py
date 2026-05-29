from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import rich_click as click

from manage_nat.helpers import with_privileges
from manage_nat.network.setup import VPP_SERVICE_NAME

from ..results import write_json
from ..trex.run.udp import (
    CLIENT_PORT,
    SERVER_PORT,
    TREX_SERVER_HOST,
    build_udp_burst_streams,
    configure_l3_mode,
    load_trex_stl_api,
)
from .base import (
    FAILOVER_PROFILE_SUMMARY_FILE_NAME,
    FailoverConfig,
    FailoverTimeConfig,
    build_failover_profile_results_dir,
)

RECOVERY_BURST_DURATION_SEC = 0.05
RECOVERY_BURST_MAX_FLOWS = 128
VPPCTL_POLL_TIMEOUT_SEC = 1.0


def run_failover_udp_profile(config: FailoverConfig, time_config: FailoverTimeConfig) -> None:
    """Меряет время восстановления dataplane после `systemctl restart vpp`."""
    api = load_trex_stl_api()
    client = api.STLClient(server=TREX_SERVER_HOST)
    ports = [CLIENT_PORT, SERVER_PORT]
    poll_period_sec = time_config.poll_interval_ms / 1000.0
    restart_process: subprocess.Popen[bytes] | None = None

    try:
        client.connect()
        client.reset(ports=ports)
        configure_l3_mode(client)

        click.echo("profile: warmup burst")
        warmup_streams, _ = build_udp_burst_streams(
            api=api,
            flow_count=config.flow_count,
            packet_size=config.packet_size,
            trex_data_cores=1,
            duration_sec=max(RECOVERY_BURST_DURATION_SEC, time_config.warmup_sec),
        )
        send_udp_burst(client, streams=warmup_streams)
        pre_restart_server_rx_pkts = read_server_rx_counter(client)
        restart_start_mono = time.monotonic()

        restart_process = subprocess.Popen(
            with_privileges(["systemctl", "restart", VPP_SERVICE_NAME]),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        click.echo("profile: restart started, probing recovery")

        recovery_flow_count = min(config.flow_count, RECOVERY_BURST_MAX_FLOWS)
        recovery_streams, _ = build_udp_burst_streams(
            api=api,
            flow_count=recovery_flow_count,
            packet_size=config.packet_size,
            trex_data_cores=1,
            duration_sec=RECOVERY_BURST_DURATION_SEC,
        )

        t1_vppctl_ready_sec: float | None = None
        t2_counter_growth_sec: float | None = None
        counter_before = pre_restart_server_rx_pkts
        next_heartbeat_sec = 1.0

        while True:
            now = time.monotonic()
            elapsed_sec = now - restart_start_mono
            if elapsed_sec > time_config.waiting_sec:
                break

            vppctl_ready = vppctl_is_ready()
            if vppctl_ready:
                if t1_vppctl_ready_sec is None:
                    t1_vppctl_ready_sec = elapsed_sec

            memif_connected = vpp_memif_connected()

            if vppctl_ready and memif_connected:
                recovery_ok = run_trex_recovery(client, recovery_streams)
                if recovery_ok:
                    counter_after = read_server_rx_counter(client)
                    if counter_after > counter_before:
                        if t2_counter_growth_sec is None:
                            t2_counter_growth_sec = elapsed_sec
                    counter_before = counter_after

            if elapsed_sec >= next_heartbeat_sec:
                click.echo(
                    "profile: "
                    f"t={elapsed_sec:.1f}s, "
                    f"vppctl={'up' if vppctl_ready else 'down'}, "
                    f"memif={'up' if memif_connected else 'down'}"
                )
                next_heartbeat_sec += 1.0

            if t2_counter_growth_sec is not None:
                break

            next_tick = now + poll_period_sec
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)

        if restart_process.wait(timeout=60) != 0:
            raise RuntimeError("VPP restart process failed during profile run")

        summary_path = write_profile_summary(
            t1_vppctl_ready_sec=t1_vppctl_ready_sec,
            t2_counter_growth_sec=t2_counter_growth_sec,
            config=config,
        )
        click.echo(f"summary: {summary_path}")
    finally:
        if restart_process is not None and restart_process.poll() is None:
            restart_process.terminate()
        try:
            client.stop(ports=ports)
        except Exception:
            pass
        try:
            client.disconnect()
        except Exception:
            pass


def send_udp_burst(client: Any, streams: list[Any]) -> None:
    """Отправляет burst с client-порта и дожидается завершения."""
    client.remove_all_streams(ports=[CLIENT_PORT])
    client.add_streams(streams, ports=[CLIENT_PORT])
    client.start(ports=[CLIENT_PORT], force=True)
    client.wait_on_traffic(ports=[CLIENT_PORT])


def run_trex_recovery(client: Any, streams: list[Any]) -> bool:
    """Переинициализирует TRex dataplane и отправляет recovery burst."""
    try:
        configure_l3_mode(client)
        send_udp_burst(client, streams=streams)
        return True
    except Exception:
        return False


def read_server_rx_counter(client: Any) -> int:
    """Возвращает RX-счетчик server-side TRex интерфейса."""
    stats = client.get_stats(ports=[SERVER_PORT])
    server_stats = stats.get(SERVER_PORT) or stats.get(str(SERVER_PORT))
    if server_stats is None:
        raise RuntimeError("TRex did not return server port stats")
    if "rx_pkts" in server_stats:
        return int(server_stats["rx_pkts"])
    if "ipackets" in server_stats:
        return int(server_stats["ipackets"])
    if "opackets" in server_stats:
        return int(server_stats["opackets"])
    return 0


def vppctl_is_ready() -> bool:
    """Проверяет доступность `vppctl`."""
    try:
        result = subprocess.run(
            with_privileges(["vppctl", "show", "version"]),
            text=True,
            capture_output=True,
            check=False,
            timeout=VPPCTL_POLL_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


def vpp_memif_connected() -> bool:
    """Проверяет, что memif10/0 и memif20/0 находятся в состоянии up."""
    try:
        result = subprocess.run(
            with_privileges(["vppctl", "show", "interface"]),
            text=True,
            capture_output=True,
            check=False,
            timeout=VPPCTL_POLL_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return False
    if result.returncode != 0:
        return False

    lines = result.stdout.lower().splitlines()
    memif10_up = any("memif10/0" in line and "up" in line for line in lines)
    memif20_up = any("memif20/0" in line and "up" in line for line in lines)
    return memif10_up and memif20_up


def write_profile_summary(
    t1_vppctl_ready_sec: float | None,
    t2_counter_growth_sec: float | None,
    config: FailoverConfig,
) -> Path:
    """Пишет краткий `summary.json` для failover profile."""
    output_dir = build_failover_profile_results_dir(config)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / FAILOVER_PROFILE_SUMMARY_FILE_NAME
    control_plane_recovery_time = t1_vppctl_ready_sec
    data_plane_recovery_time = (
        None
        if t1_vppctl_ready_sec is None or t2_counter_growth_sec is None
        else t2_counter_growth_sec - t1_vppctl_ready_sec
    )
    summary = {
        "control_plane_recovery_time": control_plane_recovery_time,
        "data_plane_recovery_time": data_plane_recovery_time,
    }
    write_json(output_path, summary)
    return output_path

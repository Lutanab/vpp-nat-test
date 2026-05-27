from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import rich_click as click

from manage_nat.helpers import run_command, with_privileges
from manage_nat.network.setup import (
    FAILOVER_STARTUP_CONFIG_RE,
    NAT44_MAX_SESSIONS,
    NAT_FO_PORT_RANGE_END,
    NAT_FO_PORT_RANGE_START,
    NAT_FO_PUBLIC_ADDR,
    VPP_SERVICE_NAME,
    configure_vpp_nat_plugins_for_mode,
    configure_vpp_workers,
    find_section_end,
    read_startup_conf,
    remove_stale_memif_sockets,
    restart_vpp_service,
    write_startup_conf,
)

from ..config import PROJECT_ROOT, ensure_valid_nat_mode, load_simple_yaml
from ..trex.run.tcp import build_tcp_burst_streams, build_tcp_reverse_burst_streams, read_tcp_counters
from ..trex.run.udp import CLIENT_PORT, SERVER_PORT, TREX_SERVER_HOST, configure_l3_mode, load_trex_stl_api
from ..trex.setup import launch_trex_failover_server, stop_existing_trex_server, wait_for_launched_trex_server

DEFAULT_FAILOVER_CONFIG_PATH = PROJECT_ROOT / "configs" / "failover" / "config.yaml"
FAILOVER_TOPOLOGY_TEMPLATE_PATH = PROJECT_ROOT / "configs" / "failover" / "nat_fo_topology.vpp.template"
FAILOVER_TREX_CONFIG_PATH = PROJECT_ROOT / "configs" / "trex" / "failover" / "trex_cfg.yaml"
VPP_FAILOVER_TOPOLOGY_PATH = Path("/etc/vpp/nat_fo_topology.vpp")
SUPPORTED_FAILOVER_WORKERS = 1
UNIX_SECTION_RE = re.compile(r"^\s*unix\s*\{\s*$")
MEMIF_SOCKET_READY_TIMEOUT_SEC = 10
MEMIF_SOCKET_READY_POLL_SEC = 0.2
NAT44_TCP_O2I_RE = re.compile(r"^\s*o2i\s+(?P<ip>\S+)\s+proto\s+TCP\s+port\s+(?P<port>\d+)\s+fib\s+\d+\s*$")
FAILOVER_MEMIF_SOCKET_PATHS = (
    Path("/run/vpp/memif-inside-a.sock"),
    Path("/run/vpp/memif-outside.sock"),
)


@dataclass(frozen=True, slots=True)
class FailoverConfig:
    test_name: str
    nat_mode: str
    n_workers: int
    flow_count: int
    packet_size: int
    warmup_sec: float
    waiting_sec: float


def run_failover_simple_tcp() -> None:
    """Готовит VPP/TRex failover topology для будущего simple TCP сценария."""
    config = load_failover_config(DEFAULT_FAILOVER_CONFIG_PATH)

    configure_vpp_nat_plugins_for_mode(config.nat_mode)
    configure_vpp_workers(config.n_workers)
    write_failover_topology(config.nat_mode)
    ensure_failover_exec_in_startup_conf()

    stop_existing_trex_server(config_paths=(FAILOVER_TREX_CONFIG_PATH,))
    stop_vpp_service()
    remove_stale_memif_sockets()
    trex_process = launch_trex_failover_server(FAILOVER_TREX_CONFIG_PATH)
    wait_for_failover_memif_sockets()
    restart_vpp_service()
    wait_for_launched_trex_server(trex_process)

    run_failover_tcp_streams(config)


def run_failover_profile() -> None:
    """Заглушка для будущего failover profile сценария."""
    load_failover_config(DEFAULT_FAILOVER_CONFIG_PATH)
    click.echo("Failover profile runner is not implemented yet.")


def load_failover_config(path: Path) -> FailoverConfig:
    if not path.exists():
        raise FileNotFoundError(f"Failover config file not found: {path}")

    raw_config = load_simple_yaml(path)
    config = FailoverConfig(
        test_name=str(raw_config.get("test_name") or "simple_tcp"),
        nat_mode=ensure_valid_nat_mode(str(require(raw_config, "nat_mode"))),
        n_workers=int(raw_config.get("n_workers", SUPPORTED_FAILOVER_WORKERS)),
        flow_count=int(raw_config.get("flow_count", 1)),
        packet_size=int(raw_config.get("packet_size", 64)),
        warmup_sec=float(raw_config.get("warmup_sec", 10)),
        waiting_sec=float(raw_config.get("waiting_sec", 10)),
    )
    validate_failover_config(config)
    return config


def validate_failover_config(config: FailoverConfig) -> None:
    if config.n_workers != SUPPORTED_FAILOVER_WORKERS:
        raise ValueError(
            "failover tests currently support exactly "
            f"n_workers={SUPPORTED_FAILOVER_WORKERS}; got {config.n_workers}"
        )
    if config.flow_count <= 0:
        raise ValueError("flow_count must be positive")
    if config.packet_size <= 0:
        raise ValueError("packet_size must be positive")
    if config.warmup_sec < 0:
        raise ValueError("warmup_sec must be non-negative")
    if config.waiting_sec < 0:
        raise ValueError("waiting_sec must be non-negative")


def require(data: dict[str, object], key: str) -> object:
    if key not in data or data[key] is None:
        raise ValueError(f"missing required failover config key: {key}")
    return data[key]


def write_failover_topology(nat_mode: str) -> None:
    if not FAILOVER_TOPOLOGY_TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"Failover topology template not found: {FAILOVER_TOPOLOGY_TEMPLATE_PATH}")

    content = FAILOVER_TOPOLOGY_TEMPLATE_PATH.read_text(encoding="utf-8").rstrip()
    nat_lines = nat_runtime_lines(nat_mode)
    if nat_lines:
        content = "\n".join([content, "", *nat_lines])
    content += "\n"

    subprocess.run(
        with_privileges(["tee", str(VPP_FAILOVER_TOPOLOGY_PATH)]),
        text=True,
        input=content,
        capture_output=True,
        check=True,
    )
    click.echo(f"  ✓ failover topology записана: {VPP_FAILOVER_TOPOLOGY_PATH}")


def nat_runtime_lines(nat_mode: str) -> list[str]:
    if nat_mode == "none":
        return []
    if nat_mode == "nat44":
        return [
            f"nat44 plugin enable sessions {NAT44_MAX_SESSIONS}",
            "set interface nat44 in loop0 out memif20/0",
            "nat44 add interface address memif20/0",
        ]
    if nat_mode == "nat_fo":
        return [
            f"nat_fo set public-addr {NAT_FO_PUBLIC_ADDR}",
            f"nat_fo set port-range {NAT_FO_PORT_RANGE_START} {NAT_FO_PORT_RANGE_END}",
            "nat_fo interface inside loop0",
            "nat_fo interface outside memif20/0",
        ]
    raise ValueError(f"Unsupported NAT mode: {nat_mode}")


def ensure_failover_exec_in_startup_conf() -> None:
    lines = read_startup_conf().splitlines()
    lines = [line for line in lines if not FAILOVER_STARTUP_CONFIG_RE.match(line)]
    exec_line = f"  exec {VPP_FAILOVER_TOPOLOGY_PATH}"

    unix_start = find_unix_section_start(lines)
    if unix_start is None:
        lines.extend(["", "unix {", exec_line, "}", ""])
    else:
        unix_end = find_section_end(lines, unix_start)
        lines.insert(unix_end, exec_line)

    write_startup_conf("\n".join(lines))
    click.echo(f"  ✓ startup.conf обновлен: exec {VPP_FAILOVER_TOPOLOGY_PATH}")


def find_unix_section_start(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        if UNIX_SECTION_RE.match(line):
            return index
    return None


def stop_vpp_service() -> None:
    run_command(with_privileges(["systemctl", "stop", VPP_SERVICE_NAME]))


def wait_for_failover_memif_sockets() -> None:
    """Ждет, пока TRex memif server создаст socket-файлы и начнет слушать VPP slave."""
    deadline = time.monotonic() + MEMIF_SOCKET_READY_TIMEOUT_SEC
    while time.monotonic() < deadline:
        if all(path.exists() for path in FAILOVER_MEMIF_SOCKET_PATHS) and failover_memif_sockets_listen():
            click.echo("  ✓ TRex memif server sockets готовы")
            return
        time.sleep(MEMIF_SOCKET_READY_POLL_SEC)

    missing = ", ".join(str(path) for path in FAILOVER_MEMIF_SOCKET_PATHS if not path.exists())
    if not missing:
        missing = "listeners are not ready"
    raise RuntimeError(f"Timed out waiting for TRex memif sockets: {missing}")


def failover_memif_sockets_listen() -> bool:
    result = subprocess.run(["ss", "-xlpn"], text=True, capture_output=True, check=False)
    if result.returncode != 0:
        return False
    lines = result.stdout.splitlines()
    return all(any(str(path) in line and "LISTEN" in line for line in lines) for path in FAILOVER_MEMIF_SOCKET_PATHS)


def run_failover_tcp_streams(config: FailoverConfig) -> None:
    """Проверяет восстановление NAT-сессий TCP-подобными STL burst flow."""
    api = load_trex_stl_api()
    client = api.STLClient(server=TREX_SERVER_HOST)
    ports = [CLIENT_PORT, SERVER_PORT]

    try:
        client.connect()
        client.reset(ports=ports)
        configure_l3_mode(client)
        streams, pg_ids = build_tcp_burst_streams(
            api=api,
            flow_count=config.flow_count,
            packet_size=config.packet_size,
            trex_data_cores=SUPPORTED_FAILOVER_WORKERS,
            duration_sec=config.warmup_sec,
        )
        client.remove_all_streams(ports=[CLIENT_PORT])
        client.add_streams(streams, ports=[CLIENT_PORT])
        click.echo(f"  ✓ TCP burst streams готовы: flows={config.flow_count}, pg_ids={pg_ids}")

        client.clear_stats(ports=ports)
        client.start(ports=[CLIENT_PORT], force=True)
        client.wait_on_traffic(ports=[CLIENT_PORT])
        pre_tx, pre_rx = read_tcp_counters(client, pg_ids=pg_ids)
        click.echo(f"  ✓ pre-restart burst: tx={pre_tx}, rx={pre_rx}")
        public_ip, public_port_start = resolve_tcp_public_mapping_range(config)
        reverse_streams, reverse_pg_ids = build_tcp_reverse_burst_streams(
            api=api,
            public_ip=public_ip,
            public_dport_start=public_port_start,
            flow_count=config.flow_count,
            packet_size=config.packet_size,
            trex_data_cores=SUPPORTED_FAILOVER_WORKERS,
            duration_sec=config.warmup_sec,
        )
        click.echo(f"  ✓ public TCP mappings готовы: {public_ip}:{public_port_start}-{public_port_start + config.flow_count - 1}")

        click.echo("  ✓ restarting VPP after TCP NAT session creation")
        restart_vpp_service()
        click.echo(f"  ✓ waiting {config.waiting_sec:g}s after restart")
        time.sleep(config.waiting_sec)

        configure_l3_mode(client)
        client.remove_all_streams(ports=[SERVER_PORT])
        client.add_streams(reverse_streams, ports=[SERVER_PORT])
        client.clear_stats(ports=ports)
        client.start(ports=[SERVER_PORT], force=True)
        client.wait_on_traffic(ports=[SERVER_PORT])
        post_tx, post_rx = read_tcp_counters(client, pg_ids=reverse_pg_ids)
        survived_flows = min(post_rx, config.flow_count)
        survival_percent = (survived_flows / config.flow_count) * 100

        click.echo(f"  ✓ post-restart o2i burst: tx={post_tx}, rx={post_rx}")
        click.echo("TCP NAT session recovery:")
        click.echo(f"created_flows={config.flow_count}")
        click.echo(f"survived_flows={survived_flows}")
        click.echo(f"survival_percent={survival_percent:.6f}")
    finally:
        try:
            client.stop(ports=ports)
        except Exception:
            pass
        try:
            client.disconnect()
        except Exception:
            pass


def resolve_tcp_public_mapping_range(config: FailoverConfig) -> tuple[str, int]:
    if config.nat_mode != "nat44":
        raise NotImplementedError(
            "simple-tcp failover recovery currently verifies reverse mappings only for nat44; "
            f"got nat_mode={config.nat_mode}"
        )

    public_ports = read_nat44_tcp_public_ports()
    if len(public_ports) < config.flow_count:
        raise RuntimeError(
            f"NAT44 created only {len(public_ports)} TCP public mappings, expected at least {config.flow_count}"
        )

    expected_ports = public_ports[: config.flow_count]
    public_ip = expected_ports[0][0]
    ports = [port for _, port in expected_ports]
    if any(ip != public_ip for ip, _ in expected_ports):
        raise RuntimeError("NAT44 TCP public mappings use more than one public IP")
    if ports != list(range(ports[0], ports[0] + len(ports))):
        raise RuntimeError("NAT44 TCP public mappings are not contiguous; reverse burst cannot model them yet")

    return public_ip, ports[0]


def read_nat44_tcp_public_ports() -> list[tuple[str, int]]:
    result = subprocess.run(
        with_privileges(["vppctl", "show nat44 sessions"]),
        text=True,
        capture_output=True,
        check=True,
    )
    ports: list[tuple[str, int]] = []
    for line in result.stdout.splitlines():
        match = NAT44_TCP_O2I_RE.match(line)
        if match is not None:
            ports.append((match.group("ip"), int(match.group("port"))))
    return sorted(ports, key=lambda item: item[1])

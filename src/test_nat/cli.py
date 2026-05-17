from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import rich_click as click

from .config import HostTestConfig, load_test_configs
from .runner import BoundarySearchError, run_load_search
from .trex.run.simple import CapturedTcpPacket, SimpleTcpTestResult, run_simple_tcp_test
from .trex.run.udp import run_udp_test
from .trex.setup import setup_trex_server


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def app() -> None:
    """Host-side NAT benchmark orchestration."""


@app.group("setup")
def setup_group() -> None:
    """Deploy benchmark runtime components."""


@setup_group.command("trex")
def setup_trex_command() -> None:
    """Deploy TRex server for the current VPP memif topology."""
    setup_trex_server()


@app.group("trex")
def trex_group() -> None:
    """Run TRex-based benchmark workflows."""


@trex_group.group("run")
def trex_run_group() -> None:
    """Run TRex traffic tests."""


@trex_run_group.command("udp")
@click.option(
    "--target-pps",
    type=int,
    required=True,
    help="Target UDP packets per second. Example: 100000 or 1000000.",
)
@click.option(
    "--duration",
    type=float,
    required=True,
    help="Test duration in seconds. Example: 10 or 30.5.",
)
def trex_run_udp_command(target_pps: int, duration: float) -> None:
    """Run UDP traffic through the current TRex/VPP topology."""
    loss_percent = run_udp_test(target_pps=target_pps, duration=duration)
    click.echo(f"Loss Percent:")
    click.echo(f"{loss_percent:.6f}")


@app.group("run")
def run_group() -> None:
    """Run functional checks from the host."""


@run_group.command("simple")
def run_simple_command() -> None:
    """Run a minimal TCP NAT correctness check."""
    print_simple_tcp_result(run_simple_tcp_test())


@app.group("load")
def load_group() -> None:
    """Run load benchmark workflows from the host."""


@load_group.command("run")
@click.option(
    "--load-config",
    "--config",
    type=click.Path(path_type=Path, dir_okay=False),
    help="Path to load YAML config. Defaults to configs/load/test_config.yaml.",
)
@click.option(
    "--search-config",
    type=click.Path(path_type=Path, dir_okay=False),
    help="Path to search YAML config. Defaults to configs/search/test_config.yaml.",
)
def load_run_command(
    load_config: Path | None,
    search_config: Path | None,
) -> None:
    """Load configs and run the host-side boundary search."""
    resolved_config, search, load_config_path, search_config_path = load_test_configs(load_config, search_config)
    reset_known_hosts_for_test_vms(resolved_config)
    try:
        run_load_search(
            resolved_config,
            search,
            load_config_path,
            search_config_path,
        )
    except BoundarySearchError as exc:
        raise click.ClickException(str(exc)) from exc


def reset_known_hosts_for_test_vms(config: HostTestConfig) -> None:
    """Drop stale SSH host keys after VM recreation."""
    targets = (
        ("external_vm", config.external_vm_ssh_target, config.external_vm_ssh_port),
        ("user_vm_1", config.user_vm_ssh_target, config.user_vm_ssh_port),
    )
    for vm_name, ssh_target, ssh_port in targets:
        host = extract_ssh_host(ssh_target)
        for known_hosts_pattern in known_hosts_patterns(host, ssh_port):
            subprocess.run(
                ["ssh-keygen", "-R", known_hosts_pattern],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        click.echo(f"known_hosts очищен для {vm_name}: {host}:{ssh_port}")


def extract_ssh_host(ssh_target: str) -> str:
    host = ssh_target.rsplit("@", 1)[-1]
    if host.startswith("[") and "]" in host:
        return host[1 : host.index("]")]
    if host.count(":") == 1:
        return host.split(":", 1)[0]
    return host


def known_hosts_patterns(host: str, port: int) -> tuple[str, ...]:
    if port == 22:
        return (host,)
    return (host, f"[{host}]:{port}")


def print_simple_tcp_result(result: SimpleTcpTestResult) -> None:
    """Печатает результат минимальной TCP-проверки NAT."""
    click.echo(f"NAT: {format_nat_status(result.nat_applied)}")
    click.echo(result.verdict)

    if not result.captured_packets:
        click.echo("Packets: <empty>")
        return
    for packet in result.captured_packets:
        print_captured_tcp_packet(packet)


def format_nat_status(nat_applied: bool | None) -> str:
    """Форматирует короткий статус NAT для CLI."""
    if nat_applied is True:
        return "works"
    if nat_applied is False:
        return "does not work"
    return "unknown"


def print_captured_tcp_packet(packet: CapturedTcpPacket) -> None:
    """Печатает только адреса и порты TCP-пакета."""
    src = format_endpoint(packet.ip_src, packet.tcp_src_port)
    dst = format_endpoint(packet.ip_dst, packet.tcp_dst_port)
    click.echo(f"Packet: {src} -> {dst}")


def format_endpoint(ip: str | None, port: int | None) -> str:
    """Форматирует `ip:port`, сохраняя пустые значения читаемыми."""
    rendered_ip = ip or "?"
    rendered_port = str(port) if port is not None else "?"
    return f"{rendered_ip}:{rendered_port}"


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

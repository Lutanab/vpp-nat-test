from __future__ import annotations

import re
import subprocess
import time
from contextlib import redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

import rich_click as click

from manage_nat.helpers import run_command, with_privileges
from manage_nat.network.setup import (
    FAILOVER_STARTUP_CONFIG_RE,
    NAT44_MAX_SESSIONS,
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

from ..config import PROJECT_ROOT, ensure_valid_nat_mode, load_simple_yaml, slugify_value
from ..trex.setup import launch_trex_failover_server, stop_existing_trex_server, wait_for_launched_trex_server

DEFAULT_FAILOVER_CONFIG_PATH = PROJECT_ROOT / "configs" / "failover" / "config.yaml"
DEFAULT_FAILOVER_TIME_CONFIG_PATH = PROJECT_ROOT / "configs" / "failover" / "config.time.yaml"
FAILOVER_TOPOLOGY_TEMPLATE_PATH = PROJECT_ROOT / "configs" / "failover" / "nat_fo_topology.vpp.template"
FAILOVER_TREX_CONFIG_PATH = PROJECT_ROOT / "configs" / "trex" / "failover" / "trex_cfg.yaml"
FAILOVER_PROFILE_RESULTS_ROOT = PROJECT_ROOT / "results" / "failover_profile"
FAILOVER_PROFILE_SUMMARY_FILE_NAME = "summary.json"
VPP_FAILOVER_TOPOLOGY_PATH = Path("/etc/vpp/nat_fo_topology.vpp")
SUPPORTED_FAILOVER_WORKERS = 1
MEMIF_SOCKET_READY_TIMEOUT_SEC = 10
MEMIF_SOCKET_READY_POLL_SEC = 0.2
UDP_CAPTURE_DRAIN_SEC = 0.5
UDP_CAPTURE_FILTER = "udp or (vlan and udp)"
FAILOVER_MEMIF_SOCKET_PATHS = (
    Path("/run/vpp/memif-inside-a.sock"),
    Path("/run/vpp/memif-outside.sock"),
)

UNIX_SECTION_RE = re.compile(r"^\s*unix\s*\{\s*$")


@dataclass(frozen=True, slots=True)
class FailoverConfig:
    test_name: str
    nat_mode: str
    n_workers: int
    flow_count: int
    packet_size: int
    target_pps: int


@dataclass(frozen=True, slots=True)
class FailoverTimeConfig:
    warmup_sec: float
    waiting_sec: float
    poll_interval_ms: int


@dataclass(frozen=True, slots=True, order=True)
class ObservedUdpMapping:
    ip: str
    port: int


@dataclass(frozen=True, slots=True)
class UdpMappingRange:
    ip: str
    port_start: int
    count: int


def load_failover_config(path: Path) -> FailoverConfig:
    """Загружает failover-конфиг и применяет значения по умолчанию."""
    if not path.exists():
        raise FileNotFoundError(f"Failover config file not found: {path}")

    raw_config = load_simple_yaml(path)
    config = FailoverConfig(
        test_name=str(raw_config.get("test_name") or "simple"),
        nat_mode=ensure_valid_nat_mode(str(require(raw_config, "nat_mode"))),
        n_workers=int(raw_config.get("n_workers", SUPPORTED_FAILOVER_WORKERS)),
        flow_count=int(raw_config.get("flow_count", 1)),
        packet_size=int(raw_config.get("packet_size", 64)),
        target_pps=int(raw_config.get("target_pps", 100_000)),
    )
    validate_failover_config(config)
    return config


def load_failover_time_config(path: Path) -> FailoverTimeConfig:
    """Загружает failover time-конфиг и применяет значения по умолчанию."""
    if not path.exists():
        raise FileNotFoundError(f"Failover time config file not found: {path}")

    raw_config = load_simple_yaml(path)
    config = FailoverTimeConfig(
        warmup_sec=float(raw_config.get("warmup_sec", 10)),
        waiting_sec=float(raw_config.get("waiting_sec", 10)),
        poll_interval_ms=int(raw_config.get("poll_interval_ms", 25)),
    )
    validate_failover_time_config(config)
    return config


def validate_failover_config(config: FailoverConfig) -> None:
    """Проверяет корректность failover-параметров."""
    if config.n_workers != SUPPORTED_FAILOVER_WORKERS:
        raise ValueError(
            "failover tests currently support exactly "
            f"n_workers={SUPPORTED_FAILOVER_WORKERS}; got {config.n_workers}"
        )
    if config.flow_count <= 0:
        raise ValueError("flow_count must be positive")
    if config.packet_size <= 0:
        raise ValueError("packet_size must be positive")
    if config.target_pps <= 0:
        raise ValueError("target_pps must be positive")


def validate_failover_time_config(config: FailoverTimeConfig) -> None:
    """Проверяет корректность failover time-параметров."""
    if config.warmup_sec < 0:
        raise ValueError("warmup_sec must be non-negative")
    if config.waiting_sec < 0:
        raise ValueError("waiting_sec must be non-negative")
    if config.poll_interval_ms <= 0:
        raise ValueError("poll_interval_ms must be positive")


def build_failover_profile_results_dir(config: FailoverConfig) -> Path:
    """Строит путь results-директории failover profile по параметрам config.yaml."""
    segments = (
        ("test_name", config.test_name),
        ("flow_count", config.flow_count),
        ("n_workers", config.n_workers),
        ("packet_size", config.packet_size),
        ("nat_mode", config.nat_mode),
        ("target_pps", config.target_pps),
    )
    path = FAILOVER_PROFILE_RESULTS_ROOT
    for key, value in segments:
        path /= f"{key}_{slugify_value(value)}"
    return path


def require(data: dict[str, object], key: str) -> object:
    """Возвращает обязательный ключ из yaml-данных или бросает ошибку."""
    if key not in data or data[key] is None:
        raise ValueError(f"missing required failover config key: {key}")
    return data[key]


def prepare_failover_runtime(config: FailoverConfig) -> None:
    """Подготавливает VPP+TRex failover стенд в тихом режиме."""
    click.echo("setup: preparing failover runtime")
    click.echo("setup: applying VPP mode, workers and topology")
    with quiet_stdout():
        configure_vpp_nat_plugins_for_mode(config.nat_mode)
        configure_vpp_workers(config.n_workers)
        write_failover_topology(config.nat_mode)
        ensure_failover_exec_in_startup_conf()
    click.echo("setup: (re)starting TRex failover server")
    with quiet_stdout():
        stop_existing_trex_server(config_paths=(FAILOVER_TREX_CONFIG_PATH,))
        stop_vpp_service()
        remove_stale_memif_sockets()
        trex_process = launch_trex_failover_server(FAILOVER_TREX_CONFIG_PATH)
    click.echo("setup: waiting for TRex memif sockets")
    with quiet_stdout():
        wait_for_failover_memif_sockets()
    click.echo("setup: restarting VPP and verifying TRex control")
    with quiet_stdout():
        restart_vpp_service()
        wait_for_launched_trex_server(trex_process)
    click.echo("setup: failover runtime ready")


def quiet_stdout():
    """Возвращает context manager для подавления stdout."""
    return redirect_stdout(StringIO())


def write_failover_topology(nat_mode: str) -> None:
    """Записывает failover topology в `/etc/vpp/nat_fo_topology.vpp`."""
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
    """Возвращает NAT-блок команд для topology-файла."""
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
            "nat_fo interface inside loop0",
            "nat_fo interface outside memif20/0",
        ]
    raise ValueError(f"Unsupported NAT mode: {nat_mode}")


def ensure_failover_exec_in_startup_conf() -> None:
    """Добавляет `exec /etc/vpp/nat_fo_topology.vpp` в `unix {}` startup.conf."""
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
    """Ищет начало секции `unix {` в startup.conf."""
    for index, line in enumerate(lines):
        if UNIX_SECTION_RE.match(line):
            return index
    return None


def stop_vpp_service() -> None:
    """Останавливает VPP systemd-сервис."""
    run_command(with_privileges(["systemctl", "stop", VPP_SERVICE_NAME]))


def wait_for_failover_memif_sockets() -> None:
    """Ждет, пока TRex memif server создаст socket-файлы и начнет их слушать."""
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
    """Проверяет, что оба failover memif socket находятся в состоянии LISTEN."""
    result = subprocess.run(["ss", "-xlpn"], text=True, capture_output=True, check=False)
    if result.returncode != 0:
        return False
    lines = result.stdout.splitlines()
    return all(any(str(path) in line and "LISTEN" in line for line in lines) for path in FAILOVER_MEMIF_SOCKET_PATHS)

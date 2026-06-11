from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import rich_click as click

from manage_nat.config import TREX_INSTALL_BASE_DIR, TREX_SERVER_BINARY_NAME, TREX_SERVER_LINK_PATH
from manage_nat.helpers import PROJECT_ROOT
from manage_nat.nat_mode import parse_configured_workers
from manage_nat.network.setup import (
    configure_memif_rx_placement,
    inside_host_ip_for_pair,
    inside_ip_cidr_for_pair,
    memif_pair_count_for_workers,
    memif_topology_pair_count,
    memif_topology_pairs,
    outside_host_ip_for_pair,
    outside_ip_cidr_for_pair,
)

TREX_CFG_PATH = PROJECT_ROOT / "configs" / "trex" / "trex_cfg.yaml"
TREX_PID_PATH = PROJECT_ROOT / "configs" / "trex" / "trex.pid"
TREX_LOG_PATH = PROJECT_ROOT / "configs" / "trex" / "trex.log"
TREX_RPC_HOST = "127.0.0.1"
TREX_RPC_PORT = 4501
TREX_READY_TIMEOUT_SECONDS = 30
TREX_READY_POLL_INTERVAL_SECONDS = 1
VPP_MEMIF_PLACEMENT_TIMEOUT_SECONDS = 10
VPP_MEMIF_PLACEMENT_POLL_INTERVAL_SECONDS = 0.5
TREX_INSIDE_A_IP = "10.8.1.2"
TREX_OUTSIDE_IP = "10.8.0.2"
TREX_PORT_BANDWIDTH_GB = 200
TREX_MEMORY_BASE_MB = 1024
TREX_MEMORY_PER_PAIR_MB = 1024
TREX_MEMORY_MIN_MB = 2048
TREX_MEMORY_MAX_WORKERS_TARGET = 10
TREX_MBUF_FACTOR = "0.2"

TREX_DATA_CORES_PER_PAIR = 1


@dataclass(frozen=True)
class TrexPort:
    """Описывает соответствие TRex-порта и VPP memif-сокета."""

    name: str
    port_id: int
    vdev_name: str
    socket_path: str
    ip: str
    default_gw: str


@dataclass(frozen=True)
class TrexPortPair:
    """Описывает inside/outside пару TRex-портов для одной memif-пары."""

    pair_index: int
    inside: TrexPort
    outside: TrexPort


def cidr_ip(value: str) -> str:
    """Возвращает IP-часть из строки вида `10.8.1.1/24`."""
    return value.split("/", 1)[0]


def resolve_worker_count(n_workers: int | None = None) -> int:
    """Возвращает нормализованное число workers для построения load-топологии."""
    return max(0, parse_configured_workers() or 0) if n_workers is None else max(0, n_workers)


def active_memif_pair_count_for_workers(n_workers: int | None = None) -> int:
    """Возвращает число memif-пар, которые участвуют в трафике для заданного числа workers."""
    worker_count = resolve_worker_count(n_workers)
    return min(memif_topology_pair_count(), memif_pair_count_for_workers(worker_count))


def build_trex_port_pairs() -> tuple[TrexPortPair, ...]:
    """Строит полный фиксированный список TRex inside/outside пар под runtime-топологию."""
    pair_count = memif_topology_pair_count()
    memif_pairs = memif_topology_pairs()

    pairs: list[TrexPortPair] = []
    for pair_index in range(pair_count):
        inside_endpoint, outside_endpoint = memif_pairs[pair_index]
        inside_port_id = pair_index * 2
        outside_port_id = inside_port_id + 1
        inside_port = TrexPort(
            name="inside-a" if pair_index == 0 else f"inside-{pair_index}",
            port_id=inside_port_id,
            vdev_name=f"net_memif{inside_port_id}",
            socket_path=inside_endpoint.socket_path,
            ip=inside_host_ip_for_pair(pair_index),
            default_gw=cidr_ip(inside_ip_cidr_for_pair(pair_index)),
        )
        outside_port = TrexPort(
            name="outside" if pair_index == 0 else f"outside-{pair_index}",
            port_id=outside_port_id,
            vdev_name=f"net_memif{outside_port_id}",
            socket_path=outside_endpoint.socket_path,
            ip=outside_host_ip_for_pair(pair_index),
            default_gw=cidr_ip(outside_ip_cidr_for_pair(pair_index)),
        )
        pairs.append(TrexPortPair(pair_index=pair_index, inside=inside_port, outside=outside_port))
    return tuple(pairs)


def build_active_trex_port_pairs(n_workers: int | None = None) -> tuple[TrexPortPair, ...]:
    """Возвращает первые N порт-пар из фиксированной топологии для текущего числа workers."""
    pair_count = active_memif_pair_count_for_workers(n_workers)
    return build_trex_port_pairs()[:pair_count]


def load_trex_ports() -> tuple[TrexPort, ...]:
    """Возвращает плоский список TRex-портов для load-топологии."""
    ports: list[TrexPort] = []
    for pair in build_trex_port_pairs():
        ports.append(pair.inside)
        ports.append(pair.outside)
    return tuple(ports)


def resolve_trex_data_cores(n_workers: int | None = None, requested_cores: int | None = None) -> int:
    """Возвращает `-c` для TRex: data cores per port pair."""
    del n_workers
    return max(1, requested_cores or TREX_DATA_CORES_PER_PAIR)


def resolve_trex_limit_memory_mb(n_workers: int | None = None) -> int:
    """Возвращает `limit_memory` под число memif-пар с запасом до 10 workers."""
    del n_workers
    pair_count = max(memif_topology_pair_count(), TREX_MEMORY_MAX_WORKERS_TARGET)
    return max(TREX_MEMORY_MIN_MB, TREX_MEMORY_BASE_MB + pair_count * TREX_MEMORY_PER_PAIR_MB)


def resolve_trex_server_binary() -> Path:
    """Находит `t-rex-64` по configured path, PATH или каталогу установки."""
    if TREX_SERVER_LINK_PATH.is_file():
        return TREX_SERVER_LINK_PATH.resolve()

    path_binary = shutil.which(TREX_SERVER_BINARY_NAME)
    if path_binary is not None:
        return Path(path_binary).resolve()

    release_binaries = sorted(TREX_INSTALL_BASE_DIR.glob(f"v*/{TREX_SERVER_BINARY_NAME}"))
    for binary in reversed(release_binaries):
        if binary.is_file():
            return binary

    raise RuntimeError(
        f"{TREX_SERVER_BINARY_NAME} not found. Run `manage-nat prepare` first. "
        f"Checked: {TREX_SERVER_LINK_PATH}, PATH, {TREX_INSTALL_BASE_DIR}/v*/{TREX_SERVER_BINARY_NAME}"
    )


def ensure_memif_sockets_exist() -> None:
    """Проверяет, что VPP уже создал нужные memif-сокеты."""
    missing = [port.socket_path for port in load_trex_ports() if not Path(port.socket_path).exists()]
    if missing:
        rendered = ", ".join(missing)
        raise RuntimeError(
            "TRex memif sockets are missing. "
            f"Run `manage-nat setup-vpp <mode>` first. Missing: {rendered}"
        )


def render_trex_config() -> str:
    """Генерирует TRex server config для inside/outside memif-портов стенда."""
    ports = load_trex_ports()
    limit_memory_mb = resolve_trex_limit_memory_mb()
    interface_lines = "\n".join(
        f'    - "--vdev={port.vdev_name},role=slave,id=0,socket-abstract=no,socket={port.socket_path}"'
        for port in ports
    )
    port_info_lines = "\n".join(
        f"    - ip: {port.ip}\n      default_gw: {port.default_gw}"
        for port in ports
    )
    return f"""- port_limit: {len(ports)}
  version: 2
  limit_memory: {limit_memory_mb}
  port_bandwidth_gb: {TREX_PORT_BANDWIDTH_GB}
  interfaces:
{interface_lines}
  port_info:
{port_info_lines}
"""


def write_trex_config(config_path: Path = TREX_CFG_PATH) -> None:
    """Записывает TRex server config в репозиторий."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(render_trex_config(), encoding="utf-8")


def read_trex_pid() -> int | None:
    """Считывает PID запущенного TRex из pid-file."""
    if not TREX_PID_PATH.exists():
        return None
    raw_pid = TREX_PID_PATH.read_text(encoding="utf-8").strip()
    if not raw_pid:
        return None
    try:
        return int(raw_pid)
    except ValueError:
        return None


def read_trex_process_pids(config_path: Path = TREX_CFG_PATH) -> tuple[int, ...]:
    """Находит живые TRex-процессы, запущенные с config_path."""
    result = subprocess.run(
        ["pgrep", "-f", str(config_path.resolve())],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return ()

    pids: list[int] = []
    for raw_pid in result.stdout.splitlines():
        try:
            pid = int(raw_pid.strip())
        except ValueError:
            continue
        pids.append(pid)
    return tuple(pids)


def process_is_running(pid: int) -> bool:
    """Проверяет, жив ли процесс по PID."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def privileged_command(command: list[str]) -> list[str]:
    """Возвращает root-команду без интерактивного sudo prompt в background-процессах."""
    if os.geteuid() == 0:
        return command
    return ["sudo", "-n", *command]


def ensure_sudo_ready() -> None:
    """Проверяет sudo до запуска TRex, чтобы prompt не улетел в trex.log."""
    if os.geteuid() == 0:
        return
    command = ["sudo", "-v"] if sys.stdin.isatty() else ["sudo", "-n", "true"]
    result = subprocess.run(
        command,
        capture_output=not sys.stdin.isatty(),
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "TRex requires sudo/root privileges. "
            "Run `sudo -v` in a terminal first, then retry `manage-nat setup-trex`, "
            "or run the command as root."
        )


def stop_existing_trex_server(config_paths: Iterable[Path] | None = None) -> None:
    """Останавливает ранее запущенный через setup TRex server."""
    paths = tuple(config_paths or (TREX_CFG_PATH,))
    pids: set[int] = set()
    for config_path in paths:
        pids.update(read_trex_process_pids(config_path))
    pid = read_trex_pid()
    if pid is not None:
        pids.add(pid)

    live_pids = {pid for pid in pids if process_is_running(pid)}
    if not live_pids:
        TREX_PID_PATH.unlink(missing_ok=True)
        return

    rendered_pids = ", ".join(str(pid) for pid in sorted(live_pids))
    click.echo(f"  Останавливаю старый TRex server (pid={rendered_pids})")
    ensure_sudo_ready()
    for pid in sorted(live_pids):
        subprocess.run(privileged_command(["kill", str(pid)]), check=False)

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if all(not process_is_running(pid) for pid in live_pids):
            TREX_PID_PATH.unlink(missing_ok=True)
            return
        time.sleep(0.5)

    for pid in sorted(live_pids):
        if process_is_running(pid):
            subprocess.run(privileged_command(["kill", "-9", str(pid)]), check=False)
    TREX_PID_PATH.unlink(missing_ok=True)


def wait_for_trex_rpc_ready(process: subprocess.Popen[bytes]) -> None:
    """Ждет, пока TRex server начнет слушать RPC endpoint."""
    deadline = time.monotonic() + TREX_READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"TRex server exited early with code {process.returncode}. "
                f"Check log: {TREX_LOG_PATH}"
            )
        try:
            with socket.create_connection((TREX_RPC_HOST, TREX_RPC_PORT), timeout=1):
                return
        except OSError:
            time.sleep(TREX_READY_POLL_INTERVAL_SECONDS)

    raise RuntimeError(
        f"Timed out waiting for TRex RPC at {TREX_RPC_HOST}:{TREX_RPC_PORT}. "
        f"Check log: {TREX_LOG_PATH}"
    )


def trex_rpc_is_ready() -> bool:
    """Проверяет, что уже запущенный TRex server отвечает по RPC."""
    try:
        with socket.create_connection((TREX_RPC_HOST, TREX_RPC_PORT), timeout=1):
            return True
    except OSError:
        return False


def launch_trex_server_process(
    trex_binary: Path,
    config_path: Path,
    data_cores: int | None = None,
) -> subprocess.Popen[bytes]:
    """Запускает TRex server process без ожидания RPC-ready."""
    TREX_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    trex_workdir = trex_binary.parent
    trex_data_cores = resolve_trex_data_cores(requested_cores=data_cores)
    command = privileged_command([
        str(trex_binary),
        "-i",
        "-c",
        str(trex_data_cores),
        "--cfg",
        str(config_path.resolve()),
        "--mbuf-factor",
        TREX_MBUF_FACTOR,
    ])

    log_file = TREX_LOG_PATH.open("wb")
    process = subprocess.Popen(
        command,
        cwd=str(trex_workdir),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log_file.close()
    return process


def start_trex_server(trex_binary: Path, config_path: Path, data_cores: int | None = None) -> int:
    """Запускает TRex server в фоне и возвращает PID."""
    process = launch_trex_server_process(trex_binary=trex_binary, config_path=config_path, data_cores=data_cores)
    wait_for_trex_rpc_ready(process)
    TREX_PID_PATH.write_text(f"{process.pid}\n", encoding="utf-8")
    return process.pid


def configure_vpp_memif_rx_placement() -> None:
    """Применяет RX placement после подключения TRex к memif-сокетам."""
    n_workers = parse_configured_workers()
    if not n_workers:
        click.echo("  ✓ RX placement memif-очередей пропущен (worker threads disabled)")
        return

    deadline = time.monotonic() + VPP_MEMIF_PLACEMENT_TIMEOUT_SECONDS
    last_error: RuntimeError | None = None
    while time.monotonic() < deadline:
        try:
            configure_memif_rx_placement(n_workers)
            return
        except RuntimeError as exc:
            last_error = exc
            time.sleep(VPP_MEMIF_PLACEMENT_POLL_INTERVAL_SECONDS)

    details = f": {last_error}" if last_error is not None else ""
    raise RuntimeError(f"Timed out waiting for VPP memif queues before RX placement{details}")


def setup_trex_server(config_path: Path = TREX_CFG_PATH) -> None:
    """Деплоит TRex server для memif-топологии VPP."""
    click.echo("=== Деплой TRex server ===")
    trex_data_cores = resolve_trex_data_cores()
    trex_binary = resolve_trex_server_binary()
    ensure_memif_sockets_exist()
    ensure_sudo_ready()
    write_trex_config(config_path)
    stop_existing_trex_server(config_paths=(TREX_CFG_PATH,))
    trex_pid = start_trex_server(trex_binary=trex_binary, config_path=config_path, data_cores=trex_data_cores)
    configure_vpp_memif_rx_placement()

    click.echo(f"  ✓ Конфиг TRex записан: {config_path}")
    click.echo(f"  ✓ TRex binary: {trex_binary}")
    click.echo(f"  ✓ TRex dataplane cores per port pair: {trex_data_cores}")
    click.echo(f"  ✓ TRex server запущен: pid={trex_pid}, rpc={TREX_RPC_HOST}:{TREX_RPC_PORT}")
    click.echo(f"  ✓ TRex log: {TREX_LOG_PATH}")
    for port in load_trex_ports():
        click.echo(f"  - port {port.port_id}: {port.name} -> {port.socket_path}")


def ensure_trex_server_for_test() -> None:
    """Использует уже поднятый TRex server или поднимает новый, если RPC недоступен."""
    if trex_rpc_is_ready():
        click.echo(f"=== Использую запущенный TRex server ({TREX_RPC_HOST}:{TREX_RPC_PORT}) ===")
        return
    setup_trex_server()

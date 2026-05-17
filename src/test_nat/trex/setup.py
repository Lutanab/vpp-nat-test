from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import rich_click as click

from manage_nat.config import TREX_INSTALL_BASE_DIR, TREX_SERVER_BINARY_NAME, TREX_SERVER_LINK_PATH
from manage_nat.helpers import PROJECT_ROOT
from manage_nat.network.setup import (
    MEMIF_INSIDE_A,
    MEMIF_OUTSIDE,
    NAT_INSIDE_BVI_IP_CIDR,
    NAT_OUTSIDE_IP_CIDR,
)

TREX_CFG_PATH = PROJECT_ROOT / "configs" / "trex" / "trex_cfg.yaml"
TREX_PID_PATH = PROJECT_ROOT / "configs" / "trex" / "trex.pid"
TREX_LOG_PATH = PROJECT_ROOT / "configs" / "trex" / "trex.log"
TREX_RPC_HOST = "127.0.0.1"
TREX_RPC_PORT = 4501
TREX_READY_TIMEOUT_SECONDS = 30
TREX_READY_POLL_INTERVAL_SECONDS = 1
TREX_INSIDE_A_IP = "10.8.1.2"
TREX_OUTSIDE_IP = "10.8.0.2"


@dataclass(frozen=True)
class TrexPort:
    """Описывает соответствие TRex-порта и VPP memif-сокета."""

    name: str
    port_id: int
    vdev_name: str
    socket_path: str
    ip: str
    default_gw: str


def cidr_ip(value: str) -> str:
    """Возвращает IP-часть из строки вида `10.8.1.1/24`."""
    return value.split("/", 1)[0]


TREX_PORTS = (
    TrexPort(
        name="inside-a",
        port_id=0,
        vdev_name="net_memif0",
        socket_path=MEMIF_INSIDE_A.socket_path,
        ip=TREX_INSIDE_A_IP,
        default_gw=cidr_ip(NAT_INSIDE_BVI_IP_CIDR),
    ),
    TrexPort(
        name="outside",
        port_id=1,
        vdev_name="net_memif1",
        socket_path=MEMIF_OUTSIDE.socket_path,
        ip=TREX_OUTSIDE_IP,
        default_gw=cidr_ip(NAT_OUTSIDE_IP_CIDR),
    ),
)


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
    missing = [port.socket_path for port in TREX_PORTS if not Path(port.socket_path).exists()]
    if missing:
        rendered = ", ".join(missing)
        raise RuntimeError(
            "TRex memif sockets are missing. "
            f"Run `manage-nat network setup <mode>` first. Missing: {rendered}"
        )


def render_trex_config() -> str:
    """Генерирует TRex server config для трех memif-портов стенда."""
    interface_lines = "\n".join(
        f'    - "--vdev={port.vdev_name},role=slave,id=0,socket-abstract=no,socket={port.socket_path}"'
        for port in TREX_PORTS
    )
    port_info_lines = "\n".join(
        f"    - ip: {port.ip}\n      default_gw: {port.default_gw}"
        for port in TREX_PORTS
    )
    return f"""- port_limit: {len(TREX_PORTS)}
  version: 2
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


def process_is_running(pid: int) -> bool:
    """Проверяет, жив ли процесс по PID."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stop_existing_trex_server() -> None:
    """Останавливает ранее запущенный через setup TRex server."""
    pid = read_trex_pid()
    if pid is None:
        return
    if not process_is_running(pid):
        TREX_PID_PATH.unlink(missing_ok=True)
        return

    click.echo(f"  Останавливаю старый TRex server (pid={pid})")
    subprocess.run(["sudo", "kill", str(pid)], check=False)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not process_is_running(pid):
            TREX_PID_PATH.unlink(missing_ok=True)
            return
        time.sleep(0.5)

    subprocess.run(["sudo", "kill", "-9", str(pid)], check=False)
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


def start_trex_server(trex_binary: Path, config_path: Path) -> int:
    """Запускает TRex server в фоне и возвращает PID."""
    TREX_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    trex_workdir = trex_binary.parent
    log_file = TREX_LOG_PATH.open("ab")
    process = subprocess.Popen(
        ["sudo", str(trex_binary), "-i", "--cfg", str(config_path.resolve())],
        cwd=str(trex_workdir),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log_file.close()
    wait_for_trex_rpc_ready(process)
    TREX_PID_PATH.write_text(f"{process.pid}\n", encoding="utf-8")
    return process.pid


def setup_trex_server(config_path: Path = TREX_CFG_PATH) -> None:
    """Деплоит TRex server для memif-топологии VPP."""
    click.echo("=== Деплой TRex server ===")
    trex_binary = resolve_trex_server_binary()
    ensure_memif_sockets_exist()
    write_trex_config(config_path)
    stop_existing_trex_server()
    trex_pid = start_trex_server(trex_binary=trex_binary, config_path=config_path)

    click.echo(f"  ✓ Конфиг TRex записан: {config_path}")
    click.echo(f"  ✓ TRex binary: {trex_binary}")
    click.echo(f"  ✓ TRex server запущен: pid={trex_pid}, rpc={TREX_RPC_HOST}:{TREX_RPC_PORT}")
    click.echo(f"  ✓ TRex log: {TREX_LOG_PATH}")
    for port in TREX_PORTS:
        click.echo(f"  - port {port.port_id}: {port.name} -> {port.socket_path}")

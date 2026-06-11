from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import rich_click as click

from test_nat.trex.setup import (
    TREX_CFG_PATH,
    TREX_LOG_PATH,
    TREX_RPC_HOST,
    TREX_RPC_PORT,
    process_is_running,
    read_trex_pid,
    read_trex_process_pids,
    setup_trex_server,
    stop_existing_trex_server,
)

TREX_MODES = ("vpp",)


@dataclass(frozen=True)
class TrexStatus:
    """Короткое состояние TRex server, запущенного этим стендом."""

    running: bool
    pids: tuple[int, ...]
    rpc_host: str
    rpc_port: int
    rpc_ready: bool
    config_path: Path | None
    connected_to: str
    log_path: Path


def setup_trex(mode: str = "vpp") -> None:
    """Поднимает TRex server в выбранном режиме."""
    if mode != "vpp":
        raise ValueError(f"Unsupported TRex mode '{mode}'. Expected one of: {', '.join(TREX_MODES)}")
    rerun_as_root()
    try:
        setup_trex_server(config_path=TREX_CFG_PATH)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


def teardown_trex() -> None:
    """Останавливает TRex server, запущенный для runtime-топологии."""
    rerun_as_root()
    click.echo("=== Остановка TRex server ===")
    try:
        stop_existing_trex_server(config_paths=(TREX_CFG_PATH,))
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("✓ TRex server остановлен")


def rerun_as_root() -> None:
    """Перезапускает текущий manage-nat command через sudo, если он не root."""
    if os.geteuid() == 0:
        return
    clear_trex_log()
    command = [resolve_current_executable(), *sys.argv[1:]]
    raise SystemExit(subprocess.run(["sudo", *command], check=False).returncode)


def resolve_current_executable() -> str:
    """Возвращает абсолютный путь к текущему CLI entrypoint, если возможно."""
    executable = sys.argv[0]
    if "/" in executable:
        return str(Path(executable).resolve())
    return shutil.which(executable) or executable


def clear_trex_log() -> None:
    """Сбрасывает старый TRex log до попытки sudo elevation."""
    TREX_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    TREX_LOG_PATH.write_text("", encoding="utf-8")


def get_trex_status() -> TrexStatus:
    """Возвращает текущее состояние TRex без обращения к STL API."""
    pids_by_config = {
        config_path: tuple(
            pid for pid in read_trex_process_pids(config_path) if process_is_running(pid)
        )
        for config_path in (TREX_CFG_PATH,)
    }
    pids = set().union(*pids_by_config.values())
    pid_from_file = read_trex_pid()
    if pid_from_file is not None and process_is_running(pid_from_file):
        pids.add(pid_from_file)

    running = bool(pids)
    active_config_path = detect_active_config_path(pids_by_config)
    connected_to = detect_trex_connection(active_config_path) if running else "not-running"
    return TrexStatus(
        running=running,
        pids=tuple(sorted(pids)),
        rpc_host=TREX_RPC_HOST,
        rpc_port=TREX_RPC_PORT,
        rpc_ready=running and trex_rpc_is_ready(),
        config_path=active_config_path,
        connected_to=connected_to,
        log_path=TREX_LOG_PATH,
    )


def detect_active_config_path(pids_by_config: dict[Path, tuple[int, ...]]) -> Path | None:
    """Определяет config path по pgrep-совпадению."""
    for config_path, pids in pids_by_config.items():
        if pids:
            return config_path
    if TREX_CFG_PATH.exists():
        return TREX_CFG_PATH
    return None


def detect_trex_connection(config_path: Path | None) -> str:
    """Определяет, к чему подключен TRex, по текущему cfg."""
    if config_path is None or not config_path.exists():
        return "unknown"
    content = config_path.read_text(encoding="utf-8", errors="replace")
    if "/run/vpp/memif-" in content:
        return "vpp"
    return "unknown"


def trex_rpc_is_ready() -> bool:
    """Проверяет доступность TRex RPC endpoint."""
    try:
        with socket.create_connection((TREX_RPC_HOST, TREX_RPC_PORT), timeout=1):
            return True
    except OSError:
        return False

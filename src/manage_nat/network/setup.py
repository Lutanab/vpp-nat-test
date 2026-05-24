from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import rich_click as click

from ..helpers import run_command, with_privileges
from ..nat_mode import (
    MANAGED_BLOCK_BEGIN,
    MANAGED_BLOCK_END,
    STARTUP_CONF_PATH,
    ensure_valid_nat_mode,
)

VPP_SERVICE_NAME = "vpp.service"
BRIDGE_DOMAIN_ID = 10
NAT44_MAX_SESSIONS = 10000
NAT_FO_PUBLIC_ADDR = "10.8.0.1"
NAT_FO_PORT_RANGE_START = 20000
NAT_FO_PORT_RANGE_END = 40000
NAT_INSIDE_BVI_IP_CIDR = "10.8.1.1/24"
NAT_OUTSIDE_IP_CIDR = "10.8.0.1/24"
UNKNOWN_INPUT_RE = re.compile(r"unknown input|unknown command|parse error", re.IGNORECASE)
NAT_PLUGIN_LINE_RE = re.compile(r"^\s*plugin\s+nat_plugin\.so\s+\{.*\}\s*$")
NAT_FO_PLUGIN_LINE_RE = re.compile(r"^\s*plugin\s+nat_fo_plugin\.so\s+\{.*\}\s*$")
CPU_SECTION_LINE_RE = re.compile(r"^\s*cpu\s*\{\s*$")
WORKERS_LINE_RE = re.compile(r"^\s*workers\s+\d+\s*$")
MAIN_CORE_LINE_RE = re.compile(r"^\s*main-core\s+\d+\s*$")
CORELIST_WORKERS_LINE_RE = re.compile(r"^\s*corelist-workers\s+.+$")
VPP_READY_TIMEOUT_SECONDS = 25
VPP_READY_POLL_INTERVAL_SECONDS = 1
VPP_MAIN_CORE = 7
VPP_WORKER_CORE_START = 8
VPP_MAX_WORKERS = 7


@dataclass(frozen=True)
class MemifEndpoint:
    """Описание memif-конца, который поднимается в VPP."""

    socket_id: int
    interface_id: int
    socket_path: str
    role: str

    @property
    def interface_name(self) -> str:
        """Возвращает ожидаемое имя интерфейса в VPP."""
        return f"memif{self.socket_id}/{self.interface_id}"


MEMIF_INSIDE_A = MemifEndpoint(
    socket_id=10,
    interface_id=0,
    socket_path="/run/vpp/memif-inside-a.sock",
    role="master",
)
MEMIF_INSIDE_B = MemifEndpoint(
    socket_id=11,
    interface_id=0,
    socket_path="/run/vpp/memif-inside-b.sock",
    role="master",
)
MEMIF_OUTSIDE = MemifEndpoint(
    socket_id=20,
    interface_id=0,
    socket_path="/run/vpp/memif-outside.sock",
    role="master",
)
MULTIQUEUE_MEMIF_ENDPOINTS = (MEMIF_INSIDE_A, MEMIF_OUTSIDE)


def run_vppctl_command(command: str, description: str) -> str:
    """Запускает `vppctl <command>` и валидирует, что CLI-команда распознана."""
    result = subprocess.run(
        with_privileges(["vppctl", command]),
        text=True,
        capture_output=True,
        check=False,
    )
    output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part).strip()

    if result.returncode != 0 or UNKNOWN_INPUT_RE.search(output):
        details = output or f"vppctl returned code {result.returncode}"
        raise RuntimeError(f"{description} failed: '{command}'. Details: {details}")

    if output:
        click.echo(f"  ✓ {description}: {output}")
    else:
        click.echo(f"  ✓ {description}")
    return output


def plugin_states_for_mode(nat_mode: str) -> tuple[str, str]:
    """Возвращает desired-состояния плагинов `(nat_fo, nat44)` для NAT-режима."""
    if nat_mode == "none":
        return "disable", "disable"
    if nat_mode == "nat44":
        return "disable", "enable"
    if nat_mode == "nat_fo":
        return "enable", "disable"
    raise ValueError(f"Unsupported NAT mode: {nat_mode}")


def read_startup_conf() -> str:
    """Считывает `/etc/vpp/startup.conf` через `sudo`."""
    result = subprocess.run(
        with_privileges(["cat", str(STARTUP_CONF_PATH)]),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise FileNotFoundError(f"Failed to read {STARTUP_CONF_PATH}: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def write_startup_conf(content: str) -> None:
    """Перезаписывает `/etc/vpp/startup.conf` через `sudo tee`."""
    subprocess.run(
        with_privileges(["tee", str(STARTUP_CONF_PATH)]),
        text=True,
        input=content,
        capture_output=True,
        check=True,
    )


def remove_managed_block(lines: list[str]) -> list[str]:
    """Удаляет ранее управляемый блок плагинов из startup.conf."""
    cleaned: list[str] = []
    in_managed_block = False
    for line in lines:
        if line.strip() == MANAGED_BLOCK_BEGIN:
            in_managed_block = True
            continue
        if line.strip() == MANAGED_BLOCK_END:
            in_managed_block = False
            continue
        if in_managed_block:
            continue
        cleaned.append(line)
    return cleaned


def remove_nat_plugin_lines(lines: list[str]) -> list[str]:
    """Удаляет прямые строки `plugin nat*_plugin.so {...}` во избежание конфликтов."""
    result: list[str] = []
    for line in lines:
        if NAT_PLUGIN_LINE_RE.match(line) or NAT_FO_PLUGIN_LINE_RE.match(line):
            continue
        result.append(line)
    return result


def configure_vpp_nat_plugins_for_mode(nat_mode: str) -> None:
    """Записывает managed-блок nat-плагинов в startup.conf."""
    nat_fo_state, nat44_state = plugin_states_for_mode(nat_mode)
    lines = read_startup_conf().splitlines()
    lines = remove_managed_block(lines)
    lines = remove_nat_plugin_lines(lines)
    while lines and lines[-1].strip() == "":
        lines.pop()
    lines.extend(
        [
            "",
            MANAGED_BLOCK_BEGIN,
            "plugins {",
            f"  plugin nat_fo_plugin.so {{ {nat_fo_state} }}",
            f"  plugin nat_plugin.so {{ {nat44_state} }}",
            "}",
            MANAGED_BLOCK_END,
            "",
        ]
    )
    write_startup_conf("\n".join(lines))
    click.echo(
        "  ✓ startup.conf обновлен: "
        f"nat_fo_plugin={nat_fo_state}, nat_plugin={nat44_state}"
    )


def find_section_end(lines: list[str], section_start: int) -> int:
    """Возвращает индекс закрывающей `}` для секции с `{` на section_start."""
    balance = 0
    for index in range(section_start, len(lines)):
        line = lines[index]
        balance += line.count("{")
        balance -= line.count("}")
        if balance == 0:
            return index
    raise RuntimeError(f"Unclosed section in startup.conf starting at line {section_start + 1}")


def configure_vpp_workers(n_workers: int) -> None:
    """Устанавливает `cpu`-параметры VPP в startup.conf."""
    if n_workers < 0 or n_workers > VPP_MAX_WORKERS:
        raise ValueError(f"n_workers must be in range 0..{VPP_MAX_WORKERS}")

    corelist_workers = (
        f"{VPP_WORKER_CORE_START}-{VPP_WORKER_CORE_START + n_workers - 1}"
        if n_workers > 0
        else None
    )

    lines = read_startup_conf().splitlines()
    cpu_start = -1
    for index, line in enumerate(lines):
        if CPU_SECTION_LINE_RE.match(line):
            cpu_start = index
            break

    if cpu_start >= 0:
        cpu_end = find_section_end(lines, cpu_start)
        body = lines[cpu_start + 1 : cpu_end]
        cleaned_body = [
            line
            for line in body
            if not WORKERS_LINE_RE.match(line)
            and not MAIN_CORE_LINE_RE.match(line)
            and not CORELIST_WORKERS_LINE_RE.match(line)
        ]
        cleaned_body.append(f"  main-core {VPP_MAIN_CORE}")
        if corelist_workers is not None:
            cleaned_body.append(f"  corelist-workers {corelist_workers}")
        lines = lines[: cpu_start + 1] + cleaned_body + lines[cpu_end:]
    else:
        while lines and lines[-1].strip() == "":
            lines.pop()
        lines.extend(
            [
                "",
                "cpu {",
                f"  main-core {VPP_MAIN_CORE}",
                *([f"  corelist-workers {corelist_workers}"] if corelist_workers is not None else []),
                "}",
                "",
            ]
        )

    write_startup_conf("\n".join(lines))
    click.echo(
        "  ✓ startup.conf обновлен: "
        f"main-core={VPP_MAIN_CORE}, "
        f"corelist-workers={corelist_workers if corelist_workers is not None else 'disabled'}"
    )


def restart_vpp_service() -> None:
    """Перезапускает VPP и дожидается готовности CLI-сокета."""
    if not vpp_service_exists():
        raise RuntimeError(
            "Service vpp.service is not installed. "
            "Install VPP packages first (for example via repository build/deb install)."
        )

    run_command(with_privileges(["systemctl", "restart", VPP_SERVICE_NAME]))
    wait_for_vpp_ready(timeout_seconds=VPP_READY_TIMEOUT_SECONDS)


def vpp_service_exists() -> bool:
    """Проверяет, что unit `vpp.service` присутствует в systemd."""
    result = subprocess.run(
        ["systemctl", "list-unit-files", "--type=service", VPP_SERVICE_NAME],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    return VPP_SERVICE_NAME in result.stdout


def vpp_service_is_active() -> bool:
    """Проверяет, что `vpp.service` в состоянии active."""
    result = subprocess.run(
        ["systemctl", "is-active", "--quiet", VPP_SERVICE_NAME],
        check=False,
    )
    return result.returncode == 0


def wait_for_vpp_ready(timeout_seconds: int) -> None:
    """Ожидает, пока `vppctl show version` начнет отвечать без ошибок."""
    deadline = time.monotonic() + timeout_seconds
    last_output = ""
    while time.monotonic() < deadline:
        result = subprocess.run(
            with_privileges(["vppctl", "show", "version"]),
            text=True,
            capture_output=True,
            check=False,
        )
        output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part).strip()
        if result.returncode == 0 and not UNKNOWN_INPUT_RE.search(output):
            click.echo("  ✓ VPP готов к приему vppctl команд")
            return
        last_output = output
        time.sleep(VPP_READY_POLL_INTERVAL_SECONDS)

    details = [f"Timed out waiting for VPP CLI readiness ({timeout_seconds}s)."]
    if last_output:
        details.append(f"Last vppctl output: {last_output}")
    if not vpp_service_is_active():
        status = subprocess.run(
            with_privileges(["systemctl", "status", "--no-pager", "--lines=20", VPP_SERVICE_NAME]),
            text=True,
            capture_output=True,
            check=False,
        )
        journal = subprocess.run(
            with_privileges(["journalctl", "-u", VPP_SERVICE_NAME, "-n", "40", "--no-pager"]),
            text=True,
            capture_output=True,
            check=False,
        )
        status_text = (status.stdout or status.stderr).strip()
        journal_text = (journal.stdout or journal.stderr).strip()
        if status_text:
            details.append("systemctl status excerpt:\n" + status_text)
        if journal_text:
            details.append("journalctl excerpt:\n" + journal_text)
    raise RuntimeError("\n\n".join(details))


def memif_queue_count_for_endpoint(endpoint: MemifEndpoint, n_workers: int) -> int:
    """Возвращает количество RX/TX-очередей для memif-интерфейса."""
    if endpoint in MULTIQUEUE_MEMIF_ENDPOINTS:
        return max(1, n_workers)
    return 1


def create_memif_endpoint(endpoint: MemifEndpoint, queue_count: int) -> None:
    """Создаёт memif-сокет и memif-интерфейс в VPP."""
    if queue_count < 1:
        raise ValueError("memif queue_count must be positive")

    run_vppctl_command(
        f"create memif socket id {endpoint.socket_id} filename {endpoint.socket_path}",
        f"Создан memif socket id={endpoint.socket_id}",
    )
    run_vppctl_command(
        (
            "create interface memif "
            f"id {endpoint.interface_id} socket-id {endpoint.socket_id} {endpoint.role}"
            f" rx-queues {queue_count} tx-queues {queue_count}"
        ),
        f"Создан memif интерфейс {endpoint.interface_name} (queues={queue_count})",
    )
    run_command(with_privileges(["chmod", "666", endpoint.socket_path]))


def configure_memif_rx_placement(n_workers: int) -> None:
    """Раскладывает RX-очереди hot-path memif-интерфейсов по VPP worker threads."""
    if n_workers <= 0:
        click.echo("  ✓ RX placement memif-очередей пропущен (worker threads disabled)")
        return

    for queue_index in range(n_workers):
        for endpoint in MULTIQUEUE_MEMIF_ENDPOINTS:
            run_vppctl_command(
                (
                    f"set interface rx-placement {endpoint.interface_name} "
                    f"queue {queue_index} worker {queue_index}"
                ),
                (
                    f"RX queue {queue_index} интерфейса {endpoint.interface_name} "
                    f"назначена worker {queue_index}"
                ),
            )


def remove_stale_memif_sockets() -> None:
    """Удаляет старые memif-сокеты перед пересозданием интерфейсов."""
    for endpoint in (MEMIF_INSIDE_A, MEMIF_INSIDE_B, MEMIF_OUTSIDE):
        run_command(with_privileges(["rm", "-f", endpoint.socket_path]))


def create_bvi_interface() -> str:
    """Создает loopback и возвращает его имя (будет использоваться как BVI)."""
    output = run_vppctl_command("create loopback interface", "Создан loopback интерфейс для BVI")
    for token in reversed(output.split()):
        if token.startswith("loop"):
            return token
    raise RuntimeError(f"Failed to parse BVI interface name from output: {output}")


def configure_l2_and_l3(bvi_interface: str) -> None:
    """Собирает L2/L3-часть топологии: inside-bridge + BVI + outside."""
    run_vppctl_command(f"create bridge-domain {BRIDGE_DOMAIN_ID}", f"Создан bridge-domain {BRIDGE_DOMAIN_ID}")
    run_vppctl_command(
        f"set interface l2 bridge {MEMIF_INSIDE_A.interface_name} {BRIDGE_DOMAIN_ID}",
        "Inside A добавлен в bridge-domain",
    )
    run_vppctl_command(
        f"set interface l2 bridge {MEMIF_INSIDE_B.interface_name} {BRIDGE_DOMAIN_ID}",
        "Inside B добавлен в bridge-domain",
    )
    run_vppctl_command(
        f"set interface l2 bridge {bvi_interface} {BRIDGE_DOMAIN_ID} bvi",
        "BVI добавлен в bridge-domain",
    )
    run_vppctl_command(
        f"set interface ip address {bvi_interface} {NAT_INSIDE_BVI_IP_CIDR}",
        f"BVI получил IP {NAT_INSIDE_BVI_IP_CIDR}",
    )
    run_vppctl_command(
        f"set interface ip address {MEMIF_OUTSIDE.interface_name} {NAT_OUTSIDE_IP_CIDR}",
        f"Outside memif получил IP {NAT_OUTSIDE_IP_CIDR}",
    )


def set_interfaces_up(bvi_interface: str) -> None:
    """Поднимает все интерфейсы стенда."""
    interfaces = [
        MEMIF_INSIDE_A.interface_name,
        MEMIF_INSIDE_B.interface_name,
        MEMIF_OUTSIDE.interface_name,
        bvi_interface,
    ]
    for iface in interfaces:
        run_vppctl_command(f"set interface state {iface} up", f"Поднят интерфейс {iface}")


def configure_nat_runtime_mode(nat_mode: str, bvi_interface: str) -> None:
    """Применяет runtime-конфигурацию NAT для already-up топологии."""
    if nat_mode == "none":
        click.echo("  ✓ NAT runtime-конфигурация пропущена (режим none)")
        return

    outside_iface = MEMIF_OUTSIDE.interface_name
    if nat_mode == "nat44":
        run_vppctl_command(
            f"nat44 plugin enable sessions {NAT44_MAX_SESSIONS}",
            f"NAT44 включен (sessions={NAT44_MAX_SESSIONS})",
        )
        run_vppctl_command(
            f"set interface nat44 in {bvi_interface} out {outside_iface}",
            "Назначены NAT44 роли inside/outside",
        )
        run_vppctl_command(
            f"nat44 add interface address {outside_iface}",
            "Внешний NAT44 адрес назначен по outside интерфейсу",
        )
        run_vppctl_command("show nat44 summary", "Проверка NAT44 summary")
        return

    run_vppctl_command(
        f"nat_fo set public-addr {NAT_FO_PUBLIC_ADDR}",
        f"NAT_FO public-addr={NAT_FO_PUBLIC_ADDR}",
    )
    run_vppctl_command(
        f"nat_fo set port-range {NAT_FO_PORT_RANGE_START} {NAT_FO_PORT_RANGE_END}",
        "NAT_FO port-range установлен",
    )
    run_vppctl_command(
        f"nat_fo interface inside {bvi_interface}",
        "NAT_FO inside интерфейс назначен",
    )
    run_vppctl_command(
        f"nat_fo interface outside {outside_iface}",
        "NAT_FO outside интерфейс назначен",
    )
    run_vppctl_command("show nat_fo", "Проверка NAT_FO summary")


def setup_network(project_root: Path, nat_mode: str, n_workers: int = 0) -> None:
    """Поднимает VPP runtime-топологию memif+BVI и применяет NAT-режим."""
    del project_root  # API-совместимость с другими workflow-функциями.
    mode = ensure_valid_nat_mode(nat_mode)
    click.echo(f"=== Подготовка сети (nat-mode={mode}, n_workers={n_workers}) ===")

    configure_vpp_nat_plugins_for_mode(mode)
    configure_vpp_workers(n_workers)
    restart_vpp_service()
    remove_stale_memif_sockets()

    for endpoint in (MEMIF_INSIDE_A, MEMIF_INSIDE_B, MEMIF_OUTSIDE):
        create_memif_endpoint(
            endpoint,
            queue_count=memif_queue_count_for_endpoint(endpoint, n_workers),
        )

    bvi_interface = create_bvi_interface()
    configure_l2_and_l3(bvi_interface)
    set_interfaces_up(bvi_interface)
    configure_nat_runtime_mode(mode, bvi_interface)

    click.echo("✓ Сетевая топология готова")

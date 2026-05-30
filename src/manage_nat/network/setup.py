from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import rich_click as click

from ..config import VPP_CPU_MAIN_CORE, VPP_CPU_MAX_WORKERS
from ..helpers import run_command, with_privileges
from ..nat_mode import (
    MANAGED_BLOCK_BEGIN,
    MANAGED_BLOCK_END,
    STARTUP_CONF_PATH,
    ensure_valid_nat_mode,
)

VPP_SERVICE_NAME = "vpp.service"
NAT44_MAX_SESSIONS = 10000
NAT_FO_PUBLIC_ADDR = "10.8.0.1"
NAT_INSIDE_IP_CIDR = "10.8.1.1/24"
NAT_OUTSIDE_IP_CIDR = "10.8.0.1/24"
NAT_INSIDE_SUBNET_PREFIX = "10.8.1"
NAT_OUTSIDE_SUBNET_PREFIX = "10.8.0"
UNKNOWN_INPUT_RE = re.compile(r"unknown input|unknown command|parse error", re.IGNORECASE)
NAT_PLUGIN_LINE_RE = re.compile(r"^\s*plugin\s+nat_plugin\.so\s+\{.*\}\s*$")
NAT_FO_PLUGIN_LINE_RE = re.compile(r"^\s*plugin\s+nat_fo_plugin\.so\s+\{.*\}\s*$")
CPU_SECTION_LINE_RE = re.compile(r"^\s*cpu\s*\{\s*$")
WORKERS_LINE_RE = re.compile(r"^\s*workers\s+\d+\s*$")
MAIN_CORE_LINE_RE = re.compile(r"^\s*main-core\s+\d+\s*$")
CORELIST_WORKERS_LINE_RE = re.compile(r"^\s*corelist-workers\s+.+$")
FAILOVER_STARTUP_CONFIG_RE = re.compile(r"^\s*(startup-config|exec)\s+.*nat_fo_topology\.vpp\s*$")
VPP_READY_TIMEOUT_SECONDS = 25
VPP_READY_POLL_INTERVAL_SECONDS = 1
INSIDE_MEMIF_SOCKET_ID_BASE = 10
OUTSIDE_MEMIF_SOCKET_ID_BASE = 20
FIXED_MEMIF_PAIR_COUNT = 8
PAIR_HOST_STRIDE = 4
PAIR_SUBNET_PREFIX_LEN = 30


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
    socket_id=INSIDE_MEMIF_SOCKET_ID_BASE,
    interface_id=0,
    socket_path="/run/vpp/memif-inside-a.sock",
    role="master",
)
MEMIF_OUTSIDE = MemifEndpoint(
    socket_id=OUTSIDE_MEMIF_SOCKET_ID_BASE,
    interface_id=0,
    socket_path="/run/vpp/memif-outside.sock",
    role="master",
)
STALE_MEMIF_SOCKET_PATHS = ("/run/vpp/memif-inside-b.sock",)


def memif_pair_count_for_workers(n_workers: int) -> int:
    """Возвращает число inside/outside memif-пар для текущей конфигурации workers."""
    return max(1, n_workers)


def memif_topology_pair_count() -> int:
    """Возвращает фиксированное число memif-пар в runtime-топологии."""
    return FIXED_MEMIF_PAIR_COUNT


def pair_host_base(pair_index: int) -> int:
    """Возвращает базовый host-octet для пары (inside/outside)."""
    if pair_index < 0:
        raise ValueError("pair_index must be non-negative")
    return 1 + pair_index * PAIR_HOST_STRIDE


def inside_ip_cidr_for_pair(pair_index: int) -> str:
    """Возвращает inside IP/CIDR для memif-пары."""
    return f"{NAT_INSIDE_SUBNET_PREFIX}.{pair_host_base(pair_index)}/{PAIR_SUBNET_PREFIX_LEN}"


def outside_ip_cidr_for_pair(pair_index: int) -> str:
    """Возвращает outside IP/CIDR для memif-пары."""
    return f"{NAT_OUTSIDE_SUBNET_PREFIX}.{pair_host_base(pair_index)}/{PAIR_SUBNET_PREFIX_LEN}"


def inside_host_ip_for_pair(pair_index: int) -> str:
    """Возвращает TRex inside host IP для memif-пары."""
    return f"{NAT_INSIDE_SUBNET_PREFIX}.{pair_host_base(pair_index) + 1}"


def outside_host_ip_for_pair(pair_index: int) -> str:
    """Возвращает TRex outside host IP для memif-пары."""
    return f"{NAT_OUTSIDE_SUBNET_PREFIX}.{pair_host_base(pair_index) + 1}"


def memif_inside_endpoint_for_pair(pair_index: int) -> MemifEndpoint:
    """Возвращает inside memif endpoint для указанной пары."""
    if pair_index == 0:
        return MEMIF_INSIDE_A
    return MemifEndpoint(
        socket_id=INSIDE_MEMIF_SOCKET_ID_BASE + pair_index,
        interface_id=0,
        socket_path=f"/run/vpp/memif-inside-{pair_index}.sock",
        role="master",
    )


def memif_outside_endpoint_for_pair(pair_index: int) -> MemifEndpoint:
    """Возвращает outside memif endpoint для указанной пары."""
    if pair_index == 0:
        return MEMIF_OUTSIDE
    return MemifEndpoint(
        socket_id=OUTSIDE_MEMIF_SOCKET_ID_BASE + pair_index,
        interface_id=0,
        socket_path=f"/run/vpp/memif-outside-{pair_index}.sock",
        role="master",
    )


def memif_pairs_for_workers(n_workers: int) -> tuple[tuple[MemifEndpoint, MemifEndpoint], ...]:
    """Строит список inside/outside memif-пар для текущего количества workers."""
    pair_count = memif_pair_count_for_workers(n_workers)
    return tuple(
        (
            memif_inside_endpoint_for_pair(pair_index),
            memif_outside_endpoint_for_pair(pair_index),
        )
        for pair_index in range(pair_count)
    )


def memif_topology_pairs() -> tuple[tuple[MemifEndpoint, MemifEndpoint], ...]:
    """Строит полный фиксированный список inside/outside memif-пар runtime-топологии."""
    return tuple(
        (
            memif_inside_endpoint_for_pair(pair_index),
            memif_outside_endpoint_for_pair(pair_index),
        )
        for pair_index in range(memif_topology_pair_count())
    )


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


def disable_failover_startup_topology() -> None:
    """Отключает failover startup topology, чтобы normal setup не конфликтовал с runtime CLI."""
    lines = read_startup_conf().splitlines()
    cleaned = [line for line in lines if not FAILOVER_STARTUP_CONFIG_RE.match(line)]
    if cleaned == lines:
        return
    write_startup_conf("\n".join(cleaned))
    click.echo("  ✓ failover startup topology отключена для normal network setup")


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
    if n_workers < 0 or n_workers > VPP_CPU_MAX_WORKERS:
        raise ValueError(f"n_workers must be in range 0..{VPP_CPU_MAX_WORKERS}")

    worker_core_start = VPP_CPU_MAIN_CORE + 1
    corelist_workers = (
        f"{worker_core_start}-{worker_core_start + n_workers - 1}"
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
        cleaned_body.append(f"  main-core {VPP_CPU_MAIN_CORE}")
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
                f"  main-core {VPP_CPU_MAIN_CORE}",
                *([f"  corelist-workers {corelist_workers}"] if corelist_workers is not None else []),
                "}",
                "",
            ]
        )

    write_startup_conf("\n".join(lines))
    click.echo(
        "  ✓ startup.conf обновлен: "
        f"main-core={VPP_CPU_MAIN_CORE}, "
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
    del endpoint, n_workers
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

    pairs = memif_pairs_for_workers(n_workers)
    if len(pairs) < n_workers:
        raise RuntimeError(f"Insufficient memif pairs for workers: pairs={len(pairs)}, workers={n_workers}")

    for worker_index in range(n_workers):
        inside_endpoint, outside_endpoint = pairs[worker_index]
        for endpoint in (inside_endpoint, outside_endpoint):
            run_vppctl_command(
                f"set interface rx-placement {endpoint.interface_name} queue 0 worker {worker_index}",
                f"RX queue 0 интерфейса {endpoint.interface_name} назначена worker {worker_index}",
            )


def remove_stale_memif_sockets() -> None:
    """Удаляет старые memif-сокеты перед пересозданием интерфейсов."""
    for inside_endpoint, outside_endpoint in memif_topology_pairs():
        for endpoint in (inside_endpoint, outside_endpoint):
            run_command(with_privileges(["rm", "-f", endpoint.socket_path]))
    for socket_path in STALE_MEMIF_SOCKET_PATHS:
        run_command(with_privileges(["rm", "-f", socket_path]))


def configure_l3_interfaces() -> None:
    """Назначает IP-адреса напрямую inside/outside memif-интерфейсам."""
    for pair_index, (inside_endpoint, outside_endpoint) in enumerate(memif_topology_pairs()):
        inside_ip_cidr = inside_ip_cidr_for_pair(pair_index)
        outside_ip_cidr = outside_ip_cidr_for_pair(pair_index)
        run_vppctl_command(
            f"set interface ip address {inside_endpoint.interface_name} {inside_ip_cidr}",
            f"Inside memif {inside_endpoint.interface_name} получил IP {inside_ip_cidr}",
        )
        run_vppctl_command(
            f"set interface ip address {outside_endpoint.interface_name} {outside_ip_cidr}",
            f"Outside memif {outside_endpoint.interface_name} получил IP {outside_ip_cidr}",
        )


def set_interfaces_up() -> None:
    """Поднимает все интерфейсы стенда."""
    interfaces: list[str] = []
    for inside_endpoint, outside_endpoint in memif_topology_pairs():
        interfaces.extend((inside_endpoint.interface_name, outside_endpoint.interface_name))
    for iface in interfaces:
        run_vppctl_command(f"set interface state {iface} up", f"Поднят интерфейс {iface}")


def configure_nat_runtime_mode(nat_mode: str, n_workers: int) -> None:
    """Применяет runtime-конфигурацию NAT для already-up топологии."""
    if nat_mode == "none":
        click.echo("  ✓ NAT runtime-конфигурация пропущена (режим none)")
        return

    pairs = memif_pairs_for_workers(n_workers)
    if nat_mode == "nat44":
        run_vppctl_command(
            f"nat44 plugin enable sessions {NAT44_MAX_SESSIONS}",
            f"NAT44 включен (sessions={NAT44_MAX_SESSIONS})",
        )
        for inside_endpoint, outside_endpoint in pairs:
            run_vppctl_command(
                f"set interface nat44 in {inside_endpoint.interface_name} out {outside_endpoint.interface_name}",
                f"NAT44 роли назначены: {inside_endpoint.interface_name} -> {outside_endpoint.interface_name}",
            )
            run_vppctl_command(
                f"nat44 add interface address {outside_endpoint.interface_name}",
                f"NAT44 внешний адрес добавлен с {outside_endpoint.interface_name}",
            )
        run_vppctl_command("show nat44 summary", "Проверка NAT44 summary")
        return

    run_vppctl_command(
        f"nat_fo set public-addr {NAT_FO_PUBLIC_ADDR}",
        f"NAT_FO public-addr={NAT_FO_PUBLIC_ADDR}",
    )
    for pair_index, (inside_endpoint, outside_endpoint) in enumerate(pairs):
        run_vppctl_command(
            f"nat_fo interface inside {inside_endpoint.interface_name}",
            f"NAT_FO inside интерфейс назначен: {inside_endpoint.interface_name}",
        )
        run_vppctl_command(
            f"nat_fo interface outside {outside_endpoint.interface_name}",
            f"NAT_FO outside интерфейс назначен: {outside_endpoint.interface_name}",
        )
        run_vppctl_command(
            f"nat_fo map internal {inside_host_ip_for_pair(pair_index)} public {cidr_ip_no_mask(outside_ip_cidr_for_pair(pair_index))}",
            (
                "NAT_FO mapping добавлен: "
                f"{inside_host_ip_for_pair(pair_index)} -> {cidr_ip_no_mask(outside_ip_cidr_for_pair(pair_index))}"
            ),
        )
    run_vppctl_command("show nat_fo", "Проверка NAT_FO summary")


def cidr_ip_no_mask(value: str) -> str:
    """Возвращает IP-часть из строки вида `10.8.1.1/24`."""
    return value.split("/", 1)[0]


def setup_network(project_root: Path, nat_mode: str, n_workers: int = 0) -> None:
    """Поднимает VPP runtime-топологию inside/outside memif и применяет NAT-режим."""
    del project_root  # API-совместимость с другими workflow-функциями.
    mode = ensure_valid_nat_mode(nat_mode)
    click.echo(f"=== Подготовка сети (nat-mode={mode}, n_workers={n_workers}) ===")

    disable_failover_startup_topology()
    configure_vpp_nat_plugins_for_mode(mode)
    configure_vpp_workers(n_workers)
    restart_vpp_service()
    remove_stale_memif_sockets()

    for inside_endpoint, outside_endpoint in memif_topology_pairs():
        for endpoint in (inside_endpoint, outside_endpoint):
            create_memif_endpoint(
                endpoint,
                queue_count=memif_queue_count_for_endpoint(endpoint, n_workers),
            )

    configure_l3_interfaces()
    set_interfaces_up()
    configure_nat_runtime_mode(mode, n_workers)

    click.echo("✓ Сетевая топология готова")

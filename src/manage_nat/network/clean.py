from __future__ import annotations

import subprocess

import rich_click as click

from ..helpers import run_command, with_privileges

INTERFACE_PREFIXES = ("br", "host", "tap", "vpp")


def stop_vpp_service_if_running() -> None:
    """Останавливает systemd-сервис VPP, если он существует и активен."""
    service_name = "vpp.service"

    unit_files = subprocess.run(
        ["systemctl", "list-unit-files", "--type=service"],
        text=True,
        capture_output=True,
        check=False,
    )
    if service_name not in unit_files.stdout:
        click.echo("  Сервис vpp.service не найден, пропускаю остановку")
        return

    active = subprocess.run(
        ["systemctl", "is-active", "--quiet", service_name],
        check=False,
    )
    if active.returncode != 0:
        click.echo("  Сервис vpp.service уже остановлен")
        return

    run_command(with_privileges(["systemctl", "stop", service_name]))
    click.echo("  ✓ Сервис vpp.service остановлен")


def list_prefixed_interfaces() -> list[str]:
    """Возвращает список интерфейсов с целевыми префиксами."""
    result = subprocess.run(
        ["ip", "-o", "link", "show"],
        text=True,
        capture_output=True,
        check=True,
    )
    names: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.split(": ", 2)
        if len(parts) < 2:
            continue
        iface = parts[1].split("@", 1)[0]
        if iface.startswith(INTERFACE_PREFIXES):
            names.append(iface)
    return names


def delete_interfaces(interfaces: list[str]) -> None:
    """Удаляет интерфейсы, игнорируя ошибки down-операций."""
    for iface in interfaces:
        subprocess.run(with_privileges(["ip", "link", "set", iface, "down"]), check=False)
        subprocess.run(with_privileges(["ip", "link", "delete", iface]), check=False)
        click.echo(f"  ✓ Удалён интерфейс {iface}")


def clean_network() -> None:
    """Очищает runtime-сетевую топологию (VPP + интерфейсы хоста)."""
    click.echo("=== Teardown VPP runtime ===")
    stop_vpp_service_if_running()

    interfaces = list_prefixed_interfaces()
    if not interfaces:
        click.echo("  Интерфейсы с целевыми префиксами не найдены")
        click.echo("✓ VPP runtime остановлен")
        return

    delete_interfaces(interfaces)
    click.echo("✓ VPP runtime остановлен")

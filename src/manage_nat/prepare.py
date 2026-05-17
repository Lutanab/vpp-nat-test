from __future__ import annotations

import subprocess
from pathlib import Path

import rich_click as click

from .helpers import run_command, with_privileges

APT_PACKAGES = (
    "socat",
    "iptables",
)


def ensure_vpp_directory(project_root: Path) -> Path:
    """Проверяет, что директория `vpp/` существует."""
    vpp_dir = project_root / "vpp"
    if not vpp_dir.is_dir():
        raise FileNotFoundError(f"VPP directory not found: {vpp_dir}")
    return vpp_dir


def install_vpp_dependencies(vpp_dir: Path) -> None:
    """Устанавливает зависимости VPP через make-цели."""
    click.echo("=== Установка зависимостей VPP ===")
    run_command(with_privileges(["make", "install-dep"]), cwd=vpp_dir)
    run_command(with_privileges(["make", "install-ext-deps"]), cwd=vpp_dir)


def is_package_installed(name: str) -> bool:
    """Проверяет, установлен ли apt-пакет."""
    result = subprocess.run(
        ["dpkg", "-s", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def install_apt_dependencies() -> None:
    """Устанавливает необходимые системные пакеты."""
    click.echo("=== Установка системных пакетов ===")
    run_command(with_privileges(["apt-get", "update"]))
    missing = [pkg for pkg in APT_PACKAGES if not is_package_installed(pkg)]
    if not missing:
        click.echo("  ✓ Все пакеты уже установлены")
        return
    run_command(with_privileges(["apt-get", "install", "-y", *missing]))


def run_prepare(project_root: Path) -> None:
    """Готовит окружение для NAT-тестового стенда."""
    click.echo("=== Подготовка окружения ===")
    vpp_dir = ensure_vpp_directory(project_root)
    install_vpp_dependencies(vpp_dir)
    install_apt_dependencies()
    click.echo("✓ Подготовка завершена")

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import rich_click as click

from .config import (
    TREX_CONSOLE_BINARY_NAME,
    TREX_CONSOLE_LINK_PATH,
    TREX_DOWNLOAD_URL,
    TREX_INSTALL_BASE_DIR,
    TREX_SERVER_BINARY_NAME,
    TREX_SERVER_LINK_PATH,
)
from .helpers import PROJECT_ROOT, run_command, with_privileges

APT_PACKAGES = (
    "socat",
    "iptables",
    "wget",
    "ca-certificates",
)
TREX_INSECURE_DOWNLOAD_ENV = "VPP_NAT_TEST_TREX_INSECURE_DOWNLOAD"


def ensure_vpp_directory() -> Path:
    """Проверяет, что директория `vpp/` существует."""
    vpp_dir = PROJECT_ROOT / "vpp"
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


def trex_is_available() -> bool:
    """Проверяет, что серверный и консольный бинарь TRex доступны в PATH."""
    return shutil.which(TREX_SERVER_BINARY_NAME) is not None and shutil.which(TREX_CONSOLE_BINARY_NAME) is not None


def list_trex_release_dirs() -> set[Path]:
    """Возвращает директории вида `v*` внутри каталога установки TRex."""
    if not TREX_INSTALL_BASE_DIR.exists():
        return set()
    return {path for path in TREX_INSTALL_BASE_DIR.glob("v*") if path.is_dir()}


def resolve_trex_release_dir(before_dirs: set[Path], after_dirs: set[Path]) -> Path:
    """Определяет директорию release TRex после распаковки архива."""
    candidates = sorted(after_dirs - before_dirs)
    if not candidates:
        candidates = sorted(after_dirs)
    for path in reversed(candidates):
        if (path / TREX_SERVER_BINARY_NAME).is_file() and (path / TREX_CONSOLE_BINARY_NAME).is_file():
            return path
    raise RuntimeError(
        f"TRex archive extracted, but binaries were not found under {TREX_INSTALL_BASE_DIR} "
        f"({TREX_SERVER_BINARY_NAME}, {TREX_CONSOLE_BINARY_NAME})"
    )


def ensure_trex_symlinks(release_dir: Path) -> None:
    """Создает симлинки на TRex бинарники в стандартном пути `/usr/local/bin`."""
    server_src = release_dir / TREX_SERVER_BINARY_NAME
    console_src = release_dir / TREX_CONSOLE_BINARY_NAME
    run_command(with_privileges(["ln", "-sfn", str(server_src), str(TREX_SERVER_LINK_PATH)]))
    run_command(with_privileges(["ln", "-sfn", str(console_src), str(TREX_CONSOLE_LINK_PATH)]))
    run_command(with_privileges(["chmod", "755", str(TREX_SERVER_LINK_PATH), str(TREX_CONSOLE_LINK_PATH)]))


def trex_download_command(archive_path: Path) -> list[str]:
    """Возвращает команду скачивания TRex с опциональным insecure TLS fallback."""
    command = ["wget", "--no-cache", "-O", str(archive_path)]
    if os.environ.get(TREX_INSECURE_DOWNLOAD_ENV) == "1":
        click.echo(f"  ! {TREX_INSECURE_DOWNLOAD_ENV}=1: wget будет запущен с --no-check-certificate")
        command.append("--no-check-certificate")
    command.append(TREX_DOWNLOAD_URL)
    return command


def insecure_trex_download_command(archive_path: Path) -> list[str]:
    """Возвращает команду скачивания TRex без проверки TLS-сертификата."""
    return ["wget", "--no-cache", "--no-check-certificate", "-O", str(archive_path), TREX_DOWNLOAD_URL]


def download_trex_archive(archive_path: Path) -> None:
    """Скачивает TRex archive, при TLS/proxy ошибке делает явный insecure fallback."""
    command = trex_download_command(archive_path)
    try:
        run_command(command)
        return
    except subprocess.CalledProcessError:
        if "--no-check-certificate" in command:
            raise
        click.echo(
            "  ! Не удалось скачать TRex с проверкой TLS. "
            "Повторяю с --no-check-certificate для proxy-окружения."
        )
        run_command(insecure_trex_download_command(archive_path))


def prepare_trex() -> None:
    """Устанавливает TRex и публикует бинарники через `/usr/local/bin`."""
    click.echo("=== Установка TRex ===")
    if trex_is_available():
        click.echo("  ✓ TRex уже доступен в PATH")
        return

    run_command(with_privileges(["mkdir", "-p", str(TREX_INSTALL_BASE_DIR)]))
    before_dirs = list_trex_release_dirs()

    with tempfile.TemporaryDirectory(prefix="trex-download-") as tmp_dir:
        archive_path = Path(tmp_dir) / "trex-latest.tar.gz"
        download_trex_archive(archive_path)
        run_command(
            with_privileges(
                ["tar", "-xzf", str(archive_path), "-C", str(TREX_INSTALL_BASE_DIR)]
            )
        )

    after_dirs = list_trex_release_dirs()
    release_dir = resolve_trex_release_dir(before_dirs, after_dirs)
    ensure_trex_symlinks(release_dir)

    if not trex_is_available():
        raise RuntimeError(
            "TRex binaries were installed, but commands are still unavailable in PATH. "
            f"Expected links: {TREX_SERVER_LINK_PATH}, {TREX_CONSOLE_LINK_PATH}"
        )
    click.echo(
        "  ✓ TRex установлен "
        f"({TREX_SERVER_BINARY_NAME}, {TREX_CONSOLE_BINARY_NAME} -> {release_dir})"
    )


def run_prepare() -> None:
    """Готовит окружение для NAT-тестового стенда."""
    click.echo("=== Подготовка окружения ===")
    vpp_dir = ensure_vpp_directory()
    install_vpp_dependencies(vpp_dir)
    install_apt_dependencies()
    prepare_trex()
    click.echo("✓ Подготовка завершена")

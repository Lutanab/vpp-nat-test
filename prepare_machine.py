#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


########
# CONSTANTS
########

ROOT = Path(__file__).resolve().parent
NAT_FO_DIR = ROOT / "vpp" / "src" / "plugins" / "nat_fo"
NAT_FO_REPO = "https://github.com/Lutanab/vpp_nat_fo.git"
VPP_DIR = ROOT / "vpp"
VPP_BUILD_ROOT = VPP_DIR / "build-root"
VPP_PACKAGE = "vpp"
VPP_WORKERS = "1"
UV_INSTALL_COMMAND = 'curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="/usr/bin" sh'
SYSTEMD_DIR = Path("/etc/systemd/system")
VPP_SLICE = "vpp.slice"
TOP_LEVEL_UNITS = ("init.scope", "system.slice", "user.slice", "machine.slice")


@dataclass(frozen=True)
class CpuIsolationState:
    """Текущее состояние CPU isolation по systemd unit-ам."""

    consistent: bool
    total_cpus: int
    vpp_cpus: int | None
    vpp_allowed: str | None
    default_allowed: str | None
    unit_allowed: dict[str, str | None]


########
# HELPERS
########

def run(cmd: list[str], cwd: Path = ROOT) -> None:
    """Печатает и выполняет команду в указанной рабочей директории."""
    print(f"\n==> {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def require_not_root() -> None:
    """Запрещает запуск всего скрипта через sudo, чтобы не ломать Git ownership."""
    if os.geteuid() == 0:
        raise RuntimeError("не запускайте весь скрипт через sudo; он сам вызывает sudo только где нужно")


def has_foreign_owner(path: Path) -> bool:
    """Проверяет, есть ли внутри path файлы не текущего пользователя."""
    uid = os.getuid()
    gid = os.getgid()
    try:
        if not path.exists():
            return False
        if path.stat().st_uid != uid or path.stat().st_gid != gid:
            return True
        if path.is_file():
            return False
        for root, dirs, files in os.walk(path):
            for name in dirs + files:
                item = Path(root) / name
                stat = item.stat()
                if stat.st_uid != uid or stat.st_gid != gid:
                    return True
    except PermissionError:
        return True
    return False


def ensure_user_owns(path: Path) -> None:
    """Возвращает владение path текущему пользователю, если его забрал sudo."""
    if not has_foreign_owner(path):
        return
    uid = os.getuid()
    gid = os.getgid()
    print(f"Исправляю владельца Git-файлов: {path}", flush=True)
    run(["sudo", "chown", "-R", f"{uid}:{gid}", str(path)])


def git_repo(path: Path) -> bool:
    """Проверяет, что путь существует и является рабочим деревом Git."""
    print(f"Проверяю Git-репозиторий: {path}", flush=True)
    return (
        path.exists()
        and subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


def has_origin(path: Path) -> bool:
    """Проверяет, что в Git-репозитории настроен remote `origin`."""
    print(f"Проверяю remote origin: {path}", flush=True)
    return subprocess.run(
        ["git", "-C", str(path), "remote", "get-url", "origin"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def cpu_range(start: int, count: int) -> str:
    """Форматирует непрерывный диапазон CPU для systemd."""
    end = start + count - 1
    return str(start) if start == end else f"{start}-{end}"


def parse_cpu_set(value: str | None) -> set[int] | None:
    """Парсит systemd CPU set вида `0-3,8` в множество CPU."""
    if value is None or value.strip() == "":
        return None

    cpus: set[int] = set()
    for chunk in value.split(","):
        chunk = chunk.strip()
        if "-" in chunk:
            start, end = (int(part) for part in chunk.split("-", 1))
            cpus.update(range(start, end + 1))
        else:
            cpus.add(int(chunk))
    return cpus


def write_if_changed(path: Path, content: str) -> bool:
    """Идемпотентно записывает файл и возвращает факт изменения."""
    content = content.rstrip() + "\n"
    if path.exists() and path.read_text(encoding="utf-8", errors="replace") == content:
        return False

    if os.geteuid() == 0:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(0o644)
    else:
        run(["sudo", "mkdir", "-p", str(path.parent)])
        print(f"\n==> sudo tee {path}", flush=True)
        subprocess.run(["sudo", "tee", str(path)], input=content, text=True, stdout=subprocess.DEVNULL, check=True)
        run(["sudo", "chmod", "644", str(path)])
    return True


def package_installed(name: str) -> bool:
    """Проверяет, установлен ли deb-пакет."""
    return subprocess.run(
        ["dpkg", "-s", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def uv_available() -> bool:
    """Проверяет, доступен ли бинарь uv."""
    return shutil.which("uv") is not None or any(
        path.exists() for path in (Path("/usr/bin/uv"), Path("/usr/local/bin/uv"), Path.home() / ".local/bin/uv")
    )


def vpp_deb_packages() -> list[str]:
    """Возвращает собранные VPP deb-пакеты."""
    packages = sorted(VPP_BUILD_ROOT.glob("*.deb"))
    if not packages:
        raise RuntimeError(f"VPP deb-пакеты не найдены в {VPP_BUILD_ROOT}")
    return [str(package) for package in packages]


def unit_section(unit: str) -> str:
    """Возвращает секцию systemd unit-файла для CPU-настроек."""
    return "Scope" if unit.endswith(".scope") else "Slice"


def top_level_units() -> list[str]:
    """Возвращает top-level cgroup unit-ы, кроме `vpp.slice` и `machine.slice`."""
    units = set(TOP_LEVEL_UNITS)
    for path in Path("/sys/fs/cgroup").iterdir():
        if path.is_dir() and (path.name.endswith(".slice") or path.name.endswith(".scope")):
            units.add(path.name)
    units.discard(VPP_SLICE)
    units.discard("machine.slice")
    return sorted(units)


def systemd_property(unit: str, prop: str) -> str | None:
    """Читает одно свойство systemd unit-а."""
    result = subprocess.run(
        ["systemctl", "show", unit, f"--property={prop}", "--value"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def inspect_cpu_isolation(total_cpus: int) -> CpuIsolationState:
    """Собирает и проверяет текущую схему CPU isolation."""
    units = top_level_units()
    unit_allowed = {unit: systemd_property(unit, "AllowedCPUs") for unit in units}
    vpp_allowed = systemd_property(VPP_SLICE, "AllowedCPUs")
    vpp_set = parse_cpu_set(vpp_allowed)

    vpp_cpus = len(vpp_set) if vpp_set else None
    default_allowed = None
    consistent = False

    if vpp_cpus is not None and vpp_cpus < total_cpus:
        default_count = total_cpus - vpp_cpus
        expected_default = set(range(0, default_count))
        expected_vpp = set(range(default_count, total_cpus))
        expected_default_text = cpu_range(0, default_count)

        consistent = vpp_set == expected_vpp and all(
            parse_cpu_set(allowed) == expected_default for allowed in unit_allowed.values()
        )
        default_allowed = expected_default_text if consistent else None

    return CpuIsolationState(
        consistent=consistent,
        total_cpus=total_cpus,
        vpp_cpus=vpp_cpus,
        vpp_allowed=vpp_allowed,
        default_allowed=default_allowed,
        unit_allowed=unit_allowed,
    )


def ask_yes_no(prompt: str, default: bool) -> bool:
    """Интерактивно спрашивает Y/N."""
    suffix = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{prompt} [{suffix}]: ").strip().lower()
        if raw == "":
            return default
        if raw in {"y", "yes", "д", "да"}:
            return True
        if raw in {"n", "no", "н", "нет"}:
            return False
        print("Введите Y или N.", flush=True)


def ask_vpp_core_count(total_cpus: int, default: int | None = None) -> int:
    """Интерактивно спрашивает, сколько CPU выделить под VPP."""
    default = default or max(1, total_cpus // 2)
    maximum = total_cpus - 1

    while True:
        raw = input(f"Сколько CPU выделить под VPP [{default}]: ").strip()
        try:
            value = default if raw == "" else int(raw)
        except ValueError:
            print(f"Введите число от 1 до {maximum}.", flush=True)
            continue
        if 1 <= value <= maximum:
            return value
        print(f"Введите число от 1 до {maximum}.", flush=True)


########
# WORKFLOW STEPS
########

def ensure_cpu_isolation() -> None:
    """Идемпотентно настраивает CPU isolation через systemd slices."""
    total_cpus = os.cpu_count()
    if total_cpus is None or total_cpus < 2:
        raise RuntimeError("не удалось определить минимум 2 CPU")

    state = inspect_cpu_isolation(total_cpus)
    print(f"CPU всего: {total_cpus}", flush=True)
    print(f"{VPP_SLICE}: {state.vpp_allowed or '<не настроен>'}", flush=True)
    for unit, allowed in state.unit_allowed.items():
        print(f"{unit}: {allowed or '<не настроен>'}", flush=True)

    changed = False

    if state.consistent:
        print("CPU isolation сейчас уже сконфигурирована согласованно.", flush=True)
        if not ask_yes_no("Хотите изменить конфигурацию", default=False):
            changed |= ensure_vpp_service_slice()
            if changed:
                run(["sudo", "systemctl", "daemon-reload"])
            print("CPU isolation оставлена без изменений.", flush=True)
            return
        vpp_cpus = ask_vpp_core_count(total_cpus, default=state.vpp_cpus)
    else:
        print("CPU isolation не настроена или настроена несогласованно.", flush=True)
        vpp_cpus = ask_vpp_core_count(total_cpus)

    default_cpus = cpu_range(0, total_cpus - vpp_cpus)
    vpp_cpu_range = cpu_range(total_cpus - vpp_cpus, vpp_cpus)

    print(f"Настраиваю CPU: default={default_cpus}; VPP={vpp_cpu_range}", flush=True)

    for unit in top_level_units():
        changed |= write_if_changed(
            SYSTEMD_DIR / f"{unit}.d" / "cpuaffinity.conf",
            f"[{unit_section(unit)}]\nAllowedCPUs={default_cpus}\n",
        )

    changed |= write_if_changed(
        SYSTEMD_DIR / VPP_SLICE,
        f"[Unit]\nDescription=VPP dedicated slice\n\n[Slice]\nAllowedCPUs={vpp_cpu_range}\n",
    )
    changed |= ensure_vpp_service_slice()

    run(["sudo", "systemctl", "daemon-reload"])
    print("CPU isolation настроена." if changed else "CPU isolation уже актуальна.", flush=True)
    print("Перезагрузите машину перед запуском бенчмарков.", flush=True)


def ensure_vpp_service_slice() -> bool:
    """Идемпотентно привязывает `vpp.service` к `vpp.slice`."""
    return write_if_changed(
        SYSTEMD_DIR / "vpp.service.d" / "cpu.conf",
        f"[Service]\nSlice={VPP_SLICE}\n",
    )


def ensure_submodules() -> None:
    """Идемпотентно подгружает top-level submodule-ы репозитория."""
    print("Подгружаю submodule-ы...", flush=True)
    ensure_user_owns(ROOT / ".git")
    ensure_user_owns(VPP_DIR / ".git")
    run(["git", "submodule", "sync"])
    run(["git", "submodule", "update", "--init"])


def ensure_nat_fo() -> None:
    """Идемпотентно подготавливает `nat_fo` как обычный Git-репозиторий."""
    print("Подготавливаю nat_fo...", flush=True)
    if not (ROOT / "vpp").is_dir():
        raise RuntimeError("vpp/ is missing after submodule update")

    if git_repo(NAT_FO_DIR):
        ensure_user_owns(NAT_FO_DIR)
        print("nat_fo уже существует, обновляю...", flush=True)
        remote_cmd = "set-url" if has_origin(NAT_FO_DIR) else "add"
        run(["git", "remote", remote_cmd, "origin", NAT_FO_REPO], NAT_FO_DIR)
        run(["git", "fetch", "origin", "--tags", "--prune"], NAT_FO_DIR)
    elif NAT_FO_DIR.exists() and any(NAT_FO_DIR.iterdir()):
        raise RuntimeError(f"{NAT_FO_DIR} exists, but is not a git repository")
    else:
        print("nat_fo не найден, клонирую...", flush=True)
        NAT_FO_DIR.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", NAT_FO_REPO, str(NAT_FO_DIR)])

    print("nat_fo готов.", flush=True)


def ensure_python_env() -> None:
    """Синхронизирует Python-окружение из lock-файла."""
    print("Готовлю Python-окружение...", flush=True)
    run(["uv", "sync", "--locked"])


def ensure_uv() -> None:
    """Идемпотентно устанавливает uv в /usr/bin."""
    if uv_available():
        print("uv уже установлен.", flush=True)
        return
    print("Устанавливаю uv...", flush=True)
    run(["sudo", "sh", "-c", UV_INSTALL_COMMAND])


def ensure_dependencies() -> None:
    """Идемпотентно ставит системные зависимости и TRex через manage-nat."""
    print("Готовлю зависимости стенда...", flush=True)
    run(["uv", "run", "manage-nat", "prepare"])


def ensure_vpp_package() -> None:
    """Собирает и устанавливает VPP, если пакет еще не установлен."""
    if package_installed(VPP_PACKAGE):
        print("VPP deb-пакет уже установлен.", flush=True)
        return
    if not VPP_DIR.is_dir():
        raise RuntimeError(f"директория VPP не найдена: {VPP_DIR}")

    print("Собираю VPP deb-пакеты...", flush=True)
    for package in VPP_BUILD_ROOT.glob("*.deb"):
        package.unlink()
    run(["make", "pkg-deb-debug"], VPP_DIR)

    print("Устанавливаю VPP deb-пакеты...", flush=True)
    run(["sudo", "dpkg", "-i", *vpp_deb_packages()])


def ensure_vpp_none_mode() -> None:
    """Идемпотентно поднимает VPP-топологию без NAT."""
    print("Поднимаю VPP без NAT...", flush=True)
    run(["uv", "run", "manage-nat", "setup-vpp", "none", "--n-workers", VPP_WORKERS])


########
# ENTRYPOINT
########

def main() -> int:
    """Запускает текущие шаги полного workflow и возвращает код завершения."""
    print("Старт полного тестового workflow.", flush=True)
    try:
        require_not_root()
        ensure_submodules()
        ensure_nat_fo()
        ensure_uv()
        ensure_python_env()
        ensure_cpu_isolation()
        ensure_dependencies()
        ensure_vpp_package()
        ensure_vpp_none_mode()
        print("\nМашина и VPP-стенд подготовлены.", flush=True)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

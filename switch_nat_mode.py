#!/usr/bin/env python3

"""Automate full NAT mode switch workflow for this project."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

VALID_NAT_MODES = ("none", "nat44", "nat_fo")
HEALTHCHECK_PORT = 7000
HEALTHCHECK_TIMEOUT_SECONDS = 120
HEALTHCHECK_INTERVAL_SECONDS = 1


@dataclass(frozen=True)
class VmHealthTarget:
    name: str
    ip: str


def with_privileges(command: Sequence[str]) -> list[str]:
    if os.geteuid() == 0:
        return list(command)
    return ["sudo", *command]


def run_command(command: Sequence[str], cwd: Path | None = None) -> None:
    rendered = shlex.join(command)
    if cwd is not None:
        print(f"\n==> {rendered} (cwd: {cwd})")
    else:
        print(f"\n==> {rendered}")
    subprocess.run(command, cwd=str(cwd) if cwd else None, check=True)


def ensure_manage_exists(project_root: Path) -> Path:
    manage_path = (project_root / "manage").resolve()
    if not manage_path.exists():
        raise FileNotFoundError(f"Не найден manage-скрипт: {manage_path}")
    return manage_path


def rebuild_vpp_packages(project_root: Path) -> None:
    vpp_dir = project_root / "vpp"
    build_root = vpp_dir / "build-root"

    if not vpp_dir.is_dir():
        raise FileNotFoundError(f"Не найдена директория VPP: {vpp_dir}")

    print("\n=== nat_fo выбран: пересборка и переустановка VPP пакетов ===")
    run_command(with_privileges(["make", "pkg-deb-debug"]), cwd=vpp_dir)

    deb_packages = sorted(build_root.glob("*.deb"))
    if not deb_packages:
        raise RuntimeError(f"После сборки не найдены пакеты .deb в {build_root}")

    run_command(with_privileges(["dpkg", "-i", *[str(pkg) for pkg in deb_packages]]))


def load_vm_health_targets(project_root: Path) -> list[VmHealthTarget]:
    probe_script = r"""
set -euo pipefail
source ./cli/constants.sh
printf '%s:%s\n' "$EXTERNAL_VM_SYSTEMD_UNIT" "$EXTERNAL_VM_LIBVIRT_IP"
for i in "${!USER_MACHINES_SYSTEMD_UNIT_NAME[@]}"; do
  printf '%s:%s\n' "${USER_MACHINES_SYSTEMD_UNIT_NAME[$i]}" "${USER_MACHINES_LIBVIRT_IP[$i]}"
done
"""
    result = subprocess.run(
        ["bash", "-lc", probe_script],
        cwd=str(project_root),
        text=True,
        capture_output=True,
        check=True,
    )

    targets: list[VmHealthTarget] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if ":" not in line:
            raise RuntimeError(f"Некорректная строка VM health target: '{line}'")
        name, ip = line.split(":", 1)
        name = name.strip()
        ip = ip.strip()
        if not name or not ip:
            raise RuntimeError(f"Некорректный target VM: '{line}'")
        targets.append(VmHealthTarget(name=name, ip=ip))

    if not targets:
        raise RuntimeError("Не удалось определить VM для healthcheck из cli/constants.sh")
    return targets


def probe_vm_http_200(target: VmHealthTarget) -> tuple[bool, str]:
    url = f"http://{target.ip}:{HEALTHCHECK_PORT}/"
    req = urllib.request.Request(url=url, method="GET")
    # Healthchecks for libvirt-managed VM addresses must not be sent through
    # user/system HTTP(S) proxy settings, otherwise responses can be false 503.
    no_proxy_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with no_proxy_opener.open(req, timeout=1.0) as response:
            status = response.getcode()
            return status == 200, f"HTTP {status}"
    except urllib.error.HTTPError as err:
        return False, f"HTTP {err.code}"
    except Exception as err:
        return False, err.__class__.__name__


def run_vms_healthcheck(project_root: Path) -> None:
    targets = load_vm_health_targets(project_root)
    pending: dict[str, VmHealthTarget] = {target.name: target for target in targets}
    deadline = time.monotonic() + HEALTHCHECK_TIMEOUT_SECONDS

    print("\n=== Healthcheck VM сервисов (порт 7000) ===")
    for target in targets:
        print(f"  - {target.name}: http://{target.ip}:{HEALTHCHECK_PORT}/")

    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        while pending and time.monotonic() < deadline:
            iteration_started = time.monotonic()
            probes = {
                name: pool.submit(probe_vm_http_200, target)
                for name, target in pending.items()
            }

            for name, future in probes.items():
                ok, detail = future.result()
                if ok:
                    elapsed = int(time.monotonic() - (deadline - HEALTHCHECK_TIMEOUT_SECONDS))
                    target = pending.pop(name)
                    print(f"  ✓ {target.name} ({target.ip}:{HEALTHCHECK_PORT}) готов [{elapsed}s, {detail}]")

            if not pending:
                break

            spent = time.monotonic() - iteration_started
            sleep_for = HEALTHCHECK_INTERVAL_SECONDS - spent
            if sleep_for > 0:
                time.sleep(sleep_for)

    if pending:
        pending_names = ", ".join(
            f"{target.name}({target.ip}:{HEALTHCHECK_PORT})" for target in pending.values()
        )
        raise RuntimeError(
            f"Healthcheck timeout {HEALTHCHECK_TIMEOUT_SECONDS}s. Не дождались HTTP 200 от: {pending_names}"
        )

    print("  ✓ Healthcheck пройден для всех VM")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Переключение NAT режима с полным циклом: stop-vms -> clean-network "
            "-> [rebuild для nat_fo] -> setup-network -> setup-vms."
        )
    )
    parser.add_argument("nat_mode", choices=VALID_NAT_MODES, help="none | nat44 | nat_fo")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(__file__).resolve().parent
    manage_path = ensure_manage_exists(project_root)
    nat_mode = args.nat_mode

    print(f"=== Переключение NAT режима: {nat_mode} ===")

    run_command(with_privileges([str(manage_path), "stop-vms"]), cwd=project_root)
    run_command(with_privileges([str(manage_path), "clean-network"]), cwd=project_root)

    if nat_mode == "nat_fo":
        rebuild_vpp_packages(project_root)

    run_command(
        with_privileges([str(manage_path), "setup-network", "--nat-mode", nat_mode]),
        cwd=project_root,
    )
    run_command(with_privileges([str(manage_path), "setup-vms"]), cwd=project_root)
    run_vms_healthcheck(project_root)

    print("\n✓ NAT режим успешно переключён и VM запущены")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"\n✗ Команда завершилась ошибкой (код {exc.returncode})", file=sys.stderr)
        raise SystemExit(exc.returncode)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"\n✗ {exc}", file=sys.stderr)
        raise SystemExit(1)

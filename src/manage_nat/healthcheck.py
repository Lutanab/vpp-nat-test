from __future__ import annotations

import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import rich_click as click

from .helpers import run_shell_script

HEALTHCHECK_PORT = 7000
HEALTHCHECK_TIMEOUT_SECONDS = 120
HEALTHCHECK_INTERVAL_SECONDS = 1


@dataclass(frozen=True)
class VmHealthTarget:
    """Цель healthcheck: systemd-юнит VM и её libvirt IP."""

    name: str
    ip: str


def load_vm_health_targets(project_root: Path) -> list[VmHealthTarget]:
    """Считывает список VM для healthcheck из `cli/constants.sh`."""
    script = r"""
set -euo pipefail
source ./cli/constants.sh
printf '%s:%s\n' "$EXTERNAL_VM_SYSTEMD_UNIT" "$EXTERNAL_VM_LIBVIRT_IP"
for i in "${!USER_MACHINES_SYSTEMD_UNIT_NAME[@]}"; do
  printf '%s:%s\n' "${USER_MACHINES_SYSTEMD_UNIT_NAME[$i]}" "${USER_MACHINES_LIBVIRT_IP[$i]}"
done
"""
    output = run_shell_script(script, cwd=project_root)

    targets: list[VmHealthTarget] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(":", 1)
        if len(parts) != 2:
            raise RuntimeError(f"Malformed VM health target line: '{line}'")
        name, ip = parts[0].strip(), parts[1].strip()
        if not name or not ip:
            raise RuntimeError(f"Malformed VM health target: '{line}'")
        targets.append(VmHealthTarget(name=name, ip=ip))

    if not targets:
        raise RuntimeError("Failed to discover VM health targets from cli/constants.sh")
    return targets


def probe_vm_http_200(target: VmHealthTarget) -> tuple[bool, str]:
    """Проверяет, что VM отвечает HTTP 200 на health endpoint."""
    url = f"http://{target.ip}:{HEALTHCHECK_PORT}/"
    request = urllib.request.Request(url=url, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=1.0) as response:
            status = response.getcode()
            return status == 200, f"HTTP {status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, exc.__class__.__name__


def run_vms_healthcheck(project_root: Path) -> None:
    """Ожидает, пока все VM начнут возвращать HTTP 200 на health-порту."""
    targets = load_vm_health_targets(project_root)
    pending: dict[str, VmHealthTarget] = {target.name: target for target in targets}
    started_at = time.monotonic()

    click.echo("\n=== VM healthcheck (port 7000) ===")
    for target in targets:
        click.echo(f"  - {target.name}: http://{target.ip}:{HEALTHCHECK_PORT}/")

    with ThreadPoolExecutor(max_workers=max(1, len(targets))) as pool:
        while pending and (time.monotonic() - started_at) < HEALTHCHECK_TIMEOUT_SECONDS:
            iteration_started_at = time.monotonic()
            probes = {
                name: pool.submit(probe_vm_http_200, target)
                for name, target in pending.items()
            }

            for name, future in probes.items():
                ok, detail = future.result()
                if ok:
                    elapsed = int(time.monotonic() - started_at)
                    target = pending.pop(name)
                    click.echo(
                        f"  ✓ {target.name} ({target.ip}:{HEALTHCHECK_PORT}) ready "
                        f"[{elapsed}s, {detail}]"
                    )

            if not pending:
                break

            spent = time.monotonic() - iteration_started_at
            sleep_for = HEALTHCHECK_INTERVAL_SECONDS - spent
            if sleep_for > 0:
                time.sleep(sleep_for)

    if pending:
        pending_names = ", ".join(
            f"{target.name}({target.ip}:{HEALTHCHECK_PORT})" for target in pending.values()
        )
        raise RuntimeError(
            f"Healthcheck timeout {HEALTHCHECK_TIMEOUT_SECONDS}s. Missing HTTP 200 from: {pending_names}"
        )

    click.echo("  ✓ Healthcheck passed for all VMs")

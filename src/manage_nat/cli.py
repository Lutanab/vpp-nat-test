from __future__ import annotations

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

import rich_click as click

from ._nat_mode import STARTUP_CONF_PATH, VALID_NAT_MODES, parse_managed_nat_mode

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
        click.echo(f"\n==> {rendered} (cwd: {cwd})")
    else:
        click.echo(f"\n==> {rendered}")
    subprocess.run(command, cwd=str(cwd) if cwd else None, check=True)


def ensure_manage_exists(project_root: Path) -> Path:
    manage_path = (project_root / "manage").resolve()
    if not manage_path.exists():
        raise FileNotFoundError(f"Manage script not found: {manage_path}")
    return manage_path


def rebuild_vpp_packages(project_root: Path) -> None:
    vpp_dir = project_root / "vpp"
    build_root = vpp_dir / "build-root"

    if not vpp_dir.is_dir():
        raise FileNotFoundError(f"VPP directory not found: {vpp_dir}")

    click.echo("\n=== nat_fo selected: rebuilding and reinstalling VPP packages ===")
    run_command(with_privileges(["make", "pkg-deb-debug"]), cwd=vpp_dir)

    deb_packages = sorted(build_root.glob("*.deb"))
    if not deb_packages:
        raise RuntimeError(f"No .deb packages found in {build_root} after build")

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
            raise RuntimeError(f"Malformed VM health target line: '{line}'")
        name, ip = line.split(":", 1)
        if not name.strip() or not ip.strip():
            raise RuntimeError(f"Malformed VM health target: '{line}'")
        targets.append(VmHealthTarget(name=name.strip(), ip=ip.strip()))

    if not targets:
        raise RuntimeError("Failed to discover VM health targets from cli/constants.sh")
    return targets


def probe_vm_http_200(target: VmHealthTarget) -> tuple[bool, str]:
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
    targets = load_vm_health_targets(project_root)
    pending: dict[str, VmHealthTarget] = {target.name: target for target in targets}
    deadline = time.monotonic() + HEALTHCHECK_TIMEOUT_SECONDS

    click.echo("\n=== VM healthcheck (port 7000) ===")
    for target in targets:
        click.echo(f"  - {target.name}: http://{target.ip}:{HEALTHCHECK_PORT}/")

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
                    click.echo(
                        f"  ✓ {target.name} ({target.ip}:{HEALTHCHECK_PORT}) ready "
                        f"[{elapsed}s, {detail}]"
                    )

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
            f"Healthcheck timeout {HEALTHCHECK_TIMEOUT_SECONDS}s. Missing HTTP 200 from: {pending_names}"
        )

    click.echo("  ✓ Healthcheck passed for all VMs")


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def app() -> None:
    """Manage the repository NAT mode workflow."""


@app.command("show")
@click.option(
    "--startup-conf",
    type=click.Path(path_type=Path, dir_okay=False),
    default=STARTUP_CONF_PATH,
    show_default=True,
    help="Path to the VPP startup.conf file with the managed NAT block.",
)
def show_command(startup_conf: Path) -> None:
    """Show the currently configured NAT mode."""
    current_mode = parse_managed_nat_mode(startup_conf_path=startup_conf)
    if current_mode is None:
        raise click.ClickException(
            f"Failed to detect the current NAT mode from {startup_conf}. "
            "The managed VPP plugin block may be missing."
        )
    click.echo(current_mode)


@app.command("switch")
@click.argument("nat_mode", type=click.Choice(VALID_NAT_MODES))
@click.option(
    "--project-root",
    type=click.Path(path_type=Path, file_okay=False, dir_okay=True),
    default=Path(__file__).resolve().parents[2],
    show_default=False,
    help="Repository root. Intended for advanced use and testing.",
)
def switch_command(nat_mode: str, project_root: Path) -> None:
    """Switch NAT mode with the full stop/clean/setup/start workflow."""
    manage_path = ensure_manage_exists(project_root)

    click.echo(f"=== Switching NAT mode to: {nat_mode} ===")

    current_mode = parse_managed_nat_mode()
    if current_mode is not None:
        click.echo(f"Current managed NAT mode: {current_mode}")

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

    click.echo("\n✓ NAT mode successfully switched and VMs are up")


def main() -> int:
    try:
        app.main(standalone_mode=False)
        return 0
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except subprocess.CalledProcessError as exc:
        click.echo(f"\nCommand failed with exit code {exc.returncode}", err=True)
        return exc.returncode
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        click.echo(f"\nError: {exc}", err=True)
        return 1
    except click.Abort:
        click.echo("Aborted", err=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

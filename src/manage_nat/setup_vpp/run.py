from __future__ import annotations

import subprocess
import time
from pathlib import Path

import rich_click as click

from ..config import (
    VPP_INSIDE_INTERFACE_IP_CIDR,
    VPP_INSIDE_INTERFACE_NAME,
    VPP_OUTSIDE_INTERFACE_IP_CIDR,
    VPP_OUTSIDE_INTERFACE_NAME,
)
from ..helpers import run_command, with_privileges
from ..nat_mode import ensure_valid_nat_mode
from .files import write_privileged_file_if_changed
from .spec import PRIMARY, SECONDARY, VppInstance, instances_for_target
from .startup import render_startup_conf
from .systemd import ensure_runtime_dirs, ensure_secondary_unit, service_is_available

VPP_READY_TIMEOUT_SECONDS = 25
VPP_READY_POLL_SECONDS = 1


def vppctl(instance: VppInstance, command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        with_privileges(["vppctl", "-s", str(instance.cli_socket), command]),
        text=True,
        capture_output=True,
        check=False,
    )


def wait_for_vpp(instance: VppInstance) -> None:
    deadline = time.monotonic() + VPP_READY_TIMEOUT_SECONDS
    last_output = ""
    while time.monotonic() < deadline:
        result = vppctl(instance, "show version")
        output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        if result.returncode == 0:
            click.echo(f"  ✓ {instance.name} ready: {instance.cli_socket}")
            return
        last_output = output
        time.sleep(VPP_READY_POLL_SECONDS)
    raise RuntimeError(f"{instance.name} did not become ready. Last output: {last_output}")


def write_startup_configs(instances: tuple[VppInstance, ...], nat_mode: str, n_workers: int) -> bool:
    changed = False
    for instance in instances:
        content = render_startup_conf(instance, nat_mode=nat_mode, n_workers=n_workers)
        changed = write_privileged_file_if_changed(str(instance.startup_conf), content) or changed
        click.echo(f"  ✓ startup {instance.name}: {instance.startup_conf}")
    return changed


def restart_services(instances: tuple[VppInstance, ...]) -> None:
    if PRIMARY in instances and not service_is_available(PRIMARY.service):
        raise RuntimeError("vpp.service is not installed. Install VPP packages first.")
    if SECONDARY in instances:
        ensure_secondary_unit()
    run_command(with_privileges(["systemctl", "daemon-reload"]))
    for instance in instances:
        run_command(with_privileges(["systemctl", "enable", "--now", instance.service]))
        run_command(with_privileges(["systemctl", "restart", instance.service]))
        wait_for_vpp(instance)


def setup_vpp(project_root: Path, nat_mode: str, n_workers: int, target: str = "all") -> None:
    del project_root
    mode = ensure_valid_nat_mode(nat_mode)
    instances = instances_for_target(target)
    if n_workers < 0:
        raise ValueError("n_workers must be non-negative")

    click.echo(f"=== VPP setup (target={target}, nat-mode={mode}, n_workers={n_workers}) ===")
    ensure_runtime_dirs(instances)
    write_startup_configs(instances, mode, n_workers)
    restart_services(instances)
    click.echo("=== VPP topology handoff ===")
    click.echo(
        "  external agent should create/connect interfaces: "
        f"inside={VPP_INSIDE_INTERFACE_NAME} ({VPP_INSIDE_INTERFACE_IP_CIDR}), "
        f"outside={VPP_OUTSIDE_INTERFACE_NAME} ({VPP_OUTSIDE_INTERFACE_IP_CIDR})"
    )

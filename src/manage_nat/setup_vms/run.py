from __future__ import annotations

from pathlib import Path

import rich_click as click

from ..helpers import run_command, with_privileges
from .cloud_init import write_cloud_init
from .network import ensure_vm_management_network
from .spec import VM_SPECS
from .storage import ensure_disk
from .systemd import install_unit, remove_legacy_units, start_unit


def setup_vms(project_root: Path) -> None:
    click.echo("=== VM setup ===")
    remove_legacy_units()
    ensure_vm_management_network()
    unit_changed_by_name: dict[str, bool] = {}
    seed_changed_by_name: dict[str, bool] = {}
    for spec in VM_SPECS:
        ensure_disk(project_root, spec)
        seed_changed_by_name[spec.name] = write_cloud_init(project_root, spec)
        unit_changed_by_name[spec.name] = install_unit(project_root, spec)

    run_command(with_privileges(["systemctl", "daemon-reload"]))
    for spec in VM_SPECS:
        start_unit(spec, restart=unit_changed_by_name[spec.name] or seed_changed_by_name[spec.name])

    click.echo("=== VM access ===")
    for spec in VM_SPECS:
        click.echo(
            f"  {spec.name}: ssh -p {spec.ssh_host_port} zero@<host>, "
            f"mgmt={spec.management_ip}, console={spec.console_socket}"
        )

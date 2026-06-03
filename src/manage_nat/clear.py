from __future__ import annotations

import rich_click as click

from .helpers import run_command, with_privileges
from .setup_vpp.run import vppctl
from .setup_vpp.spec import PRIMARY, SECONDARY, VppInstance
from .setup_vpp.systemd import service_is_active

NAT_FO_SHM_FILE = "/dev/shm/nat_fo_sessions"


def vpp_is_ready(instance: VppInstance) -> bool:
    if not service_is_active(instance.service):
        return False
    return vppctl(instance, "show version").returncode == 0


def nat_fo_cli(instance: VppInstance, command: str, *, required: bool = True) -> str:
    result = vppctl(instance, command)
    output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    if result.returncode != 0 and required:
        raise click.ClickException(f"{instance.name}: `{command}` failed: {output}")
    if output:
        click.echo(f"  {instance.name}: {command}\n{indent(output)}")
    else:
        click.echo(f"  ✓ {instance.name}: {command}")
    return output


def indent(text: str) -> str:
    return "\n".join(f"    {line}" for line in text.splitlines())


def nat_role(instance: VppInstance) -> str:
    output = nat_fo_cli(instance, "nat_fo ha status", required=False)
    if "nat_fo ha role: active" in output:
        return "active"
    if "nat_fo ha role: standby" in output:
        return "standby"
    return "unknown"


def remove_shm_file() -> None:
    run_command(with_privileges(["rm", "-f", NAT_FO_SHM_FILE]))
    click.echo(f"  ✓ removed {NAT_FO_SHM_FILE}")


def clear_nat() -> None:
    click.echo("=== NAT clear ===")
    instances = (PRIMARY, SECONDARY)
    live = tuple(instance for instance in instances if vpp_is_ready(instance))

    for instance in instances:
        state = "ready" if instance in live else "down"
        click.echo(f"  {instance.name}: {state}")

    if not live:
        click.echo("=== Clear shm ===")
        remove_shm_file()
        return

    roles = {instance: nat_role(instance) for instance in live}
    active = next((instance for instance in live if roles[instance] == "active"), None)
    cleaner = active or live[0]
    keep_cleaner_active = active is not None

    click.echo("=== Local runtime clear ===")
    for instance in live:
        nat_fo_cli(instance, "nat_fo ha standby")

    click.echo("=== Shared shm clear ===")
    nat_fo_cli(cleaner, "nat_fo ha takeover")
    nat_fo_cli(cleaner, "nat_fo clear sessions")
    if not keep_cleaner_active:
        nat_fo_cli(cleaner, "nat_fo ha standby")

    click.echo("=== NAT clear done ===")

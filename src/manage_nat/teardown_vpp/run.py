from __future__ import annotations

import subprocess

import rich_click as click

from ..helpers import with_privileges
from ..setup_vpp.spec import PRIMARY, SECONDARY


def services_for_target(target: str) -> tuple[str, ...]:
    if target == "all":
        return (SECONDARY.service, PRIMARY.service)
    if target == "primary":
        return (PRIMARY.service,)
    if target == "secondary":
        return (SECONDARY.service,)
    raise click.ClickException(f"unknown VPP target: {target}")


def service_state(service: str) -> str:
    result = subprocess.run(
        ["systemctl", "is-active", service],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() or "unknown"


def stop_service(service: str) -> None:
    before = service_state(service)
    result = subprocess.run(
        with_privileges(["systemctl", "stop", service]),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 and before not in {"inactive", "failed", "unknown"}:
        message = result.stderr.strip() or result.stdout.strip() or f"failed to stop {service}"
        raise click.ClickException(message)
    after = service_state(service)
    click.echo(f"  ✓ {service}: {before} -> {after}")


def teardown_vpp(target: str = "all") -> None:
    click.echo(f"=== VPP teardown (target={target}) ===")
    for service in services_for_target(target):
        stop_service(service)

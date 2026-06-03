from __future__ import annotations

import time

import rich_click as click

from ..helpers import run_command, with_privileges
from ..setup_vpp.spec import PRIMARY
from ..teardown_vpp.run import stop_service

PRIMARY_FAILOVER_PAUSE_SECONDS = 15.0


def start_service(service: str) -> None:
    run_command(with_privileges(["systemctl", "start", service]))
    click.echo(f"  ✓ started {service}")


def failover_scenario() -> None:
    click.echo("=== Scenario: failover ===")

    click.echo("=== Stop primary ===")
    stop_service(PRIMARY.service)
    click.echo(f"  ... wait {PRIMARY_FAILOVER_PAUSE_SECONDS:g}s")
    time.sleep(PRIMARY_FAILOVER_PAUSE_SECONDS)

    click.echo("=== Restore primary ===")
    start_service(PRIMARY.service)

    click.echo("=== Scenario: failover done ===")

from __future__ import annotations

import time

import rich_click as click

from ..config import VPP_HA_CTLD_SERVICE_NAME
from ..helpers import run_command, with_privileges
from ..setup_vpp.run import wait_for_vpp
from ..setup_vpp.spec import PRIMARY, SECONDARY
from ..setup_vpp.systemd import service_is_active
from ..teardown_vpp.run import stop_service

PRIMARY_RESTART_PAUSE_SECONDS = 3.0
HA_START_DELAY_SECONDS = 0.4


def require_primary_ready() -> None:
    if not service_is_active(PRIMARY.service):
        raise click.ClickException(f"{PRIMARY.service} must be active before scenario restart.")
    try:
        wait_for_vpp(PRIMARY)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


def start_service(service: str) -> None:
    run_command(with_privileges(["systemctl", "start", service]))
    click.echo(f"  ✓ started {service}")


def restart_scenario() -> None:
    click.echo("=== Scenario: restart ===")
    stop_service(SECONDARY.service)
    stop_service(VPP_HA_CTLD_SERVICE_NAME)
    require_primary_ready()

    stop_service(PRIMARY.service)
    click.echo(f"  ... wait {PRIMARY_RESTART_PAUSE_SECONDS:g}s")
    time.sleep(PRIMARY_RESTART_PAUSE_SECONDS)

    start_service(PRIMARY.service)
    click.echo(f"  ... wait {HA_START_DELAY_SECONDS:g}s")
    time.sleep(HA_START_DELAY_SECONDS)
    start_service(VPP_HA_CTLD_SERVICE_NAME)

from __future__ import annotations

from pathlib import Path

import rich_click as click

from .config import VPP_CPU_MAX_WORKERS, VPP_FAILOVER_WORKERS
from .nat_mode import (
    STARTUP_CONF_PATH,
    VALID_NAT_MODES,
    parse_managed_nat_mode,
)
from .helpers import PROJECT_ROOT
from .prepare import run_prepare
from .setup_vms import setup_vms
from .setup_vpp import setup_vpp
from .setup_vpp.spec import PRIMARY, SECONDARY, VPP_TARGETS
from .setup_vpp.systemd import service_is_active
from .show_vhosts import show_vhosts
from .scenario import restart_scenario
from .teardown_vpp import teardown_vpp

DEFAULT_NAT_MODE = "nat_fo"


def parse_setup_vpp_args(args: tuple[str, ...]) -> tuple[str, str]:
    nat_mode = DEFAULT_NAT_MODE
    target = "all"
    nat_mode_seen = False
    target_seen = False

    for arg in args:
        if arg in VALID_NAT_MODES:
            if nat_mode_seen:
                raise click.BadParameter("NAT mode is specified more than once.")
            nat_mode = arg
            nat_mode_seen = True
            continue
        if arg in VPP_TARGETS:
            if target_seen:
                raise click.BadParameter("VPP target is specified more than once.")
            target = arg
            target_seen = True
            continue
        choices = ", ".join((*VALID_NAT_MODES, *VPP_TARGETS))
        raise click.BadParameter(f"unknown setup-vpp argument '{arg}'. Choices: {choices}.")

    return nat_mode, target


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def app() -> None:
    """Управление NAT-режимом в репозитории."""


@app.command("prepare")
@click.option(
    "--project-root",
    type=click.Path(path_type=Path, file_okay=False, dir_okay=True),
    default=PROJECT_ROOT,
    show_default=False,
    help="Корень репозитория.",
)
def prepare_command(project_root: Path) -> None:
    """Готовит окружение (VPP deps, системные пакеты и TRex)."""
    run_prepare(project_root=project_root)


@app.command("setup-vms")
@click.option(
    "--project-root",
    type=click.Path(path_type=Path, file_okay=False, dir_okay=True),
    default=PROJECT_ROOT,
    show_default=False,
    help="Корень репозитория.",
)
def setup_vms_command(project_root: Path) -> None:
    """Поднимает VM с vhost-user и management SSH интерфейсами."""
    setup_vms(project_root=project_root)


@app.command("setup-vpp")
@click.argument("args", nargs=-1, metavar="[NAT_MODE] [TARGET]")
@click.option(
    "--n_workers",
    "--n-workers",
    type=click.IntRange(min=0, max=VPP_CPU_MAX_WORKERS),
    default=VPP_FAILOVER_WORKERS,
    show_default=True,
    help=(
        f"Количество worker thread(s) для каждого VPP, 0..{VPP_CPU_MAX_WORKERS}. "
        "Интерфейсы к VM создает внешний агент."
    ),
)
@click.option(
    "--project-root",
    type=click.Path(path_type=Path, file_okay=False, dir_okay=True),
    default=PROJECT_ROOT,
    show_default=False,
    help="Корень репозитория.",
)
def setup_vpp_command(args: tuple[str, ...], n_workers: int, project_root: Path) -> None:
    """Поднимает VPP. NAT_MODE: none/nat44/nat_fo. TARGET: all/primary/secondary."""
    nat_mode, target = parse_setup_vpp_args(args)
    setup_vpp(project_root=project_root, nat_mode=nat_mode, n_workers=n_workers, target=target)


@app.command("teardown-vpp")
@click.argument("target", type=click.Choice(VPP_TARGETS), default="all", required=False)
def teardown_vpp_command(target: str) -> None:
    """Останавливает primary/secondary VPP systemd services."""
    teardown_vpp(target=target)


@app.group("scenario")
def scenario_group() -> None:
    """Запускает готовые тестовые сценарии."""


@scenario_group.command("restart")
def scenario_restart_command() -> None:
    """Рестартует active VPP node и затем запускает vpp-ha-ctld."""
    restart_scenario()


@app.command("show")
@click.option(
    "--startup-conf",
    type=click.Path(path_type=Path, dir_okay=False),
    default=STARTUP_CONF_PATH,
    show_default=True,
    help="Path to the VPP startup.conf file with the managed NAT block.",
)
def show_command(startup_conf: Path) -> None:
    """Показывает состояние NAT/VPP и vhost-user подключений."""
    current_mode = parse_managed_nat_mode(startup_conf_path=startup_conf)
    if current_mode is None:
        raise click.ClickException(
            f"Failed to detect the current NAT mode from {startup_conf}. "
            "The managed VPP plugin block may be missing."
        )
    click.echo(f"nat_mode={current_mode}")
    click.echo(f"primary={'up' if service_is_active(PRIMARY.service) else 'down'}")
    click.echo(f"secondary={'up' if service_is_active(SECONDARY.service) else 'down'}")
    click.echo("vhosts:")
    show_vhosts()

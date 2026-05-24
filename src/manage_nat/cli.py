from __future__ import annotations

from pathlib import Path

import rich_click as click

from .nat_mode import (
    STARTUP_CONF_PATH,
    VALID_NAT_MODES,
    parse_configured_workers,
    parse_managed_nat_mode,
)
from .helpers import PROJECT_ROOT
from .network.clean import clean_network
from .network.setup import setup_network
from .prepare import run_prepare
from .switch import switch_nat_mode


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


@app.command("show")
@click.option(
    "--startup-conf",
    type=click.Path(path_type=Path, dir_okay=False),
    default=STARTUP_CONF_PATH,
    show_default=True,
    help="Path to the VPP startup.conf file with the managed NAT block.",
)
def show_command(startup_conf: Path) -> None:
    """Показывает текущий NAT-режим в managed-блоке startup.conf."""
    current_mode = parse_managed_nat_mode(startup_conf_path=startup_conf)
    if current_mode is None:
        raise click.ClickException(
            f"Failed to detect the current NAT mode from {startup_conf}. "
            "The managed VPP plugin block may be missing."
        )
    workers = parse_configured_workers(startup_conf_path=startup_conf)
    if workers is None:
        click.echo(f"nat_mode={current_mode}")
        click.echo("n_workers=not-set")
        return
    click.echo(f"nat_mode={current_mode}")
    click.echo(f"n_workers={workers}")


@app.group("network")
def network_group() -> None:
    """Операции с runtime-сетевой топологией."""


@network_group.command("setup")
@click.argument("nat_mode", type=click.Choice(VALID_NAT_MODES))
@click.option(
    "--n_workers",
    "--n-workers",
    type=click.IntRange(min=0, max=7),
    default=0,
    show_default=True,
    help="Количество VPP worker thread(s), 0..7 (corelist-workers от 8-го ядра).",
)
@click.option(
    "--project-root",
    type=click.Path(path_type=Path, file_okay=False, dir_okay=True),
    default=PROJECT_ROOT,
    show_default=False,
    help="Корень репозитория.",
)
def network_setup_command(nat_mode: str, n_workers: int, project_root: Path) -> None:
    """Поднимает сеть и применяет выбранный NAT-режим."""
    setup_network(project_root=project_root, nat_mode=nat_mode, n_workers=n_workers)


@network_group.command("clean")
@click.option(
    "--project-root",
    type=click.Path(path_type=Path, file_okay=False, dir_okay=True),
    default=PROJECT_ROOT,
    show_default=False,
    help="Корень репозитория.",
)
def network_clean_command(project_root: Path) -> None:
    """Очищает runtime-сетевую топологию."""
    clean_network(project_root=project_root)


@app.command("switch")
@click.argument("nat_mode", type=click.Choice(VALID_NAT_MODES))
@click.option(
    "--n_workers",
    "--n-workers",
    type=click.IntRange(min=0, max=7),
    default=0,
    show_default=True,
    help="Количество VPP worker thread(s), 0..7 (corelist-workers от 8-го ядра).",
)
@click.option(
    "--project-root",
    type=click.Path(path_type=Path, file_okay=False, dir_okay=True),
    default=PROJECT_ROOT,
    show_default=False,
    help="Repository root. Intended for advanced use and testing.",
)
@click.option(
    "--restart",
    is_flag=True,
    help="Force the full stop/clean/rebuild/setup/start workflow even if the requested NAT mode is already configured.",
)
def switch_command(nat_mode: str, n_workers: int, project_root: Path, restart: bool) -> None:
    """Переключает NAT-режим полным stop/clean/setup/start workflow."""
    switch_nat_mode(
        nat_mode=nat_mode,
        n_workers=n_workers,
        project_root=project_root,
        restart=restart,
    )

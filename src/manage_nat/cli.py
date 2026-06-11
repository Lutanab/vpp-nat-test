from __future__ import annotations

import rich_click as click

from .config import MEMIF_RING_SIZE_DEFAULT, VPP_CPU_MAIN_CORE, VPP_CPU_MAX_WORKERS
from .nat_mode import (
    VALID_NAT_MODES,
    parse_configured_workers,
    parse_managed_nat_mode,
)
from .network.clean import clean_network
from .network.setup import setup_network, vpp_service_exists, vpp_service_is_active
from .prepare import run_prepare
from .trex import TREX_MODES, get_trex_status, setup_trex, teardown_trex


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def app() -> None:
    """Управление VPP/TRex NAT-стендом."""


@app.command("prepare")
def prepare_command() -> None:
    """Готовит окружение (VPP deps, системные пакеты и TRex)."""
    run_prepare()


@app.command("show")
def show_command() -> None:
    """Показывает текущее состояние VPP/TRex стенда."""
    current_mode = parse_managed_nat_mode()
    if current_mode is None:
        raise click.ClickException(
            "Failed to detect the current NAT mode. "
            "Run `manage-nat setup-vpp <mode>` first."
        )
    workers = parse_configured_workers()
    click.echo(f"nat_mode={current_mode}")
    click.echo(f"n_workers={workers if workers is not None else 'not-set'}")
    click.echo(f"vpp_service={format_vpp_service_state()}")

    trex_status = get_trex_status()
    click.echo(f"trex_running={'yes' if trex_status.running else 'no'}")
    click.echo(f"trex_pids={format_pids(trex_status.pids)}")
    click.echo(f"trex_connected_to={trex_status.connected_to}")
    click.echo(
        "trex_rpc="
        f"{trex_status.rpc_host}:{trex_status.rpc_port} "
        f"({'ready' if trex_status.rpc_ready else 'not-ready'})"
    )
    click.echo(f"trex_config={trex_status.config_path or 'unknown'}")
    click.echo(f"trex_log={trex_status.log_path}")


def format_vpp_service_state() -> str:
    """Возвращает короткое состояние systemd unit `vpp.service`."""
    if not vpp_service_exists():
        return "missing"
    return "active" if vpp_service_is_active() else "inactive"


def format_pids(pids: tuple[int, ...]) -> str:
    """Форматирует список PID для machine-readable вывода."""
    if not pids:
        return "none"
    return ",".join(str(pid) for pid in pids)


@app.command("setup-vpp")
@click.argument("nat_mode", type=click.Choice(VALID_NAT_MODES))
@click.option(
    "--n_workers",
    "--n-workers",
    type=click.IntRange(min=0, max=VPP_CPU_MAX_WORKERS),
    default=0,
    show_default=True,
    help=(
        f"Количество VPP worker thread(s), 0..{VPP_CPU_MAX_WORKERS} "
        f"(main-core={VPP_CPU_MAIN_CORE}, workers с {VPP_CPU_MAIN_CORE + 1})."
    ),
)
@click.option(
    "--memif-ring-size",
    type=click.IntRange(min=1),
    default=MEMIF_RING_SIZE_DEFAULT,
    show_default=True,
    help="Размер memif-кольца (`ring-size`) для создаваемых интерфейсов.",
)
def setup_vpp_command(nat_mode: str, n_workers: int, memif_ring_size: int) -> None:
    """Поднимает VPP в нужном NAT-режиме и с нужным числом workers."""
    setup_network(
        nat_mode=nat_mode,
        n_workers=n_workers,
        memif_ring_size=memif_ring_size,
    )


@app.command("teardown-vpp")
def teardown_vpp_command() -> None:
    """Останавливает VPP и очищает runtime-сетевую топологию."""
    clean_network()


@app.command("setup-trex")
@click.argument("mode", type=click.Choice(TREX_MODES), default="vpp", required=False)
def setup_trex_command(mode: str) -> None:
    """Поднимает TRex server (сейчас поддерживается режим `vpp`)."""
    setup_trex(mode=mode)


@app.command("teardown-trex")
def teardown_trex_command() -> None:
    """Останавливает TRex server."""
    teardown_trex()

from __future__ import annotations

from pathlib import Path

import rich_click as click

from .nat_mode import parse_managed_nat_mode
from .helpers import run_command, with_privileges
from .network.clean import clean_network
from .network.setup import setup_network


def rebuild_vpp_packages(project_root: Path) -> None:
    """Пересобирает и переустанавливает VPP deb-пакеты."""
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


def switch_nat_mode(nat_mode: str, project_root: Path, restart: bool) -> None:
    """Выполняет полный workflow переключения NAT-режима."""
    click.echo(f"=== Switching NAT mode to: {nat_mode} ===")

    current_mode = parse_managed_nat_mode()
    if current_mode is not None:
        click.echo(f"Current managed NAT mode: {current_mode}")
    if current_mode == nat_mode and not restart:
        click.echo("\n✓ NAT mode is already configured. Use --restart to rebuild/recreate the runtime topology.")
        return
    if restart:
        click.echo("Restart requested: forcing the full workflow.")

    clean_network(project_root)

    if nat_mode == "nat_fo":
        rebuild_vpp_packages(project_root)

    setup_network(project_root=project_root, nat_mode=nat_mode)

    click.echo("\n✓ NAT mode successfully switched and runtime topology is up")

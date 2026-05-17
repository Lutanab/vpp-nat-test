from __future__ import annotations

from pathlib import Path

import rich_click as click

from ..nat_mode import ensure_valid_nat_mode
from ..helpers import run_shell_script


def setup_network(project_root: Path, nat_mode: str) -> None:
    """Поднимает VPP runtime-топологию и применяет NAT-режим."""
    mode = ensure_valid_nat_mode(nat_mode)
    click.echo(f"=== Подготовка сети (nat-mode={mode}) ===")

    script = r"""
set -euo pipefail
source ./cli/constants.sh
source ./cli/shell_helpers/vpp_helpers.sh

configure_vpp_nat_plugins_for_mode "$NAT_MODE"
setup_vpp_service

echo "=== Включение IPv4 forwarding ==="
sudo sysctl -w net.ipv4.ip_forward=1

create_external_vhost_interface
for socket_path in "${VHOST_SOCKETS[@]}"; do
  check_and_create_vhost_socket "$socket_path"
done

prepare_vpp_network

vpp_ifaces=()
vpp_ifaces+=("$EXTERNAL_VHOST_VPP_IFACE")
vpp_ifaces+=("$VPP_BVI_INTERFACE")
vpp_ifaces+=("${VHOST_USER_VPP_IFACES[@]}")
set_vpp_ifaces_up "${vpp_ifaces[@]}"

configure_vpp_nat_runtime_mode "$NAT_MODE" "$VPP_BVI_INTERFACE" "$EXTERNAL_VHOST_VPP_IFACE"
"""
    run_shell_script(script, cwd=project_root, env={"NAT_MODE": mode}, capture_output=False)
    click.echo("✓ Сетевая топология готова")

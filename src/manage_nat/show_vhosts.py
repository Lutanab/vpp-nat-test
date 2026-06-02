from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

import rich_click as click

from .config import (
    VM_EXTERNAL_VHOST_SOCKET_PATH,
    VM_USER_VHOST_SOCKET_PATH,
    VPP_EXTERNAL_VHOST_INTERFACE_NAME,
    VPP_PRIMARY_RUN_DIR,
    VPP_SECONDARY_RUN_DIR,
    VPP_USER_VHOST_INTERFACE_NAME,
)
from .helpers import with_privileges


@dataclass(frozen=True, slots=True)
class VhostTarget:
    label: str
    socket_path: str
    expected_interface: str


@dataclass(frozen=True, slots=True)
class VppCli:
    label: str
    cli_socket: str


TARGETS = (
    VhostTarget("external_vm", str(VM_EXTERNAL_VHOST_SOCKET_PATH), VPP_EXTERNAL_VHOST_INTERFACE_NAME),
    VhostTarget("user_vm", str(VM_USER_VHOST_SOCKET_PATH), VPP_USER_VHOST_INTERFACE_NAME),
)
VPP_CLIS = (
    VppCli("primary", str(VPP_PRIMARY_RUN_DIR / "cli.sock")),
    VppCli("secondary", str(VPP_SECONDARY_RUN_DIR / "cli.sock")),
)


def run_vppctl(cli: VppCli, command: str) -> str | None:
    result = subprocess.run(
        with_privileges(["vppctl", "-s", cli.cli_socket, command]),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def interface_state(interface_output: str | None, interface_name: str) -> str:
    if interface_output is None:
        return "unreachable"
    for line in interface_output.splitlines():
        if line.startswith(interface_name):
            parts = line.split()
            return parts[2] if len(parts) >= 3 else "present"
    return "missing"


def find_interface_for_socket(vhost_output: str | None, target: VhostTarget) -> str | None:
    if vhost_output is None or target.socket_path not in vhost_output:
        return None
    lines = vhost_output.splitlines()
    for index, line in enumerate(lines):
        if target.socket_path not in line:
            continue
        window = "\n".join(lines[max(0, index - 6) : index + 3])
        expected_match = re.search(re.escape(target.expected_interface), window)
        if expected_match:
            return expected_match.group(0)
        generic_match = re.search(r"VirtualEthernet\S+", window)
        if generic_match:
            return generic_match.group(0).rstrip(":")
    return target.expected_interface


def format_cell(vhost_output: str | None, interface_output: str | None, target: VhostTarget) -> str:
    if vhost_output is None:
        return "unreachable"
    interface_name = find_interface_for_socket(vhost_output, target)
    if interface_name is None:
        return "-"
    return f"{interface_name} {interface_state(interface_output, interface_name)}"


def show_vhosts() -> None:
    outputs = {
        cli.label: (
            run_vppctl(cli, "show vhost-user"),
            run_vppctl(cli, "show interface"),
        )
        for cli in VPP_CLIS
    }
    rows = []
    for target in TARGETS:
        primary_vhost, primary_interfaces = outputs["primary"]
        secondary_vhost, secondary_interfaces = outputs["secondary"]
        rows.append(
            (
                target.label,
                target.socket_path,
                format_cell(primary_vhost, primary_interfaces, target),
                format_cell(secondary_vhost, secondary_interfaces, target),
            )
        )

    headers = ("vm", "socket", "primary", "secondary")
    widths = [len(header) for header in headers]
    for row in rows:
        widths = [max(width, len(value)) for width, value in zip(widths, row)]

    click.echo("  ".join(header.ljust(width) for header, width in zip(headers, widths)))
    click.echo("  ".join("-" * width for width in widths))
    for row in rows:
        click.echo("  ".join(value.ljust(width) for value, width in zip(row, widths)))

from __future__ import annotations

import subprocess
import tempfile

import rich_click as click

from ..config import (
    VM_EXTERNAL_MANAGEMENT_IP,
    VM_EXTERNAL_MANAGEMENT_MAC,
    VM_EXTERNAL_NAME,
    VM_EXTERNAL_SSH_HOST_PORT,
    VM_MANAGEMENT_BRIDGE_NAME,
    VM_MANAGEMENT_GATEWAY_IP,
    VM_MANAGEMENT_NETMASK,
    VM_MANAGEMENT_NETWORK_NAME,
    VM_USER_MANAGEMENT_IP,
    VM_USER_MANAGEMENT_MAC,
    VM_USER_NAME,
    VM_USER_SSH_HOST_PORT,
)
from ..helpers import run_command, with_privileges

IP_FORWARD_SYSCTL_PATH = "/etc/sysctl.d/99-vpp-nat-test.conf"
PORT_FORWARD_SCRIPT_PATH = "/usr/local/sbin/vpp-vm-ssh-forward.sh"
PORT_FORWARD_UNIT_PATH = "/etc/systemd/system/vpp-vm-ssh-forward.service"
LEGACY_SSH_FORWARDS = ("8222:10.8.2.12",)


def write_privileged_file(path: str, content: str, mode: str | None = None) -> None:
    subprocess.run(
        with_privileges(["tee", path]),
        text=True,
        input=content,
        capture_output=True,
        check=True,
    )
    if mode is not None:
        subprocess.run(with_privileges(["chmod", mode, path]), check=True)


def read_privileged_file(path: str) -> str | None:
    result = subprocess.run(
        with_privileges(["cat", path]),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def write_privileged_file_if_changed(path: str, content: str, mode: str | None = None) -> bool:
    changed = read_privileged_file(path) != content
    if changed:
        write_privileged_file(path, content, mode)
    elif mode is not None:
        subprocess.run(with_privileges(["chmod", mode, path]), check=True)
    return changed


def ensure_libvirtd_running() -> None:
    if subprocess.run(["systemctl", "is-active", "--quiet", "libvirtd"], check=False).returncode == 0:
        return
    run_command(with_privileges(["systemctl", "start", "libvirtd"]))


def management_network_xml() -> str:
    return f"""<network>
  <name>{VM_MANAGEMENT_NETWORK_NAME}</name>
  <forward mode='nat'/>
  <bridge name='{VM_MANAGEMENT_BRIDGE_NAME}' stp='on' delay='0'/>
  <ip address='{VM_MANAGEMENT_GATEWAY_IP}' netmask='{VM_MANAGEMENT_NETMASK}'>
    <dhcp>
      <range start='10.8.2.2' end='10.8.2.254'/>
      <host mac='{VM_EXTERNAL_MANAGEMENT_MAC}' name='{VM_EXTERNAL_NAME}' ip='{VM_EXTERNAL_MANAGEMENT_IP}'/>
      <host mac='{VM_USER_MANAGEMENT_MAC}' name='{VM_USER_NAME}' ip='{VM_USER_MANAGEMENT_IP}'/>
    </dhcp>
  </ip>
</network>
"""


def network_is_defined() -> bool:
    result = subprocess.run(
        with_privileges(["virsh", "net-info", VM_MANAGEMENT_NETWORK_NAME]),
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def network_matches_expected() -> bool:
    result = subprocess.run(
        with_privileges(["virsh", "net-dumpxml", VM_MANAGEMENT_NETWORK_NAME]),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    expected_parts = (
        f"<bridge name='{VM_MANAGEMENT_BRIDGE_NAME}'",
        f"<ip address='{VM_MANAGEMENT_GATEWAY_IP}' netmask='{VM_MANAGEMENT_NETMASK}'>",
        f"<host mac='{VM_EXTERNAL_MANAGEMENT_MAC}' name='{VM_EXTERNAL_NAME}' ip='{VM_EXTERNAL_MANAGEMENT_IP}'/>",
        f"<host mac='{VM_USER_MANAGEMENT_MAC}' name='{VM_USER_NAME}' ip='{VM_USER_MANAGEMENT_IP}'/>",
    )
    return all(part in result.stdout for part in expected_parts)


def ensure_network_started() -> None:
    run_command(with_privileges(["virsh", "net-autostart", VM_MANAGEMENT_NETWORK_NAME]))
    result = subprocess.run(
        with_privileges(["virsh", "net-info", VM_MANAGEMENT_NETWORK_NAME]),
        text=True,
        capture_output=True,
        check=True,
    )
    if "Active:         yes" not in result.stdout:
        run_command(with_privileges(["virsh", "net-start", VM_MANAGEMENT_NETWORK_NAME]))


def define_management_network() -> None:
    ensure_libvirtd_running()
    if network_is_defined() and network_matches_expected():
        ensure_network_started()
        click.echo(f"  ✓ libvirt network {VM_MANAGEMENT_NETWORK_NAME} already matches")
        return

    if network_is_defined():
        subprocess.run(with_privileges(["virsh", "net-destroy", VM_MANAGEMENT_NETWORK_NAME]), check=False)
        subprocess.run(with_privileges(["virsh", "net-undefine", VM_MANAGEMENT_NETWORK_NAME]), check=False)

    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as tmp:
        tmp.write(management_network_xml())
        tmp_path = tmp.name
    try:
        run_command(with_privileges(["virsh", "net-define", tmp_path]))
    finally:
        subprocess.run(["rm", "-f", tmp_path], check=False)
    ensure_network_started()


def ensure_qemu_bridge_scripts() -> None:
    ifup = f"""#!/bin/sh
set -eu
ip link set "$1" up
brctl addif {VM_MANAGEMENT_BRIDGE_NAME} "$1" 2>/dev/null || ip link set "$1" master {VM_MANAGEMENT_BRIDGE_NAME}
"""
    ifdown = f"""#!/bin/sh
set -eu
brctl delif {VM_MANAGEMENT_BRIDGE_NAME} "$1" 2>/dev/null || ip link set "$1" nomaster
ip link set "$1" down
"""
    write_privileged_file_if_changed("/etc/qemu-ifup-virbr0", ifup, "755")
    write_privileged_file_if_changed("/etc/qemu-ifdown-virbr0", ifdown, "755")


def ensure_ssh_forwarding() -> None:
    write_privileged_file_if_changed(IP_FORWARD_SYSCTL_PATH, "net.ipv4.ip_forward=1\n")
    run_command(with_privileges(["sysctl", "-w", "net.ipv4.ip_forward=1"]))
    cleanup_pairs = (
        f"{VM_EXTERNAL_SSH_HOST_PORT}:{VM_EXTERNAL_MANAGEMENT_IP}",
        f"{VM_USER_SSH_HOST_PORT}:{VM_USER_MANAGEMENT_IP}",
        *LEGACY_SSH_FORWARDS,
    )
    script = f"""#!/bin/bash
set -eu
delete_rule() {{
  table="$1"; shift
  while iptables -t "$table" -C "$@" 2>/dev/null; do
    iptables -t "$table" -D "$@"
  done
}}
for pair in {" ".join(f'"{pair}"' for pair in cleanup_pairs)}; do
  host_port="${{pair%%:*}}"
  target_ip="${{pair#*:}}"
  delete_rule nat PREROUTING -p tcp --dport "$host_port" -j DNAT --to-destination "$target_ip:22"
  delete_rule nat OUTPUT -p tcp -m addrtype --dst-type LOCAL --dport "$host_port" -j DNAT --to-destination "$target_ip:22"
  delete_rule nat POSTROUTING -p tcp -d "$target_ip" --dport 22 -j SNAT --to-source {VM_MANAGEMENT_GATEWAY_IP}
  delete_rule filter FORWARD -p tcp -d "$target_ip" --dport 22 -m conntrack --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT
  delete_rule filter FORWARD -p tcp -s "$target_ip" --sport 22 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
done

socat TCP-LISTEN:{VM_EXTERNAL_SSH_HOST_PORT},reuseaddr,fork,bind=0.0.0.0 TCP:{VM_EXTERNAL_MANAGEMENT_IP}:22 &
external_pid="$!"
socat TCP-LISTEN:{VM_USER_SSH_HOST_PORT},reuseaddr,fork,bind=0.0.0.0 TCP:{VM_USER_MANAGEMENT_IP}:22 &
user_pid="$!"
trap 'kill "$external_pid" "$user_pid" 2>/dev/null || true' INT TERM
wait -n "$external_pid" "$user_pid"
"""
    unit = f"""[Unit]
Description=VPP NAT VM SSH forwarding
After=libvirtd.service network-online.target
Wants=libvirtd.service network-online.target

[Service]
Type=simple
ExecStart={PORT_FORWARD_SCRIPT_PATH}
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
"""
    write_privileged_file_if_changed(PORT_FORWARD_SCRIPT_PATH, script, "755")
    write_privileged_file_if_changed(PORT_FORWARD_UNIT_PATH, unit)
    run_command(with_privileges(["systemctl", "daemon-reload"]))
    run_command(with_privileges(["systemctl", "enable", "--now", "vpp-vm-ssh-forward.service"]))
    run_command(with_privileges(["systemctl", "restart", "vpp-vm-ssh-forward.service"]))


def ensure_vm_management_network() -> None:
    click.echo("=== VM management network ===")
    define_management_network()
    ensure_qemu_bridge_scripts()
    ensure_ssh_forwarding()
    click.echo(
        "  ✓ SSH: "
        f"{VM_EXTERNAL_SSH_HOST_PORT}->{VM_EXTERNAL_NAME}, "
        f"{VM_USER_SSH_HOST_PORT}->{VM_USER_NAME}"
    )

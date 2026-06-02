from __future__ import annotations

import subprocess
from pathlib import Path

import rich_click as click

from ..helpers import run_command, with_privileges
from .network import write_privileged_file_if_changed
from .spec import VmSpec, disk_image_path, seed_image_path, vm_host_mount_dir

LEGACY_UNITS = (
    "vpp-external-machine.service",
    "vpp-user-machine1.service",
    "vpp-user-machine2.service",
    "vpp-libvirt-port-forward.service",
)
LEGACY_FILES = (
    *(f"/etc/systemd/system/{unit}" for unit in LEGACY_UNITS),
    "/usr/local/sbin/vpp-libvirt-port-forward.sh",
)


def render_unit(project_root: Path, spec: VmSpec) -> str:
    memory = f"{spec.memory_mb}M"
    host_mount = vm_host_mount_dir(project_root, spec)
    return f"""[Unit]
Description=VPP NAT test VM: {spec.name}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
UMask=0000
ExecStartPre=/bin/mkdir -p /run/vpp /run/vpp/console
ExecStartPre=/bin/mkdir -p {spec.serial_log.parent}
ExecStartPre=/bin/rm -f {spec.vhost_socket} {spec.console_socket} {spec.monitor_socket}
ExecStart=/usr/bin/qemu-system-x86_64 \\
  -name {spec.name} \\
  -machine accel=kvm,mem-merge=off \\
  -m {spec.memory_mb} \\
  -object memory-backend-file,id=mem0,size={memory},mem-path=/dev/shm/{spec.unit}-mem,share=on \\
  -numa node,memdev=mem0 \\
  -smp {spec.cpus} \\
  -cpu host \\
  -enable-kvm \\
  -drive file={disk_image_path(project_root, spec)},format=qcow2,if=virtio \\
  -drive file={seed_image_path(project_root, spec)},if=virtio,format=raw,media=cdrom,readonly=on \\
  -netdev tap,id=net1,script=/etc/qemu-ifup-virbr0,downscript=/etc/qemu-ifdown-virbr0 \\
  -device virtio-net-pci,netdev=net1,mac={spec.management_mac} \\
  -chardev socket,id=char0,path={spec.vhost_socket},server=on,wait=off \\
  -netdev vhost-user,id=net0,chardev=char0 \\
  -device virtio-net-pci,netdev=net0,mac={spec.dataplane_mac} \\
  -virtfs local,path={host_mount},security_model=none,mount_tag=hostshare,id=hostshare \\
  -chardev socket,id=serial0,path={spec.console_socket},server=on,wait=off,logfile={spec.serial_log},logappend=off \\
  -serial chardev:serial0 \\
  -display none \\
  -monitor unix:{spec.monitor_socket},server=on,wait=off
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def install_unit(project_root: Path, spec: VmSpec) -> bool:
    vm_host_mount_dir(project_root, spec).mkdir(parents=True, exist_ok=True)
    unit_path = f"/etc/systemd/system/{spec.unit}.service"
    changed = write_privileged_file_if_changed(unit_path, render_unit(project_root, spec))
    click.echo(f"  ✓ unit {spec.unit}{' updated' if changed else ' unchanged'}")
    return changed


def unit_is_active(unit: str) -> bool:
    return subprocess.run(["systemctl", "is-active", "--quiet", unit], check=False).returncode == 0


def start_unit(spec: VmSpec, restart: bool) -> None:
    run_command(with_privileges(["systemctl", "enable", "--now", spec.unit]))
    if restart or not unit_is_active(spec.unit):
        run_command(with_privileges(["systemctl", "restart", spec.unit]))
        click.echo(f"  ✓ started {spec.name}: {spec.vhost_socket}")
    else:
        click.echo(f"  ✓ active {spec.name}: {spec.vhost_socket}")


def remove_legacy_units() -> None:
    click.echo("=== Legacy VM units ===")
    subprocess.run(
        with_privileges(["systemctl", "stop", *LEGACY_UNITS]),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    subprocess.run(
        with_privileges(["systemctl", "disable", *LEGACY_UNITS]),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    run_command(with_privileges(["rm", "-f", *LEGACY_FILES]))
    run_command(with_privileges(["systemctl", "daemon-reload"]))
    subprocess.run(
        with_privileges(["systemctl", "reset-failed", *LEGACY_UNITS]),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    click.echo("  ✓ old VM units removed")

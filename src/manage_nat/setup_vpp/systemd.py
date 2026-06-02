from __future__ import annotations

import subprocess

from ..helpers import run_command, with_privileges
from .files import write_privileged_file_if_changed
from .spec import SECONDARY, VppInstance

SECONDARY_UNIT_PATH = "/etc/systemd/system/vpp-secondary.service"


def secondary_unit() -> str:
    return f"""[Unit]
Description=vector packet processing engine secondary instance
After=network.target

[Service]
Type=simple
ExecStartPre=-/sbin/modprobe uio_pci_generic
ExecStartPre=/bin/mkdir -p {SECONDARY.run_dir}
ExecStart=/usr/bin/vpp -c {SECONDARY.startup_conf}
ExecStopPost=/bin/rm -f /dev/shm/{SECONDARY.api_prefix}-db /dev/shm/{SECONDARY.api_prefix}-global_vm /dev/shm/{SECONDARY.api_prefix}-vpe-api
Restart=always
Slice=vpp.slice

[Install]
WantedBy=multi-user.target
"""


def ensure_runtime_dirs(instances: tuple[VppInstance, ...]) -> None:
    paths = [str(instance.run_dir) for instance in instances]
    run_command(with_privileges(["mkdir", "-p", *paths, "/var/log/vpp"]))


def ensure_secondary_unit() -> bool:
    return write_privileged_file_if_changed(SECONDARY_UNIT_PATH, secondary_unit())


def service_is_available(service: str) -> bool:
    result = subprocess.run(
        ["systemctl", "list-unit-files", "--type=service", service],
        text=True,
        capture_output=True,
        check=False,
    )
    return service in result.stdout


def service_is_active(service: str) -> bool:
    result = subprocess.run(
        ["systemctl", "is-active", "--quiet", service],
        check=False,
    )
    return result.returncode == 0

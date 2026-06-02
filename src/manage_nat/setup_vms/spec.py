from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import (
    VM_BASE_IMAGE_NAME,
    VM_CONFIG_DIR,
    VM_EXTERNAL_CONSOLE_SOCKET_PATH,
    VM_EXTERNAL_DATAPLANE_GATEWAY,
    VM_EXTERNAL_DATAPLANE_IP_CIDR,
    VM_EXTERNAL_DATAPLANE_MAC,
    VM_EXTERNAL_DATAPLANE_PEER_CIDR,
    VM_EXTERNAL_MANAGEMENT_IP,
    VM_EXTERNAL_MANAGEMENT_MAC,
    VM_EXTERNAL_MONITOR_SOCKET_PATH,
    VM_EXTERNAL_NAME,
    VM_EXTERNAL_SERIAL_LOG_PATH,
    VM_EXTERNAL_SSH_HOST_PORT,
    VM_EXTERNAL_VHOST_SOCKET_PATH,
    VM_HOST_MOUNT_DIR,
    VM_STORAGE_DIR,
    VM_USER_CONSOLE_SOCKET_PATH,
    VM_USER_DATAPLANE_GATEWAY,
    VM_USER_DATAPLANE_IP_CIDR,
    VM_USER_DATAPLANE_MAC,
    VM_USER_DATAPLANE_PEER_CIDR,
    VM_USER_MANAGEMENT_IP,
    VM_USER_MANAGEMENT_MAC,
    VM_USER_MONITOR_SOCKET_PATH,
    VM_USER_NAME,
    VM_USER_SERIAL_LOG_PATH,
    VM_USER_SSH_HOST_PORT,
    VM_USER_VHOST_SOCKET_PATH,
)


@dataclass(frozen=True)
class VmSpec:
    name: str
    hostname: str
    unit: str
    memory_mb: int
    cpus: int
    vhost_socket: Path
    console_socket: Path
    monitor_socket: Path
    serial_log: Path
    dataplane_ip_cidr: str
    dataplane_gateway: str
    dataplane_peer_cidr: str
    dataplane_mac: str
    management_ip: str
    management_mac: str
    ssh_host_port: int


VM_SPECS = (
    VmSpec(
        name=VM_EXTERNAL_NAME,
        hostname="external-vm",
        unit="vpp-external-vm",
        memory_mb=2048,
        cpus=2,
        vhost_socket=VM_EXTERNAL_VHOST_SOCKET_PATH,
        console_socket=VM_EXTERNAL_CONSOLE_SOCKET_PATH,
        monitor_socket=VM_EXTERNAL_MONITOR_SOCKET_PATH,
        serial_log=VM_EXTERNAL_SERIAL_LOG_PATH,
        dataplane_ip_cidr=VM_EXTERNAL_DATAPLANE_IP_CIDR,
        dataplane_gateway=VM_EXTERNAL_DATAPLANE_GATEWAY,
        dataplane_peer_cidr=VM_EXTERNAL_DATAPLANE_PEER_CIDR,
        dataplane_mac=VM_EXTERNAL_DATAPLANE_MAC,
        management_ip=VM_EXTERNAL_MANAGEMENT_IP,
        management_mac=VM_EXTERNAL_MANAGEMENT_MAC,
        ssh_host_port=VM_EXTERNAL_SSH_HOST_PORT,
    ),
    VmSpec(
        name=VM_USER_NAME,
        hostname="user-vm",
        unit="vpp-user-vm",
        memory_mb=4096,
        cpus=2,
        vhost_socket=VM_USER_VHOST_SOCKET_PATH,
        console_socket=VM_USER_CONSOLE_SOCKET_PATH,
        monitor_socket=VM_USER_MONITOR_SOCKET_PATH,
        serial_log=VM_USER_SERIAL_LOG_PATH,
        dataplane_ip_cidr=VM_USER_DATAPLANE_IP_CIDR,
        dataplane_gateway=VM_USER_DATAPLANE_GATEWAY,
        dataplane_peer_cidr=VM_USER_DATAPLANE_PEER_CIDR,
        dataplane_mac=VM_USER_DATAPLANE_MAC,
        management_ip=VM_USER_MANAGEMENT_IP,
        management_mac=VM_USER_MANAGEMENT_MAC,
        ssh_host_port=VM_USER_SSH_HOST_PORT,
    ),
)


def project_path(project_root: Path, relative: Path) -> Path:
    return relative if relative.is_absolute() else project_root / relative


def base_image_path(project_root: Path) -> Path:
    return project_path(project_root, VM_STORAGE_DIR) / VM_BASE_IMAGE_NAME


def vm_storage_dir(project_root: Path, spec: VmSpec) -> Path:
    return project_path(project_root, VM_STORAGE_DIR) / spec.name


def vm_config_dir(project_root: Path, spec: VmSpec) -> Path:
    return project_path(project_root, VM_CONFIG_DIR) / spec.name


def vm_host_mount_dir(project_root: Path, spec: VmSpec) -> Path:
    return project_path(project_root, VM_HOST_MOUNT_DIR) / spec.name


def disk_image_path(project_root: Path, spec: VmSpec) -> Path:
    return vm_storage_dir(project_root, spec) / "disk.img"


def seed_image_path(project_root: Path, spec: VmSpec) -> Path:
    return vm_storage_dir(project_root, spec) / "seed.iso"

from __future__ import annotations

from pathlib import Path

TREX_DOWNLOAD_URL = "https://trex-tgn.cisco.com/trex/release/latest"
TREX_INSTALL_BASE_DIR = Path("/opt/trex")
TREX_SERVER_BINARY_NAME = "t-rex-64"
TREX_CONSOLE_BINARY_NAME = "trex-console"
TREX_SERVER_LINK_PATH = Path("/usr/local/bin") / TREX_SERVER_BINARY_NAME
TREX_CONSOLE_LINK_PATH = Path("/usr/local/bin") / TREX_CONSOLE_BINARY_NAME


# ============================================================================
# Machine-local VPP CPU pinning
#
# When moving this test rig to another machine, this is the block to adjust:
# - VPP_CPU_MAIN_CORE is written as `main-core`.
# - VPP worker cores are allocated from VPP_CPU_MAIN_CORE + 1 upward.
# - VPP_CPU_MAX_WORKERS limits how many consecutive worker cores may be used.
# ============================================================================
VPP_CPU_MAIN_CORE = 7

# Fixed number of memif inside/outside pairs in runtime topology.
VPP_FIXED_MEMIF_PAIRS = 5
VPP_CPU_MAX_WORKERS = VPP_FIXED_MEMIF_PAIRS
VPP_FAILOVER_WORKERS = 1


# memif ring-size for `create interface memif ... ring-size <size> ...`
MEMIF_RING_SIZE_DEFAULT = 16384


# ============================================================================
# VM topology
# ============================================================================
VM_STORAGE_DIR = Path("virtual_machines/storage")
VM_CONFIG_DIR = Path("virtual_machines/configs")
VM_HOST_MOUNT_DIR = Path("virtual_machines/host_mounts")
VM_BASE_IMAGE_NAME = "noble-server-cloudimg-amd64.img"
VM_MIN_DISK_SIZE = "15G"
VM_USER_NAME = "user_vm"
VM_EXTERNAL_NAME = "external_vm"

VM_USER_VHOST_SOCKET_PATH = Path("/run/vpp/vhost-user.sock")
VM_EXTERNAL_VHOST_SOCKET_PATH = Path("/run/vpp/vhost-external.sock")
VM_USER_CONSOLE_SOCKET_PATH = Path("/run/vpp/console/vpp-user-vm.sock")
VM_EXTERNAL_CONSOLE_SOCKET_PATH = Path("/run/vpp/console/vpp-external-vm.sock")
VM_USER_MONITOR_SOCKET_PATH = Path("/run/vpp/console/vpp-user-vm-monitor.sock")
VM_EXTERNAL_MONITOR_SOCKET_PATH = Path("/run/vpp/console/vpp-external-vm-monitor.sock")
VM_USER_SERIAL_LOG_PATH = Path("/var/log/vpp/vpp-user-vm-serial.log")
VM_EXTERNAL_SERIAL_LOG_PATH = Path("/var/log/vpp/vpp-external-vm-serial.log")

VPP_USER_VHOST_INTERFACE_NAME = "VirtualEthernet0/0/1"
VPP_EXTERNAL_VHOST_INTERFACE_NAME = "VirtualEthernet0/0/0"
VPP_USER_VHOST_INTERFACE_INSTANCE = 1
VPP_EXTERNAL_VHOST_INTERFACE_INSTANCE = 0

VM_USER_DATAPLANE_IP_CIDR = "10.8.1.2/24"
VM_USER_DATAPLANE_GATEWAY = "10.8.1.1"
VM_USER_DATAPLANE_PEER_CIDR = "10.8.0.0/24"
VM_EXTERNAL_DATAPLANE_IP_CIDR = "10.8.0.2/24"
VM_EXTERNAL_DATAPLANE_GATEWAY = "10.8.0.1"
VM_EXTERNAL_DATAPLANE_PEER_CIDR = "10.8.1.0/24"

VM_MANAGEMENT_BRIDGE_NAME = "virbr0"
VM_MANAGEMENT_NETWORK_NAME = "default"
VM_MANAGEMENT_GATEWAY_IP = "10.8.2.1"
VM_MANAGEMENT_NETMASK = "255.255.255.0"
VM_USER_MANAGEMENT_IP = "10.8.2.11"
VM_EXTERNAL_MANAGEMENT_IP = "10.8.2.10"
VM_USER_SSH_HOST_PORT = 8122
VM_EXTERNAL_SSH_HOST_PORT = 8022

VM_USER_DATAPLANE_MAC = "52:54:00:00:01:02"
VM_EXTERNAL_DATAPLANE_MAC = "52:54:00:00:01:01"
VM_USER_MANAGEMENT_MAC = "52:54:00:10:02:02"
VM_EXTERNAL_MANAGEMENT_MAC = "52:54:00:10:02:01"


# ============================================================================
# VPP active/standby instances
# ============================================================================
VPP_PRIMARY_NAME = "vpp"
VPP_SECONDARY_NAME = "vpp-secondary"
VPP_PRIMARY_SERVICE_NAME = "vpp.service"
VPP_SECONDARY_SERVICE_NAME = "vpp-secondary.service"
VPP_HA_CTLD_SERVICE_NAME = "vpp-ha-ctld.service"
VPP_PRIMARY_STARTUP_CONF_PATH = Path("/etc/vpp/startup.conf")
VPP_SECONDARY_STARTUP_CONF_PATH = Path("/etc/vpp/startup-secondary.conf")
VPP_PRIMARY_RUN_DIR = Path("/run/vpp")
VPP_SECONDARY_RUN_DIR = Path("/run/vpp-secondary")
VPP_PRIMARY_LOG_PATH = Path("/var/log/vpp/vpp.log")
VPP_SECONDARY_LOG_PATH = Path("/var/log/vpp/vpp-secondary.log")
VPP_PRIMARY_API_PREFIX = "vpp"
VPP_SECONDARY_API_PREFIX = "vpp-secondary"

VPP_INSIDE_INTERFACE_NAME = VPP_USER_VHOST_INTERFACE_NAME
VPP_OUTSIDE_INTERFACE_NAME = VPP_EXTERNAL_VHOST_INTERFACE_NAME
VPP_INSIDE_INTERFACE_IP_CIDR = VM_USER_DATAPLANE_GATEWAY + "/24"
VPP_OUTSIDE_INTERFACE_IP_CIDR = VM_EXTERNAL_DATAPLANE_GATEWAY + "/24"

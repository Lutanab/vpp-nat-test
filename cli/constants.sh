
# Определяем корень проекта
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Константы для физического интерфейса eth0 (оставлены для справки, но не используются в новой архитектуре)
# export ETH0_INTERFACE="enp2s0"
# export ETH0_IP="198.162.0.10"
# export ETH0_NETMASK="255.255.255.0"
# export ETH0_CIDR="24"
# export ETH0_GATEWAY="198.162.0.1"

# DEPRECATED: Константы для bridge интерфейса br0 (больше не используется)
# export BR0_INTERFACE="br0"
# export BR0_IP="10.8.0.1"
# export BR0_NETMASK="255.255.255.0"
# export BR0_CIDR="24"

# DEPRECATED: Константы для tap-интерфейсов (больше не используются)
# export TAP_INTERFACES=("tap0")

export VM_STORAGE_PATH="${PROJECT_ROOT}/virtual_machines/storage"
export VM_BASE_IMAGE_PATH="${VM_STORAGE_PATH}/noble-server-cloudimg-amd64.img"
# Минимальный размер виртуального диска для каждой VM (увеличивается автоматически, без shrink)
export VM_MIN_DISK_SIZE="15G"
export USER_MACHINES_DIR=(
    "user_vm_1"
    "user_vm_2"
)
export VM_CONFIG_PATH="${PROJECT_ROOT}/virtual_machines/configs"

# Константы для vhost-user сокетов
export VHOST_SOCKETS=(
    "/var/run/vpp/vhost1.sock"
    "/var/run/vpp/vhost2.sock"
)

export USER_MACHINES_IP=("10.8.1.2" "10.8.1.3")
export USER_MACHINES_MAC=("52:54:00:00:01:02" "52:54:00:00:01:03")
export USER_MACHINES_SYSTEMD_UNIT_NAME=("vpp-user-machine1" "vpp-user-machine2")

# Глобальная переменная для хранения имен vhost-user интерфейсов в VPP
export VHOST_USER_VPP_IFACES=()

# Константы для внешней VM (external VM) - подключение через vhost-user
export EXTERNAL_VM_DIR="external_vm"
export EXTERNAL_VHOST_SOCKET="/var/run/vpp/vhost0.sock"
export EXTERNAL_VM_IP="10.8.0.2"
export EXTERNAL_VM_MAC="52:54:00:00:01:01"
export EXTERNAL_VM_SYSTEMD_UNIT="vpp-external-machine"
# Глобальная переменная для имени vhost интерфейса external VM в VPP
export EXTERNAL_VHOST_VPP_IFACE=""

# Константы для libvirt management сети (интерфейс ens4 в VM)
export LIBVIRT_NETWORK_NAME="default"
export LIBVIRT_BRIDGE_NAME="virbr0"
export LIBVIRT_GATEWAY_IP="10.8.2.1"
export LIBVIRT_NETMASK="255.255.255.0"
export LIBVIRT_DHCP_RANGE_START="10.8.2.2"
export LIBVIRT_DHCP_RANGE_END="10.8.2.254"

# Фиксированные leases для management-интерфейсов VM
export EXTERNAL_VM_LIBVIRT_IP="10.8.2.10"
export EXTERNAL_VM_LIBVIRT_MAC="52:54:00:10:02:01"
export USER_MACHINES_LIBVIRT_IP=("10.8.2.11" "10.8.2.12")
export USER_MACHINES_LIBVIRT_MAC=("52:54:00:10:02:02" "52:54:00:10:02:03")

# SSH host-port forwarding: host:<port> -> vm:22
export EXTERNAL_VM_SSH_HOST_PORT="8022"
export USER_MACHINES_SSH_HOST_PORT=("8122" "8222")

# Константы для veth-пары (DEPRECATED - заменено на external VM vhost)
# export VETH_HOST_IN_IF_NAME="vpp0"
# export VETH_HOST_OUT_IF_NAME="vpp0-out"
# export VETH_VPP_IF_NAME=""

# Константы для VPP bridge domain и BVI
export VPP_BRIDGE_DOMAIN_ID="10"
export VPP_BVI_INTERFACE=""
export VPP_BVI_IP="10.8.1.1"
export VPP_BVI_CIDR="24"

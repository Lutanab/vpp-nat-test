
# Константы для физического интерфейса eth0
export ETH0_INTERFACE="enp2s0"
export ETH0_IP="198.162.0.10"
export ETH0_NETMASK="255.255.255.0"
export ETH0_CIDR="24"
export ETH0_GATEWAY="198.162.0.1"

# Константы для bridge интерфейса br0
export BR0_INTERFACE="br0"
export BR0_IP="10.8.0.1"
export BR0_NETMASK="255.255.255.0"
export BR0_CIDR="24"

# Константы для tap-интерфейсов
export TAP_INTERFACES=("tap0")

# Константы для vhost-user сокетов
export VHOST_SOCKETS=(
    "/var/run/vpp/vhost1.sock"
    "/var/run/vpp/vhost2.sock"
)

# Глобальная переменная для хранения имен vhost-user интерфейсов в VPP
export VHOST_USER_VPP_IFACES=()

# Константы для veth-пары
export VETH_VPP_IF_NAME="vpp0"
export VETH_HOST_IF_NAME="host0"
# Имя veth интерфейса внутри VPP
export VETH_VPP_HOST_IF_NAME="host-vpp0"

# Константы для VPP bridge domain и BVI
export VPP_BRIDGE_DOMAIN_ID="10"
export VPP_BVI_INTERFACE=""
export VPP_BVI_IP="10.8.1.1"
export VPP_BVI_CIDR="24"


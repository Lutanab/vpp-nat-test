#!/bin/bash

set -e

# Подключаем вспомогательные функции
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/shell_helpers/vpp_helpers.sh"

ensure_vpp_service
create_veth_pair_and_connect_to_vpp

#echo "=== Проверка и создание vhost-user сокетов ==="
#VHOST_SOCKETS=(
#    "/var/run/vpp/vhost1.sock"
#    "/var/run/vpp/vhost2.sock"
#)
#for socket in "${VHOST_SOCKETS[@]}"; do
#    check_and_create_vhost_socket "$socket"
#    echo ""
#done
#echo "=== Все vhost-user сокеты готовы ==="
#echo ""

# Пример использования (закомментирован, так как это будет частью другой функции)
# qemu-system-x86_64 \
#   -m 2048 \
#   -cpu host \
#   -enable-kvm \
#   -drive file=disk.img,format=qcow2 \
#   \
#   -chardev socket,id=char0,path=/var/run/vpp/vhost1.sock \
#   -netdev vhost-user,id=net0,chardev=char0 \
#   -device virtio-net-pci,netdev=net0,mac=52:54:00:00:01:01
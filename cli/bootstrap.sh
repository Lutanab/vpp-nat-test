#!/bin/bash

set -e

# Подключаем вспомогательные функции
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/shell_helpers/vpp_helpers.sh"
source "${SCRIPT_DIR}prepare_network.sh"

# Функция для проверки и создания всех vhost-user сокетов
setup_vhost_sockets() {
    echo "=== Проверка и создание vhost-user сокетов ==="
    
    # Проверяем, что VPP запущен
    if ! vppctl show version &> /dev/null; then
        echo "  ✗ Ошибка: VPP не запущен или недоступен"
        exit 1
    fi
    
    # Создаем каждый vhost-user сокет
    for socket_path in "${VHOST_SOCKETS[@]}"; do
        check_and_create_vhost_socket "$socket_path"
        echo ""
    done
    
    echo "  ✓ Все vhost-user сокеты готовы"
    echo ""
}

# Основная функция bootstrap
bootstrap_main() {
    setup_vpp_service
    create_veth_pair_and_connect_to_vpp
    prepare_network_main
    setup_vhost_sockets
}

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
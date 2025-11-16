#!/bin/bash

# Функция для идемпотентного создания bridge и назначения IP
create_br0_bridge() {
    local bridge_name="$1"
    
    if [ -z "$bridge_name" ]; then
        echo "  ✗ Ошибка: не указано имя bridge"
        exit 1
    fi
    
    echo "=== Создание bridge интерфейса $bridge_name ==="
    
    # Проверяем, существует ли bridge
    if ip link show "$bridge_name" &>/dev/null; then
        echo "  Интерфейс $bridge_name уже существует"
        
        # Проверяем, что это действительно bridge
        if [ ! -d "/sys/class/net/$bridge_name/bridge" ]; then
            # Если интерфейс существует, но не является bridge, удаляем его
            echo "  Интерфейс $bridge_name существует, но не является bridge. Удаление..."
            sudo ip link set "$bridge_name" down 2>/dev/null || true
            sudo ip link delete "$bridge_name" 2>/dev/null || true
            echo "  ✓ Старый интерфейс удален"
        else
            echo "  ✓ Bridge $bridge_name уже создан"
        fi
    fi
    
    # Создаем bridge, если его нет
    if ! ip link show "$bridge_name" &>/dev/null; then
        echo "  Создание bridge $bridge_name..."
        if sudo ip link add name "$bridge_name" type bridge; then
            echo "  ✓ Bridge $bridge_name создан"
        else
            echo "  ✗ Ошибка при создании bridge $bridge_name"
            exit 1
        fi
    fi
    
    # Проверяем и назначаем IP адрес
    local current_ip=$(ip addr show "$bridge_name" 2>/dev/null | grep -oP 'inet \K[\d.]+' || echo "")
    
    if [ -z "$current_ip" ] || [ "$current_ip" != "$BR0_IP" ]; then
        echo "  Назначение IP адреса $BR0_IP/$BR0_CIDR на $bridge_name..."
        
        # Удаляем старый IP, если он есть
        if [ -n "$current_ip" ]; then
            sudo ip addr del "$current_ip/$BR0_CIDR" dev "$bridge_name" 2>/dev/null || true
        fi
        
        # Назначаем новый IP
        if sudo ip addr add "$BR0_IP/$BR0_CIDR" dev "$bridge_name"; then
            echo "  ✓ IP адрес $BR0_IP/$BR0_CIDR назначен на $bridge_name"
        else
            echo "  ✗ Ошибка при назначении IP адреса на $bridge_name"
            exit 1
        fi
    else
        echo "  ✓ IP адрес $BR0_IP/$BR0_CIDR уже назначен на $bridge_name"
    fi
    
    echo "  ✓ Bridge $bridge_name создан, IP адрес назначен (интерфейс в состоянии DOWN)"
    echo ""
}

# Функция для идемпотентного создания одного tap-интерфейса
create_tap_interface() {
    local tap_name="$1"
    
    if [ -z "$tap_name" ]; then
        echo "  ✗ Ошибка: не указано имя tap-интерфейса"
        exit 1
    fi
    
    echo "  Создание tap-интерфейса $tap_name..."
    
    # Проверяем, существует ли tap-интерфейс
    if ip link show "$tap_name" &>/dev/null; then
        # Проверяем, что это действительно tap-интерфейс (tun_flags - это файл, а не директория)
        if [ ! -f "/sys/class/net/$tap_name/tun_flags" ]; then
            echo "  Интерфейс $tap_name существует, но не является tap. Удаление..."
            sudo ip link set "$tap_name" down 2>/dev/null || true
            sudo ip link delete "$tap_name" 2>/dev/null || true
            echo "  ✓ Старый интерфейс удален"
        else
            echo "  ✓ Tap-интерфейс $tap_name уже существует"
        fi
    fi
    
    # Создаем tap-интерфейс, если его нет
    if ! ip link show "$tap_name" &>/dev/null; then
        echo "  Создание tap-интерфейса $tap_name..."
        if sudo ip tuntap add mode tap name "$tap_name"; then
            echo "  ✓ Tap-интерфейс $tap_name создан (интерфейс в состоянии DOWN)"
        else
            echo "  ✗ Ошибка при создании tap-интерфейса $tap_name"
            exit 1
        fi
    else
        echo "  ✓ Tap-интерфейс $tap_name создан (интерфейс в состоянии DOWN)"
    fi
}

# Функция для идемпотентного подключения tap-интерфейса к bridge
connect_tap_to_bridge() {
    local tap_name="$1"
    local bridge_name="$2"
    
    if [ -z "$tap_name" ]; then
        echo "  ✗ Ошибка: не указано имя tap-интерфейса"
        exit 1
    fi
    
    if [ -z "$bridge_name" ]; then
        echo "  ✗ Ошибка: не указано имя bridge"
        exit 1
    fi
    
    # Проверяем, что tap-интерфейс существует
    if ! ip link show "$tap_name" &>/dev/null; then
        echo "  ✗ Ошибка: tap-интерфейс $tap_name не существует"
        exit 1
    fi
    
    # Проверяем, что bridge существует
    if ! ip link show "$bridge_name" &>/dev/null; then
        echo "  ✗ Ошибка: bridge $bridge_name не существует"
        exit 1
    fi
    
    # Проверяем, подключен ли tap к bridge
    local master=$(ip link show "$tap_name" 2>/dev/null | grep -oP 'master \K\w+' || echo "")
    
    if [ -z "$master" ] || [ "$master" != "$bridge_name" ]; then
        echo "  Подключение $tap_name к bridge $bridge_name..."
        
        # Отключаем от другого bridge, если подключен
        if [ -n "$master" ] && [ "$master" != "$bridge_name" ]; then
            sudo ip link set "$tap_name" nomaster 2>/dev/null || true
        fi
        
        # Подключаем к нужному bridge
        if sudo ip link set "$tap_name" master "$bridge_name"; then
            echo "  ✓ Tap-интерфейс $tap_name подключен к bridge $bridge_name"
        else
            echo "  ✗ Ошибка при подключении $tap_name к bridge $bridge_name"
            exit 1
        fi
    else
        echo "  ✓ Tap-интерфейс $tap_name уже подключен к bridge $bridge_name"
    fi
}


# Функция для настройки NAT через iptables
setup_nat() {
    local out_interface="$1"
    
    if [ -z "$out_interface" ]; then
        echo "  ✗ Ошибка: не указан выходной интерфейс для NAT"
        exit 1
    fi
    
    echo "=== Настройка NAT для интерфейса $out_interface ==="
    
    # Проверяем, существует ли интерфейс
    if ! ip link show "$out_interface" &>/dev/null; then
        echo "  ✗ Ошибка: интерфейс $out_interface не существует"
        exit 1
    fi
    
    # Проверяем, существует ли уже правило MASQUERADE
    if sudo iptables -t nat -C POSTROUTING -o "$out_interface" -j MASQUERADE 2>/dev/null; then
        echo "  ✓ Правило NAT MASQUERADE для $out_interface уже существует"
    else
        echo "  Добавление правила NAT MASQUERADE для $out_interface..."
        if sudo iptables -t nat -A POSTROUTING -o "$out_interface" -j MASQUERADE; then
            echo "  ✓ Правило NAT MASQUERADE для $out_interface добавлено"
        else
            echo "  ✗ Ошибка при добавлении правила NAT"
            exit 1
        fi
    fi
    
    echo ""
}

# Функция для поднятия интерфейсов в состояние UP
set_host_ifaces_up() {
    if [ $# -eq 0 ]; then
        echo "  ✗ Ошибка: не указаны имена интерфейсов для поднятия"
        exit 1
    fi
    
    echo "=== Поднятие интерфейсов в состояние UP ==="
    
    for iface_name in "$@"; do
        if [ -z "$iface_name" ]; then
            continue
        fi
        
        # Проверяем, существует ли интерфейс
        if ! ip link show "$iface_name" &>/dev/null; then
            echo "  ✗ Ошибка: интерфейс $iface_name не существует"
            exit 1
        fi
        
        # Проверяем, поднят ли интерфейс
        if ! ip link show "$iface_name" | grep -q "state UP"; then
            echo "  Поднятие интерфейса $iface_name..."
            if sudo ip link set "$iface_name" up; then
                echo "  ✓ Интерфейс $iface_name поднят"
            else
                echo "  ✗ Ошибка при поднятии интерфейса $iface_name"
                exit 1
            fi
        else
            echo "  ✓ Интерфейс $iface_name уже поднят"
        fi
    done
    
    echo "  ✓ Все интерфейсы подняты"
    echo ""
}

# Функция для проверки и создания всех vhost-user сокетов
setup_vhost_sockets() {
    echo "=== Проверка и создание vhost-user сокетов ==="
    
    # Создаем каждый vhost-user сокет
    for socket_path in "${VHOST_SOCKETS[@]}"; do
        check_and_create_vhost_socket "$socket_path"
        echo ""
    done
    
    echo "  ✓ Все vhost-user сокеты готовы"
    echo ""
}

# Функция подготовки сети хоста
prepare_host_network() {
    echo "=========================================="
    echo "Подготовка сетевой топологии хоста"
    echo "=========================================="
    echo ""
    
    sudo sysctl -w net.ipv4.ip_forward=1
    
    # Создание bridge
    create_br0_bridge "$BR0_INTERFACE"
    
    # Создание tap-интерфейсов
    echo "=== Создание tap-интерфейсов ==="
    for tap_name in "${TAP_INTERFACES[@]}"; do
        create_tap_interface "$tap_name"
    done
    echo ""
    
    # Подключение tap-интерфейсов к bridge
    echo "=== Подключение tap-интерфейсов к bridge $BR0_INTERFACE ==="
    for tap_name in "${TAP_INTERFACES[@]}"; do
        connect_tap_to_bridge "$tap_name" "$BR0_INTERFACE"
    done
    echo ""
    
    # Настройка NAT
    setup_nat "$ETH0_INTERFACE"
    
    echo "=========================================="
    echo "✓ Подготовка сетевой топологии хоста завершена"
    echo "=========================================="
}

set_ifaces_up() {
    echo "=========================================="
    echo "Финальное поднятие интерфейсов"
    echo "=========================================="
    echo ""

    # Поднимаем хостовые интерфейсы
    set_host_ifaces_up "$BR0_INTERFACE" "$VETH_VPP_IF_NAME" "$VETH_HOST_IF_NAME" "${TAP_INTERFACES[@]}"

    # Поднимаем интерфейсы внутри VPP
    local vpp_ifaces=()
    vpp_ifaces+=("$VETH_VPP_HOST_IF_NAME")
    vpp_ifaces+=("$VPP_BVI_INTERFACE")
    vpp_ifaces+=("${VHOST_USER_VPP_IFACES[@]}")

    set_vpp_ifaces_up "${vpp_ifaces[@]}"
}

setup_network_main() {
    setup_vpp_service
    setup_vhost_sockets
    create_veth_pair_and_connect_to_vpp "$VETH_VPP_IF_NAME" "$VETH_HOST_IF_NAME" "$VETH_VPP_HOST_IF_NAME"
    prepare_host_network
    #echo "${VHOST_USER_VPP_IFACES[@]}"
    prepare_vpp_network
    set_ifaces_up
}


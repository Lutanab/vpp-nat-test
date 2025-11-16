#!/bin/bash

# Функция для проверки, что VPP запущен и доступен (без перезапуска)
ensure_vpp_service() {
    local service_name="vpp"
    
    # Проверяем существование сервиса
    if ! systemctl list-unit-files --type=service | grep -qE "^${service_name}\.service"; then
        echo "Ошибка: Сервис ${service_name}.service не найден."
        echo "Убедитесь, что VPP установлен и сервис настроен."
        exit 1
    fi
    
    # Проверяем, что сервис активен
    if ! systemctl is-active --quiet "${service_name}.service"; then
        echo "Ошибка: Сервис VPP не запущен."
        echo "Запустите VPP перед выполнением команды."
        exit 1
    fi
    
    # Проверяем доступность VPP через vppctl
    if ! command -v vppctl &> /dev/null; then
        echo "Ошибка: vppctl не найден. Убедитесь, что VPP установлен и доступен в PATH."
        exit 1
    fi
    
    if ! vppctl show version &> /dev/null; then
        echo "Ошибка: VPP не доступен через vppctl."
        exit 1
    fi
}

# Функция для проверки и управления сервисом VPP
setup_vpp_service() {
    local service_name="vpp"
    
    echo "=== Проверка сервиса VPP ==="
    
    # Проверяем существование сервиса
    if ! systemctl list-unit-files --type=service | grep -qE "^${service_name}\.service"; then
        echo "Ошибка: Сервис ${service_name}.service не найден."
        echo "Убедитесь, что VPP установлен и сервис настроен."
        exit 1
    fi
    
    echo "  Сервис ${service_name}.service найден"
    
    # Проверяем статус сервиса
    local service_status=$(systemctl is-active "${service_name}.service" 2>/dev/null || echo "inactive")
    
    if [ "$service_status" = "active" ]; then
        echo "  Сервис VPP запущен, выполняется перезапуск..."
        if sudo systemctl restart "${service_name}.service"; then
            echo "  ✓ Сервис VPP успешно перезапущен"
        else
            echo "  ✗ Ошибка при перезапуске сервиса VPP"
            exit 1
        fi
    else
        echo "  Сервис VPP не запущен, выполняется запуск..."
        if sudo systemctl start "${service_name}.service"; then
            echo "  ✓ Сервис VPP успешно запущен"
        else
            echo "  ✗ Ошибка при запуске сервиса VPP"
            exit 1
        fi
    fi
    
    # Ждем немного, чтобы сервис полностью запустился
    sleep 3
    
    # Используем ensure_vpp_service для финальной проверки
    ensure_vpp_service
    echo "  ✓ Сервис VPP активен и готов к работе"
    echo ""
}

# Функция для создания veth-пары и подключения её к VPP
create_veth_pair_and_connect_to_vpp() {
    local vpp_if_name="$1"
    local host_if_name="$2"
    local vpp_host_if_name="$3"
    
    if [ -z "$vpp_if_name" ] || [ -z "$host_if_name" ] || [ -z "$vpp_host_if_name" ]; then
        echo "  ✗ Ошибка: не указаны все необходимые имена интерфейсов"
        echo "  Использование: create_veth_pair_and_connect_to_vpp <vpp_if_name> <host_if_name> <vpp_host_if_name>"
        exit 1
    fi
    
    echo "=== Создание veth-пары и подключение к VPP ==="
    echo "  VPP интерфейс: $vpp_if_name"
    echo "  Host интерфейс: $host_if_name"
    echo "  VPP host-interface: $vpp_host_if_name"

    # Удаляем существующую veth-пару, если она есть
    if ip link show "$vpp_if_name" &>/dev/null || ip link show "$host_if_name" &>/dev/null; then
        echo "  Удаление существующей veth-пары..."
        sudo ip link delete "$vpp_if_name" 2>/dev/null || sudo ip link delete "$host_if_name" 2>/dev/null || true
        echo "  ✓ Старая veth-пара удалена"
    fi
    
    # Создаем новую veth-пару
    echo "  Создание veth-пары: $vpp_if_name <-> $host_if_name"
    if sudo ip link add "$vpp_if_name" type veth peer name "$host_if_name"; then
        echo "  ✓ Veth-пара создана (интерфейсы в состоянии DOWN)"
    else
        echo "  ✗ Ошибка при создании veth-пары"
        exit 1
    fi
    
    # Создаем host-interface в VPP
    echo "  Создание host-interface в VPP: $vpp_host_if_name"
    local output
    if output=$(vppctl create host-interface name "$vpp_if_name" 2>&1); then
        echo "  ✓ Host-interface создан: $output"
    else
        echo "  ✗ Ошибка при создании host-interface: $output"
        exit 1
    fi
    
    # Устанавливаем IP адрес на интерфейс внутри VPP
    echo "  Установка IP адреса 10.8.0.2/24 на интерфейс $vpp_host_if_name"
    if vppctl set interface ip address "$vpp_host_if_name" 10.8.0.2/24 2>&1; then
        echo "  ✓ IP адрес установлен"
    else
        echo "  ✗ Ошибка при установке IP адреса"
        exit 1
    fi
    
    echo "  ✓ Veth-пара создана и подключена к VPP (интерфейс в VPP настроен с IP 10.8.0.2/24, состояние DOWN)"
    echo ""
}

# Функция для отключения libvirt и удаления virbr0
disable_libvirt_networking() {
    echo "=== Отключение libvirt networking ==="
    
    # Останавливаем libvirtd
    if systemctl is-active --quiet libvirtd 2>/dev/null; then
        echo "  Остановка libvirtd..."
        sudo systemctl stop libvirtd
        echo "  ✓ libvirtd остановлен"
    fi
    
    # Отключаем автозапуск
    if systemctl is-enabled --quiet libvirtd 2>/dev/null; then
        echo "  Отключение автозапуска libvirtd..."
        sudo systemctl disable libvirtd
        echo "  ✓ Автозапуск libvirtd отключен"
    fi
    
    # Удаляем сеть по умолчанию
    if sudo virsh net-list --all 2>/dev/null | grep -q "default"; then
        echo "  Удаление сети default..."
        sudo virsh net-destroy default 2>/dev/null || true
        sudo virsh net-undefine default 2>/dev/null || true
        echo "  ✓ Сеть default удалена"
    fi
    
    # Удаляем virbr0 интерфейс
    if ip link show virbr0 &>/dev/null; then
        echo "  Удаление интерфейса virbr0..."
        sudo ip link set virbr0 down 2>/dev/null || true
        sudo ip link delete virbr0 2>/dev/null || true
        echo "  ✓ Интерфейс virbr0 удален"
    fi
    
    echo "  ✓ Libvirt networking отключен"
    echo ""
}

# Функция для проверки и создания vhost-user сокета
check_and_create_vhost_socket() {
    local socket_path=$1
    local socket_name=$(basename "$socket_path")
    
    echo "Проверка vhost-user сокета: $socket_path"
    
    # Проверяем, существует ли сокет в файловой системе
    local socket_exists=false
    if [ -S "$socket_path" ]; then
        echo "  Сокет $socket_path существует в файловой системе"
        socket_exists=true
    fi
    
    # Проверяем, существует ли интерфейс в VPP
    # Ищем сокет в выводе команды show vhost-user
    local vpp_interface_exists=false
    local vhost_output=$(vppctl show vhost-user 2>/dev/null || echo "")
    if echo "$vhost_output" | grep -q "$socket_path"; then
        echo "  Интерфейс для $socket_path уже существует в VPP"
        vpp_interface_exists=true
    fi
    
    # Если сокет или интерфейс не существуют, создаем
    if [ "$socket_exists" = false ] || [ "$vpp_interface_exists" = false ]; then
        echo "  Создание vhost-user интерфейса для $socket_path..."
        
        # Убеждаемся, что директория существует
        local socket_dir=$(dirname "$socket_path")
        if [ ! -d "$socket_dir" ]; then
            echo "  Создание директории $socket_dir"
            sudo mkdir -p "$socket_dir"
            sudo chmod 755 "$socket_dir"
        fi
        
        # Удаляем старый сокет, если он существует (но не используется VPP)
        if [ -S "$socket_path" ] && [ "$vpp_interface_exists" = false ]; then
            echo "  Удаление старого сокета $socket_path"
            sudo rm -f "$socket_path"
        fi
        
        # Создаем vhost-user интерфейс через vppctl (VPP создаст сокет в режиме server)
        local output
        if output=$(vppctl create vhost-user socket "$socket_path" server 2>&1); then
            output=${output//$'\r'/}
            echo "  ✓ Vhost-user интерфейс создан: $output"
            VHOST_USER_VPP_IFACES+=("$output")
            
            # Устанавливаем права на сокет для доступа QEMU
            if [ -S "$socket_path" ]; then
                sudo chmod 666 "$socket_path"
                echo "  ✓ Права на сокет установлены"
            fi
        else
            echo "  ✗ Ошибка при создании vhost-user интерфейса: $output"
            exit 1
        fi
    else
        echo "  ✓ Vhost-user сокет $socket_path уже настроен"
    fi
}

# Функция для поднятия интерфейсов внутри VPP
set_vpp_ifaces_up() {
    if [ $# -eq 0 ]; then
        echo "  ✗ Ошибка: не указаны VPP интерфейсы для поднятия"
        exit 1
    fi

    echo "=== Поднятие VPP интерфейсов в состояние UP ==="

    ensure_vpp_service

    for vpp_iface in "$@"; do
        if [ -z "$vpp_iface" ]; then
            continue
        fi

        local iface_info
        if ! iface_info=$(vppctl show interface "$vpp_iface" 2>&1); then
            echo "  ✗ Ошибка: интерфейс $vpp_iface не найден в VPP"
            echo "    $iface_info"
            exit 1
        fi

        if echo "$iface_info" | grep -q "state up"; then
            echo "  ✓ Интерфейс $vpp_iface уже поднят"
            continue
        fi

        echo "  Поднятие интерфейса $vpp_iface..."
        if vppctl set interface state "$vpp_iface" up 2>&1; then
            echo "  ✓ Интерфейс $vpp_iface поднят"
        else
            echo "  ✗ Ошибка при поднятии интерфейса $vpp_iface"
            exit 1
        fi
    done

    echo "  ✓ Все указанные VPP интерфейсы подняты"
    echo ""
}

# Функция для подготовки VPP bridge сети
prepare_vpp_network() {
    echo "=== Подготовка VPP bridge сети ==="

    # Создаем bridge-domain
    echo "  Создание bridge-domain $VPP_BRIDGE_DOMAIN_ID"
    local bd_output
    if bd_output=$(vppctl create bridge-domain "$VPP_BRIDGE_DOMAIN_ID" 2>&1); then
        bd_output=${bd_output//$'\r'/}
        echo "  ✓ Bridge-domain $VPP_BRIDGE_DOMAIN_ID создан"
    else
        echo "  ✗ Ошибка при создании bridge-domain: $bd_output"
        exit 1
    fi

    # Присоединяем все vhost-user интерфейсы к bridge-domain
    if [ ${#VHOST_USER_VPP_IFACES[@]} -eq 0 ]; then
        echo "  ⚠ Нет vhost-user интерфейсов для присоединения к bridge-domain"
    else
        echo "  Присоединение vhost-user интерфейсов к bridge-domain $VPP_BRIDGE_DOMAIN_ID"
        for vhost_if in "${VHOST_USER_VPP_IFACES[@]}"; do
            if [ -z "$vhost_if" ]; then
                continue
            fi
            echo "    Присоединение $vhost_if к bridge-domain..."
            if vppctl set interface l2 bridge "$vhost_if" "$VPP_BRIDGE_DOMAIN_ID" 2>&1; then
                echo "    ✓ Интерфейс $vhost_if присоединен"
            else
                echo "    ✗ Ошибка при присоединении интерфейса $vhost_if к bridge-domain"
                exit 1
            fi
        done
    fi
    
    # Создаем loopback интерфейс для BVI
    echo "  Создание loopback интерфейса для BVI"
    local loop_output
    if loop_output=$(vppctl create loopback interface 2>&1); then
        loop_output=${loop_output//$'\r'/}
        VPP_BVI_INTERFACE=$(echo "$loop_output" | awk 'NF {iface=$NF} END {print iface}')
        if [ -z "$VPP_BVI_INTERFACE" ]; then
            echo "  ✗ Не удалось определить имя loopback интерфейса из вывода:"
            echo "    $loop_output"
            exit 1
        fi
        echo "  ✓ Loopback интерфейс создан: $VPP_BVI_INTERFACE"
    else
        echo "  ✗ Ошибка при создании loopback интерфейса: $loop_output"
        exit 1
    fi
    
    # Присоединяем loopback к bridge-domain как BVI
    echo "  Присоединение $VPP_BVI_INTERFACE к bridge-domain $VPP_BRIDGE_DOMAIN_ID как BVI"
    if vppctl set interface l2 bridge "$VPP_BVI_INTERFACE" "$VPP_BRIDGE_DOMAIN_ID" bvi 2>&1; then
        echo "  ✓ Интерфейс $VPP_BVI_INTERFACE присоединен как BVI"
    else
        echo "  ✗ Ошибка при присоединении BVI к bridge-domain"
        exit 1
    fi
    
    # Устанавливаем IP адрес на BVI интерфейс
    echo "  Установка IP адреса $VPP_BVI_IP/$VPP_BVI_CIDR на BVI интерфейс $VPP_BVI_INTERFACE"
    if vppctl set interface ip address "$VPP_BVI_INTERFACE" "$VPP_BVI_IP/$VPP_BVI_CIDR" 2>&1; then
        echo "  ✓ IP адрес установлен"
    else
        echo "  ✗ Ошибка при установке IP адреса на BVI"
        exit 1
    fi
    
    echo "  ✓ VPP bridge сеть подготовлена (bridge-domain $VPP_BRIDGE_DOMAIN_ID, BVI $VPP_BVI_INTERFACE с IP $VPP_BVI_IP/$VPP_BVI_CIDR, состояние DOWN)"
    echo ""
}


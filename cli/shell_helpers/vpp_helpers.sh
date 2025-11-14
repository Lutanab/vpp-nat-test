#!/bin/bash

# Функция для проверки и управления сервисом VPP
ensure_vpp_service() {
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
    
    # Проверяем, что сервис действительно активен
    if systemctl is-active --quiet "${service_name}.service"; then
        echo "  ✓ Сервис VPP активен и готов к работе"
    else
        echo "  ✗ Сервис VPP не активен после запуска/перезапуска"
        exit 1
    fi
    
    echo ""
}

# Функция для создания veth-пары и подключения её к VPP
create_veth_pair_and_connect_to_vpp() {
    local vpp_if_name="${1:-vpp0}"
    local host_if_name="${2:-host0}"
    local vpp_host_if_name="host-${vpp_if_name}"
    
    echo "=== Создание veth-пары и подключение к VPP ==="
    echo "  VPP интерфейс: $vpp_if_name"
    echo "  Host интерфейс: $host_if_name"

    # Удаляем существующую veth-пару, если она есть
    if ip link show "$vpp_if_name" &>/dev/null || ip link show "$host_if_name" &>/dev/null; then
        echo "  Удаление существующей veth-пары..."
        sudo ip link delete "$vpp_if_name" 2>/dev/null || sudo ip link delete "$host_if_name" 2>/dev/null || true
        echo "  ✓ Старая veth-пара удалена"
    fi
    
    # Создаем новую veth-пару
    echo "  Создание veth-пары: $vpp_if_name <-> $host_if_name"
    if sudo ip link add "$vpp_if_name" type veth peer name "$host_if_name"; then
        echo "  ✓ Veth-пара создана"
    else
        echo "  ✗ Ошибка при создании veth-пары"
        exit 1
    fi
    
    # Поднимаем оба интерфейса
    echo "  Поднятие интерфейсов..."
    if sudo ip link set "$host_if_name" up; then
        echo "  ✓ Интерфейс $host_if_name поднят"
    else
        echo "  ✗ Ошибка при поднятии интерфейса $host_if_name"
        exit 1
    fi
    
    if sudo ip link set "$vpp_if_name" up; then
        echo "  ✓ Интерфейс $vpp_if_name поднят"
    else
        echo "  ✗ Ошибка при поднятии интерфейса $vpp_if_name"
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
    
    # Поднимаем интерфейс в VPP
    echo "  Поднятие интерфейса в VPP: $vpp_host_if_name"
    if vppctl set int state "$vpp_host_if_name" up 2>&1; then
        # Проверяем, что интерфейс действительно поднят
        sleep 1
        local updated_interfaces=$(vppctl show interface 2>/dev/null || echo "")
        if echo "$updated_interfaces" | grep -qE "^${vpp_host_if_name}\s.*up"; then
            echo "  ✓ Интерфейс $vpp_host_if_name поднят в VPP"
        else
            echo "  ✗ Интерфейс $vpp_host_if_name не поднят в VPP после команды"
            exit 1
        fi
    else
        echo "  ✗ Ошибка при выполнении команды поднятия интерфейса $vpp_host_if_name"
        exit 1
    fi
    
    echo "  ✓ Veth-пара настроена и подключена к VPP"
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
    
    # Проверяем, что VPP запущен и доступен
    if ! command -v vppctl &> /dev/null; then
        echo "Ошибка: vppctl не найден. Убедитесь, что VPP установлен и доступен в PATH."
        exit 1
    fi
    
    # Проверяем доступность VPP через vppctl
    if ! vppctl show version &> /dev/null; then
        echo "Ошибка: VPP не запущен или недоступен. Запустите VPP перед выполнением скрипта."
        exit 1
    fi
    
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
            echo "  ✓ Vhost-user интерфейс создан: $output"
            
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


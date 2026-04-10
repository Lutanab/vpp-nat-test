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

# Функция для создания external vhost-user интерфейса для внешней VM
create_external_vhost_interface() {
    echo "=== Создание vhost-user интерфейса для внешней VM ==="
    
    local socket_path="$EXTERNAL_VHOST_SOCKET"
    echo "  Создание vhost-user интерфейса: $socket_path"
    
    # Убеждаемся, что директория существует
    local socket_dir=$(dirname "$socket_path")
    if [ ! -d "$socket_dir" ]; then
        echo "  Создание директории $socket_dir"
        sudo mkdir -p "$socket_dir"
        sudo chmod 755 "$socket_dir"
    fi
    
    # Удаляем старый сокет, если он существует
    if [ -S "$socket_path" ]; then
        echo "  Удаление старого сокета $socket_path"
        sudo rm -f "$socket_path"
    fi
    
    # Создаем vhost-user интерфейс через vppctl (VPP создаст сокет в режиме server)
    local output
    if output=$(vppctl create vhost-user socket "$socket_path" server 2>&1); then
        output=${output//$'\r'/}
        EXTERNAL_VHOST_VPP_IFACE="$output"
        echo "  ✓ Vhost-user интерфейс создан: $output"
        
        # Устанавливаем права на сокет для доступа QEMU (ждём его создания)
        sleep 1
        if [ -S "$socket_path" ]; then
            sudo chmod 666 "$socket_path"
            echo "  ✓ Права на сокет установлены"
        fi
    else
        echo "  ✗ Ошибка при создании vhost-user интерфейса: $output"
        exit 1
    fi
    
    # Устанавливаем IP адрес на интерфейс внутри VPP (сторона VPP в сети 10.8.0.0/24)
    echo "  Установка IP адреса 10.8.0.1/24 на интерфейс $EXTERNAL_VHOST_VPP_IFACE"
    if vppctl set interface ip address "$EXTERNAL_VHOST_VPP_IFACE" 10.8.0.1/24 2>&1; then
        echo "  ✓ IP адрес установлен"
    else
        echo "  ✗ Ошибка при установке IP адреса"
        exit 1
    fi
    
    echo "  ✓ External vhost-интерфейс готов: $EXTERNAL_VHOST_VPP_IFACE (10.8.0.1/24, DOWN)"
    echo "  ℹ External VM должна использовать IP 10.8.0.2/24 с gateway 10.8.0.1"
    echo ""
}

# DEPRECATED: Функция для создания veth-пары (заменено на external vhost)
# create_veth_pair_and_connect_to_vpp() {
#     ... (закомментировано)
# }

# configure_libvirt_networking перенесена в cli/shell_helpers/libvirt.sh

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

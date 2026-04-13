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
    echo "  Установка IP адреса $EXTERNAL_VPP_IP/$EXTERNAL_VPP_CIDR на интерфейс $EXTERNAL_VHOST_VPP_IFACE"
    if vppctl set interface ip address "$EXTERNAL_VHOST_VPP_IFACE" "$EXTERNAL_VPP_IP/$EXTERNAL_VPP_CIDR" 2>&1; then
        echo "  ✓ IP адрес установлен"
    else
        echo "  ✗ Ошибка при установке IP адреса"
        exit 1
    fi
    
    echo "  ✓ External vhost-интерфейс готов: $EXTERNAL_VHOST_VPP_IFACE ($EXTERNAL_VPP_IP/$EXTERNAL_VPP_CIDR, DOWN)"
    echo "  ℹ External VM должна использовать IP $EXTERNAL_VM_IP/$EXTERNAL_VPP_CIDR с gateway $EXTERNAL_VPP_IP"
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

configure_vpp_nat_plugins_for_mode() {
    local nat_mode="$1"
    local startup_conf="/etc/vpp/startup.conf"
    local natmvp_plugin_state=""
    local nat44_plugin_state=""

    case "$nat_mode" in
        none)
            natmvp_plugin_state="disable"
            nat44_plugin_state="disable"
            ;;
        nat44)
            natmvp_plugin_state="disable"
            nat44_plugin_state="enable"
            ;;
        natmvp)
            natmvp_plugin_state="enable"
            nat44_plugin_state="disable"
            ;;
        *)
            echo "✗ Ошибка: неподдерживаемый nat-mode '$nat_mode'"
            exit 1
            ;;
    esac

    echo "=== Настройка VPP plugins для NAT режима '$nat_mode' ==="

    if ! sudo test -f "$startup_conf"; then
        echo "  ✗ Файл $startup_conf не найден"
        exit 1
    fi

    local tmp_original
    local tmp_without_block
    local tmp_final
    tmp_original="$(mktemp)"
    tmp_without_block="$(mktemp)"
    tmp_final="$(mktemp)"

    # Считываем текущий startup.conf
    sudo cat "$startup_conf" > "$tmp_original"

    # Удаляем ранее управляемый блок (если есть)
    awk '
        /^# BEGIN VPP_NAT_TEST_MANAGED_PLUGINS$/ { skip=1; next }
        /^# END VPP_NAT_TEST_MANAGED_PLUGINS$/ { skip=0; next }
        skip != 1 { print }
    ' "$tmp_original" > "$tmp_without_block"

    # Убираем любые существующие живые строки настройки nat-плагинов, чтобы избежать дубликатов
    sed -E \
        -e '/^[[:space:]]*plugin[[:space:]]+natmvp_plugin\.so[[:space:]]*\{.*\}[[:space:]]*$/d' \
        -e '/^[[:space:]]*plugin[[:space:]]+nat_plugin\.so[[:space:]]*\{.*\}[[:space:]]*$/d' \
        "$tmp_without_block" > "$tmp_final"

    cat >> "$tmp_final" << EOF

# BEGIN VPP_NAT_TEST_MANAGED_PLUGINS
plugins {
  plugin natmvp_plugin.so { $natmvp_plugin_state }
  plugin nat_plugin.so { $nat44_plugin_state }
}
# END VPP_NAT_TEST_MANAGED_PLUGINS
EOF

    sudo tee "$startup_conf" >/dev/null < "$tmp_final"

    rm -f "$tmp_original" "$tmp_without_block" "$tmp_final"

    echo "  ✓ Обновлён $startup_conf"
    echo "    - natmvp_plugin.so: $natmvp_plugin_state"
    echo "    - nat_plugin.so: $nat44_plugin_state"
    echo ""
}

run_vppctl_command_or_fail() {
    local description="$1"
    local command="$2"
    local output

    if output=$(vppctl "$command" 2>&1); then
        # В некоторых версиях vppctl "unknown input" может вернуться с кодом 0,
        # поэтому дополнительно валидируем текст ответа.
        if echo "$output" | grep -Eiq 'unknown input|unknown command|parse error'; then
            echo "  ✗ $description"
            echo "    Команда: vppctl $command"
            echo "$output" | sed 's/^/    /'
            exit 1
        fi
        echo "  ✓ $description"
        if [ -n "$output" ]; then
            echo "$output" | sed 's/^/    /'
        fi
    else
        echo "  ✗ $description"
        if [ -n "$output" ]; then
            echo "$output" | sed 's/^/    /'
        fi
        exit 1
    fi
}

ensure_natmvp_runtime_available_or_fail() {
    local plugin_path=""
    local plugins_output=""
    local probe_output=""

    # Проверяем, что плагин действительно установлен в системе.
    plugin_path="$(find /usr/lib -maxdepth 4 -type f -name natmvp_plugin.so 2>/dev/null | head -n 1)"
    if [ -z "$plugin_path" ]; then
        echo "  ✗ NATMVP plugin не найден в системе (natmvp_plugin.so)"
        echo "    Похоже, VPP установлен из пакетов без natmvp."
        echo "    Пересоберите и переустановите пакеты из этого репозитория:"
        echo "      cd vpp"
        echo "      sudo make pkg-deb-debug"
        echo "      sudo dpkg -i build-root/*.deb"
        exit 1
    fi

    # Проверяем, что плагин загрузился в текущем процессе VPP после restart.
    if ! plugins_output=$(vppctl show plugins 2>&1); then
        echo "  ✗ Не удалось выполнить 'vppctl show plugins'"
        echo "$plugins_output" | sed 's/^/    /'
        exit 1
    fi

    if ! echo "$plugins_output" | grep -q "natmvp_plugin.so"; then
        echo "  ✗ VPP не загрузил natmvp_plugin.so (несмотря на nat-mode=natmvp)"
        echo "    Проверьте /etc/vpp/startup.conf и перезапустите VPP:"
        echo "      sudo systemctl restart vpp"
        echo "      sudo vppctl show plugins | grep natmvp"
        exit 1
    fi

    # Финальная проверка: команда CLI natmvp должна распознаваться.
    probe_output="$(vppctl show natmvp 2>&1 || true)"
    if echo "$probe_output" | grep -Eiq 'unknown input|unknown command|parse error'; then
        echo "  ✗ CLI 'natmvp' недоступен в VPP"
        echo "$probe_output" | sed 's/^/    /'
        exit 1
    fi
}

configure_vpp_nat_runtime_mode() {
    local nat_mode="$1"
    local inside_iface="$2"
    local outside_iface="$3"

    if [ -z "$inside_iface" ] || [ -z "$outside_iface" ]; then
        echo "✗ Ошибка: для NAT-конфигурации не заданы inside/outside интерфейсы"
        exit 1
    fi

    echo "=== Конфигурация NAT режима '$nat_mode' ==="

    case "$nat_mode" in
        none)
            echo "  ✓ NAT отключен (режим none)"
            ;;
        nat44)
            run_vppctl_command_or_fail "NAT44 plugin включен (sessions ${NAT44_MAX_SESSIONS})" "nat44 plugin enable sessions ${NAT44_MAX_SESSIONS}"
            run_vppctl_command_or_fail "Роли NAT44 интерфейсов установлены" "set interface nat44 in ${inside_iface} out ${outside_iface}"
            run_vppctl_command_or_fail "Внешний NAT44 адрес привязан к интерфейсу ${outside_iface}" "nat44 add interface address ${outside_iface}"
            run_vppctl_command_or_fail "NAT44 summary" "show nat44 summary"
            ;;
        natmvp)
            ensure_natmvp_runtime_available_or_fail
            run_vppctl_command_or_fail "NATMVP public address установлен (${NATMVP_PUBLIC_ADDR})" "natmvp set public-addr ${NATMVP_PUBLIC_ADDR}"
            run_vppctl_command_or_fail "NATMVP диапазон портов установлен (${NATMVP_PORT_RANGE_START}-${NATMVP_PORT_RANGE_END})" "natmvp set port-range ${NATMVP_PORT_RANGE_START} ${NATMVP_PORT_RANGE_END}"
            run_vppctl_command_or_fail "NATMVP inside интерфейс установлен (${inside_iface})" "natmvp interface inside ${inside_iface}"
            run_vppctl_command_or_fail "NATMVP outside интерфейс установлен (${outside_iface})" "natmvp interface outside ${outside_iface}"
            run_vppctl_command_or_fail "NATMVP summary" "show natmvp"
            ;;
        *)
            echo "  ✗ Ошибка: неподдерживаемый nat-mode '$nat_mode'"
            exit 1
            ;;
    esac

    echo ""
}

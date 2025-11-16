#!/bin/bash

# Остановка systemd-юнита VPP, если он существует и активен
stop_vpp_service_if_running() {
    local service_name="vpp"

    echo "=== Остановка сервиса ${service_name}.service (если запущен) ==="

    if ! systemctl list-unit-files --type=service | grep -qE "^${service_name}\.service"; then
        echo "  Сервис ${service_name}.service не найден, пропускаю остановку"
        echo ""
        return
    fi

    if systemctl is-active --quiet "${service_name}.service"; then
        echo "  Остановка сервиса ${service_name}.service..."
        if sudo systemctl stop "${service_name}.service"; then
            echo "  ✓ Сервис остановлен"
        else
            echo "  ✗ Не удалось остановить сервис ${service_name}.service"
            exit 1
        fi
    else
        echo "  Сервис ${service_name}.service уже остановлен"
    fi

    echo ""
}

# Удаление сетевых интерфейсов с указанными префиксами
delete_prefixed_interfaces() {
    local prefixes=("br" "host" "tap" "vpp")
    local interfaces=()
    local all_ifaces

    echo "=== Удаление сетевых интерфейсов с префиксами: ${prefixes[*]} ==="

    mapfile -t all_ifaces < <(ip -o link show | awk -F': ' '{print $2}' | cut -d'@' -f1)

    for iface in "${all_ifaces[@]}"; do
        for prefix in "${prefixes[@]}"; do
            if [[ "$iface" == "${prefix}"* ]]; then
                interfaces+=("$iface")
                break
            fi
        done
    done

    if [ ${#interfaces[@]} -eq 0 ]; then
        echo "  Интерфейсы с указанными префиксами не найдены"
        echo ""
        return
    fi

    for iface in "${interfaces[@]}"; do
        echo "  Удаление интерфейса $iface..."
        sudo ip link set "$iface" down 2>/dev/null || true
        if sudo ip link delete "$iface" 2>/dev/null; then
            echo "    ✓ Интерфейс $iface удален"
        else
            echo "    ✗ Не удалось удалить интерфейс $iface"
            # exit 1
        fi
    done

    echo "  ✓ Все найденные интерфейсы удалены"
    echo ""
}

clean_network_main() {
    echo "=========================================="
    echo "Очистка сетевой топологии хоста"
    echo "=========================================="
    echo ""

    stop_vpp_service_if_running
    delete_prefixed_interfaces

    echo "=========================================="
    echo "✓ Очистка сети завершена"
    echo "=========================================="
}


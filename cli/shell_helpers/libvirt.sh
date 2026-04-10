#!/bin/bash

validate_libvirt_network_constants() {
    local user_vms_count="${#USER_MACHINES_DIR[@]}"

    if [ "${#USER_MACHINES_LIBVIRT_IP[@]}" -ne "$user_vms_count" ]; then
        echo "  ✗ Ошибка: USER_MACHINES_LIBVIRT_IP не совпадает по размеру с USER_MACHINES_DIR"
        exit 1
    fi

    if [ "${#USER_MACHINES_LIBVIRT_MAC[@]}" -ne "$user_vms_count" ]; then
        echo "  ✗ Ошибка: USER_MACHINES_LIBVIRT_MAC не совпадает по размеру с USER_MACHINES_DIR"
        exit 1
    fi

    if [ "${#USER_MACHINES_SSH_HOST_PORT[@]}" -ne "$user_vms_count" ]; then
        echo "  ✗ Ошибка: USER_MACHINES_SSH_HOST_PORT не совпадает по размеру с USER_MACHINES_DIR"
        exit 1
    fi
}

generate_libvirt_dhcp_hosts_xml() {
    local dhcp_hosts_xml=""
    dhcp_hosts_xml+="      <host mac='${EXTERNAL_VM_LIBVIRT_MAC}' name='${EXTERNAL_VM_DIR}' ip='${EXTERNAL_VM_LIBVIRT_IP}'/>"$'\n'

    local user_vms_count="${#USER_MACHINES_DIR[@]}"
    for ((i = 0; i < user_vms_count; i++)); do
        dhcp_hosts_xml+="      <host mac='${USER_MACHINES_LIBVIRT_MAC[$i]}' name='${USER_MACHINES_DIR[$i]}' ip='${USER_MACHINES_LIBVIRT_IP[$i]}'/>"$'\n'
    done

    printf "%s" "$dhcp_hosts_xml"
}

recreate_default_libvirt_network() {
    local network_name="$LIBVIRT_NETWORK_NAME"
    local dhcp_hosts_xml
    dhcp_hosts_xml="$(generate_libvirt_dhcp_hosts_xml)"

    # Проверяем и запускаем libvirtd если не запущен
    if ! systemctl is-active --quiet libvirtd 2>/dev/null; then
        echo "  Запуск libvirtd..."
        sudo systemctl start libvirtd
        echo "  ✓ libvirtd запущен"
    else
        echo "  ✓ libvirtd уже запущен"
    fi

    # Проверяем существование сети default
    if sudo virsh net-list --all 2>/dev/null | grep -q -E "^ ?${network_name} "; then
        echo "  Сеть ${network_name} уже существует, удаляем её..."
        sudo virsh net-destroy "${network_name}" 2>/dev/null || true
        sudo virsh net-undefine "${network_name}" 2>/dev/null || true
        echo "  ✓ Старая сеть ${network_name} удалена"
    fi

    # Создаем XML конфигурацию для сети default с подсетью 10.8.2.0/24
    local network_xml
    network_xml=$(cat <<EOF
<network>
  <name>${network_name}</name>
  <forward mode='nat'>
    <nat>
      <port start='1024' end='65535'/>
    </nat>
  </forward>
  <bridge name='${LIBVIRT_BRIDGE_NAME}' stp='on' delay='0'/>
  <ip address='${LIBVIRT_GATEWAY_IP}' netmask='${LIBVIRT_NETMASK}'>
    <dhcp>
      <range start='${LIBVIRT_DHCP_RANGE_START}' end='${LIBVIRT_DHCP_RANGE_END}'/>
${dhcp_hosts_xml}    </dhcp>
  </ip>
</network>
EOF
)

    # Создаем временный файл с конфигурацией
    local temp_xml
    temp_xml="$(mktemp)"
    echo "$network_xml" > "$temp_xml"

    # Определяем сеть в libvirt
    echo "  Создание сети ${network_name} (${LIBVIRT_GATEWAY_IP}/${LIBVIRT_NETMASK})..."
    if sudo virsh net-define "$temp_xml" 2>&1; then
        echo "  ✓ Сеть ${network_name} определена"
    else
        echo "  ✗ Ошибка при определении сети ${network_name}"
        rm -f "$temp_xml"
        exit 1
    fi

    # Удаляем временный файл
    rm -f "$temp_xml"

    # Включаем автозапуск сети
    echo "  Включение автозапуска сети ${network_name}..."
    if sudo virsh net-autostart "${network_name}" 2>&1; then
        echo "  ✓ Автозапуск сети ${network_name} включен"
    else
        echo "  ✗ Ошибка при включении автозапуска"
        exit 1
    fi

    # Запускаем сеть
    echo "  Запуск сети ${network_name}..."
    if sudo virsh net-start "${network_name}" 2>&1; then
        echo "  ✓ Сеть ${network_name} запущена"
    else
        echo "  ✗ Ошибка при запуске сети ${network_name}"
        exit 1
    fi

    # Проверяем статус сети
    echo "  Проверка статуса сети..."
    if sudo virsh net-info "${network_name}" 2>/dev/null; then
        echo "  ✓ Сеть ${network_name} настроена и активна"
    fi
}

setup_qemu_bridge_helper() {
    # Настройка QEMU bridge helper для доступа к virbr0
    echo "  Настройка QEMU bridge helper..."
    local qemu_bridge_conf="/etc/qemu/bridge.conf"

    # Создаем директорию если не существует
    if [ ! -d "/etc/qemu" ]; then
        sudo mkdir -p /etc/qemu
    fi

    # Создаем или обновляем bridge.conf
    if ! grep -q "^allow ${LIBVIRT_BRIDGE_NAME}$" "$qemu_bridge_conf" 2>/dev/null; then
        {
            sudo touch "$qemu_bridge_conf"
            echo "allow ${LIBVIRT_BRIDGE_NAME}" | sudo tee -a "$qemu_bridge_conf" > /dev/null
        }
        echo "  ✓ Обновлен $qemu_bridge_conf"
    else
        echo "  ✓ $qemu_bridge_conf уже настроен"
    fi

    # Устанавливаем права на qemu-bridge-helper
    local bridge_helper=""
    if [ -f "/usr/lib/qemu/qemu-bridge-helper" ]; then
        bridge_helper="/usr/lib/qemu/qemu-bridge-helper"
    elif [ -f "/usr/libexec/qemu-bridge-helper" ]; then
        bridge_helper="/usr/libexec/qemu-bridge-helper"
    fi

    if [ -n "$bridge_helper" ]; then
        sudo chmod u+s "$bridge_helper"
        echo "  ✓ Установлены SUID права на qemu-bridge-helper ($bridge_helper)"
    else
        echo "  ⚠ Предупреждение: qemu-bridge-helper не найден"
    fi
}

create_qemu_tap_bridge_scripts() {
    # Создаем скрипты для подключения tap интерфейсов к virbr0
    echo "  Создание QEMU ifup/ifdown скриптов для ${LIBVIRT_BRIDGE_NAME}..."

    # Скрипт для поднятия интерфейса
    sudo tee /etc/qemu-ifup-virbr0 > /dev/null << EOF
#!/bin/bash
# Скрипт для подключения tap интерфейса к ${LIBVIRT_BRIDGE_NAME}
BRIDGE="${LIBVIRT_BRIDGE_NAME}"
TAP="\$1"

if [ -z "\$TAP" ]; then
    echo "Ошибка: не указан tap интерфейс"
    exit 1
fi

# Поднимаем интерфейс
ip link set "\$TAP" up

# Добавляем в bridge
brctl addif "\$BRIDGE" "\$TAP" 2>/dev/null || ip link set "\$TAP" master "\$BRIDGE"

exit 0
EOF

    sudo chmod +x /etc/qemu-ifup-virbr0
    echo "  ✓ Создан /etc/qemu-ifup-virbr0"

    # Скрипт для удаления интерфейса
    sudo tee /etc/qemu-ifdown-virbr0 > /dev/null << EOF
#!/bin/bash
# Скрипт для отключения tap интерфейса от ${LIBVIRT_BRIDGE_NAME}
BRIDGE="${LIBVIRT_BRIDGE_NAME}"
TAP="\$1"

if [ -z "\$TAP" ]; then
    echo "Ошибка: не указан tap интерфейс"
    exit 1
fi

# Удаляем из bridge
brctl delif "\$BRIDGE" "\$TAP" 2>/dev/null || ip link set "\$TAP" nomaster

# Опускаем интерфейс
ip link set "\$TAP" down

exit 0
EOF

    sudo chmod +x /etc/qemu-ifdown-virbr0
    echo "  ✓ Создан /etc/qemu-ifdown-virbr0"
}

ensure_ipv4_forwarding_for_libvirt() {
    local sysctl_file="/etc/sysctl.d/99-vpp-nat-test.conf"
    echo "  Включение и фиксация net.ipv4.ip_forward=1..."
    echo "net.ipv4.ip_forward=1" | sudo tee "$sysctl_file" > /dev/null
    sudo sysctl -w net.ipv4.ip_forward=1 > /dev/null
    echo "  ✓ net.ipv4.ip_forward=1 включен"
}

install_libvirt_ssh_port_forward_script() {
    if ! command -v iptables >/dev/null 2>&1; then
        echo "  ✗ Ошибка: iptables не найден. Установите пакет iptables."
        exit 1
    fi

    local script_path="/usr/local/sbin/vpp-libvirt-port-forward.sh"
    local host_ports=("${EXTERNAL_VM_SSH_HOST_PORT}" "${USER_MACHINES_SSH_HOST_PORT[@]}")
    local target_ips=("${EXTERNAL_VM_LIBVIRT_IP}" "${USER_MACHINES_LIBVIRT_IP[@]}")

    local host_ports_literal=""
    local target_ips_literal=""
    local idx
    for idx in "${!host_ports[@]}"; do
        host_ports_literal="${host_ports_literal} \"${host_ports[$idx]}\""
        target_ips_literal="${target_ips_literal} \"${target_ips[$idx]}\""
    done

    sudo tee "$script_path" > /dev/null << EOF
#!/bin/bash
set -euo pipefail

ensure_rule() {
    local table="\$1"
    shift
    if iptables -t "\$table" -C "\$@" 2>/dev/null; then
        return 0
    fi
    iptables -t "\$table" -A "\$@"
}

ensure_forward_rule() {
    if iptables -C FORWARD "\$@" 2>/dev/null; then
        return 0
    fi
    iptables -I FORWARD 1 "\$@"
}

HOST_PORTS=(${host_ports_literal})
TARGET_IPS=(${target_ips_literal})
TARGET_GUEST_PORT="22"

for idx in "\${!HOST_PORTS[@]}"; do
    host_port="\${HOST_PORTS[\$idx]}"
    target_ip="\${TARGET_IPS[\$idx]}"
    target_dst="\${target_ip}:\${TARGET_GUEST_PORT}"

    ensure_rule nat PREROUTING -p tcp --dport "\${host_port}" -j DNAT --to-destination "\${target_dst}"
    ensure_rule nat OUTPUT -p tcp -m addrtype --dst-type LOCAL --dport "\${host_port}" -j DNAT --to-destination "\${target_dst}"
    ensure_forward_rule -p tcp -d "\${target_ip}" --dport "\${TARGET_GUEST_PORT}" -m conntrack --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT
    ensure_rule filter FORWARD -p tcp -s "\${target_ip}" --sport "\${TARGET_GUEST_PORT}" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
done
EOF

    sudo chmod +x "$script_path"
    echo "  ✓ Скрипт проброса портов установлен: $script_path"
}

install_libvirt_ssh_port_forward_unit() {
    local service_path="/etc/systemd/system/vpp-libvirt-port-forward.service"

    sudo tee "$service_path" > /dev/null << 'EOF'
[Unit]
Description=VPP NAT test libvirt SSH port forwarding
After=libvirtd.service network-online.target
Wants=libvirtd.service network-online.target
ConditionPathExists=/usr/local/sbin/vpp-libvirt-port-forward.sh

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/vpp-libvirt-port-forward.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable --now vpp-libvirt-port-forward.service
    echo "  ✓ systemd-юнит проброса портов включен"
}

show_libvirt_ssh_port_forwarding_summary() {
    local host_ports=("${EXTERNAL_VM_SSH_HOST_PORT}" "${USER_MACHINES_SSH_HOST_PORT[@]}")
    local target_names=("${EXTERNAL_VM_DIR}" "${USER_MACHINES_DIR[@]}")
    local target_ips=("${EXTERNAL_VM_LIBVIRT_IP}" "${USER_MACHINES_LIBVIRT_IP[@]}")

    echo "  SSH проброс портов хоста:"
    local idx
    for idx in "${!host_ports[@]}"; do
        echo "    ${host_ports[$idx]} -> ${target_names[$idx]} (${target_ips[$idx]}):22"
    done
}

# Функция для настройки libvirt сети default 10.8.2.0/24 + SSH host-port forwarding
configure_libvirt_networking() {
    echo "=== Настройка libvirt networking ==="

    validate_libvirt_network_constants
    recreate_default_libvirt_network
    setup_qemu_bridge_helper
    create_qemu_tap_bridge_scripts
    ensure_ipv4_forwarding_for_libvirt
    install_libvirt_ssh_port_forward_script
    install_libvirt_ssh_port_forward_unit
    show_libvirt_ssh_port_forwarding_summary

    echo "  ✓ Libvirt networking настроен"
    echo ""
}

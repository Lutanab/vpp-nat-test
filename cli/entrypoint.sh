#!/bin/bash

set -e

# Определяем директорию скрипта (cli/)
# Используем readlink для получения реального пути, если скрипт запущен через символическую ссылку
SCRIPT_PATH="${BASH_SOURCE[0]}"
if [ -L "$SCRIPT_PATH" ]; then
    SCRIPT_PATH="$(readlink -f "$SCRIPT_PATH")"
fi
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
# Определяем корневую директорию проекта (на уровень выше)
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Подключаем вспомогательные функции
source "${SCRIPT_DIR}/shell_helpers/vpp_helpers.sh"
source "${SCRIPT_DIR}/prepare_network.sh"

# Функция для вывода справки
show_help() {
    cat << EOF
Использование: $0 <команда> [опции]

Команды:
    prepare          Подготовка окружения (установка зависимостей, QEMU, отключение libvirt)
    bootstrap        Настройка VPP (запуск сервиса, создание veth-пары)
    prepare-network  Подготовка сетевой топологии (bridge, tap-интерфейсы, NAT)
    help             Показать эту справку

Примеры:
    $0 prepare
    $0 bootstrap
    $0 prepare-network
    $0 help
EOF
}

# Команда prepare
cmd_prepare() {
    echo "=========================================="
    echo "Подготовка окружения для VPP и QEMU"
    echo "=========================================="
    echo ""

    # 1. Установка зависимостей VPP
    echo "=== Установка зависимостей VPP ==="
    VPP_DIR="${PROJECT_ROOT}/vpp"

    if [ ! -d "$VPP_DIR" ]; then
        echo "Ошибка: Директория VPP не найдена: $VPP_DIR"
        echo "Убедитесь, что VPP находится в директории vpp/"
        exit 1
    fi

    echo "Переход в директорию VPP: $VPP_DIR"
    cd "$VPP_DIR"

    echo "Установка зависимостей (make install-dep)..."
    if make install-dep; then
        echo "  ✓ Зависимости VPP установлены"
    else
        echo "  ✗ Ошибка при установке зависимостей VPP"
        exit 1
    fi

    echo "Установка внешних зависимостей (make install-ext-deps)..."
    if make install-ext-deps; then
        echo "  ✓ Внешние зависимости VPP установлены"
    else
        echo "  ✗ Ошибка при установке внешних зависимостей VPP"
        exit 1
    fi

    echo ""

    # 2. Установка apt-пакетов для QEMU
    echo "=== Установка пакетов для QEMU ==="
    QEMU_PACKAGES=(
        "qemu-kvm"
        "libvirt-daemon-system"
        "libvirt-clients"
        "bridge-utils"
        "virt-manager"
    )

    echo "Обновление списка пакетов..."
    sudo apt-get update

    for package in "${QEMU_PACKAGES[@]}"; do
        if dpkg -l | grep -q "^ii.*${package}"; then
            echo "  ✓ Пакет $package уже установлен"
        else
            echo "  Установка пакета $package..."
            if sudo apt-get install -y "$package"; then
                echo "  ✓ Пакет $package установлен"
            else
                echo "  ✗ Ошибка при установке пакета $package"
                exit 1
            fi
        fi
    done

    echo ""

    # 3. Отключение libvirt networking
    disable_libvirt_networking

    echo "=========================================="
    echo "✓ Подготовка окружения завершена"
    echo "=========================================="
}

# Команда bootstrap
cmd_bootstrap() {
    ensure_vpp_service
    create_veth_pair_and_connect_to_vpp

    # Можно раскомментировать при необходимости
    # echo "=== Проверка и создание vhost-user сокетов ==="
    # VHOST_SOCKETS=(
    #     "/var/run/vpp/vhost1.sock"
    #     "/var/run/vpp/vhost2.sock"
    # )
    # for socket in "${VHOST_SOCKETS[@]}"; do
    #     check_and_create_vhost_socket "$socket"
    #     echo ""
    # done
    # echo "=== Все vhost-user сокеты готовы ==="
    # echo ""
}

# Команда prepare-network
cmd_prepare_network() {
    # Вызываем prepare_network_main функцию из prepare_network.sh
    prepare_network_main
}

# Главная логика
main() {
    # Проверяем наличие команды
    if [ $# -eq 0 ]; then
        echo "Ошибка: не указана команда"
        echo ""
        show_help
        exit 1
    fi

    local command="$1"
    shift

    case "$command" in
        prepare)
            cmd_prepare "$@"
            ;;
        bootstrap)
            cmd_bootstrap "$@"
            ;;
        prepare-network)
            cmd_prepare_network "$@"
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            echo "Ошибка: неизвестная команда '$command'"
            echo ""
            show_help
            exit 1
            ;;
    esac
}

# Запуск главной функции
main "$@"


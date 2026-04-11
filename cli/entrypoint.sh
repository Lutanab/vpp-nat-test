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

# Подключаем константы
source "${SCRIPT_DIR}/constants.sh"

# Подключаем вспомогательные функции
source "${SCRIPT_DIR}/shell_helpers/vpp_helpers.sh"
source "${SCRIPT_DIR}/prepare.sh"
source "${SCRIPT_DIR}/setup_network.sh"
source "${SCRIPT_DIR}/clean_network.sh"
source "${SCRIPT_DIR}/virtual_machines.sh"

# Функция для вывода справки
show_help() {
    cat << EOF
Использование: $0 <команда> [опции]

Команды:
    prepare          Подготовка окружения (установка зависимостей, QEMU, настройка libvirt)
    setup-network    Подготовка сетевой топологии (обязательно: --nat-mode <none|nat44|natmvp>)
    setup-vms        Подготовка и запуск всех ВМ
    stop-vms         Остановка всех ВМ
    clean-network    Очистка сетевой топологии и vhost сокетов
    help             Показать эту справку
EOF
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
            prepare_main "$@"
            ;;
        setup-network)
            setup_network_main "$@"
            ;;
        setup-vms)
            configure_and_deploy_vms "$@"
            ;;
        stop-vms)
            stop_vms "$@"
            ;;
        clean-network)
            clean_network_main "$@"
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

#!/bin/bash

set -e

# Подключаем вспомогательные функции
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SCRIPT_DIR}/shell_helpers/vpp_helpers.sh"

# Основная функция подготовки окружения
prepare_main() {
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
        "cloud-image-utils"
        "socat"
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

    # 3. Настройка libvirt networking
    configure_libvirt_networking

    echo "=========================================="
    echo "✓ Подготовка окружения завершена"
    echo "=========================================="
}


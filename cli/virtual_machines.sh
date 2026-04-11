size_to_bytes() {
  local size="$1"

  if command -v numfmt >/dev/null 2>&1; then
    numfmt --from=iec "${size}" 2>/dev/null && return 0
  fi

  if [[ "${size}" =~ ^[0-9]+$ ]]; then
    echo "${size}"
    return 0
  fi

  if [[ "${size}" =~ ^([0-9]+)([KkMmGgTt])$ ]]; then
    local number="${BASH_REMATCH[1]}"
    local unit="${BASH_REMATCH[2]}"
    case "${unit}" in
      K|k) echo $((number * 1024)); return 0 ;;
      M|m) echo $((number * 1024 * 1024)); return 0 ;;
      G|g) echo $((number * 1024 * 1024 * 1024)); return 0 ;;
      T|t) echo $((number * 1024 * 1024 * 1024 * 1024)); return 0 ;;
    esac
  fi

  return 1
}

ensure_vm_disk_min_size() {
  local disk_image="$1"
  local min_size="${VM_MIN_DISK_SIZE:-15G}"

  if [[ ! -f "${disk_image}" ]]; then
    echo "  ✗ Диск не найден: ${disk_image}"
    return 1
  fi

  local current_virtual_size_bytes
  current_virtual_size_bytes="$(qemu-img info --force-share "${disk_image}" 2>/dev/null | sed -n 's/^virtual size: .* (\([0-9][0-9]*\) bytes)$/\1/p')"
  if [[ -z "${current_virtual_size_bytes}" ]]; then
    echo "  ✗ Не удалось определить текущий виртуальный размер диска: ${disk_image}"
    return 1
  fi

  local min_size_bytes
  min_size_bytes="$(size_to_bytes "${min_size}")"
  if [[ -z "${min_size_bytes}" ]]; then
    echo "  ✗ Некорректный размер VM_MIN_DISK_SIZE='${min_size}'. Пример: 15G"
    return 1
  fi

  if (( current_virtual_size_bytes >= min_size_bytes )); then
    echo "  ✓ Размер диска достаточный: ${disk_image} (>= ${min_size})"
    return 0
  fi

  echo "  Увеличение виртуального диска до ${min_size}: ${disk_image}"
  if qemu-img resize "${disk_image}" "${min_size}" >/dev/null; then
    echo "  ✓ Диск увеличен до ${min_size}"
  else
    echo "  ✗ Не удалось увеличить диск: ${disk_image}"
    echo "    Убедитесь, что VM остановлена (например: sudo ./manage stop-vms)"
    return 1
  fi
}

ensure_vm_storage_ready() {
  echo "=== Подготовка storage для виртуальных машин ==="

  # Базовая директория для VM-образов
  mkdir -p "${VM_STORAGE_PATH}"

  if [[ ! -f "${VM_BASE_IMAGE_PATH}" ]]; then
    echo "  ✗ Базовый cloud-image не найден: ${VM_BASE_IMAGE_PATH}"
    echo "  Скачайте его, например:"
    echo "    mkdir -p ${VM_STORAGE_PATH}"
    echo "    wget -O ${VM_BASE_IMAGE_PATH} https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
    return 1
  fi

  if ! command -v qemu-img >/dev/null 2>&1; then
    echo "  ✗ Команда qemu-img не найдена. Установите qemu-utils."
    return 1
  fi

  local vm_dirs=("${EXTERNAL_VM_DIR}" "${USER_MACHINES_DIR[@]}")
  for vm_dir in "${vm_dirs[@]}"; do
    local vm_storage_dir="${VM_STORAGE_PATH}/${vm_dir}"
    local disk_image="${vm_storage_dir}/disk.img"

    mkdir -p "${vm_storage_dir}"

    if [[ -f "${disk_image}" ]]; then
      echo "  ✓ Диск уже существует: ${disk_image}"
      ensure_vm_disk_min_size "${disk_image}" || return 1
      continue
    fi

    echo "  Создание диска VM: ${disk_image}"
    if qemu-img create -f qcow2 -F qcow2 -b "${VM_BASE_IMAGE_PATH}" "${disk_image}" >/dev/null; then
      echo "  ✓ Диск создан (overlay на базовый образ)"
      ensure_vm_disk_min_size "${disk_image}" || return 1
    else
      echo "  ✗ Не удалось создать диск: ${disk_image}"
      return 1
    fi
  done

  echo "  ✓ Storage для VM подготовлен"
  echo ""
}

vm_host_mount_dir() {
  local vm_dir="$1"
  echo "${PROJECT_ROOT}/virtual_machines/host_mounts/${vm_dir}"
}

ensure_vm_host_mounts_ready() {
  echo "=== Подготовка хостовых директорий для /mnt/host в VM ==="

  local owner_user="${SUDO_USER:-$USER}"
  local owner_group
  owner_group="$(id -gn "${owner_user}" 2>/dev/null || echo "${owner_user}")"

  local vm_dirs=("${EXTERNAL_VM_DIR}" "${USER_MACHINES_DIR[@]}")
  for vm_dir in "${vm_dirs[@]}"; do
    local host_mount_dir
    host_mount_dir="$(vm_host_mount_dir "${vm_dir}")"
    mkdir -p "${host_mount_dir}"
    chown "${owner_user}:${owner_group}" "${host_mount_dir}" 2>/dev/null || true
    echo "  ✓ ${host_mount_dir}"
  done

  echo "  ✓ Хостовые директории для VM готовы"
  echo ""
}

prepare_vm_seed() {
  local vm_dir="$1"
  local seed_image="$2"
  local config_dir="${VM_CONFIG_PATH}/${vm_dir}"
  local user_data="${config_dir}/user-data.yaml"
  local meta_data_template="${config_dir}/meta-data.yaml.template"
  local meta_data="${config_dir}/meta-data.yaml"
  local network_config="${config_dir}/network-config.yaml"

  if [[ -z "${vm_dir}" || -z "${seed_image}" ]]; then
    echo "Usage: prepare_vm_seed <vm_dir> <seed_image_path>" >&2
    return 1
  fi

  echo "=== Создание seed-образа для ${vm_dir} ==="
  local VM_NAME="$vm_dir"
  local INSTANCE_ID="${VM_NAME}-$(uuidgen)"
  VM_NAME="$VM_NAME" INSTANCE_ID="$INSTANCE_ID" envsubst '$VM_NAME $INSTANCE_ID' < "${meta_data_template}" > "${meta_data}"
  echo "  → Конфиги метаданных сгенерированы: ${meta_data}"
  echo "  → Файл: ${seed_image}"
  cloud-localds --network-config=${network_config} "${seed_image}" "${user_data}" "${meta_data}"
  echo "  ✓ Seed-образ создан"
  echo ""
}

create_user_vm_systemd_unit() {
  local vm_dir="$1"
  local socket_path="$2"
  local mac_address="$3"
  local unit_name="$4"
  local libvirt_mac="$5"
  local disk_image="${VM_STORAGE_PATH}/${vm_dir}/disk.img"
  local seed_image="${VM_STORAGE_PATH}/${vm_dir}/seed.iso"
  local unit_path="/etc/systemd/system/${unit_name}.service"
  local host_mount_dir
  host_mount_dir="$(vm_host_mount_dir "${vm_dir}")"

  if [[ -z "${vm_dir}" || -z "${socket_path}" || -z "${mac_address}" || -z "${unit_name}" || -z "${libvirt_mac}" ]]; then
    echo "Usage: create_user_vm_systemd_unit <vm_dir> <socket_path> <mac_address> <unit_name> <libvirt_mac>" >&2
    return 1
  fi

  echo "=== Создание systemd-юнита ${unit_name} ==="
  prepare_vm_seed "${vm_dir}" "${seed_image}"

  local console_path="/var/run/vpp/console/${unit_name}.sock"

  sudo tee "${unit_path}" >/dev/null <<EOF
[Unit]
Description=User VM managed by systemd
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStartPre=/bin/mkdir -p /var/run/vpp/console
ExecStartPre=/bin/chmod 777 /var/run/vpp/console
ExecStart=/usr/bin/qemu-system-x86_64 \
  -name user-vm \
  -machine accel=kvm,mem-merge=off \
  -m 4096 \
  -object memory-backend-file,id=mem0,size=4096M,mem-path=/dev/shm/${unit_name}-mem,share=on \
  -numa node,memdev=mem0 \
  -smp 2 \
  -cpu host \
  -enable-kvm \
  -drive file=${disk_image},format=qcow2,if=virtio \
  -drive file=${seed_image},if=virtio,format=raw,media=cdrom,readonly=on \
  -chardev socket,id=char0,path=${socket_path} \
  -netdev vhost-user,id=net0,chardev=char0 \
  -device virtio-net-pci,netdev=net0,mac=${mac_address} \
  -netdev tap,id=net1,script=/etc/qemu-ifup-virbr0,downscript=/etc/qemu-ifdown-virbr0 \
  -device virtio-net-pci,netdev=net1,mac=${libvirt_mac} \
  -virtfs local,path=${host_mount_dir},security_model=none,mount_tag=hostshare,id=hostshare \
  -serial unix:${console_path},server,nowait \
  -nographic
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  echo "  ✓ systemd-юнит сохранён в ${unit_path}"
  echo ""
}

create_external_vm_systemd_unit() {
  local vm_dir="$EXTERNAL_VM_DIR"
  local socket_path="$EXTERNAL_VHOST_SOCKET"
  local mac_address="$EXTERNAL_VM_MAC"
  local libvirt_mac="$EXTERNAL_VM_LIBVIRT_MAC"
  local unit_name="$EXTERNAL_VM_SYSTEMD_UNIT"
  local disk_image="${VM_STORAGE_PATH}/${vm_dir}/disk.img"
  local seed_image="${VM_STORAGE_PATH}/${vm_dir}/seed.iso"
  local unit_path="/etc/systemd/system/${unit_name}.service"
  local host_mount_dir
  host_mount_dir="$(vm_host_mount_dir "${vm_dir}")"

  echo "=== Создание systemd-юнита для внешней VM ${unit_name} ==="
  prepare_vm_seed "${vm_dir}" "${seed_image}"

  local console_path="/var/run/vpp/console/${unit_name}.sock"

  sudo tee "${unit_path}" >/dev/null <<EOF
[Unit]
Description=External VM for NAT traffic
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStartPre=/bin/mkdir -p /var/run/vpp/console
ExecStartPre=/bin/chmod 777 /var/run/vpp/console
ExecStart=/usr/bin/qemu-system-x86_64 \\
  -name external-vm \\
  -machine accel=kvm,mem-merge=off \\
  -m 2048 \\
  -object memory-backend-file,id=mem0,size=2048M,mem-path=/dev/shm/external-vm-mem,share=on \\
  -numa node,memdev=mem0 \\
  -smp 2 \\
  -cpu host \\
  -enable-kvm \\
  -drive file=${disk_image},format=qcow2,if=virtio \\
  -drive file=${seed_image},if=virtio,format=raw,media=cdrom,readonly=on \\
  -chardev socket,id=char0,path=${socket_path} \\
  -netdev vhost-user,id=net0,chardev=char0 \\
  -device virtio-net-pci,netdev=net0,mac=${mac_address} \\
  -netdev tap,id=net1,script=/etc/qemu-ifup-virbr0,downscript=/etc/qemu-ifdown-virbr0 \\
  -device virtio-net-pci,netdev=net1,mac=${libvirt_mac} \\
  -virtfs local,path=${host_mount_dir},security_model=none,mount_tag=hostshare,id=hostshare \\
  -serial unix:${console_path},server,nowait \\
  -nographic
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  echo "  ✓ systemd-юнит для external VM сохранён в ${unit_path}"
  echo ""
}

configure_and_deploy_vms() {
  echo "=========================================="
  echo "Настройка виртуальных машин"
  echo "=========================================="
  echo ""

  # Создаем директорию для консольных сокетов с правами доступа
  sudo mkdir -p /var/run/vpp/console
  sudo chmod 777 /var/run/vpp/console
  echo "  ✓ Директория для консолей создана: /var/run/vpp/console"
  echo ""

  # Подготавливаем storage и диски VM
  ensure_vm_storage_ready
  ensure_vm_host_mounts_ready

  # Создаём внешнюю VM для NAT трафика
  echo "--- Настройка внешней VM ---"
  create_external_vm_systemd_unit

  # Создаём пользовательские VM
  local user_vms_count="${#VHOST_SOCKETS[@]}"
  for ((i = 0; i < user_vms_count; i++)); do
    local vm_dir="${USER_MACHINES_DIR[$i]}"
    local socket_path="${VHOST_SOCKETS[$i]}"
    local mac_address="${USER_MACHINES_MAC[$i]}"
    local unit_name="${USER_MACHINES_SYSTEMD_UNIT_NAME[$i]}"
    local libvirt_mac="${USER_MACHINES_LIBVIRT_MAC[$i]}"

    echo "--- Настройка ${unit_name} ---"
    create_user_vm_systemd_unit "${vm_dir}" "${socket_path}" "${mac_address}" "${unit_name}" "${libvirt_mac}"
  done

  sudo systemctl daemon-reload
  echo "  ✓ systemd перезагружен"
  echo ""

  # Запускаем внешнюю VM
  echo "=== Запуск виртуальных машин ==="
  echo "  Запуск ${EXTERNAL_VM_SYSTEMD_UNIT}..."
  if sudo systemctl start "${EXTERNAL_VM_SYSTEMD_UNIT}"; then
    echo "  ✓ ${EXTERNAL_VM_SYSTEMD_UNIT} запущен"
  else
    echo "  ✗ Ошибка при запуске ${EXTERNAL_VM_SYSTEMD_UNIT}"
    exit 1
  fi

  # Запускаем пользовательские VM
  for ((i = 0; i < user_vms_count; i++)); do
    local unit_name="${USER_MACHINES_SYSTEMD_UNIT_NAME[$i]}"
    echo "  Запуск ${unit_name}..."
    if sudo systemctl start "${unit_name}"; then
      echo "  ✓ ${unit_name} запущен"
    else
      echo "  ✗ Ошибка при запуске ${unit_name}"
      exit 1
    fi
  done

  echo ""
  echo "=========================================="
  echo "✓ Все виртуальные машины запущены"
  echo "=========================================="
}

stop_vms() {
  echo "=========================================="
  echo "Остановка виртуальных машин"
  echo "=========================================="
  echo ""

  # Останавливаем внешнюю VM
  echo "  Остановка ${EXTERNAL_VM_SYSTEMD_UNIT}..."
  if sudo systemctl is-active --quiet "${EXTERNAL_VM_SYSTEMD_UNIT}" 2>/dev/null; then
    if sudo systemctl stop "${EXTERNAL_VM_SYSTEMD_UNIT}"; then
      echo "  ✓ ${EXTERNAL_VM_SYSTEMD_UNIT} остановлен"
    else
      echo "  ✗ Ошибка при остановке ${EXTERNAL_VM_SYSTEMD_UNIT}"
    fi
  else
    echo "  ℹ ${EXTERNAL_VM_SYSTEMD_UNIT} не запущен"
  fi

  # Останавливаем пользовательские VM
  local user_vms_count="${#USER_MACHINES_SYSTEMD_UNIT_NAME[@]}"
  for ((i = 0; i < user_vms_count; i++)); do
    local unit_name="${USER_MACHINES_SYSTEMD_UNIT_NAME[$i]}"
    echo "  Остановка ${unit_name}..."
    if sudo systemctl is-active --quiet "${unit_name}" 2>/dev/null; then
      if sudo systemctl stop "${unit_name}"; then
        echo "  ✓ ${unit_name} остановлен"
      else
        echo "  ✗ Ошибка при остановке ${unit_name}"
      fi
    else
      echo "  ℹ ${unit_name} не запущен"
    fi
  done

  echo ""
  echo "=========================================="
  echo "✓ Все виртуальные машины остановлены"
  echo "=========================================="
}

prepare_vm_seed() {
  local vm_dir="$1"
  local seed_image="$2"
  local config_dir="${VM_CONFIG_PATH}/${vm_dir}"
  local user_data="${config_dir}/user-data"
  local meta_data="${config_dir}/meta-data"

  if [[ -z "${vm_dir}" || -z "${seed_image}" ]]; then
    echo "Usage: prepare_vm_seed <vm_dir> <seed_image_path>" >&2
    return 1
  fi

  echo "=== Создание seed-образа для ${vm_dir} ==="
  echo "  → Файл: ${seed_image}"
  cloud-localds "${seed_image}" "${user_data}" "${meta_data}"
  echo "  ✓ Seed-образ создан"
  echo ""
}

create_user_vm_systemd_unit() {
  local vm_dir="$1"
  local socket_path="$2"
  local mac_address="$3"
  local unit_name="$4"
  local disk_image="${VM_STORAGE_PATH}/${vm_dir}/disk.img"
  local seed_image="${VM_STORAGE_PATH}/${vm_dir}/seed.iso"
  local unit_path="/etc/systemd/system/${unit_name}.service"

  if [[ -z "${vm_dir}" || -z "${socket_path}" || -z "${mac_address}" || -z "${unit_name}" ]]; then
    echo "Usage: create_user_vm_systemd_unit <vm_dir> <socket_path> <mac_address> <unit_name>" >&2
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
ExecStart=/usr/bin/qemu-system-x86_64 \
  -name user-vm \
  -machine accel=kvm,mem-merge=off \
  -m 4096 \
  -object memory-backend-file,id=mem0,size=4096M,mem-path=/dev/shm/user-vm1-mem,share=on \
  -numa node,memdev=mem0 \
  -smp 2 \
  -cpu host \
  -enable-kvm \
  -drive file=${disk_image},format=qcow2,if=virtio \
  -drive file=${seed_image},if=virtio,format=raw,media=cdrom,readonly=on \
  -chardev socket,id=char0,path=${socket_path} \
  -netdev vhost-user,id=net0,chardev=char0 \
  -device virtio-net-pci,netdev=net0,mac=${mac_address} \
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

configure_and_deploy_vms() {
  echo "=========================================="
  echo "Настройка виртуальных машин"
  echo "=========================================="
  echo ""

  mkdir -p /var/run/vpp/console
  local user_vms_count="${#VHOST_SOCKETS[@]}"

  for ((i = 0; i < user_vms_count; i++)); do
    local vm_dir="${USER_MACHINES_DIR[$i]}"
    local socket_path="${VHOST_SOCKETS[$i]}"
    local mac_address="${USER_MACHINES_MAC[$i]}"
    local unit_name="${USER_MACHINES_SYSTEMD_UNIT_NAME[$i]}"

    echo "--- Настройка ${unit_name} ---"
    create_user_vm_systemd_unit "${vm_dir}" "${socket_path}" "${mac_address}" "${unit_name}"
  done

  systemctl daemon-reload
  echo "  ✓ systemd перезагружен"
  echo ""
  echo "=========================================="
  echo "✓ Настройка виртуальных машин завершена"
  echo "=========================================="
}
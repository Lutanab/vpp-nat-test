## Управление проектом

Проект использует единый CLI скрипт для управления всеми операциями:

```bash
./manage <команда>
```

### Доступные команды:

- `prepare` - Подготовка окружения (установка зависимостей VPP, QEMU, настройка libvirt)
- `setup-network` - Подготовка сетевой топологии (VPP bridge, vhost-интерфейсы, external vhost)
- `setup-vms` - Подготовка и запуск всех виртуальных машин (external VM + user VMs)
- `stop-vms` - Остановка всех виртуальных машин
- `clean-network` - Очистка сетевой топологии и vhost сокетов
- `help` - Показать справку

### Примеры использования:

```bash
# 1. Подготовка окружения (один раз при установке)
sudo ./manage prepare

# 2. Подготовка сетевой топологии VPP
sudo ./manage setup-network

# 3. Запуск виртуальных машин
sudo ./manage setup-vms

# 4. Остановка виртуальных машин
sudo ./manage stop-vms

# 5. Очистка сетевой конфигурации
sudo ./manage clean-network

# Справка
./manage help
```

## Базовая установка vpp

Перед тем как собирать:
```bash
sudo ./manage prepare
```

Очередной раз собрать:
```bash
sudo make pkg-deb-debug
sudo dpkg -i build-root/*.deb
```

Если перетер конфиги (напр startup.conf):
```bash
sudo cp ./configs/startu.conf /etc/vpp/startup.conf
sudo systemctl restart vpp
```

## Подключение к консолям виртуальных машин

После запуска виртуальных машин через `sudo ./manage setup-vms`, вы можете подключиться к их консолям через UNIX сокеты:

```bash
# Подключение к external VM
sudo socat -,raw,echo=0 unix-connect:/var/run/vpp/console/vpp-external-machine.sock

# Подключение к user VM 1
sudo socat -,raw,echo=0 unix-connect:/var/run/vpp/console/vpp-user-machine1.sock

# Подключение к user VM 2
sudo socat -,raw,echo=0 unix-connect:/var/run/vpp/console/vpp-user-machine2.sock
```

**Примечание:** Для выхода из консоли используйте `Ctrl+C` или закройте терминал.

## Проверка статуса виртуальных машин

```bash
# Проверка статуса всех VM
sudo systemctl status vpp-external-machine
sudo systemctl status vpp-user-machine1
sudo systemctl status vpp-user-machine2

# Просмотр логов VM
sudo journalctl -u vpp-external-machine -f
sudo journalctl -u vpp-user-machine1 -f
```

## Troubleshooting

### Проблема: VM не запускаются, ошибка "bridge helper failed"

Если вы видите в логах ошибку:
```
failed to parse default acl file '/etc/qemu/bridge.conf'
qemu-system-x86_64: -netdev bridge,id=net1,br=virbr0: bridge helper failed
```

**Решение:** Запустите `sudo ./manage prepare` еще раз. Эта команда автоматически настроит:
- Файл `/etc/qemu/bridge.conf` с разрешением на использование `virbr0`
- SUID права на `/usr/lib/qemu/qemu-bridge-helper`

После этого перезапустите виртуальные машины:
```bash
sudo ./manage stop-vms
sudo ./manage setup-vms
```
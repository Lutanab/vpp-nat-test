## Управление проектом

Проект использует единый CLI скрипт для управления всеми операциями:

```bash
./manage <команда>
```

### Доступные команды:

- `prepare` - Подготовка окружения (установка зависимостей VPP, QEMU, настройка libvirt)
- `setup-network` - Подготовка сетевой топологии (VPP bridge, vhost-интерфейсы, external vhost) + обязательный `--nat-mode <none|nat44|natmvp>`
- `setup-vms` - Подготовка и запуск всех виртуальных машин (external VM + user VMs)
- `stop-vms` - Остановка всех виртуальных машин
- `clean-network` - Очистка сетевой топологии и vhost сокетов
- `help` - Показать справку

### Размер дисков VM

При `sudo ./manage setup-vms` для каждой VM автоматически поддерживается минимальный размер виртуального диска `15G` (настраивается через `VM_MIN_DISK_SIZE` в `cli/constants.sh`).
Если диск уже содержит данные, он будет только увеличен (без shrink).

### Примеры использования:

```bash
# 1. Подготовка окружения (один раз при установке)
sudo ./manage prepare

# 2. Подготовка сетевой топологии VPP (обязательно указать NAT режим)
sudo ./manage setup-network --nat-mode none
# или:
sudo ./manage setup-network --nat-mode nat44
sudo ./manage setup-network --nat-mode natmvp

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
cd vpp
sudo make pkg-deb-debug
sudo dpkg -i build-root/*.deb
cd ../
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

## SSH доступ к VM через порты хоста

После `sudo ./manage prepare` автоматически настраивается проброс SSH-портов хоста на management-интерфейсы VM в `libvirt` сети:

- `8022 -> external_vm (10.8.2.10):22`
- `8122 -> user_vm_1 (10.8.2.11):22`
- `8222 -> user_vm_2 (10.8.2.12):22`

Примеры подключения:

```bash
ssh -p 8022 zero@<IP-хоста>
ssh -p 8122 zero@<IP-хоста>
ssh -p 8222 zero@<IP-хоста>
```

Проверить, что правила проброса активны:

```bash
sudo systemctl status vpp-libvirt-port-forward.service
sudo iptables -t nat -S PREROUTING | grep -E "8022|8122|8222"
```

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

## NAT режимы в `setup-network`

`setup-network` теперь принимает обязательный аргумент:

```bash
sudo ./manage setup-network --nat-mode <none|nat44|natmvp>
```

Поддерживаются режимы:
- `none` — NAT в VPP не используется;
- `nat44` — используется дефолтный VPP NAT44;
- `natmvp` — используется кастомный плагин `natmvp`.

Что делает команда автоматически:
- обновляет в `/etc/vpp/startup.conf` управляемый блок плагинов (`nat_plugin.so` / `natmvp_plugin.so`);
- перезапускает VPP;
- поднимает VPP-сетевую топологию (vhost + bridge/BVI);
- применяет runtime-конфигурацию выбранного NAT-режима.

### `natmvp` режим

```bash
sudo ./manage setup-network --nat-mode natmvp
```

Автоматически применяются команды:
- `natmvp set public-addr 10.8.0.1`
- `natmvp set port-range 20000 40000`
- `natmvp interface inside <BVI>`
- `natmvp interface outside <external-vhost>`

Проверка:

```bash
sudo vppctl show natmvp
```

### `nat44` режим

```bash
sudo ./manage setup-network --nat-mode nat44
```

Автоматически применяются команды:
- `nat44 plugin enable sessions 10000`
- `set interface nat44 in <BVI> out <external-vhost>`
- `nat44 add interface address <external-vhost>`

Проверка:

```bash
sudo vppctl show nat44 summary
sudo vppctl show nat44 interfaces
```

### `none` режим

```bash
sudo ./manage setup-network --nat-mode none
```

В этом режиме NAT-плагины выключаются в managed-блоке `startup.conf`, и runtime NAT-конфигурация не применяется.

### Смена NAT-режима (рекомендуемый сценарий)

При переключении режима NAT лучше выполнять полный цикл перезапуска топологии:

```bash
# 1) Остановить все VM
sudo ./manage stop-vms

# 2) Очистить сеть (остановка VPP + удаление старых интерфейсов)
sudo ./manage clean-network

# 3) Поднять сеть с нужным NAT-режимом
sudo ./manage setup-network --nat-mode <none|nat44|natmvp>

# 4) Снова запустить VM
sudo ./manage setup-vms
```

Это снижает риск «залипших» vhost-сокетов и рассинхронизации между VPP и уже запущенными VM.

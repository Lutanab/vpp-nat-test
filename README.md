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

### Размер дисков VM

При `sudo ./manage setup-vms` для каждой VM автоматически поддерживается минимальный размер виртуального диска `15G` (настраивается через `VM_MIN_DISK_SIZE` в `cli/constants.sh`).
Если диск уже содержит данные, он будет только увеличен (без shrink).

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

## MVP NAT-плагин `natmvp` (инструкция по применению)

В репозиторий добавлен MVP stateful NAT-плагина VPP: `vpp/src/plugins/natmvp`.

Ключевые свойства MVP:
- две таблицы (`in2out` и `out2in`) со ссылкой на общий объект сессии;
- per-bucket lock;
- фиксированный порядок захвата локов для двухтабличных операций;
- `exp_time` в сессии + lazy cleanup на write-path (при insert в oversized bucket).

### 1. Сборка и установка VPP с плагином

```bash
cd vpp
sudo make pkg-deb-debug
sudo dpkg -i build-root/*.deb
cd ..
```

### 2. Включение плагина в `startup.conf`

Добавьте в `/etc/vpp/startup.conf`:

```conf
plugins {
  plugin natmvp_plugin.so { enable }
}
```

После этого перезапустите VPP:

```bash
sudo systemctl restart vpp
```

### 3. Базовая конфигурация в `vppctl`

```bash
sudo vppctl
```

Пример настройки:

```vpp
natmvp set public-addr 203.0.113.10
natmvp set port-range 20000 40000

natmvp interface inside GigabitEthernet0/8/0
natmvp interface outside GigabitEthernet0/9/0
```

Где:
- `inside` — интерфейс для исходящего трафика internal -> remote;
- `outside` — интерфейс для входящего трафика remote -> public.

### 4. Проверка

```vpp
show natmvp
```

Команда выводит:
- текущий public IP;
- диапазон портов;
- число сессий (`live` и `total`).

### 5. Очистка сессий

```vpp
natmvp clear sessions
```

## Дефолтный NAT-плагин VPP (`nat44`) для сравнения

Если хотите сравнить поведение с вашим `natmvp`, поднимайте стандартный NAT VPP по шагам ниже.

### 1. Соберите и установите VPP

```bash
cd vpp
sudo make pkg-deb-debug
sudo dpkg -i build-root/*.deb
cd ..
```

### 2. Переключите плагины в `startup.conf`

В `/etc/vpp/startup.conf` укажите:

```conf
plugins {
  plugin natmvp_plugin.so { disable }
  plugin nat_plugin.so { enable }
}
```

После этого перезапустите VPP:

```bash
sudo systemctl restart vpp
```

### 3. Включите `nat44` и задайте роли интерфейсов

```bash
sudo vppctl
```

Пример минимальной динамической NAT44-конфигурации:

```vpp
nat44 plugin enable sessions 10000
set interface nat44 in GigabitEthernet0/8/0 out GigabitEthernet0/9/0
nat44 add address 203.0.113.10
```

Альтернатива для внешнего адреса с интерфейса:

```vpp
nat44 add interface address GigabitEthernet0/9/0
```

### 4. Проверка состояния

```vpp
show nat44 summary
show nat44 interfaces
show nat44 addresses
show nat44 sessions
```

### 5. Очистка сессий / выключение

```vpp
clear nat44 ed sessions
nat44 plugin disable
```

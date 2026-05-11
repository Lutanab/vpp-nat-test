## Управление проектом

Проект использует единый CLI скрипт для управления всеми операциями:

```bash
./manage <команда>
```

### Доступные команды:

- `prepare` - Подготовка окружения (установка зависимостей VPP, QEMU, настройка libvirt)
- `setup-network` - Подготовка сетевой топологии (VPP bridge, vhost-интерфейсы, external vhost) + обязательный `--nat-mode <none|nat44|nat_fo>`
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
sudo ./manage setup-network --nat-mode nat_fo

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

## SSH доступ к VM через virbr0

Если команда вида `ssh zero@10.8.2.10` отвечает `Permission denied (publickey)`,
значит VM принимает только SSH-ключи, а публичный ключ хоста еще не добавлен в
`authorized_keys` пользователя `zero`. Парольный SSH в cloud-init отключен
параметром `ssh_pwauth: false`, поэтому `ssh-copy-id` в такой ситуации не
поможет.

Сначала подготовьте публичный ключ на хосте и положите его в host mount каждой
VM:

```bash
test -f ~/.ssh/id_ed25519.pub || ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519

cp ~/.ssh/id_ed25519.pub virtual_machines/host_mounts/external_vm/host_id_ed25519.pub
cp ~/.ssh/id_ed25519.pub virtual_machines/host_mounts/user_vm_1/host_id_ed25519.pub
cp ~/.ssh/id_ed25519.pub virtual_machines/host_mounts/user_vm_2/host_id_ed25519.pub
```

Затем зайдите в нужную VM. Например, через serial console:

```bash
sudo socat -,raw,echo=0 unix-connect:/var/run/vpp/console/vpp-external-machine.sock
```

Внутри VM добавьте ключ:

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
cat /mnt/host/host_id_ed25519.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

После этого с хоста можно подключаться напрямую через `virbr0`-IP:

```bash
ssh zero@10.8.2.10  # external_vm
ssh zero@10.8.2.11  # user_vm_1
ssh zero@10.8.2.12  # user_vm_2
```

Если VM была пересоздана и SSH ругается на host key, удалите старый ключ:

```bash
ssh-keygen -R 10.8.2.10
ssh-keygen -R 10.8.2.11
ssh-keygen -R 10.8.2.12
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

## Health-серверы в ВМ

На каждой VM (`external_vm`, `user_vm_1`, `user_vm_2`) настроен небольшой Python health-сервер на порту `7000`.
Он запускается как обычный `systemd`-демон вместе с ОС и на любой HTTP-запрос отвечает `HTTP 200`.

Это позволяет быстро проверить, жива ли VM:

```bash
curl --noproxy '*' -i http://10.8.2.10:7000/
curl --noproxy '*' -i http://10.8.2.11:7000/
curl --noproxy '*' -i http://10.8.2.12:7000/
```

Если VM работает и сервис поднят, в ответе будет статус `200`.

Если в окружении заданы `http_proxy/https_proxy`, без `--noproxy` запросы к `10.8.2.x`
могут уходить в прокси и давать `503`, хотя сеть libvirt и VM исправны.

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
sudo ./manage setup-network --nat-mode <none|nat44|nat_fo>
```

Поддерживаются режимы:
- `none` — NAT в VPP не используется;
- `nat44` — используется дефолтный VPP NAT44;
- `nat_fo` — используется кастомный плагин `nat_fo`.

Что делает команда автоматически:
- обновляет в `/etc/vpp/startup.conf` управляемый блок плагинов (`nat_plugin.so` / `nat_fo_plugin.so`);
- перезапускает VPP;
- поднимает VPP-сетевую топологию (vhost + bridge/BVI);
- применяет runtime-конфигурацию выбранного NAT-режима.

### `nat_fo` режим

```bash
sudo ./manage setup-network --nat-mode nat_fo
```

Автоматически применяются команды:
- `nat_fo set public-addr 10.8.0.1`
- `nat_fo set port-range 20000 40000`
- `nat_fo interface inside <BVI>`
- `nat_fo interface outside <external-vhost>`

Проверка:

```bash
sudo vppctl show nat_fo
```

#### CLI `nat_fo` (быстрый справочник)

```bash
# Общий статус плагина (public addr, портовый диапазон, live/total сессии)
sudo vppctl show nat_fo

# Очистить все текущие NAT-сессии
sudo vppctl nat_fo clear sessions

# Очистить runtime-сессии и durable session store в shmem
sudo vppctl nat_fo shm clear

# Полностью пересоздать shm-сегмент для чистого теста recovery
sudo vppctl nat_fo shm unlink

# Проверить, что плагин загружен
sudo vppctl show plugins | grep nat_fo
```

Что важно: в текущей реализации `nat_fo` CLI показывает агрегированную статистику (`live/total`),  
отдельной команды для детального списка каждой сессии пока нет.

`show nat_fo` теперь также показывает состояние `shmem`: путь сегмента, число слотов,
а также сколько сессий было восстановлено и сколько было отброшено как протухшие
при последнем recovery после старта VPP.

Если при `setup-network --nat-mode nat_fo` видно `unknown input 'nat_fo ...'`, это означает, что в системном VPP нет `nat_fo_plugin.so` (или он не загрузился после рестарта).  
Проверьте и переустановите пакеты VPP из этого репозитория:

```bash
ls /usr/lib/x86_64-linux-gnu/vpp_plugins/nat_fo_plugin.so

cd vpp
sudo make pkg-deb-debug
sudo dpkg -i build-root/*.deb
sudo systemctl restart vpp
sudo vppctl show plugins | grep nat_fo
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
sudo ./manage setup-network --nat-mode <none|nat44|nat_fo>

# 4) Снова запустить VM
sudo ./manage setup-vms
```

Это снижает риск «залипших» vhost-сокетов и рассинхронизации между VPP и уже запущенными VM.

### Автоматическое переключение NAT через CLI

Сначала подготовьте workspace:

```bash
uv sync
```

Показать текущий режим:

```bash
uv run manage-nat show
```

Переключить режим:

```bash
uv run manage-nat switch <none|nat44|nat_fo>
```

Если режим уже записан в `startup.conf`, но нужно принудительно пересобрать `nat_fo`,
пересоздать runtime-топологию и перезапустить VM:

```bash
uv run manage-nat switch --restart nat_fo
```

CLI выполняет шаги по порядку:
- `./manage stop-vms`
- `./manage clean-network`
- если выбран `nat_fo`: `cd vpp && make pkg-deb-debug`, затем `dpkg -i build-root/*.deb`
- `./manage setup-network --nat-mode <mode>`
- `./manage setup-vms`
- healthcheck: для каждой VM polling `http://<vm-libvirt-ip>:7000/` (раз в 1 сек, до 2 минут) до `HTTP 200`

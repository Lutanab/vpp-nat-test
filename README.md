# VPP NAT Active/Standby Test VM

Репозиторий содержит CLI `manage-nat` для подготовки тестового стенда с двумя VM
и двумя VPP-инстансами:

- `user_vm` - внутренняя VM;
- `external_vm` - внешняя VM;
- `vpp.service` - primary VPP;
- `vpp-secondary.service` - secondary VPP.

В текущей схеме `setup-vms` поднимает VM и их vhost-user сокеты, `setup-vpp`
поднимает два VPP-инстанса, а подключение VPP к VM vhost-user сокетам делает
внешний агент.

## Быстрый старт

```bash
uv sync

# 1. Подготовить зависимости VPP/QEMU/libvirt.
manage-nat prepare

# 2. Поднять VM.
manage-nat setup-vms

# 3. Поднять primary/secondary VPP.
manage-nat setup-vpp nat_fo

# Можно поднять только один инстанс.
manage-nat setup-vpp primary

# 4. Посмотреть состояние стенда и vhost-user подключения.
manage-nat show

# Запустить простой сценарий restart active-ноды.
manage-nat scenario restart

# Остановить оба VPP-инстанса.
manage-nat teardown-vpp

# Или остановить только один.
manage-nat teardown-vpp secondary
```

## Команды

```bash
manage-nat --help
```

Основные команды:

- `prepare` - ставит зависимости VPP/QEMU/libvirt/cloud-init.
- `setup-vms` - создает cloud-init seed, systemd units и запускает `user_vm`/`external_vm`.
- `setup-vpp [none|nat44|nat_fo] [all|primary|secondary]` - запускает VPP без подключения к VM vhost-user сокетам.
- `scenario restart` - прогоняет простой restart active-ноды.
- `teardown-vpp [all|primary|secondary]` - останавливает VPP systemd services.
- `show` - показывает NAT-режим, состояние primary/secondary VPP и vhost-user подключения.

## VM

```bash
manage-nat setup-vms
```

Команда управляет systemd units:

- `vpp-external-vm.service`
- `vpp-user-vm.service`
- `vpp-vm-ssh-forward.service`

VM параметры:

| VM | dataplane IP | management IP | SSH port | vhost-user socket |
| --- | --- | --- | --- | --- |
| `external_vm` | `10.8.0.2/24` | `10.8.2.10` | `8022` | `/run/vpp/vhost-external.sock` |
| `user_vm` | `10.8.1.2/24` | `10.8.2.11` | `8122` | `/run/vpp/vhost-user.sock` |

Dataplane routes are intentionally narrow:

- `user_vm` routes `10.8.0.0/24` via `data0 -> 10.8.1.1`;
- `external_vm` routes `10.8.1.0/24` via `data0 -> 10.8.0.1`;
- default routes, including Internet access such as `8.8.8.8`, go via `mgmt0`
  and the libvirt `virbr0` network.

SSH:

```bash
ssh -p 8022 zero@<host>  # external_vm
ssh -p 8122 zero@<host>  # user_vm
```

После первого запуска добавьте нужный ключ в каждую VM явно:

```bash
ssh-copy-id -p 8022 -i ~/.ssh/id_ed25519.pub zero@<host>  # external_vm
ssh-copy-id -p 8122 -i ~/.ssh/id_ed25519.pub zero@<host>  # user_vm
```

Первичный пароль пользователя `zero`: `orez1234`. После этого вход должен идти
по ключу обычной командой `ssh -p ... zero@<host>`. Если VM disk image
пересоздавался с нуля, повторите `ssh-copy-id`.

`vpp-vm-ssh-forward.service` держит `socat` listeners на `0.0.0.0:8022`
и `0.0.0.0:8122`. Быстрая проверка с хоста:

```bash
ss -ltnp | grep -E ':8022|:8122'
ssh -p 8022 zero@127.0.0.1
ssh -p 8122 zero@127.0.0.1
```

Serial console:

```bash
sudo socat -,raw,echo=0 unix-connect:/run/vpp/console/vpp-external-vm.sock
sudo socat -,raw,echo=0 unix-connect:/run/vpp/console/vpp-user-vm.sock
```

Serial boot logs:

```bash
sudo tail -n 120 /var/log/vpp/vpp-external-vm-serial.log
sudo tail -n 120 /var/log/vpp/vpp-user-vm-serial.log
```

Vhost-user сокеты создаются VM в server-mode. До подключения VPP в логах QEMU
может быть строка `QEMU waiting for connection`; это ожидаемое состояние.

## VPP

```bash
manage-nat setup-vpp nat_fo
```

Без указания инстанса команда готовит оба VPP-инстанса. Если нужно тронуть
только один:

```bash
manage-nat setup-vpp primary
manage-nat setup-vpp secondary
manage-nat setup-vpp nat_fo primary
```

Инстансы:

| Role | service | startup config | CLI socket |
| --- | --- | --- | --- |
| primary | `vpp.service` | `/etc/vpp/startup.conf` | `/run/vpp/cli.sock` |
| secondary | `vpp-secondary.service` | `/etc/vpp/startup-secondary.conf` | `/run/vpp-secondary/cli.sock` |

`setup-vpp` не создает vhost-user интерфейсы и не подключается к VM socket paths.
Эти интерфейсы создает `vpp-ha-ctl`; он же назначает им VPP-side IP из своей
конфигурации.

Failover-стенд по умолчанию запускает VPP с одним worker thread на инстанс.
Это значение задано в `VPP_FAILOVER_WORKERS` и используется default-ом
`manage-nat setup-vpp --n-workers`.

Ожидаемые интерфейсы VPP после подключения `vpp-ha-ctl`:

| Role | interface | IP |
| --- | --- | --- |
| outside | `VirtualEthernet0/0/0` | `10.8.0.1/24` |
| inside | `VirtualEthernet0/0/1` | `10.8.1.1/24` |

Primary `vppctl`:

```bash
sudo vppctl show version
sudo vppctl -s /run/vpp/cli.sock show version
```

Secondary `vppctl`:

```bash
sudo vppctl -s /run/vpp-secondary/cli.sock show version
```

Проверить сервисы:

```bash
systemctl status vpp.service
systemctl status vpp-secondary.service
```

Остановить оба VPP-инстанса:

```bash
manage-nat teardown-vpp
```

Остановить один инстанс:

```bash
manage-nat teardown-vpp primary
manage-nat teardown-vpp secondary
```

Проверить vhost-user подключения:

```bash
manage-nat show
```

Фрагмент `vhosts` до подключения внешним агентом:

```text
vm           socket                        primary  secondary
-----------  ----------------------------  -------  ---------
external_vm  /run/vpp/vhost-external.sock  -        -
user_vm      /run/vpp/vhost-user.sock      -        -
```

## Сценарии

```bash
manage-nat scenario restart
```

`restart` проверяет, что primary VPP (`vpp.service`) активен и отвечает через
`/run/vpp/cli.sock`, затем:

- идемпотентно останавливает `vpp-secondary.service`;
- идемпотентно останавливает `vpp-ha-ctld.service`;
- останавливает только `vpp.service`;
- ждет 3 секунды;
- запускает `vpp.service`;
- через 400 мс запускает `vpp-ha-ctld.service`.

## NAT modes

`setup-vpp` принимает режим:

```bash
manage-nat setup-vpp none
manage-nat setup-vpp nat44
manage-nat setup-vpp nat_fo
```

Режим записывается в managed plugin block обоих startup config:

- `none` - `nat_plugin.so` и `nat_fo_plugin.so` выключены;
- `nat44` - включен стандартный VPP NAT44 plugin;
- `nat_fo` - включен кастомный `nat_fo_plugin.so`.

Проверка primary:

```bash
manage-nat show
sudo vppctl show plugins | grep nat
```

Пример `manage-nat show`:

```text
nat_mode=nat_fo
primary=up
secondary=up
vhosts:
vm           socket                        primary                 secondary
-----------  ----------------------------  ----------------------  ---------
external_vm  /run/vpp/vhost-external.sock  VirtualEthernet0/0/0 up -
user_vm      /run/vpp/vhost-user.sock      VirtualEthernet0/0/1 up -
```

Проверка secondary:

```bash
sudo vppctl -s /run/vpp-secondary/cli.sock show plugins | grep nat
```

## Сборка VPP

Если нужно пересобрать и переустановить VPP packages:

```bash
cd vpp
rm -f build-root/*.deb
sudo make pkg-deb-debug
sudo dpkg -i build-root/*.deb
cd ..
```

После переустановки заново примените runtime configs:

```bash
manage-nat setup-vpp nat_fo
```

## Диагностика

Список актуальных systemd units стенда:

```bash
systemctl list-unit-files 'vpp*' --no-pager
```

Ожидаемые units:

- `vpp.service`
- `vpp-secondary.service`
- `vpp-external-vm.service`
- `vpp-user-vm.service`
- `vpp-vm-ssh-forward.service`
- `vpp.slice`

Проверить процессы:

```bash
pgrep -af 'vpp|qemu-system'
```

Проверить VM management leases:

```bash
virsh net-dhcp-leases default
```

Проверить runtime sockets:

```bash
ls -l /run/vpp /run/vpp-secondary /run/vpp/console
```

Посмотреть VPP логи:

```bash
journalctl -u vpp.service -n 80 --no-pager
journalctl -u vpp-secondary.service -n 80 --no-pager
```

Некоторые предупреждения VPP startup не являются ошибкой для текущего стенда:

- `uio_pci_generic not found` - модуль нужен для PCI/UIO сценариев, не для VM vhost-user стенда;
- `perfmon: skipping source 'intel-uncore'` - perf counters недоступны в текущей среде;
- `vat_plugin_register: ... plugin not loaded` - optional plugin не загружен.

Если `systemctl is-active vpp.service vpp-secondary.service` показывает `active`,
а `vppctl show version` отвечает для обоих CLI sockets, VPP-инстансы запущены.

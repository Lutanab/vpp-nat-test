## Управление проектом

Проект использует CLI `manage-nat` для управления NAT-режимом и сетевой топологией.

### Доступные команды `manage-nat`:

- `prepare` - Подготовка окружения (зависимости VPP и системные пакеты).
- `network setup <none|nat44|nat_fo>` - Подготовка runtime-сети VPP и применение NAT-режима.
- `network clean` - Очистка runtime-сетевой топологии.
- `show` - Показать NAT-режим из managed-блока `/etc/vpp/startup.conf`.
- `switch <none|nat44|nat_fo>` - Полный сценарий переключения NAT-режима.

### Примеры использования:

```bash
# 1. Подготовка окружения (один раз при установке)
manage-nat prepare

# 2. Подготовка сетевой топологии VPP (обязательно указать NAT режим)
manage-nat network setup none
# или:
manage-nat network setup nat44
manage-nat network setup nat_fo

# 3. Очистка сетевой конфигурации
manage-nat network clean

# Справка по manage-nat
manage-nat --help
```

## Базовая установка vpp

Перед тем как собирать:
```bash
manage-nat prepare
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

## NAT режимы в `manage-nat network setup`

Команда принимает обязательный аргумент NAT-режима:

```bash
manage-nat network setup <none|nat44|nat_fo> [--n_workers N]
```

Поддерживаются режимы:
- `none` — NAT в VPP не используется;
- `nat44` — используется дефолтный VPP NAT44;
- `nat_fo` — используется кастомный плагин `nat_fo`.

Что делает команда автоматически:
- обновляет в `/etc/vpp/startup.conf` управляемый блок плагинов (`nat_plugin.so` / `nat_fo_plugin.so`);
- обновляет `cpu`-параметры VPP в `/etc/vpp/startup.conf` (`main-core` и `corelist-workers` через `--n_workers`);
- перезапускает VPP;
- поднимает VPP-сетевую топологию (memif + bridge/BVI), создавая по `N` RX/TX-очередей на hot-path memif-интерфейсах при `--n_workers N`;
- применяет runtime-конфигурацию выбранного NAT-режима.

После подключения TRex к memif-сокетам load-test дополнительно назначает RX-очереди hot-path memif-интерфейсов воркерам 1:1: queue `i` -> worker `i`.

### `nat_fo` режим

```bash
manage-nat network setup nat_fo
```

Автоматически применяются команды:
- `nat_fo set public-addr 10.8.0.1`
- `nat_fo set port-range 20000 40000`
- `nat_fo interface inside <BVI>`
- `nat_fo interface outside <outside-memif>`

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

Если при `manage-nat network setup nat_fo` видно `unknown input 'nat_fo ...'`, это означает, что в системном VPP нет `nat_fo_plugin.so` (или он не загрузился после рестарта).  
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
manage-nat network setup nat44
```

Автоматически применяются команды:
- `nat44 plugin enable sessions 10000`
- `set interface nat44 in <BVI> out <outside-memif>`
- `nat44 add interface address <outside-memif>`

Проверка:

```bash
sudo vppctl show nat44 summary
sudo vppctl show nat44 interfaces
```

### `none` режим

```bash
manage-nat network setup none
```

В этом режиме NAT-плагины выключаются в managed-блоке `startup.conf`, и runtime NAT-конфигурация не применяется.

### Смена NAT-режима (рекомендуемый сценарий)

При переключении режима NAT лучше выполнять полный цикл пересоздания топологии:

```bash
# 1) Очистить сеть (остановка VPP + удаление старых интерфейсов)
manage-nat network clean

# 2) Поднять сеть с нужным NAT-режимом
manage-nat network setup <none|nat44|nat_fo>
```

### Автоматическое переключение NAT через CLI

Сначала подготовьте workspace:

```bash
uv sync
```

Показать текущий режим:

```bash
manage-nat show
```

Переключить режим:

```bash
manage-nat switch <none|nat44|nat_fo>
```

Если режим уже записан в `startup.conf`, но нужно принудительно пересобрать `nat_fo`
и пересоздать runtime-топологию:

```bash
manage-nat switch --restart nat_fo
```

CLI выполняет шаги по порядку:
- `manage-nat network clean`
- если выбран `nat_fo`: `cd vpp && make pkg-deb-debug`, затем `dpkg -i build-root/*.deb`
- `manage-nat network setup <mode>`

## Мониторинг ресурсов (VPP/TRex)

Найти PID процессов:

```bash
pgrep -af vpp
pgrep -af t-rex-64
```

Смотреть их вместе в `htop`:

```bash
htop -p <PID_VPP>,<PID_TREX>
```

Проверить, на какие CPU-ядра приземлились потоки VPP:

```bash
sudo vppctl show threads
```

Проверить текущую `cpu`-конфигурацию в `startup.conf`:

```bash
sudo grep -nE "^[[:space:]]*cpu[[:space:]]*\\{|^[[:space:]]*main-core|^[[:space:]]*corelist-workers" /etc/vpp/startup.conf
```

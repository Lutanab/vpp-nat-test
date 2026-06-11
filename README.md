## Управление проектом

Проект использует CLI `manage-nat` для управления VPP/TRex стендом и NAT-режимом.

### Доступные команды `manage-nat`:

- `prepare` - Подготовка окружения (зависимости VPP и системные пакеты).
- `setup-vpp <none|nat44|nat_fo>` - Поднять VPP в нужном NAT-режиме и с нужным числом workers.
- `teardown-vpp` - Остановить VPP и очистить runtime-топологию.
- `setup-trex [vpp]` - Поднять TRex server, подключенный memif-интерфейсами к VPP.
- `teardown-trex` - Остановить TRex server.
- `show` - Показать состояние VPP/TRex стенда.

### Примеры использования:

```bash
# 1. Подготовка окружения (один раз при установке)
manage-nat prepare

# 2. Подготовка VPP (обязательно указать NAT режим)
manage-nat setup-vpp none
# или:
manage-nat setup-vpp nat44
manage-nat setup-vpp nat_fo

# 3. Подключить TRex к VPP memif-топологии
manage-nat setup-trex

# 4. Показать состояние стенда
manage-nat show

# 5. Остановить runtime-компоненты
manage-nat teardown-trex
manage-nat teardown-vpp

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
rm -f build-root/*.deb
sudo make pkg-deb-debug
sudo dpkg -i build-root/*.deb
sudo systemctl restart vpp
cd ..
```

Если перетер конфиги (напр startup.conf):
```bash
sudo cp ./configs/startu.conf /etc/vpp/startup.conf
sudo systemctl restart vpp
```

## NAT режимы в `manage-nat setup-vpp`

Команда принимает обязательный аргумент NAT-режима:

```bash
manage-nat setup-vpp <none|nat44|nat_fo> [--n_workers N] [--memif-ring-size SIZE]
```

Поддерживаются режимы:
- `none` — NAT в VPP не используется;
- `nat44` — используется дефолтный VPP NAT44;
- `nat_fo` — используется кастомный плагин `nat_fo`.

Что делает команда автоматически:
- обновляет в `/etc/vpp/startup.conf` управляемый блок плагинов (`nat_plugin.so` / `nat_fo_plugin.so`);
- обновляет `cpu`-параметры VPP в `/etc/vpp/startup.conf` (`main-core` и `corelist-workers` через `--n_workers`);
- перезапускает VPP;
- поднимает фиксированную VPP-сетевую топологию из 8 memif-пар (`8` inside + `8` outside интерфейсов);
- создает memif-интерфейсы с `ring-size` (по умолчанию `16384`, можно переопределить через `--memif-ring-size`);
- в dataplane/NAT используются только первые `N` пар (по `--n_workers N`; для `n_workers=0` используется первая пара);
- применяет runtime-конфигурацию выбранного NAT-режима.

После подключения TRex к memif-сокетам load-test дополнительно назначает пары интерфейсов воркерам 1:1:
`worker i -> inside_i(queue 0) + outside_i(queue 0)`.

### `nat_fo` режим

```bash
manage-nat setup-vpp nat_fo
# или с кастомным размером кольца:
manage-nat setup-vpp nat_fo --memif-ring-size 2048
```

Автоматически применяются команды:
- `nat_fo set public-addr 10.8.0.1`
- `nat_fo interface inside <inside-memif-i>` для каждой пары
- `nat_fo interface outside <outside-memif-i>` для каждой пары
- `nat_fo map internal <inside-host-ip-i> public <outside-ip-i>` для каждой пары

`nat_fo` работает в identity-port режиме: source IP переписывается в public/external IP,
а source port остается тем же. Для явного 1:1 соответствия можно добавлять mappings:

```bash
sudo vppctl nat_fo map internal 10.8.1.2 public 10.8.0.1
```

Если mapping для internal IP не найден, используется fallback `nat_fo set public-addr`.

Проверка:

```bash
sudo vppctl show nat_fo
```

#### CLI `nat_fo` (быстрый справочник)

```bash
# Общий статус плагина (public addr, identity-port mode, mappings, live/total сессии)
sudo vppctl show nat_fo

# Добавить/обновить 1:1 mapping internal -> public/external IP
sudo vppctl nat_fo map internal 10.8.1.2 public 10.8.0.1

# Удалить mapping для internal IP
sudo vppctl nat_fo map internal 10.8.1.2 disable

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

Если при `manage-nat setup-vpp nat_fo` видно `unknown input 'nat_fo ...'`, это означает, что в системном VPP нет `nat_fo_plugin.so` (или он не загрузился после рестарта).
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
manage-nat setup-vpp nat44
```

Автоматически применяются команды:
- `nat44 plugin enable sessions 10000`
- `set interface nat44 in <inside-memif> out <outside-memif>`
- `nat44 add interface address <outside-memif>`

Проверка:

```bash
sudo vppctl show nat44 summary
sudo vppctl show nat44 interfaces
```

### `none` режим

```bash
manage-nat setup-vpp none
```

В этом режиме NAT-плагины выключаются в managed-блоке `startup.conf`, и runtime NAT-конфигурация не применяется.

### Управление VPP/TRex runtime

`setup-vpp` идемпотентен: его можно повторно запускать для нужного режима, числа workers и ring-size.
TRex поднимается отдельной командой после VPP, чтобы явно контролировать порядок runtime-компонентов:

```bash
# Поднять или пересоздать VPP runtime
manage-nat setup-vpp <none|nat44|nat_fo> [--n-workers N] [--memif-ring-size SIZE]

# Подключить TRex к VPP memif-сокетам
manage-nat setup-trex

# Остановить runtime
manage-nat teardown-trex
manage-nat teardown-vpp
```

### Workspace

Сначала подготовьте Python-окружение:

```bash
uv sync
```

Показать текущий режим:

```bash
manage-nat show
```

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

## Замер CPU time воркеров VPP

Скрипт `record_worker_cpu.py` пишет сырые счетчики процессорного времени для `vpp_wk_*`:

- читает `n_workers` из load config (или из `--n-workers`);
- проверяет, что существуют все `vpp_wk_0..vpp_wk_(N-1)`, иначе завершаетcя с ошибкой;
- проверяет affinity воркеров относительно `VPP_CPU_MAIN_CORE` (`worker i` должен включать `main_core + 1 + i`);
- поллит каждые `35ms` по умолчанию и пишет `worker_cpu_raw.tsv`.

Пример ручного запуска (в отдельном терминале):

```bash
python3 record_worker_cpu.py
```

Параллельно запускается прогон:

```bash
test-nat load
```

После завершения прогона остановите поллер (`Ctrl+C`) и соберите per-step таблицу:

```bash
python3 build_worker_cpu_steps.py
```

Результат: `worker_cpu_steps.tsv` в каталоге результатов.  
Ключевые колонки: `step_index`, `target_pps`, `wall_time_sec`, `cpu_time_sec`,
`avg_used_cores`, `avg_worker_utilization_percent`.

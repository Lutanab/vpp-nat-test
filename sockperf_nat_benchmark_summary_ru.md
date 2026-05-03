# Использование `sockperf` для benchmark-тестирования NAT-сервиса

## Контекст задачи

Есть стенд из трёх логических компонентов:

```text
User VM  --->  NAT service  --->  External VM
```

Цель — сравнить производительность собственного NAT-сервиса с референсным NAT-сервисом.

Оба NAT-варианта считаются userspace/systemd-сервисами, то есть их можно запускать, останавливать и измерять через systemd/cgroup.

Основной benchmark-алгоритм ищет максимальный PPS, при котором NAT-сервис ещё укладывается в допустимый `loss_rate`.

Для каждого фиксированного профиля теста задаются:

```text
nat_implementation
packet_size
n_flows
target_loss_rate
search_initial_pps
search_max_pps
search_relative_precision
step_duration
```

Поиск делается так:

1. Экспоненциальный поиск интервала `[left_pps, right_pps]`.
2. Бинарный поиск внутри найденного интервала.
3. На каждой точке вызывается ровно один `run_step`.
4. Остановка бинарного поиска по относительной точности:

```python
(right_pps - left_pps) / left_pps <= search_relative_precision
```

Итоговая метрика:

```text
max_stable_pps = left_pps
```

где `left_pps` — максимальная найденная точка, на которой `loss_rate <= target_loss_rate`.

---

## Почему основной инструмент — `sockperf`

`sockperf` хорошо подходит под текущую модель тестирования, потому что он:

- работает через обычный socket API;
- поддерживает UDP и TCP;
- имеет client/server-модель;
- умеет задавать интенсивность нагрузки через `--mps` / `--pps`;
- умеет задавать размер сообщения через `--msg-size`;
- умеет latency under load;
- отдаёт latency percentiles;
- может детектировать потери, дубликаты и out-of-order сообщения через gap detection;
- ближе к модели `run_step(target_pps, packet_size, n_flows, duration)`, чем `iperf3`.

`iperf3` можно оставить как smoke-test инструмент, но не как основной benchmark-инструмент. Он удобен для проверки связности и грубой пропускной способности, но хуже подходит для точного управления PPS, flow profile и latency percentiles.

`TRex` можно рассматривать как следующий этап, если потребуется DPDK/raw packet generator или если `sockperf` станет bottleneck-ом. Но для текущего стенда с двумя ВМ и userspace NAT-сервисом `sockperf` проще и ближе к нужной модели.

---

## Роли машин

### User VM

На user VM запускается `sockperf` client.

Он:

- генерирует трафик в сторону External VM;
- задаёт целевой PPS;
- задаёт размер сообщения;
- задаёт длительность ступеньки;
- частично измеряет latency.

Основной режим:

```bash
sockperf under-load
```

### External VM

На external VM запускается `sockperf` server.

Он:

- принимает трафик от user VM;
- отвечает на часть или все сообщения, если включён режим reply;
- может фиксировать gap detection;
- помогает определить потери, дубликаты и out-of-order сообщения.

Основной режим:

```bash
sockperf server
```

### NAT VM / NAT host

На машине с NAT запускается один из NAT-сервисов:

```text
custom NAT systemd service
reference NAT systemd service
```

На этой же машине снимаются resource-метрики:

```text
CPU cgroup-а NAT-сервиса
RAM cgroup-а NAT-сервиса
```

Дополнительно желательно собирать sanity-check метрики всей NAT-машины:

```text
total CPU на NAT VM
total RAM на NAT VM
```

---

## Базовая схема запуска

```text
User VM                         NAT service                         External VM
--------                        -----------                         -----------
sockperf client  ------------>  custom/reference NAT  ------------> sockperf server
under-load                                                          server
```

---

## Логика `run_step`

Функция `run_step` получает:

```python
run_step(
    target_pps: int,
    packet_size: int,
    n_flows: int,
    duration_sec: int,
) -> StepResult
```

Рекомендуемая логика:

1. Перед `run_step` выполняется один сброс/рестарт NAT-сервиса.
2. Проверяется, что `sockperf server` поднят на External VM.
3. На User VM запускается `sockperf under-load`.
4. В начале `run_step` допускается warmup, в ходе которого NAT-таблицы сессий прогреваются.
5. Measurement-фаза идёт уже по прогретым таблицам.
6. Собираются результаты `sockperf`.
7. Собираются CPU/RAM-метрики NAT-сервиса.
8. Возвращается dataclass с результатами ступеньки.

Пример структуры результата:

```python
from dataclasses import dataclass

@dataclass
class StepResult:
    target_pps: int
    packet_size: int
    n_flows: int
    duration_sec: int

    loss_rate: float

    latency_avg: float
    latency_p50: float
    latency_p95: float
    latency_p99: float

    nat_cpu_avg: float
    nat_cpu_max: float
    nat_ram_avg: float
    nat_ram_max: float

    sent_packets: int
    received_packets: int
    dropped_packets: int
    duplicated_packets: int | None = None
    out_of_order_packets: int | None = None
```

---

## Важное методологическое решение про warmup

В текущей версии методики фиксируется такой режим:

```text
Сброс NAT-сервиса выполняется один раз перед run_step.
Внутри run_step есть warmup.
Measurement-фаза идёт уже по прогретым NAT session tables.
```

Это приемлемый режим.

Он означает, что тест в основном измеряет forwarding по уже созданным NAT-сессиям, а не чистую скорость создания новых сессий.

Если в будущем нужно будет отдельно измерять создание новых NAT-сессий, лучше сделать отдельный benchmark-сценарий:

```text
session_creation_benchmark
```

Для текущей версии это не требуется.

---

## Базовый запуск `sockperf server`

На External VM:

```bash
sockperf server \
  -i <EXTERNAL_VM_IP> \
  -p 5001 \
  --msg-size <MAX_PACKET_SIZE> \
  --gap-detection
```

Пример:

```bash
sockperf server \
  -i 10.0.2.20 \
  -p 5001 \
  --msg-size 128 \
  --gap-detection
```

---

## Базовый запуск `sockperf under-load`

На User VM:

```bash
sockperf under-load \
  -i <EXTERNAL_VM_IP> \
  -p 5001 \
  --msg-size <PAYLOAD_SIZE> \
  --time <MEASUREMENT_SECONDS> \
  --mps <TARGET_PPS> \
  --reply-every <N>
```

Пример:

```bash
sockperf under-load \
  -i 10.0.2.20 \
  -p 5001 \
  --msg-size 128 \
  --time 60 \
  --mps 100000 \
  --reply-every 100
```

Смысл параметров:

```text
-i             IP адрес External VM
-p             порт sockperf server
--msg-size     размер сообщения/payload
--time         длительность measurement-фазы
--mps          target messages per second, фактически target PPS
--reply-every  отвечать не на каждый пакет, а на каждый N-й
```

`--reply-every` полезен при высокой нагрузке, чтобы не превращать каждый пакет в ping-pong и не удваивать нагрузку ответными пакетами.

---

## Как маппить `packet_size`

`packet_size` в методике нужно аккуратно сопоставить с `--msg-size`.

`sockperf --msg-size` задаёт размер сообщения на уровне payload, а не полный Ethernet frame size.

Поэтому нужно явно зафиксировать в коде и документации:

```text
packet_size в benchmark config = sockperf msg-size
```

или, если нужен именно L2/L3 packet size, завести преобразование:

```text
sockperf_msg_size = target_packet_size - protocol_headers_size
```

Для первой версии проще считать:

```text
packet_size == --msg-size
```

Главное — одинаково применять это для всех NAT-реализаций.

---

## Как маппить `target_pps`

Для одного flow:

```text
target_pps == --mps
```

Пример:

```bash
--mps 100000
```

означает попытку генерировать примерно 100_000 сообщений в секунду.

Для нескольких flow возможны два подхода.

---

## Как маппить `n_flows`

`n_flows` — это количество NAT-сессий / 5-tuple flow.

Практический способ:

```text
flow 1: user_ip:random_src_port -> external_ip:5001
flow 2: user_ip:random_src_port -> external_ip:5002
flow 3: user_ip:random_src_port -> external_ip:5003
...
```

### Простой вариант

Поднимать несколько `sockperf` client-процессов на User VM.

Каждый процесс идёт в свой destination port на External VM.

```text
target_pps_per_flow = target_pps / n_flows
```

Например:

```text
target_pps = 100000
n_flows = 10
target_pps_per_flow = 10000
```

Тогда запускается 10 процессов:

```bash
sockperf under-load -i <EXTERNAL_VM_IP> -p 5001 --mps 10000 ...
sockperf under-load -i <EXTERNAL_VM_IP> -p 5002 --mps 10000 ...
sockperf under-load -i <EXTERNAL_VM_IP> -p 5003 --mps 10000 ...
...
```

Плюсы:

```text
простая реализация
легко отлаживать
очевидное соответствие flow -> процесс/порт
```

Минусы:

```text
при большом n_flows будет много процессов
сам генератор может стать bottleneck-ом
```

### Более продвинутый вариант

Использовать file mode `sockperf`, где список адресов/портов задаётся через файл.

Пример файла `server_flows.conf`:

```text
U 10.0.2.20 5001
U 10.0.2.20 5002
U 10.0.2.20 5003
U 10.0.2.20 5004
```

Пример запуска server:

```bash
sockperf server \
  -f server_flows.conf \
  -F e \
  --threads-num <N_SERVER_THREADS> \
  --msg-size <MAX_PACKET_SIZE> \
  --gap-detection
```

Этот вариант лучше масштабируется, но его стоит внедрять после простой версии.

---

## Как считать packet loss

Минимальная формула:

```python
loss_rate = dropped_packets / sent_packets
```

или:

```python
loss_rate = (sent_packets - received_packets) / sent_packets
```

Важный нюанс: в режиме `under-load` не каждый пакет обязательно получает reply, особенно если используется `--reply-every`.

Поэтому для forward-direction loss желательно использовать server-side gap detection / received counters, а не только client-side replies.

Рекомендуется собирать:

```text
sent_packets
received_packets
dropped_packets
duplicated_packets
out_of_order_packets
```

И сохранять сырые значения вместе с `loss_rate`.

---

## Как трактовать latency

`sockperf` измеряет latency на основе request/reply.

Для NAT-теста это обычно RTT latency:

```text
User VM -> NAT -> External VM -> NAT -> User VM
```

Если нужно оценить one-way latency, можно использовать приближение:

```python
estimated_one_way_latency = rtt_latency / 2
```

Но в отчёте это нужно называть именно estimated one-way latency, а не настоящая one-way latency.

Рекомендуемые поля результата:

```python
latency_rtt_avg
latency_rtt_p50
latency_rtt_p95
latency_rtt_p99

latency_oneway_estimated_avg = latency_rtt_avg / 2
latency_oneway_estimated_p50 = latency_rtt_p50 / 2
latency_oneway_estimated_p95 = latency_rtt_p95 / 2
latency_oneway_estimated_p99 = latency_rtt_p99 / 2
```

Если для первой версии нужен минимум, можно сохранять только RTT latency.

---

## Как собирать CPU/RAM NAT-сервиса

Так как оба NAT-варианта являются systemd-сервисами, основной источник метрик — cgroup сервиса.

Для каждого `run_step` желательно собирать:

```text
CPU avg
CPU max
RAM avg
RAM max
```

Минимальная идея реализации:

1. Во время работы `sockperf` периодически читать cgroup-файлы сервиса.
2. Сэмплировать раз в 0.5–1 секунду.
3. После завершения ступеньки агрегировать значения.

Примерно:

```text
/sys/fs/cgroup/system.slice/<service_name>.service/
```

В зависимости от cgroup v1/v2 и systemd layout пути могут отличаться.

Дополнительно желательно собирать sanity-check метрики всей NAT-машины:

```text
total CPU
total RAM
```

Но primary resource metrics для сравнения:

```text
NAT service cgroup CPU
NAT service cgroup RAM
```

---

## Рекомендованный интерфейс wrapper-а

Для кодового агента удобно сделать обёртку примерно с такими сущностями:

```python
@dataclass
class TrafficProfile:
    packet_size: int
    n_flows: int
    duration_sec: int
    reply_every: int = 100
    protocol: str = "udp"


@dataclass
class SearchConfig:
    initial_pps: int
    max_pps: int
    target_loss_rate: float
    relative_precision: float


@dataclass
class NatServiceConfig:
    name: str
    systemd_service_name: str


@dataclass
class BenchConfig:
    user_vm_host: str
    external_vm_host: str
    external_vm_ip: str
    base_port: int
    nat_service: NatServiceConfig
    traffic_profile: TrafficProfile
    search_config: SearchConfig
```

Основные функции:

```python
def start_sockperf_server(config: BenchConfig) -> None:
    ...

def stop_sockperf_server(config: BenchConfig) -> None:
    ...

def restart_nat_service(config: BenchConfig) -> None:
    ...

def run_step(config: BenchConfig, target_pps: int) -> StepResult:
    ...

def search_max_stable_pps(config: BenchConfig) -> StepResult:
    ...
```

---

## Псевдокод поиска

```python
def search_max_stable_pps(config: BenchConfig) -> StepResult:
    initial_pps = config.search_config.initial_pps
    max_pps = config.search_config.max_pps
    target_loss = config.search_config.target_loss_rate
    rel_precision = config.search_config.relative_precision

    left_result = run_step(config, initial_pps)

    if left_result.loss_rate > target_loss:
        raise RuntimeError("initial_pps is already above target loss threshold")

    left_pps = initial_pps
    right_pps = None
    current_pps = initial_pps

    while current_pps < max_pps:
        current_pps = min(current_pps * 2, max_pps)
        result = run_step(config, current_pps)

        if result.loss_rate <= target_loss:
            left_pps = current_pps
            left_result = result
        else:
            right_pps = current_pps
            break

    if right_pps is None:
        return left_result

    while (right_pps - left_pps) / left_pps > rel_precision:
        mid_pps = (left_pps + right_pps) // 2
        result = run_step(config, mid_pps)

        if result.loss_rate <= target_loss:
            left_pps = mid_pps
            left_result = result
        else:
            right_pps = mid_pps

    return left_result
```

---

## Практические замечания

### 1. `sockperf` сам не должен стать bottleneck-ом

Перед сравнением NAT-реализаций нужно сделать baseline:

```text
User VM -> External VM без NAT
```

или с минимальным pass-through вариантом.

Это покажет, какой максимум может дать сам генератор, виртуализация и сеть.

### 2. Все параметры должны быть одинаковыми

Для custom NAT и reference NAT должны быть одинаковыми:

```text
packet_size
n_flows
duration_sec
target_loss_rate
search_relative_precision
reply_every
server/client placement
CPU pinning, если используется
```

### 3. Нужно сохранять сырые результаты

Помимо итогового `max_stable_pps`, нужно сохранять все промежуточные результаты:

```text
каждый target_pps
loss_rate на каждой ступеньке
latency на каждой ступеньке
CPU/RAM на каждой ступеньке
stdout/stderr sockperf
версию NAT-сервиса
версию sockperf
дату запуска
конфиг теста
```

Это позволит потом объяснить странные результаты.

### 4. Название latency

В результатах лучше явно писать:

```text
latency_rtt_*
```

а не просто:

```text
latency_*
```

Если используется деление RTT/2, писать:

```text
latency_oneway_estimated_*
```

### 5. Начинать лучше с простой реализации

Первый этап:

```text
1 flow
1 sockperf server
1 sockperf client
один порт
один target_pps
ручной запуск
```

Второй этап:

```text
автоматический run_step
сбор cgroup CPU/RAM
парсинг sockperf output
```

Третий этап:

```text
несколько flow через несколько процессов
```

Четвёртый этап:

```text
file mode / epoll / оптимизация генератора
```

---

## Итоговая рекомендация

Для текущего benchmark-а:

```text
Основной инструмент: sockperf
External VM: sockperf server
User VM: sockperf under-load client
NAT VM: custom/reference NAT systemd service + cgroup metrics
```

`run_step` должен запускать нагрузку через `sockperf`, собирать loss/latency из `sockperf`, собирать CPU/RAM из cgroup NAT-сервиса и возвращать единый `StepResult`.

`iperf3` оставить только для smoke-test.

`TRex` рассматривать позже, если потребуется более низкоуровневый packet generator или если `sockperf` окажется ограничивающим фактором.

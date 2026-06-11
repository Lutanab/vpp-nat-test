# Запуск worker scalability теста

## Quick start

Из корня репозитория:

```bash
python3 prepare_machine.py
python3 run_worker_scalability_tests.py
```

Если во время подготовки была изменена CPU isolation, скрипт попросит перезагрузить машину; после перезагрузки запустите второй скрипт.

## Что делает `prepare_machine.py`

Скрипт готовит машину и VPP-стенд:

1. Подгружает top-level git submodule-ы.
2. Клонирует или обновляет `nat_fo` (название кастомного плагина) как обычный git-репозиторий в `vpp/src/plugins/nat_fo`.
3. Устанавливает `uv` в `/usr/bin`, если бинарь еще не найден.
4. Выполняет `uv sync --locked`.
5. Интерактивно проверяет и настраивает CPU isolation через systemd:
6. Устанавливает неободимые для vpp зависимости.
7. Илемпотентно собирает и устанавливает vpp как deb-пакет.
8. Поднимает VPP-топологию без NAT:


## Что делает `run_worker_scalability_tests.py`

Скрипт запускает сам worker scalability эксперимент:

1. Идемпотентно настраивает конфиги тестирования.
2. Для `n_workers` от 1 до 5:
   - записывает текущее значение `n_workers` в `configs/loadtest/load/test_config.yaml`;
   - запускает `uv run test-nat load`.
3. После всех прогонов строит график

## Методология определения max PPS

Одна точка измерения устроена так: TRex в течение `measurement_sec` пытается нагрузить VPP заданным `target_pps`. Предварительно происходит warmup-системы. После остановки читаются TRex flow-stat counters: сколько пакетов реально вернулось на принимающий порт.

Ожидаемое число пакетов считается как `target_pps * measurement_sec`. Если VPP не успевает разгребать трафик, rx-ring забивается, TRex это видит и отправить меньше пакетов, чем было задано target rate. Таким образом получаем loss % как отношение реально полученных к ожидаемым пакетам.

Мы ставим простейший SLA: `loss_rate <= target_loss_rate`. Наша задача - найти максимальный pps (с заданной точностью) при котором SLA удовлетворяется. Точку где SLA выполнена назовем успешной, где не выполнено - неуспешной.

Сам max PPS ищется в две фазы. Сначала идет экспоненциальный поиск: стартуем с `search_initial_pps` и удваиваем нагрузку, пока не найдем первую неуспешную точку. Затем между последней успешной и первой неуспешной точкой идет бинарный поиск. Итоговый max PPS - последняя успешная точка в пределах точности `search_relative_precision`.

## Конфиги
Конфиги разделены на две группы: load-конфиги (то есть те которые на саму нагрузку) и search конфиги (которые влияют на способ определения этой нагрузки)

Load-конфиг:

```text
configs/loadtest/load/test_config.yaml
```
Поля:

- `test_name` - имя эксперимента, влияет на директорию в `results/`.
- `nat_mode` - NAT-режим; текущий worker scalability скрипт принудительно ставит `none`.
- `n_workers` - число VPP workers; текущий скрипт меняет его в цикле 1..5.
- `flow_count` - число trex UDP flow (суммарное).
- `packet_size` - размер пакета.
- `target_loss_rate` - максимально допустимая доля потерь пакетов.

Search-конфиг:

```text
configs/loadtest/search/test_config.yaml
```

Поля:

- `warmup_sec` - warmup перед измерением одной точки (измерения начинаются не сразу а после warmup-а системы).
- `measurement_sec` - длительность измерения одной точки.
- `search_initial_pps` - стартовый PPS.
- `search_max_pps` - верхняя граница поиска.
- `search_relative_precision` - относительная точность поиска.

## Результаты

Результаты лежат в:

```text
results/test_name_<test_name>/
```

Для каждого запуска создается отдельная вложенная директория по параметрам:

```text
results/test_name_<test_name>/
  flow_count_<N>/
    n_workers_<N>/
      target_loss_rate_<X>/
        packet_size_<N>/
          nat_mode_none/
```

Внутри директории одного запуска:

- `result.json` - финальный результат и параметры запуска.
- `history.json` - все шаги поиска PPS.
- `program.log` - текстовый лог запуска.

После worker scalability прогона дополнительно создаются:

- `results/test_name_<test_name>/pps_vs_workers.png` - график PPS от числа workers.
- `results/test_name_<test_name>/pps_vs_workers.tsv` - табличные данные для графика.

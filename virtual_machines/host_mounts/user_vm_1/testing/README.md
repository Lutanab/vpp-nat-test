# User VM Test Worker

Каталог физически расположен в `virtual_machines/host_mounts/user_vm_1/testing`, а путь
`./testing` в корне репозитория является симлинком на этот host mount.

Этот пакет теперь является worker-частью для `user_vm_1`. Host будет оркестрировать
NAT/VPP, а внутри VM будет вызываться только `test-nat-worker`.

## Установка

```bash
uv sync
```

Проверка CLI:

```bash
uv run test-nat-worker --help
```

## Simple Test

Старый `simple_test.py` сохранен и доступен так:

```bash
uv run test-nat-worker simple-test
```

## Run Step

`run-step` запускает одну ступеньку через `sockperf under-load`.
Все параметры передаются явно, без VM-local `test_config.yaml`.

Пример:

```bash
uv run test-nat-worker run-step \
  --target-pps 100000 \
  --packet-size 512 \
  --n-flows 1 \
  --warmup-sec 10 \
  --measurement-sec 60 \
  --server-ip 10.8.0.2 \
  --server-port-base 5001 \
  --reply-every 100
```

`stdout` содержит итоговый JSON. Прогресс пишется в `stderr`.

В JSON есть ключевые timestamp-и:

- `warmup_started_at`
- `measurement_started_at`
- `measurement_finished_at`

## Presets

Presets пока остаются на VM в:

```text
testing/presets/search/
```

Но `run-step` их не читает сам: host-side orchestration будет выбирать preset и
передавать worker-у уже развернутые параметры.

## Legacy

Старый Python UDP loadtest-код вынесен за пределы host mount в:

```text
legacy/python_udp_loadtest/
```

Он не является текущим worker-API, но сохранен для повторного использования.

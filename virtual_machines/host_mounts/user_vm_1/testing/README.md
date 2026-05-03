# Testing

Каталог физически расположен в `virtual_machines/host_mounts/user_vm_1/testing`, а путь `./testing` в корне репозитория является симлинком на этот host mount.

## Установка

```bash
uv sync
```

После этого из корня репозитория доступна команда:

```bash
uv run test-nat --help
uv run nat-loadtest-server --help
```

## Simple

Проверка базовой связности и NAT:

```bash
uv run test-nat simple
```

Ручные sanity-check адресов:

- `external-vm`: `curl --noproxy '*' http://10.8.0.2:8080/test`
- `user-vm-2`: `curl --noproxy '*' http://10.8.1.3:8080/test`

## Load

Конфиг для нагрузочных тестов:

```text
testing/configs/test_config.yaml
```

Шаблон:

```text
testing/configs/test_config.yaml.template
```

Стартовая заготовка:

```bash
cp testing/configs/test_config.yaml.template testing/configs/test_config.yaml
```

В шаблоне оставлены только параметры, которые описывают сам эксперимент.
Топологические значения и служебные defaults захардкожены в коде:

- `server_ip=10.8.0.2`
- `server_port=9000`
- `server_mode=echo`
- `bind_ip=0.0.0.0`
- `base_src_port=20000`
- `results_root=testing/results`

Если нужно, их всё ещё можно переопределить CLI-флагами.

Параметры search/run профиля вынесены в presets:

```text
testing/presets/search/
```

Сейчас первый пресет:

```text
testing/presets/search/standart.yaml
```

В основном конфиге указывается только:

```yaml
search_preset: standart
```

`nat_mode` в YAML больше не хранится. Его нужно передавать вручную при запуске
`run`, а `test-nat` внутри VM просто использует это значение
как метку текущего режима для результатов.

Именно имя пресета затем фигурирует в именах result-директорий как `search_preset_<name>`,
вместо отдельных wildcard-сегментов для `warmup`, `measurement_duration` и `pps_precision_delta`.

Серверная часть теперь вынесена в отдельный пакет на стороне `external_vm`:

```bash
uv run nat-loadtest-server run l34-udp --port 9000 --mode echo
uv run nat-loadtest-server status
```

Для постоянного запуска на `external_vm` лучше поднимать его как `systemd`-сервис.
Шаблон лежит в [nat_loadtest_server.service](/home/zero/projects/vpp-nat-test/virtual_machines/host_mounts/external_vm/nat_loadtest_server/nat_loadtest_server.service),
инструкция в [server README](/home/zero/projects/vpp-nat-test/virtual_machines/host_mounts/external_vm/nat_loadtest_server/README.md).

Клиентские команды:

```bash
uv run test-nat load run --nat-mode nat44
```

Пример с override:

```bash
uv run test-nat load run \
  --nat-mode nat44 \
  --max-pps 2000000
```

Без `--nat-mode` load-команды не запускаются.

Во время `run` прогресс печатается в `stderr`,
а итоговый JSON остаётся в `stdout`.

Для `run` результаты теперь складываются в три файла:

- `program.log` с полным логом работы поиска
- `history.json` с полной историей всех ступенек
- `result.json` только с конечным итогом поиска

`run` использует warmup на каждой ступеньке отдельно. Это сделано затем,
чтобы каждая точка измерялась после выхода именно на свой уровень PPS, а не зависела
от переходных эффектов предыдущей ступеньки.

Относительные пути внутри YAML резолвятся относительно директории, где лежит сам конфиг.

Важно по режимам сервера:

- для `run` нужен `echo`
- `sink` сейчас скорее вспомогательный receive-only режим

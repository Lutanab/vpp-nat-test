# NAT Loadtest Server

Standalone `uv` package for L3/L4 loadtest servers on the external machine.

## Usage

From the repository root:

```bash
uv sync
uv run nat-loadtest-server --help
```

Поднять или обновить systemd-сервис UDP L3/L4 test server:

```bash
uv run nat-loadtest-server run l34-udp \
  --bind 0.0.0.0 \
  --port 9000 \
  --mode echo
```

Supported modes:

- `sink` — receive packets and keep counters
- `echo` — receive packets and send the same payload back

## systemd service

Если вы хотите запускать сервер как daemon, используйте шаблон:

```text
nat_loadtest_server.service
```

Обычно руками это делать уже не нужно: команда

```bash
uv run nat-loadtest-server run l34-udp --port 9000 --mode echo
```

сама:

- записывает env-файл `/etc/nat-loadtest-server/l34-udp.env`
- устанавливает unit `/etc/systemd/system/nat-loadtest-server-l34-udp.service`
- делает `daemon-reload`
- включает и перезапускает сервис

Если всё же нужна ручная установка шаблона внутри `external_vm`:

```bash
SERVER_DIR="$(pwd)"
UV_BIN="$(command -v uv)"
TMP_UNIT="/tmp/nat_loadtest_server.service"

sed \
  -e "s|__SERVER_DIR__|${SERVER_DIR}|g" \
  -e "s|__UV_BIN__|${UV_BIN}|g" \
  -e "s|__ENV_FILE__|/etc/nat-loadtest-server/l34-udp.env|g" \
  nat_loadtest_server.service > "${TMP_UNIT}"

sudo install -m 0644 "${TMP_UNIT}" /etc/systemd/system/nat-loadtest-server-l34-udp.service
sudo systemctl daemon-reload
sudo systemctl enable --now nat-loadtest-server-l34-udp.service
```

Проверка:

```bash
systemctl status nat-loadtest-server-l34-udp.service --no-pager
journalctl -u nat-loadtest-server-l34-udp.service -f
```

По умолчанию установленный unit запускает:

```bash
uv run nat-loadtest-server serve l34-udp --bind 0.0.0.0 --port 9000 --mode echo
```

Если нужен другой порт или `sink`, отредактируйте `ExecStart` в unit-файле и затем выполните:

```bash
sudo systemctl daemon-reload
sudo systemctl restart nat-loadtest-server-l34-udp.service
```

Показать текущий статус и параметры серверов:

```bash
uv run nat-loadtest-server status
```

## Which mode to use

Для текущих нагрузочных тестов практически нужен именно `echo`.

Почему:

- `search-boundary` ищет предел по `loss` и опирается на ответы сервера, поэтому требует `echo`
- `run-once` в `echo` умеет считать `loss` и `latency`
- `sink` полезен только для receive-only сценария, где вам не нужны RTT/latency и pass/fail по ответам

Итого:

- для `search-boundary`: `echo`
- для большинства реальных `run-once`: тоже `echo`
- `sink` сейчас скорее вспомогательный режим на будущее

Для отладки foreground-режим существует внутренняя команда `serve`, которую systemd вызывает сам.
Обычно вручную её запускать не нужно.

Если всё же нужен прямой отладочный запуск с JSON-выводом:

```bash
uv run nat-loadtest-server serve l34-udp \
  --bind 0.0.0.0 \
  --port 9000 \
  --mode echo \
  --output-json /tmp/l34_udp_server.json
```

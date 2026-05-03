# Sockperf Server

Этот каталог лежит в host mount для `external_vm` и не является `uv`-пакетом.
Скрипт устанавливает `sockperf server` как `systemd`-сервис.

Проверка:

```bash
python3 manage_sockperf_server.py status
```

Установка и запуск:

```bash
sudo python3 manage_sockperf_server.py install \
  --bind 10.8.0.2 \
  --port 5001 \
  --msg-size 1024
```

Скрипт:

- проверяет, что `sockperf` установлен и доступен в `PATH`
- пишет `/etc/sockperf-server/sockperf-server.env`
- устанавливает `/etc/systemd/system/sockperf-server.service`
- выполняет `systemctl daemon-reload`
- включает и запускает сервис

Полезные команды:

```bash
systemctl status sockperf-server.service --no-pager
journalctl -u sockperf-server.service -f
sudo python3 manage_sockperf_server.py restart --bind 10.8.0.2 --port 5001 --msg-size 1024
sudo python3 manage_sockperf_server.py stop
```

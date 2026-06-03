# external_vm: nc server

Эта директория видна внутри `external_vm` как `/mnt/host/ha-test`.

Для HA-теста сохранения TCP-сессии здесь запускается TCP-сервер:

```bash
cd /mnt/host/ha-test
nc -lv -s 10.8.0.2 -p 5000
```

Ожидаемый клиент - `user_vm`, который подключается с `10.8.1.2` на
`10.8.0.2:5000` через VPP.

Если нужно, чтобы сервер продолжал слушать после разрыва клиента:

```bash
cd /mnt/host/ha-test
nc -lk -v -s 10.8.0.2 -p 5000
```

Во время `manage-nat scenario restart` оставь этот терминал открытым и смотри,
сохраняется ли TCP-соединение или приходит reset.

## UDP downtime receiver

Для измерения dataplane downtime запусти UDP receiver:

```bash
cd /mnt/host/ha-test
python3 udp_receiver.py
```

Receiver слушает `10.8.0.2:5005`, принимает только фиксированный payload
`nat_fo_udp_gap_v1` и пишет времена прибытия в:

```text
results/YYYY-MM-DD_HH-MM-SS_utc/arrivals.csv
```

Формат файла:

```text
monotonic_ns,unix_ns
```

Receiver работает до ручной остановки через Ctrl-C и каждые 0.5 секунды печатает
короткий status в консоль. Скрипт не считает downtime сам; этот файл потом
обрабатывается отдельно.

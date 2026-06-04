# user_vm: nc client

Эта директория видна внутри `user_vm` как `/mnt/host/ha-test`.

Сначала запусти сервер на `external_vm`, затем подключись из `user_vm`:

```bash
cd /mnt/host/ha-test
nc -v 10.8.0.2 5000
```

Для простого heartbeat поверх одной длинной TCP-сессии:

```bash
cd /mnt/host/ha-test
while true; do date -Is; sleep 1; done | nc -v 10.8.0.2 5000
```

Трафик к `10.8.0.2` должен идти через `data0 -> VPP`, а не через management
network. Пока соединение активно, запусти на хосте `manage-nat scenario restart`
и проверь, остается ли `nc` подключенным или выходит с reset.

## UDP downtime sender

Для измерения dataplane downtime сначала запусти receiver на `external_vm`, затем
запусти UDP sender:

```bash
cd /mnt/host/ha-test
python3 udp_sender.py
```

Чтобы перед основной нагрузкой создать UDP NAT-сессии с разных source ports:

```bash
python3 udp_sender.py --burst-sessions 10000
```

Burst растягивается по времени с фиксированной скоростью создания сессий
`--burst-sessions-per-second`, по умолчанию 1000 сессий/сек. Source ports для
burst-а берутся из диапазона `--burst-source-port-min..--burst-source-port-max`.

Sender шлет фиксированный payload `nat_fo_udp_gap_v1` на `10.8.0.2:5005`.
Интервал отправки задается только в скрипте константой
`POLL_INTERVAL_SECONDS`. Sender работает до ручной остановки через Ctrl-C.
Каждые 0.5 секунды sender печатает короткий status в консоль.

Каждый запуск создает служебную директорию:

```text
results/YYYY-MM-DD_HH-MM-SS_utc/
```

Файл с временами прибытия создается receiver-ом на `external_vm`.

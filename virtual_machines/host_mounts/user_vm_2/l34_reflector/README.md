# L34 Reflector Service (systemd)

В этой директории находятся:
- `reflector_server.py`: HTTP-рефлектор (возвращает src/dst IP:port)
- `l34_reflector.service`: шаблон systemd-юнита

По умолчанию сервис слушает порт `8080`.

## Установка и включение (внутри ВМ)

Выполняйте команды из этой директории (`.../l34_reflector`):

```bash
REFLECTOR_DIR="$(pwd)"
TMP_UNIT="/tmp/l34_reflector.service"

sed "s|__REFLECTOR_DIR__|${REFLECTOR_DIR}|g" l34_reflector.service > "${TMP_UNIT}"
sudo install -m 0644 "${TMP_UNIT}" /etc/systemd/system/l34_reflector.service

sudo systemctl daemon-reload
sudo systemctl enable --now l34_reflector.service
```

## Проверка

```bash
systemctl status l34_reflector.service --no-pager
curl -i http://127.0.0.1:8080/test
```

Ожидаемый результат: HTTP-статус `200` и JSON с полями `src_ip/src_port/dst_ip/dst_port`.

## Логи

```bash
journalctl -u l34_reflector.service -f
```

## Частые операции

```bash
sudo systemctl restart l34_reflector.service
sudo systemctl stop l34_reflector.service
sudo systemctl disable l34_reflector.service
```

## Если нужен другой порт

В юните можно явно указать порт, например `7000`:

```ini
ExecStart=/usr/bin/python3 __REFLECTOR_DIR__/reflector_server.py --port 7000
```

После изменения:

```bash
sudo systemctl daemon-reload
sudo systemctl restart l34_reflector.service
```

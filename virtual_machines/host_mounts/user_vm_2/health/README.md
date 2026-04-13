# Health Service (systemd)

В этой директории находятся:
- `server.py`: health HTTP-сервер на порту `7000`
- `health.service`: шаблон systemd-юнита

## Установка и включение (внутри ВМ)

Выполняйте команды из этой директории (`.../health`):

```bash
HEALTH_DIR="$(pwd)"
TMP_UNIT="/tmp/health.service"

sed "s|__HEALTH_DIR__|${HEALTH_DIR}|g" health.service > "${TMP_UNIT}"
sudo install -m 0644 "${TMP_UNIT}" /etc/systemd/system/health.service

sudo systemctl daemon-reload
sudo systemctl enable --now health.service
```

## Проверка

```bash
systemctl status health.service --no-pager
curl -i http://127.0.0.1:7000/anything
```

Ожидаемый результат: HTTP-статус `200` для любого пути и метода запроса.

## Логи

```bash
journalctl -u health.service -f
```

## Частые операции

```bash
sudo systemctl restart health.service
sudo systemctl stop health.service
sudo systemctl disable health.service
```

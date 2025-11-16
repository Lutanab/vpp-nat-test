## Управление проектом

Проект использует единый CLI скрипт для управления всеми операциями:

```bash
./manage <команда>
```

### Доступные команды:

- `prepare` - Подготовка окружения (установка зависимостей VPP, QEMU, отключение libvirt)
- `setup-network` - Подготовка сетевой топологии (bridge br0, tap-интерфейсы, NAT)
- `help` - Показать справку

### Примеры использования:

```bash
# Подготовка окружения
sudo ./manage prepare

# Подготовка сетевой топологии
sudo ./manage setup-network

# Справка
./manage help
```

## Базовая установка vpp

Перед тем как собирать:
```bash
sudo ./manage prepare
```

Очередной раз собрать:
```bash
sudo make pkg-deb-debug
sudo dpkg -i build-root/*.deb
```

Если перетер конфиги (напр startup.conf):
```bash
sudo cp ./configs/startu.conf /etc/vpp/startup.conf
sudo systemctl restart vpp
```
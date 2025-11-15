## Управление проектом

Проект использует единый CLI скрипт для управления всеми операциями:

```bash
./cli.sh <команда>
```

### Доступные команды:

- `prepare` - Подготовка окружения (установка зависимостей VPP, QEMU, отключение libvirt)
- `bootstrap` - Настройка VPP (запуск сервиса, создание veth-пары)
- `prepare-network` - Подготовка сетевой топологии (bridge br0, tap-интерфейсы, NAT)
- `help` - Показать справку

### Примеры использования:

```bash
# Подготовка окружения
sudo ./cli.sh prepare

# Настройка VPP
sudo ./cli.sh bootstrap

# Подготовка сетевой топологии
sudo ./cli.sh prepare-network

# Справка
./cli.sh help
```

## Базовая установка vpp

Перед тем как собирать:
```bash
sudo ./cli.sh prepare
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
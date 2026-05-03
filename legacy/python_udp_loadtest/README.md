# Legacy Python UDP Loadtest

Этот каталог содержит старую реализацию loadtest-а на Python UDP sockets.

Код вынесен из `virtual_machines/host_mounts/user_vm_1/testing`, чтобы VM host mount
содержал только актуальный `test-nat-worker` API на базе `sockperf`.

Эта реализация сейчас не является рабочим benchmark-контрактом, но сохранена для
повторного использования отдельных идей: структуры результатов, search-loop,
ресурсных метрик и старой UDP packet-модели.

# AD5X WebScreen

Отдельный Z-Mod plugin для удалённого просмотра и управления экраном Flashforge AD5X через браузер и встроенную карточку камеры Fluidd.

## Что уже проверено на реальном AD5X

- HelixScreen framebuffer: visible `800x480`, virtual `800x960`, 32 bpp, stride 3200.
- Pillow JPEG quality 75: около 12.6 FPS на непрерывном benchmark.
- 10 Hz polling + JPEG только при изменении framebuffer: около 17% одного CPU-ядра в измеренном активном режиме вместо ~68% при постоянном 10 FPS encode.
- При отсутствии клиентов framebuffer не читается и JPEG не кодируется; live idle acceptance показал `polls=0`, `encodes=0` и около 0.2% process CPU over uptime.
- TSC2007 Touchscreen определяется через evdev; реальные диапазоны `ABS_X=0..800`, `ABS_Y=0..480`, `ABS_PRESSURE=0..4095`.
- Для HelixScreen используется inverse affine calibration из `/srv/helixscreen/config/settings.json`; клики и touch mapping проверены на реальном принтере.

## Fluidd SCREEN

Z-Mod AD5X уже проксирует `/screen/` на `127.0.0.1:8010`. Плагин регистрирует в Moonraker отдельную камеру по той же схеме, что Creator 5 Pro:

```ini
[webcam screen]
enabled: true
service: iframe
target_fps: 10
stream_url: /screen/stream
snapshot_url: /screen/snapshot
aspect_ratio: 800:480
```

После перезапуска Moonraker/принтера в карточке **Видеокамеры** Fluidd появляется камера `SCREEN`. Iframe показывает экран и принимает pointer/touch события прямо по изображению.

HTTP surface:

- `/stream` — iframe-страница для Fluidd;
- `/streams` — MJPEG;
- `/snapshot` — одиночный JPEG;
- `/stats` — runtime counters;
- `/health` — health probe.

## Touch lifecycle

- Процесс всегда стартует с remote touch **OFF**.
- В диагностической странице `:8010/` touch можно включить/выключить кнопкой без паролей и токенов.
- В Fluidd `SCREEN` первый `pointerdown` автоматически включает touch, поэтому отдельного шага перед управлением нет.
- Потерянный `UP` снимается серверным failsafe.
- При SIGTERM/SIGINT/stop сервис также принудительно отпускает touch.
- Сервис работает с `nice=10` по умолчанию.

## Установка

После публикации standalone repository и добавления плагина в Z-Mod registry целевая установка:

```text
ENABLE_PLUGIN name=ad5x_webscreen
```

До добавления в registry repository можно клонировать в `mod_data/plugins/ad5x_webscreen`, после чего запустить `./install.sh`.

Installer:

- проверяет AD5X и наличие Pillow в Z-Mod chroot;
- создаёт runtime config вне Git checkout;
- ставит SysV hook `S71ad5x_webscreen` в Z-Mod chroot;
- регистрирует autostart через управляемый блок `mod_data/power_on.sh`;
- добавляет Moonraker update-manager и Fluidd `SCREEN` webcam includes в `plugins.moonraker.conf`;
- запускает WebScreen через lifecycle script.

## Управление

```sh
./control.sh status
./control.sh start
./control.sh stop
./control.sh restart
./control.sh config
```

Прямая диагностическая страница:

```text
http://<printer-ip>:8010/
```

В обычной работе отдельную вкладку открывать не требуется — WebScreen предназначен прежде всего для `SCREEN` в Fluidd.

## Конфигурация

Runtime config:

```text
/opt/config/mod_data/ad5x_webscreen/webscreen.ini
```

Tested profile:

```ini
[server]
bind = 0.0.0.0
port = 8010
poll_hz = 10
jpeg_quality = 75
nice = 10

[touch]
allowed = true
start_enabled = false
failsafe_seconds = 1.0
pressure = 1200
helix_settings = /srv/helixscreen/config/settings.json
```

После изменения конфигурации выполните `./control.sh restart`.

## Удаление

```text
DISABLE_PLUGIN name=ad5x_webscreen
```

`uninstall.sh` останавливает сервис, удаляет только принадлежащие WebScreen init/autostart/Moonraker include hooks и runtime config. Чтобы сохранить config при ручном удалении, задайте `AD5X_WEBSCREEN_KEEP_CONFIG=1`.

## Оставшийся acceptance gate v0.1.0

- reboot/autostart + появление `SCREEN` во Fluidd;
- update/uninstall lifecycle;
- реальная печать с открытым `SCREEN` и контроль Klipper/Moonraker на E0011 / `timer too close`.

# AD5X WebScreen

Отдельный Z-Mod plugin для удалённого просмотра и управления экраном Flashforge AD5X через браузер.

## Что уже проверено на реальном AD5X

- HelixScreen framebuffer: visible `800x480`, virtual `800x960`, 32 bpp, stride 3200.
- Pillow JPEG quality 75: около 12.6 FPS на непрерывном benchmark.
- 10 Hz polling + JPEG только при изменении framebuffer: около 17% одного CPU-ядра в измеренном режиме вместо ~68% при постоянном 10 FPS encode.
- TSC2007 Touchscreen определяется через evdev; реальные диапазоны `ABS_X=0..800`, `ABS_Y=0..480`, `ABS_PRESSURE=0..4095`.
- Для HelixScreen используется inverse affine calibration из `/srv/helixscreen/config/settings.json`; клики и touch mapping проверены на реальном принтере.

## Безопасность

- Remote touch всегда стартует **выключенным**.
- Управляющие HTTP endpoints требуют случайный control token, созданный при установке.
- При потерянном `UP` серверный failsafe принудительно отпускает touch.
- При SIGTERM/SIGINT сервис также отправляет release перед завершением.
- Сервис работает с `nice=10` по умолчанию.
- При отсутствии MJPEG-клиентов framebuffer не опрашивается и JPEG не кодируется.

Видео `/stream` и `/snapshot` не требуют token и рассчитаны на доверенную локальную сеть. Не пробрасывайте порт WebScreen напрямую в Интернет.

## Установка

После публикации standalone repository и добавления плагина в Z-Mod registry целевая установка:

```text
ENABLE_PLUGIN name=ad5x_webscreen
```

До добавления в registry repository может быть клонирован в `mod_data/plugins/ad5x_webscreen`, после чего запускается `./install.sh`.

Installer:

- проверяет AD5X и наличие Pillow в Z-Mod chroot;
- создаёт конфиг и control token вне Git checkout;
- ставит SysV hook `S71ad5x_webscreen` в Z-Mod chroot;
- регистрирует Moonraker `update_manager` через `plugins.moonraker.conf`;
- запускает сервис через безопасный lifecycle script.

## Управление

```sh
./control.sh status
./control.sh restart
./control.sh token
./control.sh config
```

По умолчанию WebScreen слушает:

```text
http://<printer-ip>:8010/
```

При первом включении remote touch Web UI запросит token. Также можно открыть URL как `?token=<token>`; JavaScript сохраняет token только в `sessionStorage` и сразу убирает его из адресной строки.

## Конфигурация

Runtime config хранится вне git checkout:

```text
/opt/config/mod_data/ad5x_webscreen/webscreen.ini
```

Основной tested profile:

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
```

После изменения конфигурации выполните `./control.sh restart`.

## Удаление

```text
DISABLE_PLUGIN name=ad5x_webscreen
```

`uninstall.sh` останавливает сервис, удаляет только принадлежащий WebScreen init-hook и Moonraker include, а затем удаляет runtime config/token. Чтобы сохранить config при ручном удалении, задайте `AD5X_WEBSCREEN_KEEP_CONFIG=1`.

## Ограничения v0.1.0

- Remote touch v0.1.0 требует валидную HelixScreen affine calibration; небезопасного direct fallback нет.
- Финальный acceptance во время реальной печати ещё требуется перед тем, как считать профиль 10 Hz полностью доказанным по Klipper latency/E0011.

#!/bin/sh
set -e

PLUGIN_NAME="ad5x_webscreen"
CHROOT_PLUGIN_DIR="/opt/config/mod_data/plugins/$PLUGIN_NAME"
UPDATE_INCLUDE="[include plugins/$PLUGIN_NAME/moonraker.update.conf]"

ZMOD_ENV=""
[ -f /usr/data/zmod/zmod/.shell/0.sh ] && ZMOD_ENV=/usr/data/zmod/zmod/.shell/0.sh
[ -z "$ZMOD_ENV" ] && [ -f /opt/config/mod/.shell/0.sh ] && ZMOD_ENV=/opt/config/mod/.shell/0.sh
[ -n "$ZMOD_ENV" ] && . "$ZMOD_ENV"

if [ "${AD5X:-0}" != "1" ]; then
    echo "$PLUGIN_NAME supports AD5X only" >&2
    exit 1
fi

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
MOD=${MOD:-/usr/data/.mod/.zmod}
MOD_CONF=${MOD_CONF:-/usr/data/config}

if [ -f /ZMOD ]; then
    CONFIG_ROOT=/opt/config
    INIT_DIR=/etc/init.d
    SERVICE="$INIT_DIR/S71ad5x_webscreen"
    RUN_SERVICE="$SERVICE"
else
    CONFIG_ROOT="$MOD_CONF"
    INIT_DIR="$MOD/etc/init.d"
    SERVICE="$INIT_DIR/S71ad5x_webscreen"
    RUN_SERVICE="chroot $MOD /etc/init.d/S71ad5x_webscreen"
fi

DATA_DIR="$CONFIG_ROOT/mod_data/$PLUGIN_NAME"
LOG_DIR="$CONFIG_ROOT/mod_data/log"
MOONRAKER_PLUGINS="$CONFIG_ROOT/mod_data/plugins.moonraker.conf"
CONFIG="$DATA_DIR/webscreen.ini"
TOKEN_FILE="$DATA_DIR/control.token"

chmod +x "$SCRIPT_DIR/webscreen.py" "$SCRIPT_DIR/S71ad5x_webscreen" "$SCRIPT_DIR/control.sh" "$SCRIPT_DIR/install.sh" "$SCRIPT_DIR/uninstall.sh" "$SCRIPT_DIR/update.sh" "$SCRIPT_DIR/power_on_hook.sh"
mkdir -p "$DATA_DIR" "$LOG_DIR" "$INIT_DIR"

if [ ! -f "$CONFIG" ]; then
    cp "$SCRIPT_DIR/webscreen.ini.example" "$CONFIG"
fi

if [ ! -s "$TOKEN_FILE" ]; then
    if command -v python3 >/dev/null 2>&1; then
        python3 -c 'import secrets; print(secrets.token_urlsafe(24))' > "$TOKEN_FILE"
    elif [ -n "${PYTHON:-}" ] && [ -x "$PYTHON" ]; then
        "$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(24))' > "$TOKEN_FILE"
    else
        dd if=/dev/urandom bs=24 count=1 2>/dev/null | od -An -tx1 | tr -d ' \n' > "$TOKEN_FILE"
        printf '\n' >> "$TOKEN_FILE"
    fi
    chmod 600 "$TOKEN_FILE"
fi

if [ -f /ZMOD ]; then
    python3 -c 'from PIL import Image' >/dev/null 2>&1 || {
        echo "Pillow is required in the Z-Mod Python environment" >&2
        exit 1
    }
else
    chroot "$MOD" /bin/sh -lc "python3 -c 'from PIL import Image'" >/dev/null 2>&1 || {
        echo "Pillow is required in the Z-Mod chroot" >&2
        exit 1
    }
fi

if [ -f /ZMOD ]; then
    ln -sf "$SCRIPT_DIR/S71ad5x_webscreen" "$SERVICE"
else
    ln -sf "$CHROOT_PLUGIN_DIR/S71ad5x_webscreen" "$SERVICE"
fi

"$SCRIPT_DIR/power_on_hook.sh" install

[ -f "$MOONRAKER_PLUGINS" ] || : > "$MOONRAKER_PLUGINS"
if ! grep -qF "$UPDATE_INCLUDE" "$MOONRAKER_PLUGINS"; then
    printf '%s\n' "$UPDATE_INCLUDE" >> "$MOONRAKER_PLUGINS"
fi

# Start immediately. The init script performs a port preflight and writes its PID only after startup succeeds.
if [ -f /ZMOD ]; then
    "$RUN_SERVICE" restart
else
    chroot "$MOD" /etc/init.d/S71ad5x_webscreen restart
fi

echo "AD5X WebScreen installed"
echo "Control token: $TOKEN_FILE"
echo "Default URL: http://<printer-ip>:8010/"

if [ "${AD5X_WEBSCREEN_NO_REBOOT:-0}" != "1" ]; then
    echo REBOOT >/tmp/printer
fi

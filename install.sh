#!/bin/sh
set -e

PLUGIN_NAME="ad5x_webscreen"
CHROOT_PLUGIN_DIR="/opt/config/mod_data/plugins/$PLUGIN_NAME"
UPDATE_INCLUDE="[include plugins/$PLUGIN_NAME/moonraker.update.conf]"
WEBCAM_INCLUDE="[include plugins/$PLUGIN_NAME/moonraker.webcam.runtime.conf]"
LEGACY_WEBCAM_INCLUDE="[include plugins/$PLUGIN_NAME/moonraker.webcam.conf]"

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

chmod +x "$SCRIPT_DIR/webscreen.py" "$SCRIPT_DIR/S71ad5x_webscreen" "$SCRIPT_DIR/control.sh" "$SCRIPT_DIR/install.sh" "$SCRIPT_DIR/uninstall.sh" "$SCRIPT_DIR/update.sh" "$SCRIPT_DIR/power_on_hook.sh" "$SCRIPT_DIR/configure_webcam.sh"
mkdir -p "$DATA_DIR" "$LOG_DIR" "$INIT_DIR"

if [ ! -f "$CONFIG" ]; then
    cp "$SCRIPT_DIR/webscreen.ini.example" "$CONFIG"
fi

migrate_legacy_security() {
    rm -f "$DATA_DIR/control.token"
    [ -f "$CONFIG" ] || return 0
    grep -q '^[[:space:]]*control_token_path[[:space:]]*=' "$CONFIG" || return 0

    tmp="${CONFIG}.tmp.$$"
    awk '
        BEGIN { skip=0 }
        /^[[:space:]]*\[security\][[:space:]]*$/ { skip=1; next }
        /^[[:space:]]*\[/ { if (skip) skip=0 }
        !skip { print }
    ' "$CONFIG" > "$tmp"
    mv "$tmp" "$CONFIG"
}

migrate_legacy_security

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
if grep -qF "$LEGACY_WEBCAM_INCLUDE" "$MOONRAKER_PLUGINS"; then
    tmp="${MOONRAKER_PLUGINS}.tmp.$$"
    awk -v legacy="$LEGACY_WEBCAM_INCLUDE" '$0 != legacy { print }' "$MOONRAKER_PLUGINS" > "$tmp"
    mv "$tmp" "$MOONRAKER_PLUGINS"
fi
for INCLUDE_LINE in "$UPDATE_INCLUDE" "$WEBCAM_INCLUDE"; do
    if ! grep -qF "$INCLUDE_LINE" "$MOONRAKER_PLUGINS"; then
        printf '%s\n' "$INCLUDE_LINE" >> "$MOONRAKER_PLUGINS"
    fi
done

if [ "${AD5X_WEBSCREEN_NO_REBOOT:-0}" = "1" ]; then
    "$SCRIPT_DIR/configure_webcam.sh" --restart-if-changed
else
    "$SCRIPT_DIR/configure_webcam.sh"
fi

# Start immediately. The init script performs a port preflight and writes its PID only after startup succeeds.
if [ -f /ZMOD ]; then
    "$RUN_SERVICE" restart
else
    chroot "$MOD" /etc/init.d/S71ad5x_webscreen restart
fi

echo "AD5X WebScreen installed"
echo "Default URL: http://<printer-ip>:8010/"

if [ "${AD5X_WEBSCREEN_NO_REBOOT:-0}" != "1" ]; then
    echo REBOOT >/tmp/printer
fi

#!/bin/sh
set -e

PLUGIN_NAME="ad5x_webscreen"
UPDATE_INCLUDE="[include plugins/$PLUGIN_NAME/moonraker.update.conf]"
WEBCAM_INCLUDE="[include plugins/$PLUGIN_NAME/moonraker.webcam.runtime.conf]"
LEGACY_WEBCAM_INCLUDE="[include plugins/$PLUGIN_NAME/moonraker.webcam.conf]"

ZMOD_ENV=""
[ -f /usr/data/zmod/zmod/.shell/0.sh ] && ZMOD_ENV=/usr/data/zmod/zmod/.shell/0.sh
[ -z "$ZMOD_ENV" ] && [ -f /opt/config/mod/.shell/0.sh ] && ZMOD_ENV=/opt/config/mod/.shell/0.sh
[ -n "$ZMOD_ENV" ] && . "$ZMOD_ENV"

MOD=${MOD:-/usr/data/.mod/.zmod}
MOD_CONF=${MOD_CONF:-/usr/data/config}
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
[ -x "$SCRIPT_DIR/power_on_hook.sh" ] && "$SCRIPT_DIR/power_on_hook.sh" remove || true

if [ -f /ZMOD ]; then
    CONFIG_ROOT=/opt/config
    INIT_DIR=/etc/init.d
    SERVICE="$INIT_DIR/S71ad5x_webscreen"
    [ -x "$SERVICE" ] && "$SERVICE" stop || true
else
    CONFIG_ROOT="$MOD_CONF"
    INIT_DIR="$MOD/etc/init.d"
    SERVICE="$INIT_DIR/S71ad5x_webscreen"
    [ -e "$SERVICE" ] && chroot "$MOD" /etc/init.d/S71ad5x_webscreen stop || true
fi

if [ -L "$SERVICE" ]; then
    target=$(readlink "$SERVICE" 2>/dev/null || true)
    case "$target" in
        *ad5x_webscreen*) rm -f "$SERVICE" ;;
    esac
fi

MOONRAKER_PLUGINS="$CONFIG_ROOT/mod_data/plugins.moonraker.conf"
if [ -f "$MOONRAKER_PLUGINS" ]; then
    tmp="$MOONRAKER_PLUGINS.tmp.$$"
    awk -v update="$UPDATE_INCLUDE" -v webcam="$WEBCAM_INCLUDE" -v legacy="$LEGACY_WEBCAM_INCLUDE" '$0 != update && $0 != webcam && $0 != legacy { print }' "$MOONRAKER_PLUGINS" > "$tmp"
    mv "$tmp" "$MOONRAKER_PLUGINS"
fi

if [ "${AD5X_WEBSCREEN_KEEP_CONFIG:-0}" != "1" ]; then
    rm -rf "$CONFIG_ROOT/mod_data/$PLUGIN_NAME"
fi
rm -f "$CONFIG_ROOT/mod_data/log/ad5x_webscreen.log"
rm -f "$SCRIPT_DIR/moonraker.webcam.runtime.conf"

echo "AD5X WebScreen uninstalled"
if [ "${AD5X_WEBSCREEN_NO_REBOOT:-0}" != "1" ]; then
    echo REBOOT >/tmp/printer
fi

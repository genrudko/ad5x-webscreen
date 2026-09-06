#!/bin/sh

PLUGIN_NAME="ad5x_webscreen"
ZMOD_ENV=""
[ -f /usr/data/zmod/zmod/.shell/0.sh ] && ZMOD_ENV=/usr/data/zmod/zmod/.shell/0.sh
[ -z "$ZMOD_ENV" ] && [ -f /opt/config/mod/.shell/0.sh ] && ZMOD_ENV=/opt/config/mod/.shell/0.sh
[ -n "$ZMOD_ENV" ] && . "$ZMOD_ENV"
MOD=${MOD:-/usr/data/.mod/.zmod}
MOD_CONF=${MOD_CONF:-/usr/data/config}

if [ -f /ZMOD ]; then
    CONFIG_ROOT=/opt/config
    SERVICE=/etc/init.d/S71ad5x_webscreen
    run_service() { "$SERVICE" "$1"; }
else
    CONFIG_ROOT="$MOD_CONF"
    run_service() { chroot "$MOD" /etc/init.d/S71ad5x_webscreen "$1"; }
fi

case "${1:-status}" in
    start|stop|restart|status) run_service "$1" ;;
    config) cat "$CONFIG_ROOT/mod_data/$PLUGIN_NAME/webscreen.ini" ;;
    *) echo "Usage: $0 {start|stop|restart|status|config}"; exit 1 ;;
esac

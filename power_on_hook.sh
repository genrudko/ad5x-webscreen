#!/bin/sh
set -eu

APP_NAME="AD5X WebScreen"
BEGIN_MARKER="# >>> AD5X WebScreen >>>"
END_MARKER="# <<< AD5X WebScreen <<<"

if [ -n "${AD5X_WEBSCREEN_POWER_ON:-}" ]; then
    POWER_ON="$AD5X_WEBSCREEN_POWER_ON"
elif [ -f /ZMOD ]; then
    POWER_ON="/opt/config/mod_data/power_on.sh"
else
    POWER_ON="/usr/data/config/mod_data/power_on.sh"
fi
TMP="${POWER_ON}.ad5x-webscreen.$$"

cleanup() {
    rm -f "$TMP"
}
trap cleanup EXIT HUP INT TERM

ensure_power_on() {
    mkdir -p "${POWER_ON%/*}"
    if [ ! -f "$POWER_ON" ]; then
        printf '%s\n' '#!/bin/sh' > "$POWER_ON"
    fi
    chmod +x "$POWER_ON" 2>/dev/null || true
}

render_without_managed_block() {
    awk -v begin="$BEGIN_MARKER" -v end="$END_MARKER" '
        $0 == begin {
            if (skip) exit 3
            skip = 1
            begin_count++
            next
        }
        $0 == end {
            if (!skip) exit 3
            skip = 0
            end_count++
            next
        }
        !skip { print }
        END {
            if (skip || begin_count != end_count) exit 3
        }
    ' "$POWER_ON" > "$TMP" || {
        echo "$APP_NAME: malformed managed block in $POWER_ON" >&2
        rm -f "$TMP"
        return 1
    }
}

install_hook() {
    ensure_power_on
    render_without_managed_block

    cat >> "$TMP" <<'BLOCK'

# >>> AD5X WebScreen >>>
AD5X_WEBSCREEN_BASE=""
for AD5X_WEBSCREEN_CANDIDATE in /usr/data/config /opt/config; do
    if [ -x "$AD5X_WEBSCREEN_CANDIDATE/mod_data/plugins/ad5x_webscreen/control.sh" ]; then
        AD5X_WEBSCREEN_BASE="$AD5X_WEBSCREEN_CANDIDATE"
        break
    fi
done
if [ -n "$AD5X_WEBSCREEN_BASE" ]; then
    AD5X_WEBSCREEN_CONTROL="$AD5X_WEBSCREEN_BASE/mod_data/plugins/ad5x_webscreen/control.sh"
    AD5X_WEBSCREEN_BOOT_LOG="$AD5X_WEBSCREEN_BASE/mod_data/ad5x_webscreen/boot.log"
    "$AD5X_WEBSCREEN_CONTROL" start >> "$AD5X_WEBSCREEN_BOOT_LOG" 2>&1 &
fi
unset AD5X_WEBSCREEN_CANDIDATE AD5X_WEBSCREEN_CONTROL AD5X_WEBSCREEN_BOOT_LOG AD5X_WEBSCREEN_BASE
# <<< AD5X WebScreen <<<
BLOCK

    chmod +x "$TMP" 2>/dev/null || true
    mv "$TMP" "$POWER_ON"
    echo "$APP_NAME: autostart registered in $POWER_ON"
}

remove_hook() {
    [ -f "$POWER_ON" ] || return 0
    render_without_managed_block
    chmod +x "$TMP" 2>/dev/null || true
    mv "$TMP" "$POWER_ON"
    echo "$APP_NAME: autostart removed from $POWER_ON"
}

check_hook() {
    [ -f "$POWER_ON" ] || exit 1
    BEGIN_COUNT=$(grep -Fxc "$BEGIN_MARKER" "$POWER_ON" 2>/dev/null || true)
    END_COUNT=$(grep -Fxc "$END_MARKER" "$POWER_ON" 2>/dev/null || true)
    [ "$BEGIN_COUNT" -eq 1 ] && [ "$END_COUNT" -eq 1 ]
}

case "${1:-install}" in
    install) install_hook ;;
    remove) remove_hook ;;
    check) check_hook ;;
    *)
        echo "Usage: $0 {install|remove|check}" >&2
        exit 2
        ;;
esac

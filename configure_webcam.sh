#!/bin/sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MOD=${MOD:-/usr/data/.mod/.zmod}
TEMPLATE=${AD5X_WEBSCREEN_WEBCAM_TEMPLATE:-$HERE/moonraker.webcam.conf.template}
OUTPUT=${AD5X_WEBSCREEN_WEBCAM_OUTPUT:-$HERE/moonraker.webcam.runtime.conf}
TMP="${OUTPUT}.tmp.$$"
RESTART_IF_CHANGED=0

cleanup() {
    rm -f "$TMP"
}
trap cleanup EXIT HUP INT TERM

case "${1:-}" in
    "") ;;
    --restart-if-changed) RESTART_IF_CHANGED=1 ;;
    *)
        echo "Usage: $0 [--restart-if-changed]" >&2
        exit 2
        ;;
esac

valid_ipv4() {
    printf '%s\n' "$1" | awk -F. '
        NF != 4 { exit 1 }
        {
            for (i = 1; i <= 4; i++) {
                if ($i !~ /^[0-9]+$/ || $i < 0 || $i > 255) exit 1
            }
            if ($1 == 127 || ($1 == 0 && $2 == 0 && $3 == 0 && $4 == 0)) exit 1
        }
    '
}

detect_ipv4() {
    if [ -n "${AD5X_WEBSCREEN_PRINTER_IP:-}" ]; then
        printf '%s\n' "$AD5X_WEBSCREEN_PRINTER_IP"
        return 0
    fi

    if command -v ip >/dev/null 2>&1; then
        iface=$(ip route 2>/dev/null | awk '$1 == "default" { for (i=1; i<=NF; i++) if ($i == "dev" && i < NF) { print $(i+1); exit } }')
        if [ -n "$iface" ]; then
            addr=$(ip -4 addr show dev "$iface" 2>/dev/null | awk '$1 == "inet" { split($2, a, "/"); if (a[1] !~ /^127\./) { print a[1]; exit } }')
            if [ -n "$addr" ]; then
                printf '%s\n' "$addr"
                return 0
            fi
        fi
        addr=$(ip -4 addr show 2>/dev/null | awk '$1 == "inet" { split($2, a, "/"); if (a[1] !~ /^127\./) { print a[1]; exit } }')
        if [ -n "$addr" ]; then
            printf '%s\n' "$addr"
            return 0
        fi
    fi

    if command -v ifconfig >/dev/null 2>&1; then
        addr=$(ifconfig 2>/dev/null | awk '
            /inet addr:/ {
                for (i=1; i<=NF; i++) if ($i ~ /^addr:/) { sub(/^addr:/, "", $i); if ($i !~ /^127\./) { print $i; exit } }
            }
            $1 == "inet" && $2 !~ /^127\./ { print $2; exit }
        ')
        if [ -n "$addr" ]; then
            printf '%s\n' "$addr"
            return 0
        fi
    fi

    return 1
}

restart_moonraker() {
    if [ -n "${AD5X_WEBSCREEN_MOONRAKER_RESTART_CMD:-}" ]; then
        "$AD5X_WEBSCREEN_MOONRAKER_RESTART_CMD"
    elif [ -f /ZMOD ]; then
        /etc/init.d/S65moonraker restart
    else
        chroot "$MOD" /etc/init.d/S65moonraker restart
    fi
}

[ -f "$TEMPLATE" ] || {
    echo "AD5X WebScreen: webcam template not found: $TEMPLATE" >&2
    exit 1
}

IP=$(detect_ipv4) || {
    echo "AD5X WebScreen: unable to detect printer IPv4 address" >&2
    exit 1
}
valid_ipv4 "$IP" || {
    echo "AD5X WebScreen: invalid printer IPv4 address: $IP" >&2
    exit 1
}

mkdir -p "${OUTPUT%/*}"
awk -v ip="$IP" '{ gsub(/@PRINTER_IP@/, ip); print }' "$TEMPLATE" > "$TMP"

if [ -f "$OUTPUT" ] && cmp -s "$TMP" "$OUTPUT"; then
    echo "AD5X WebScreen: Fluidd SCREEN webcam config unchanged ($IP)"
    exit 0
fi

mv "$TMP" "$OUTPUT"
echo "AD5X WebScreen: Fluidd SCREEN webcam config updated ($IP)"

if [ "$RESTART_IF_CHANGED" -eq 1 ]; then
    echo "AD5X WebScreen: restarting Moonraker because webcam URL changed"
    restart_moonraker
fi

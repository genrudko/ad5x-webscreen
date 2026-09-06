#!/bin/sh
set -e

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
chmod +x "$HERE/control.sh" "$HERE/S71ad5x_webscreen" "$HERE/webscreen.py"
"$HERE/control.sh" restart

echo "AD5X WebScreen update hook completed"

#!/usr/bin/bash
# Restore stock Acer driver, remove only Nitro Control-managed driver files.
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run as root with sudo.' >&2; exit 1; }
UNIT='nitro-control-rgb-driver.service'
SERVICE='/etc/systemd/system/nitro-control-rgb-driver.service'
CONTROLLER='/usr/local/libexec/nitro-control-rgb-driver'
if [[ -f "$SERVICE" ]] && ! grep -q '^# Installed explicitly by Nitro Control;' "$SERVICE"; then
    echo 'Unknown service file; refusing removal.' >&2; exit 1
fi
systemctl disable "$UNIT" >/dev/null 2>&1 || true
if systemctl is-active --quiet "$UNIT"; then
    systemctl stop "$UNIT"
fi
if grep -q '^linuwu_sense ' /proc/modules; then
    echo 'Linuwu is still loaded. Restore the stock driver before uninstalling.' >&2
    echo "Try: sudo '$CONTROLLER' stop" >&2
    exit 1
fi
modprobe acer_wmi
[[ -f "$SERVICE" ]] && rm -f -- "$SERVICE"
[[ -f "$CONTROLLER" ]] && rm -f -- "$CONTROLLER"
for directory in /usr/lib/modules/*/extra/nitro-control; do
    [[ -d "$directory" && ! -L "$directory" ]] || continue
    rm -f -- "$directory/linuwu_sense.ko" "$directory/linuwu_sense.ko.sha256"
    rmdir "$directory" 2>/dev/null || true
done
systemctl daemon-reload
echo 'Nitro Control driver integration removed; stock acer_wmi remains loaded.'
echo 'The independent Polkit RGB helper is not removed by this script.'

#!/usr/bin/bash
# Explicit, kernel-pinned installation of the user's locally tested module.
# Installs a disabled-on-boot service. Never blacklists native acer_wmi.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ $EUID -eq 0 ]] || { echo 'Run with sudo; see docs/RGB-HARDWARE.md.' >&2; exit 1; }
[[ $# -eq 1 && "$1" == /* ]] || { echo 'Usage: sudo ./scripts/install-rgb-driver-fedora.sh /absolute/path/to/patched/linuwu_sense.ko' >&2; exit 2; }
SOURCE="$1"
KERNEL="$(uname -r)"
DEST="/usr/lib/modules/$KERNEL/extra/nitro-control/linuwu_sense.ko"
SERVICE='/etc/systemd/system/nitro-control-rgb-driver.service'
CONTROLLER='/usr/local/libexec/nitro-control-rgb-driver'
[[ "$(cat /sys/class/dmi/id/product_name)" == 'Nitro AN515-58' ]] || { echo 'Unsupported model.' >&2; exit 1; }
[[ -f "$SOURCE" && ! -L "$SOURCE" ]] || { echo 'No regular compiled module at supplied path.' >&2; exit 1; }
[[ "$(modinfo -F name "$SOURCE")" == 'linuwu_sense' ]] || { echo 'Wrong module name.' >&2; exit 1; }
[[ "$(modinfo -F vermagic "$SOURCE" | cut -d' ' -f1)" == "$KERNEL" ]] || { echo 'Module is not built for this running kernel.' >&2; exit 1; }
if command -v mokutil >/dev/null 2>&1 && mokutil --sb-state 2>/dev/null | grep -qi 'SecureBoot enabled'; then
    echo 'Secure Boot is enabled. Refusing unsigned development module.' >&2; exit 1
fi
if [[ -e /etc/modprobe.d/blacklist-acer_wmi.conf || -e /etc/modules-load.d/linuwu_sense.conf || -e /etc/systemd/system/linuwu_sense.service ]]; then
    echo 'Conflicting Linuwu/acer_wmi installation detected; investigate it before installing.' >&2; exit 1
fi
if systemctl is-active --quiet nitro-control-rgb-driver.service 2>/dev/null || grep -q '^linuwu_sense ' /proc/modules; then
    echo 'An RGB driver session is active; stop it before reinstalling.' >&2; exit 1
fi
if [[ -e "$SERVICE" ]] && ! grep -q '^# Installed explicitly by Nitro Control;' "$SERVICE"; then
    echo 'Service path is occupied by an unrelated unit; refusing overwrite.' >&2; exit 1
fi
[[ -f scripts/rgb-driver-service-fedora.sh && -f scripts/nitro-control-rgb-driver.service ]] || exit 1
install -D -o root -g root -m 644 "$SOURCE" "$DEST"
sha256sum "$DEST" | cut -d' ' -f1 > "$DEST.sha256"
chown root:root "$DEST.sha256"
chmod 600 "$DEST.sha256"
if command -v restorecon >/dev/null 2>&1; then restorecon -F "$DEST" "$DEST.sha256" || true; fi
install -D -o root -g root -m 755 scripts/rgb-driver-service-fedora.sh "$CONTROLLER"
install -D -o root -g root -m 644 scripts/nitro-control-rgb-driver.service "$SERVICE"
if command -v restorecon >/dev/null 2>&1; then restorecon -F "$CONTROLLER" "$SERVICE" || true; fi
systemctl daemon-reload
# Deliberately opt out of automatic boot loading, even on reinstall.
systemctl disable nitro-control-rgb-driver.service >/dev/null 2>&1 || true
printf 'Pinned module installed: %s\nSHA256: %s\n' "$DEST" "$(cat "$DEST.sha256")"
echo 'Boot autoload remains DISABLED. No driver was loaded or replaced.'
echo 'Next: sudo systemctl start nitro-control-rgb-driver.service'

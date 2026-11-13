#!/usr/bin/env bash
# Explicitly install the locally built native WMI PoC for the *current* kernel.
# No driver replacement, boot activation, polkit change, or WMI call here.
set -Eeuo pipefail

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "Run with sudo."
[[ $# -eq 1 && "$1" == /* ]] ||
  die "Usage: sudo ./scripts/install-native-rgb-fedora.sh /absolute/path/nitro_rgb_enable_poc.ko"
SOURCE="$1"
KERNEL="$(uname -r)"
DEST="/usr/lib/modules/$KERNEL/extra/nitro-control/nitro_rgb_enable_poc.ko"
[[ "$(cat /sys/class/dmi/id/sys_vendor)" == Acer ]] || die "Unsupported vendor"
[[ "$(cat /sys/class/dmi/id/product_name)" == "Nitro AN515-58" ]] || die "Unsupported model"
[[ -f "$SOURCE" && ! -L "$SOURCE" ]] || die "Missing regular built .ko"
[[ "$(modinfo -F name "$SOURCE")" == nitro_rgb_enable_poc ]] || die "Wrong module"
[[ "$(modinfo -F vermagic "$SOURCE" | cut -d' ' -f1)" == "$KERNEL" ]] ||
  die "Module must be compiled for the running kernel"
[[ -d /sys/module/acer_wmi ]] || die "Stock acer_wmi must be loaded"
[[ ! -d /sys/module/linuwu_sense && ! -d /sys/module/nitro_rgb_enable_poc ]] ||
  die "Conflicting or in-use RGB module detected"
if systemctl is-active --quiet nitro-control-rgb-driver.service; then
  die "Stop the legacy Linuwu driver service before installing native RGB."
fi
if command -v mokutil >/dev/null 2>&1 &&
   mokutil --sb-state 2>/dev/null | grep -qi 'SecureBoot enabled'; then
  die "Unsigned development module rejected with Secure Boot enabled"
fi
[[ ! -L "$DEST" && ! -L "$DEST.sha256" ]] || die "Destination is a symlink"
install -D -o root -g root -m 644 "$SOURCE" "$DEST"
sha256sum "$DEST" | cut -d' ' -f1 > "$DEST.sha256"
chown root:root "$DEST.sha256"
chmod 600 "$DEST.sha256"
if command -v restorecon >/dev/null 2>&1; then
  restorecon -F "$DEST" "$DEST.sha256" || true
fi
echo "Native WMI RGB module pinned to $KERNEL at $DEST"
echo "No module was loaded, no Acer driver was replaced, and no autostart was enabled."
echo "Separately install the updated RGB helper: sudo ./scripts/install-rgb-helper-fedora.sh"

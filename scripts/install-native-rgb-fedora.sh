#!/usr/bin/env bash
# Explicitly install the locally built native WMI PoC for the *current* kernel
# into /usr/local/lib/nitro-control/modules/$KERNEL/ outside /usr/lib/modules.
# Serialized via /run/nitro-control/native-rgb.lock; no autoload, polkit, or WMI write.
set -Eeuo pipefail

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "Run with sudo."

ADOPT_LEGACY=0
if [[ "${1:-}" == "--adopt-legacy" ]]; then
  ADOPT_LEGACY=1
  shift
fi
[[ $# -eq 1 && "$1" == /* ]] ||
  die "Usage: sudo ./scripts/install-native-rgb-fedora.sh [--adopt-legacy] /absolute/path/nitro_rgb_enable_poc.ko"

SOURCE="$1"
KERNEL="$(uname -r)"
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
PRIVATE_BASE="/usr/local/lib/nitro-control"
DEST="$PRIVATE_BASE/modules/$KERNEL/nitro_rgb_enable_poc.ko"

[[ "$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null || true)" == Acer ]] || die "Unsupported vendor"
[[ "$(cat /sys/class/dmi/id/product_name 2>/dev/null || true)" == "Nitro AN515-58" ]] || die "Unsupported model"
[[ -f "$SOURCE" && ! -L "$SOURCE" ]] || die "Missing regular built .ko"
[[ "$(modinfo -F name "$SOURCE")" == nitro_rgb_enable_poc ]] || die "Wrong module"
[[ "$(modinfo -F vermagic "$SOURCE" | cut -d' ' -f1)" == "$KERNEL" ]] ||
  die "Module must be compiled for the running kernel"
if modinfo -p "$SOURCE" 2>/dev/null | grep -Eq '^(backlight_test|red_test|enable|effect_test):'; then
  die "Refusing experimental diagnostic build for staged runtime; rebuild without EXPERIMENTAL=1"
fi
[[ -d /sys/module/acer_wmi ]] || die "Stock acer_wmi must be loaded"
if [[ -d /sys/module/linuwu_sense || -d /sys/module/nitro_rgb_enable_poc ]] ||
   { [[ -r /proc/modules ]] && grep -Eq '^(linuwu_sense|nitro_rgb_enable_poc) ' /proc/modules; }; then
  die "Conflicting or in-use RGB module detected"
fi
if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet nitro-control-rgb-driver.service; then
  die "Stop the legacy Linuwu driver service before installing native RGB."
fi
if command -v mokutil >/dev/null 2>&1 &&
   mokutil --sb-state 2>/dev/null | grep -qi 'SecureBoot enabled'; then
  die "Unsigned development module rejected with Secure Boot enabled"
fi

EXTRA_ARGS=()
if (( ADOPT_LEGACY == 1 )); then
  EXTRA_ARGS+=(--adopt-legacy)
fi

# Delegate locking (native-rgb.lock), staged sha256sum + modinfo checks,
# transactional commit, verified migration, and depmod -a to shared root logic.
/usr/bin/python3 -I -c '
import sys
sys.path.insert(0, sys.argv[1])
from nitro_control.module_staging import _cli_main
raise SystemExit(_cli_main(sys.argv[2:]))
' "$REPO_ROOT" install --component native-rgb --kernel "$KERNEL" --source "$SOURCE" "${EXTRA_ARGS[@]}"

echo "Native WMI RGB module pinned to $KERNEL at $DEST"
echo "No module was loaded, no Acer driver was replaced, and no autostart was enabled."
echo "Separately install the updated RGB helper: sudo ./scripts/install-rgb-helper-fedora.sh"

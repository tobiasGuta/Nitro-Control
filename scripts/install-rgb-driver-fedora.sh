#!/usr/bin/bash
# Explicit, kernel-pinned installation of the user's locally tested module
# into /usr/local/lib/nitro-control/modules/$KERNEL/ outside /usr/lib/modules.
# Installs a disabled-on-boot service. Never blacklists native acer_wmi.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || { echo 'Run with sudo; see docs/RGB-HARDWARE.md.' >&2; exit 1; }

ADOPT_LEGACY=0
if [[ "${1:-}" == "--adopt-legacy" ]]; then
  ADOPT_LEGACY=1
  shift
fi
[[ $# -eq 1 && "$1" == /* ]] || { echo 'Usage: sudo ./scripts/install-rgb-driver-fedora.sh [--adopt-legacy] /absolute/path/to/patched/linuwu_sense.ko' >&2; exit 2; }

SOURCE="$1"
KERNEL="$(uname -r)"
REPO_ROOT="$(pwd -P)"
PRIVATE_BASE='/usr/local/lib/nitro-control'
DEST_DIR="$PRIVATE_BASE/modules/$KERNEL"
DEST="$DEST_DIR/linuwu_sense.ko"

[[ "$(cat /sys/class/dmi/id/product_name 2>/dev/null || true)" == 'Nitro AN515-58' ]] || { echo 'Unsupported model.' >&2; exit 1; }
[[ -f "$SOURCE" && ! -L "$SOURCE" ]] || { echo 'No regular compiled module at supplied path.' >&2; exit 1; }
[[ "$(modinfo -F name "$SOURCE")" == 'linuwu_sense' ]] || { echo 'Wrong module name.' >&2; exit 1; }
[[ "$(modinfo -F vermagic "$SOURCE" | cut -d' ' -f1)" == "$KERNEL" ]] || { echo 'Module is not built for this running kernel.' >&2; exit 1; }
if command -v mokutil >/dev/null 2>&1 && mokutil --sb-state 2>/dev/null | grep -qi 'SecureBoot enabled'; then
    echo 'Secure Boot is enabled. Refusing unsigned development module.' >&2; exit 1
fi
if [[ -e /etc/modprobe.d/blacklist-acer_wmi.conf || -e /etc/modules-load.d/linuwu_sense.conf || -e /etc/systemd/system/linuwu_sense.service ]]; then
    echo 'Conflicting Linuwu/acer_wmi installation detected; investigate it before installing.' >&2; exit 1
fi
if systemctl is-active --quiet nitro-control-rgb-driver.service 2>/dev/null ||
   { [[ -r /proc/modules ]] && grep -Eq '^(linuwu_sense|nitro_rgb_enable_poc) ' /proc/modules; } ||
   [[ -d /sys/module/linuwu_sense || -d /sys/module/nitro_rgb_enable_poc ]]; then
    echo 'An RGB driver session or conflicting module is active; stop it before reinstalling.' >&2; exit 1
fi
[[ -f scripts/rgb-driver-service-fedora.sh && -f scripts/nitro-control-rgb-driver.service ]] || exit 1

EXTRA_ARGS=()
if (( ADOPT_LEGACY == 1 )); then
  EXTRA_ARGS+=(--adopt-legacy)
fi

# Shared staging validates staged sha256sum/vermagic, commits atomically, migrates
# verified legacy copies, clears depmod indexes, and ensures:
# systemctl disable nitro-control-rgb-driver.service remains verified disabled.
/usr/bin/python3 -I -c '
import sys
sys.path.insert(0, sys.argv[1])
from nitro_control.module_staging import _cli_main
raise SystemExit(_cli_main(sys.argv[2:]))
' "$REPO_ROOT" install --component rgb-driver --kernel "$KERNEL" --source "$SOURCE" \
  --controller-src "$REPO_ROOT/scripts/rgb-driver-service-fedora.sh" \
  --unit-src "$REPO_ROOT/scripts/nitro-control-rgb-driver.service" "${EXTRA_ARGS[@]}"

printf 'Pinned module installed: %s\nSHA256: %s\n' "$DEST" "$(cat "$DEST.sha256")"
echo 'Boot autoload verified DISABLED and index clearance checked. No driver was loaded or replaced.'
echo 'Next: sudo systemctl start nitro-control-rgb-driver.service'

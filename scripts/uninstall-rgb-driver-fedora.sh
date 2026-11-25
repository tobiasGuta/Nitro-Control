#!/usr/bin/bash
# Restore stock Acer driver, remove only verified Nitro Control-managed driver files,
# and refresh stale module indexes across affected kernel versions.
set -Eeuo pipefail

[[ $EUID -eq 0 ]] || { echo 'Run as root with sudo.' >&2; exit 1; }

ADOPT_LEGACY=0
if [[ "${1:-}" == "--adopt-legacy" ]]; then
  ADOPT_LEGACY=1
  shift
fi
[[ $# -eq 0 ]] || { echo 'Usage: sudo ./scripts/uninstall-rgb-driver-fedora.sh [--adopt-legacy]' >&2; exit 2; }

if { [[ -r /proc/modules ]] && grep -q '^linuwu_sense ' /proc/modules; } &&
   ! systemctl is-active --quiet nitro-control-rgb-driver.service 2>/dev/null; then
    echo 'Linuwu is still loaded outside the active unit. Restore the stock driver before uninstalling.' >&2
    exit 1
fi

KERNEL="$(uname -r)"
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
EXTRA_ARGS=()
if (( ADOPT_LEGACY == 1 )); then
  EXTRA_ARGS+=(--adopt-legacy)
fi

# module_staging stops/disables nitro-control-rgb-driver.service cleanly before locking,
# checks service+controller provenance, runs modprobe acer_wmi, removes verified files,
# and verifies depmod -a + modprobe -R + initramfs clearance.
/usr/bin/python3 -I -c '
import sys
sys.path.insert(0, sys.argv[1])
from nitro_control.module_staging import _cli_main
raise SystemExit(_cli_main(sys.argv[2:]))
' "$REPO_ROOT" uninstall --component rgb-driver --kernel "$KERNEL" "${EXTRA_ARGS[@]}"

echo 'Nitro Control driver integration removed; stock acer_wmi remains loaded.'
echo 'The independent Polkit RGB helper is not removed by this script.'

#!/usr/bin/env bash
# Remove verified Nitro-managed native WMI RGB modules and manifests across
# /usr/local/lib/nitro-control and legacy paths, refreshing depmod indexes.
set -Eeuo pipefail

[[ $EUID -eq 0 ]] || { echo 'Run with sudo' >&2; exit 1; }

ADOPT_LEGACY=0
if [[ "${1:-}" == "--adopt-legacy" ]]; then
  ADOPT_LEGACY=1
  shift
fi
[[ $# -eq 0 ]] || { echo 'Usage: sudo ./scripts/uninstall-native-rgb-fedora.sh [--adopt-legacy]' >&2; exit 2; }

if { [[ -r /proc/modules ]] && grep -q '^nitro_rgb_enable_poc ' /proc/modules; } ||
   [[ -d /sys/module/nitro_rgb_enable_poc ]]; then
  echo 'Native RGB module is currently loaded; stop it safely before uninstalling.' >&2
  exit 1
fi

KERNEL="$(uname -r)"
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
EXTRA_ARGS=()
if (( ADOPT_LEGACY == 1 )); then
  EXTRA_ARGS+=(--adopt-legacy)
fi

# Shared uninstall enforces /usr/local/lib/nitro-control provenance and runs depmod -a.
/usr/bin/python3 -I -c '
import sys
sys.path.insert(0, sys.argv[1])
from nitro_control.module_staging import _cli_main
raise SystemExit(_cli_main(sys.argv[2:]))
' "$REPO_ROOT" uninstall --component native-rgb --kernel "$KERNEL" "${EXTRA_ARGS[@]}"

echo "Removed verified Nitro-managed native WMI RGB artifacts. No other drivers or boot configuration changed."

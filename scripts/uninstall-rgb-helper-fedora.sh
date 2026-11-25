#!/usr/bin/env bash
# Remove only Nitro Control-owned optional RGB helper artifacts, preserving unrelated or foreign files.
# Managed files: __init__.py rgb.py rgb_hardware.py module_staging.py native_rgb.py rgb_privileged.py
set -euo pipefail
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$REPO_ROOT"
if [[ ${EUID} -ne 0 ]]; then
  echo 'Run explicitly as: sudo ./scripts/uninstall-rgb-helper-fedora.sh [--adopt-legacy]' >&2
  exit 1
fi
ADOPT_ARGS=()
case "${1:-}" in
  '') ;;
  --adopt-legacy) ADOPT_ARGS+=(--adopt-legacy) ;;
  *) echo 'Usage: sudo ./scripts/uninstall-rgb-helper-fedora.sh [--adopt-legacy]' >&2; exit 2 ;;
esac
if [[ $# -gt 1 ]]; then
  echo 'Usage: sudo ./scripts/uninstall-rgb-helper-fedora.sh [--adopt-legacy]' >&2
  exit 2
fi
if [[ -f "$REPO_ROOT/nitro_control/module_staging.py" ]]; then
  STAGING_PYTHONPATH="$REPO_ROOT"
elif [[ -f /usr/local/libexec/nitro-control-rgb/nitro_control/module_staging.py ]]; then
  STAGING_PYTHONPATH="/usr/local/libexec/nitro-control-rgb"
else
  echo 'ERROR: Nitro Control staging module unavailable to verify helper provenance.' >&2
  exit 1
fi
/usr/bin/python3 -I -c '
import sys
sys.path.insert(0, sys.argv[1])
from nitro_control.module_staging import _cli_main
raise SystemExit(_cli_main(sys.argv[2:]))
' "$STAGING_PYTHONPATH" uninstall-rgb-helper "${ADOPT_ARGS[@]}"
echo 'Removed optional Nitro Control RGB helper. No kernel drivers changed.'

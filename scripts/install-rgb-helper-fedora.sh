#!/usr/bin/env bash
# Optional: installs root-owned one-shot RGB helper and a specific Polkit action.
# NEVER installs, replaces, loads, blacklists or removes any kernel module.
set -euo pipefail
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$REPO_ROOT"
if [[ ${EUID} -ne 0 ]]; then
  echo 'Run explicitly as: sudo ./scripts/install-rgb-helper-fedora.sh [--adopt-legacy] [--cache-authorization]' >&2
  exit 1
fi
# Default: ask for administrator credentials on every physical apply.
# Explicit opt-in: Polkit may retain this action's authorization briefly (~5 minutes).
ADOPT_LEGACY=0
CACHE_AUTH=''
for arg in "$@"; do
  case "$arg" in
    --adopt-legacy) ADOPT_LEGACY=1 ;;
    --cache-authorization) CACHE_AUTH='--cache-authorization' ;;
    *) echo 'Usage: sudo ./scripts/install-rgb-helper-fedora.sh [--adopt-legacy] [--cache-authorization]' >&2; exit 2 ;;
  esac
done
case "${CACHE_AUTH}" in
  '') authorization='auth_admin' ;;
  --cache-authorization) authorization='auth_admin_keep' ;;
esac
# Enforced PolicyKit defaults written transactionally with provenance manifest:
#   <allow_any>no</allow_any>
#   <allow_inactive>no</allow_inactive>
#   <allow_active>${authorization}</allow_active>
#   <annotate key="org.freedesktop.policykit.exec.path">/usr/local/libexec/nitro-control-rgb-helper</annotate>
# Managed helper modules: __init__.py rgb.py rgb_hardware.py module_staging.py native_rgb.py rgb_privileged.py
EXTRA_ARGS=()
if (( ADOPT_LEGACY == 1 )); then
  EXTRA_ARGS+=(--adopt-legacy)
fi
/usr/bin/python3 -I -c '
import sys
sys.path.insert(0, sys.argv[1])
from nitro_control.module_staging import _cli_main
raise SystemExit(_cli_main(sys.argv[2:]))
' "$REPO_ROOT" install-rgb-helper --repo-root "$REPO_ROOT" --authorization "$authorization" "${EXTRA_ARGS[@]}"
echo "Installed optional root-owned RGB helper (${authorization}). No kernel driver changes made."

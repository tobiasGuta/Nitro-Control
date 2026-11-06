#!/usr/bin/env bash
# Remove only current-kernel opt-in native WMI module and digest.
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run with sudo' >&2; exit 1; }
[[ $# -eq 0 ]] || { echo 'No arguments supported' >&2; exit 2; }
if grep -q '^nitro_rgb_enable_poc ' /proc/modules; then
  echo 'Native RGB module is currently loaded; stop it safely before uninstalling.' >&2
  exit 1
fi
MODULE="/usr/lib/modules/$(uname -r)/extra/nitro-control/nitro_rgb_enable_poc.ko"
[[ ! -L "$MODULE" && ! -L "$MODULE.sha256" ]] ||
  { echo 'Refusing symlinked module or digest' >&2; exit 1; }
rm -f -- "$MODULE" "$MODULE.sha256"
echo "Removed current-kernel native WMI RGB module. No other drivers or boot configuration changed."

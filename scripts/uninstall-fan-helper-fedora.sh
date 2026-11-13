#!/usr/bin/env bash
set -euo pipefail
if [[ ${EUID} -ne 0 || $# -ne 0 ]]; then
  echo 'Usage: sudo ./scripts/uninstall-fan-helper-fedora.sh' >&2
  exit 1
fi
rm -f -- /usr/share/polkit-1/actions/io.github.tobiasguta.NitroControl.fan.policy
rm -f -- /usr/local/libexec/nitro-control-fan-helper
rm -rf -- /usr/local/libexec/nitro-control-fan
echo 'Removed the optional fan helper.'

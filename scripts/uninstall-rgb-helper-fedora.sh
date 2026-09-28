#!/usr/bin/env bash
set -euo pipefail
if [[ ${EUID} -ne 0 ]]; then
  echo 'Run explicitly as: sudo ./scripts/uninstall-rgb-helper-fedora.sh' >&2
  exit 1
fi
rm -f /usr/local/libexec/nitro-control-rgb-helper
rm -f /usr/share/polkit-1/actions/io.github.tobiasguta.NitroControl.rgb.policy
rm -f /usr/local/libexec/nitro-control-rgb/nitro_control/{__init__.py,rgb.py,rgb_hardware.py,rgb_privileged.py}
rmdir /usr/local/libexec/nitro-control-rgb/nitro_control /usr/local/libexec/nitro-control-rgb 2>/dev/null || true
echo 'Removed optional Nitro Control RGB helper. No kernel drivers changed.'

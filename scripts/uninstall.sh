#!/usr/bin/env bash
set -euo pipefail
if (( EUID != 0 )); then
  bash "$(dirname -- "${BASH_SOURCE[0]}")/disable-tray-autostart-fedora.sh" --if-managed
fi
rm -rf -- "$HOME/.local/share/nitro-control"
rm -f -- "$HOME/.local/bin/nitro-control" "$HOME/.local/share/applications/io.github.tobiasguta.NitroControl.desktop"
echo 'Nitro Control user installation removed; source checkout untouched.'

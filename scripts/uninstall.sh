#!/usr/bin/env bash
set -euo pipefail
rm -rf -- "$HOME/.local/share/nitro-control"
rm -f -- \
  "$HOME/.local/bin/nitro-control" \
  "$HOME/.local/share/applications/io.github.tobiasguta.NitroControl.desktop" \
  "$HOME/.local/share/icons/hicolor/scalable/apps/io.github.tobiasguta.NitroControl.svg" \
  "$HOME/.local/share/icons/hicolor/symbolic/apps/io.github.tobiasguta.NitroControl-symbolic.svg"
if [ -d "$HOME/.local/share/icons/hicolor" ] && command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t -q "$HOME/.local/share/icons/hicolor" || true
fi
echo 'Nitro Control user installation removed; source checkout untouched.'

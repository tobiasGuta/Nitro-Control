#!/usr/bin/env bash
# Remove only the Nitro Control-managed per-user tray autostart entry.
set -Eeuo pipefail

if [[ $EUID -eq 0 ]]; then
  echo 'Run as your regular user, not sudo/root.' >&2
  exit 1
fi
target="$HOME/.config/autostart/io.github.tobiasguta.NitroControl.Tray.desktop"
marker="# Managed by Nitro Control optional tray autostart"

if [[ -L "$target" ]]; then
  echo "Autostart file is a symlink; refusing removal." >&2
  exit 1
fi
if [[ ! -e "$target" ]]; then
  echo "Nitro Control tray autostart was not enabled."
  exit 0
fi
if [[ ! -f "$target" ]] || ! grep -Fqx -- "$marker" "$target"; then
  echo "Unrecognized autostart file; refusing removal: $target" >&2
  exit 1
fi
rm -f -- "$target"
echo "Nitro Control tray autostart disabled. Running tray is unchanged."

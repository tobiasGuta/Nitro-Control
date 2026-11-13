#!/usr/bin/env bash
# Remove only the Nitro Control-managed per-user tray autostart entry.
set -Eeuo pipefail

if [[ $EUID -eq 0 ]]; then
  echo 'Run as your regular user, not sudo/root.' >&2
  exit 1
fi
case "${1:-}" in
  ''|--if-managed) ;;
  *) echo 'Usage: disable-tray-autostart-fedora.sh [--if-managed]' >&2; exit 2 ;;
esac
config_home="${XDG_CONFIG_HOME:-}"
[[ "$config_home" = /* ]] || config_home="$HOME/.config"
config_dirs=("$config_home")
if [[ "$config_home" != "$HOME/.config" ]]; then
  config_dirs+=("$HOME/.config")
fi
marker="# Managed by Nitro Control optional tray autostart"

removed=0
unrecognized=0
for dir in "${config_dirs[@]}"; do
  target="$dir/autostart/io.github.tobiasguta.NitroControl.Tray.desktop"
  if [[ ! -e "$target" && ! -L "$target" ]]; then
    continue
  fi
  if [[ -L "$target" || ! -f "$target" ]] || ! grep -Fqx -- "$marker" "$target"; then
    if [[ "${1:-}" != --if-managed ]]; then
      echo "Unrecognized autostart file; refusing removal: $target" >&2
      unrecognized=1
    fi
    continue
  fi
  rm -f -- "$target"
  removed=1
done
if (( removed )); then
  echo "Nitro Control tray autostart disabled. Running tray is unchanged."
elif (( ! unrecognized )) && [[ "${1:-}" != --if-managed ]]; then
  echo "Nitro Control tray autostart was not enabled."
fi
exit "$unrecognized"

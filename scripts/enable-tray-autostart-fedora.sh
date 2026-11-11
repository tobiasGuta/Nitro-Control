#!/usr/bin/env bash
# Explicit opt-in: per-user GNOME tray autostart. NEVER called by install-fedora.sh.
set -Eeuo pipefail

if [[ $EUID -eq 0 ]]; then
  echo 'Run as your regular user, not sudo/root.' >&2
  exit 1
fi
launcher="$HOME/.local/bin/nitro-control"
dir="$HOME/.config/autostart"
target="$dir/io.github.tobiasguta.NitroControl.Tray.desktop"
marker="# Managed by Nitro Control optional tray autostart"

if [[ ! -x "$launcher" || -L "$launcher" ]]; then
  echo "Missing safe executable user launcher: $launcher" >&2
  echo 'Install the GUI from your reviewed Nitro-Control checkout first.' >&2
  exit 1
fi
if [[ "$launcher" == *$'\n'* || "$launcher" == *'"'* || "$launcher" == *'\\'* ]]; then
  echo "Unsupported launcher path characters; no autostart file written." >&2
  exit 1
fi
mkdir -p -- "$dir"
if [[ -L "$target" ]]; then
  echo "Autostart file is a symlink; refusing to overwrite." >&2
  exit 1
fi
if [[ -e "$target" ]] && ! grep -Fqx -- "$marker" "$target"; then
  echo "Unrecognized autostart file; refusing to overwrite $target" >&2
  exit 1
fi
tmp="$(mktemp "$dir/.nitro-control-tray.XXXXXXXX")"
trap 'rm -f -- "$tmp"' EXIT
cat > "$tmp" <<DESKTOP
$marker
[Desktop Entry]
Type=Application
Name=Nitro Control Tray
Comment=Optional read-only hardware monitoring indicator
Exec="$launcher" --tray
Icon=utilities-system-monitor
Terminal=false
OnlyShowIn=GNOME;
X-GNOME-Autostart-enabled=true
DESKTOP
chmod 644 "$tmp"
mv -f -- "$tmp" "$target"
trap - EXIT
echo "Opt-in Nitro Control tray autostart enabled for next GNOME login."
echo "Disable: bash scripts/disable-tray-autostart-fedora.sh"

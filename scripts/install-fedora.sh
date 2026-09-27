#!/usr/bin/env bash
# Per-user installation. Does not install a kernel driver or change hardware settings.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if ! /usr/bin/python3 -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); from gi.repository import Gtk, Adw' 2>/dev/null; then
  echo 'Missing GUI dependencies. First run:' >&2
  echo '  sudo dnf install python3-gobject gtk4 libadwaita' >&2
  exit 1
fi
app_dir="$HOME/.local/share/nitro-control"
bin_dir="$HOME/.local/bin"
desktop_dir="$HOME/.local/share/applications"
mkdir -p "$app_dir" "$bin_dir" "$desktop_dir"
rm -rf "$app_dir/nitro_control"
cp -R nitro_control "$app_dir/nitro_control"
find "$app_dir/nitro_control" -type d -name __pycache__ -prune -exec rm -rf '{}' +
cat > "$bin_dir/nitro-control" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="$HOME/.local/share/nitro-control${PYTHONPATH:+:$PYTHONPATH}"
exec /usr/bin/python3 -m nitro_control "$@"
WRAPPER
chmod 755 "$bin_dir/nitro-control"
cat > "$desktop_dir/io.github.tobiasguta.NitroControl.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Nitro Control
Comment=Acer Nitro dashboard and desktop power modes
Exec=$bin_dir/nitro-control
Icon=utilities-system-monitor
Terminal=false
Categories=System;Monitor;GTK;
StartupNotify=true
DESKTOP
chmod 644 "$desktop_dir/io.github.tobiasguta.NitroControl.desktop"
echo 'Installed Nitro Control for this user.'
echo 'Launch from GNOME applications or run: ~/.local/bin/nitro-control'

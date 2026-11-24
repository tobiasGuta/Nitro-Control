#!/usr/bin/env bash
# Per-user installation. Does not install a kernel driver or change hardware settings.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if ! /usr/bin/python3 -c 'import cairo, gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); gi.require_foreign("cairo"); from gi.repository import Gtk, Adw' 2>/dev/null; then
  echo 'Missing GUI dependencies. First run:' >&2
  echo '  sudo dnf install python3-gobject python3-cairo gtk4 libadwaita' >&2
  exit 1
fi
app_dir="$HOME/.local/share/nitro-control"
bin_dir="$HOME/.local/bin"
desktop_dir="$HOME/.local/share/applications"
icon_dir="$HOME/.local/share/icons/hicolor/scalable/apps"
symbolic_icon_dir="$HOME/.local/share/icons/hicolor/symbolic/apps"
mkdir -p "$app_dir" "$bin_dir" "$desktop_dir" "$icon_dir" "$symbolic_icon_dir"
rm -rf "$app_dir/nitro_control"
cp -R nitro_control "$app_dir/nitro_control"
find "$app_dir/nitro_control" -type d -name __pycache__ -prune -exec rm -rf '{}' +
install -m 644 nitro_control/icons/hicolor/scalable/apps/io.github.tobiasguta.NitroControl.svg "$icon_dir/io.github.tobiasguta.NitroControl.svg"
install -m 644 nitro_control/icons/hicolor/symbolic/apps/io.github.tobiasguta.NitroControl-symbolic.svg "$symbolic_icon_dir/io.github.tobiasguta.NitroControl-symbolic.svg"
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t -q "$HOME/.local/share/icons/hicolor" || true
fi
cat > "$bin_dir/nitro-control" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
# Do not import an outdated nitro_control package from the caller's cwd.
app_dir="$HOME/.local/share/nitro-control"
cd -- "$app_dir"
export PYTHONPATH="$app_dir${PYTHONPATH:+:$PYTHONPATH}"
# -P prevents Python from prepending an unsafe cwd to sys.path (Python 3.11+).
exec /usr/bin/python3 -P -m nitro_control "$@"
WRAPPER
chmod 755 "$bin_dir/nitro-control"
cat > "$desktop_dir/io.github.tobiasguta.NitroControl.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Nitro Control
Comment=Acer Nitro dashboard and desktop power modes
Exec=$bin_dir/nitro-control
Icon=io.github.tobiasguta.NitroControl
Terminal=false
Categories=System;Monitor;GTK;
StartupNotify=true
DESKTOP
chmod 644 "$desktop_dir/io.github.tobiasguta.NitroControl.desktop"
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database -q "$desktop_dir" || true
fi
echo 'Installed Nitro Control for this user.'
echo 'Launch from GNOME applications or run: ~/.local/bin/nitro-control'

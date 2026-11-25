"""Offline contracts for bundled application and symbolic tray icons."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from nitro_control import APP_ID

ROOT = Path(__file__).resolve().parents[1]
ICONS_ROOT = ROOT / "nitro_control" / "icons" / "hicolor"
APP_ICON = ICONS_ROOT / "scalable" / "apps" / f"{APP_ID}.svg"
SYMBOLIC_ICON = ICONS_ROOT / "symbolic" / "apps" / f"{APP_ID}-symbolic.svg"
INSTALLER = ROOT / "scripts" / "install-fedora.sh"
UNINSTALLER = ROOT / "scripts" / "uninstall.sh"


class BundledIconTests(unittest.TestCase):
    def test_bundled_svg_icons_exist_and_parse(self):
        for path, expected_box in (
            (APP_ICON, "0 0 128 128"),
            (SYMBOLIC_ICON, "0 0 16 16"),
        ):
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file(), f"Missing icon: {path}")
                tree = ET.fromstring(path.read_text(encoding="utf-8"))
                self.assertTrue(tree.tag.endswith("svg"))
                self.assertEqual(tree.attrib.get("viewBox"), expected_box)

    def test_desktop_entries_and_code_do_not_depend_on_missing_adwaita_name(self):
        for rel in (
            "scripts/install-fedora.sh",
            "scripts/enable-tray-autostart-fedora.sh",
            "nitro_control/tray_indicator.py",
        ):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("utilities-system-monitor", text)
        tray_source = (ROOT / "nitro_control" / "tray_indicator.py").read_text(encoding="utf-8")
        self.assertIn('f"{APP_ID}-symbolic"', tray_source)
        ui_source = (ROOT / "nitro_control" / "ui.py").read_text(encoding="utf-8")
        self.assertIn("Gtk.Window.set_default_icon_name(APP_ID)", ui_source)

    def test_installer_and_uninstaller_manage_icons_in_isolated_home(self):
        for script in (INSTALLER, UNINSTALLER):
            with self.subTest(script=script.name):
                subprocess.run(["bash", "-n", str(script)], check=True)
        gui_probe = subprocess.run(
            [
                "/usr/bin/python3",
                "-c",
                'import cairo, gi; gi.require_version("Gtk", "4.0"); '
                'gi.require_version("Adw", "1"); gi.require_foreign("cairo")',
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if gui_probe.returncode != 0:
            self.skipTest("GTK4/Libadwaita/PyCairo unavailable in offline CI environment")

        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'))
            install_res = subprocess.run(
                ["bash", str(INSTALLER)],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(install_res.returncode, 0, install_res.stderr)

            app_svg = home / ".local/share/icons/hicolor/scalable/apps" / f"{APP_ID}.svg"
            sym_svg = home / ".local/share/icons/hicolor/symbolic/apps" / f"{APP_ID}-symbolic.svg"
            desktop = home / ".local/share/applications" / f"{APP_ID}.desktop"
            self.assertTrue(app_svg.is_file())
            self.assertTrue(sym_svg.is_file())
            self.assertIn(f"Icon={APP_ID}", desktop.read_text(encoding="utf-8"))
            demo_desktop = desktop.with_name(f"{APP_ID}.Demo.desktop")
            demo_entry = demo_desktop.read_text(encoding="utf-8").splitlines()
            self.assertIn(f"Icon={APP_ID}", demo_entry)
            self.assertTrue(next(line for line in demo_entry if line.startswith("Exec=")).endswith(" --demo"))
            self.assertIn("NoDisplay=true", demo_entry)

            uninstall_res = subprocess.run(
                ["bash", str(UNINSTALLER)],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(uninstall_res.returncode, 0, uninstall_res.stderr)
            self.assertFalse(app_svg.exists())
            self.assertFalse(sym_svg.exists())
            self.assertFalse(desktop.exists())
            self.assertFalse(demo_desktop.exists())


if __name__ == "__main__":
    unittest.main()

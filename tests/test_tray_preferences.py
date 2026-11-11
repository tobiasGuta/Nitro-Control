"""Offline tests for explicit GTK4 dashboard → separate GTK3 tray integration."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from nitro_control.tray_preferences import (
    load_keep_in_tray,
    save_keep_in_tray,
    start_tray_process,
)

ROOT = Path(__file__).resolve().parents[1]


class TrayPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        original = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.temp.name
        def restore():
            if original is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = original
        self.addCleanup(restore)
        self.path = Path(self.temp.name) / "nitro-control" / "tray-preferences.json"

    def test_default_is_off_and_does_not_create_files(self):
        self.assertFalse(load_keep_in_tray())
        self.assertFalse(self.path.parent.exists())

    def test_toggle_persists_boolean_while_remaining_user_only(self):
        save_keep_in_tray(True)
        self.assertTrue(load_keep_in_tray())
        self.assertEqual(json.loads(self.path.read_text()), {"keep_in_tray": True})
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        save_keep_in_tray(False)
        self.assertFalse(load_keep_in_tray())

    def test_untrusted_and_corrupt_values_fail_closed(self):
        self.path.parent.mkdir()
        for payload in (b'{"keep_in_tray":"yes"}', b'{"keep_in_tray":1}',
                        b'{"keep_in_tray":null}', b'[]', b'not-json',
                        b'{"keep_in_tray":true}' + b" " * 5000):
            with self.subTest(payload=payload[:40]):
                self.path.write_bytes(payload)
                self.assertFalse(load_keep_in_tray())
        with self.assertRaises(ValueError):
            save_keep_in_tray("yes")  # type: ignore[arg-type]

    def test_does_not_follow_symlink_file_or_directory(self):
        outside = Path(self.temp.name) / "outside"
        outside.write_text('{"keep_in_tray":true}')
        self.path.parent.mkdir()
        self.path.symlink_to(outside)
        self.assertFalse(load_keep_in_tray())
        with self.assertRaises(OSError):
            save_keep_in_tray(False)
        self.assertEqual(outside.read_text(), '{"keep_in_tray":true}')

        self.path.unlink()
        self.path.parent.rmdir()
        self.path.parent.symlink_to(Path(self.temp.name))
        self.assertFalse(load_keep_in_tray())
        with self.assertRaises(OSError):
            save_keep_in_tray(True)

    def test_refuses_group_writable_settings_directory(self):
        self.path.parent.mkdir()
        self.path.parent.chmod(0o770)
        self.assertFalse(load_keep_in_tray())
        with self.assertRaises(OSError):
            save_keep_in_tray(True)


class TrayLaunchTests(unittest.TestCase):
    def test_tray_process_is_separate_and_checkout_pinned(self):
        with patch("nitro_control.tray_preferences.subprocess.Popen") as popen:
            result = start_tray_process()
            self.assertIs(result, popen.return_value)
        args, kwargs = popen.call_args
        self.assertEqual(args[0][1:], ["-P", "-m", "nitro_control", "--tray"])
        self.assertTrue(Path(kwargs["cwd"]).joinpath("nitro_control").is_dir())
        self.assertEqual(kwargs["env"]["PYTHONPATH"], kwargs["cwd"])
        self.assertTrue(kwargs["start_new_session"])
        self.assertNotIn("sudo", args[0])
        self.assertNotIn("pkexec", args[0])

    def test_ui_switch_is_live_opt_in_and_demo_safe(self):
        source = (ROOT / "nitro_control" / "ui.py").read_text()
        self.assertIn("Keep Nitro-Control in the system tray", source)
        self.assertIn("Adw.SwitchRow(", source)
        self.assertIn('self.tray_switch.connect("notify::active", self._on_tray_setting_changed)', source)
        self.assertIn("save_keep_in_tray(requested)", source)
        self.assertIn("load_keep_in_tray()", source)
        self.assertIn("if self._keep_in_tray and not self._demo:", source)
        self.assertIn("if self._demo or self._closed:", source)
        self.assertIn('GLib.timeout_add(800, self._check_tray_started, process)', source)
        self.assertIn("To stop an already running indicator, use its Quit tray action.", source)
        close = source.split("    def _on_close(", 1)[1].split("    def _on_timer(", 1)[0]
        self.assertIn("self._closed = True", close)
        self.assertNotIn("terminate()", close)
        self.assertNotIn("kill(", close)
        # User settings and process launch must not add WMI or PWM pathways.
        preferences = (ROOT / "nitro_control" / "tray_preferences.py").read_text()
        for operation in ("pkexec", "modprobe", "insmod", "RGBClient(",
                          "PowerProfileController(", ".apply(", "pwm1_enable"):
            self.assertNotIn(operation, preferences)

    def test_regular_installer_does_not_enable_login_autostart(self):
        script = (ROOT / "scripts" / "install-fedora.sh").read_text()
        self.assertNotIn("enable-tray-autostart", script)
        self.assertNotIn("tray-preferences.json", script)
        self.assertNotIn(" --tray", script)


if __name__ == "__main__":
    unittest.main()

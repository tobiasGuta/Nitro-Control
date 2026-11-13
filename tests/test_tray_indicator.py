"""Offline tests for optional read-only AppIndicator (no GTK/display needed)."""
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from nitro_control.demo import demo_snapshot
from nitro_control.models import Fan
from nitro_control.tray_model import format_tray_snapshot

ROOT = Path(__file__).resolve().parents[1]


class TrayReadoutTests(unittest.TestCase):
    def test_reference_an515_maps_observed_cpu_gpu_fan_speeds(self):
        snapshot = replace(
            demo_snapshot(),
            model="Nitro AN515-58",
            fans=(Fan("Fan 1", 2479, "Unknown"),
                  Fan("Fan 2", 3157, "Unknown")),
        )
        fields = format_tray_snapshot(snapshot)
        self.assertEqual(fields.cpu_fan, "CPU fan: 2,479 RPM")
        self.assertEqual(fields.gpu_fan, "GPU fan: 3,157 RPM")
        self.assertIn("CPU temperature:", fields.cpu_temp)
        self.assertIn("GPU temperature:", fields.gpu_temp)
        self.assertEqual(fields.updated, "Last reading: " + snapshot.sampled_at)

    def test_missing_sensors_are_unavailable_not_zero(self):
        snapshot = replace(demo_snapshot(), model="Unknown device",
                           fans=(), cpu_celsius=None, gpu=None)
        fields = format_tray_snapshot(snapshot)
        self.assertEqual(fields.cpu_temp, "CPU temperature: Unavailable")
        self.assertEqual(fields.gpu_temp, "GPU temperature: Unavailable")
        self.assertEqual(fields.cpu_fan, "Fan 1: Unavailable")
        self.assertEqual(fields.gpu_fan, "Fan 2: Unavailable")

    def test_unknown_model_has_generic_captions(self):
        snapshot = replace(demo_snapshot(), model="Unknown device",
                           fans=(Fan("Fan 1", 1200, "Unknown"),
                                 Fan("Fan 2", 1600, "Unknown")))
        fields = format_tray_snapshot(snapshot)
        self.assertEqual(fields.cpu_fan, "Fan 1: 1,200 RPM")
        self.assertEqual(fields.gpu_fan, "Fan 2: 1,600 RPM")

    def test_zero_rpm_is_reported_as_real_zero(self):
        snapshot = replace(demo_snapshot(), model="Nitro AN515-58",
                           fans=(Fan("Fan 1", 0, "Unknown"),
                                 Fan("Fan 2", None, "Unknown")))
        fields = format_tray_snapshot(snapshot)
        self.assertEqual(fields.cpu_fan, "CPU fan: 0 RPM")
        self.assertEqual(fields.gpu_fan, "GPU fan: Unavailable")

    def test_invalid_temperature_not_rendered(self):
        from nitro_control.models import GPU
        snapshot = replace(demo_snapshot(), cpu_celsius=float("nan"),
                           gpu=GPU("Example", float("inf"), None, None, None, None))
        fields = format_tray_snapshot(snapshot)
        self.assertEqual(fields.cpu_temp, "CPU temperature: Unavailable")
        self.assertEqual(fields.gpu_temp, "GPU temperature: Unavailable")


class TrayArchitectureTests(unittest.TestCase):
    def test_tray_is_not_imported_until_explicit_cli_flag(self):
        launcher = (ROOT / "nitro_control" / "__main__.py").read_text()
        self.assertIn('parser.add_argument("--tray", action="store_true"', launcher)
        self.assertIn("if args.tray:", launcher)
        self.assertIn("from .tray_indicator import run_tray", launcher)
        self.assertLess(launcher.index("if args.tray:"), launcher.index("from .ui import run_ui"))
        result = subprocess.run(
            [sys.executable, "-m", "nitro_control", "--version"],
            cwd=ROOT, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_tray_never_accesses_privileged_hardware(self):
        source = (ROOT / "nitro_control" / "tray_indicator.py").read_text()
        self.assertIn("HardwareReader()", source)
        self.assertIn('GLib.timeout_add_seconds(POLL_SECONDS, self._on_timer)', source)
        self.assertIn('Thread(target=self._collect_background', source)
        self.assertIn('gi.require_version("Gtk", "3.0")', source)
        self.assertIn('gi.require_version("AyatanaAppIndicator3", "0.1")', source)
        self.assertIn('sys.executable, "-P", "-m", "nitro_control"', source)
        for unsafe in ("RGBClient(", "PowerProfileController(", "pkexec",
                       "modprobe ", "insmod ", "/dev/port", "os.system(",
                       "subprocess.run(", ".apply("):
            self.assertNotIn(unsafe, source)

    def test_tray_rejects_demo_and_once_without_loading_gtk(self):
        for args in (["--tray", "--demo"], ["--tray", "--once"],
                     ["--tray", "--rgb-probe"]):
            with self.subTest(args=args):
                out = subprocess.run(
                    [sys.executable, "-m", "nitro_control", *args],
                    cwd=ROOT, capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(out.returncode, 2)
                self.assertIn("--tray cannot be combined", out.stderr)

    def test_installer_does_not_enable_tray_automatically(self):
        installer = (ROOT / "scripts" / "install-fedora.sh").read_text()
        self.assertNotIn("enable-tray-autostart", installer)
        self.assertNotIn("--tray", installer)


class TrayAutostartTests(unittest.TestCase):
    def test_opt_in_enable_then_disable_in_private_home(self):
        if os.geteuid() == 0:
            self.skipTest("Autostart script correctly refuses root")
        enable = ROOT / "scripts" / "enable-tray-autostart-fedora.sh"
        disable = ROOT / "scripts" / "disable-tray-autostart-fedora.sh"
        subprocess.run(["bash", "-n", str(enable)], check=True)
        subprocess.run(["bash", "-n", str(disable)], check=True)
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            launcher = home / ".local/bin/nitro-control"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("#!/bin/sh\nexit 0\n")
            launcher.chmod(0o755)
            env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME="")
            installed = subprocess.run(["bash", str(enable)], env=env,
                                       capture_output=True, text=True, timeout=10)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            entry = home / ".config/autostart/io.github.tobiasguta.NitroControl.Tray.desktop"
            text = entry.read_text()
            self.assertIn("Managed by Nitro Control optional tray autostart", text)
            self.assertIn("--tray", text)
            self.assertIn("Exec=\"" + str(launcher) + "\" --tray", text)
            removed = subprocess.run(["bash", str(disable)], env=env,
                                     capture_output=True, text=True, timeout=10)
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertFalse(entry.exists())

    def test_refuses_to_overwrite_non_owned_desktop_file(self):
        if os.geteuid() == 0:
            self.skipTest("Autostart script correctly refuses root")
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            launcher = home / ".local/bin/nitro-control"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("#!/bin/sh\nexit 0\n")
            launcher.chmod(0o755)
            target = home / ".config/autostart/io.github.tobiasguta.NitroControl.Tray.desktop"
            target.parent.mkdir(parents=True)
            target.write_text("Not ours\n")
            env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME="")
            for script in ("enable-tray-autostart-fedora.sh",
                           "disable-tray-autostart-fedora.sh"):
                p = subprocess.run(["bash", str(ROOT / "scripts" / script)],
                                   env=env, capture_output=True, text=True)
                self.assertNotEqual(p.returncode, 0)
                self.assertEqual(target.read_text(), "Not ours\n")

    def test_custom_config_and_uninstall_cleanup(self):
        if os.geteuid() == 0:
            self.skipTest("Autostart script correctly refuses root")
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            config_home = home / "custom-config"
            launcher = home / ".local/bin/nitro-control"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("#!/bin/sh\nexit 0\n")
            launcher.chmod(0o755)
            env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(config_home))
            enable = ROOT / "scripts/enable-tray-autostart-fedora.sh"
            uninstall = ROOT / "scripts/uninstall.sh"
            relative = Path("autostart/io.github.tobiasguta.NitroControl.Tray.desktop")
            managed = config_home / relative
            legacy = home / ".config" / relative

            subprocess.run(["bash", str(enable)], env=env, check=True, timeout=10)
            self.assertTrue(managed.is_file())
            self.assertFalse(legacy.exists())
            legacy.parent.mkdir(parents=True)
            legacy.write_text("Not ours\n")
            subprocess.run(["bash", str(uninstall)], env=env, check=True, timeout=10)
            self.assertFalse(managed.exists())
            self.assertFalse(launcher.exists())
            self.assertEqual(legacy.read_text(), "Not ours\n")


if __name__ == "__main__":
    unittest.main()

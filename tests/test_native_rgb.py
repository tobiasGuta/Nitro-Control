"""Synthetic native-WMI tests. No WMI, EC, modprobe, or real sysfs access."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from nitro_control import RGB_WMI_GUID
from nitro_control.native_rgb import NativeRGB, NativeRGBError
from nitro_control.rgb import LightingPlan
from nitro_control.rgb_hardware import RGBHardware, RGBHardwareError


COLORS = LightingPlan(("#FF0000", "#00FF00", "#0000FF", "#ABCDEF"), 25)


class NativeRGBTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.kernel = os.uname().release
        self.adapter = NativeRGB(self.root, self.kernel)
        self._write("sys/class/dmi/id/sys_vendor", "Acer")
        self._write("sys/class/dmi/id/product_name", "Nitro AN515-58")
        (self.root / "sys/bus/wmi/devices" / f"{RGB_WMI_GUID}-8").mkdir(parents=True)
        (self.root / "sys/module/acer_wmi").mkdir(parents=True)
        self._write("proc/cmdline", "quiet rhgb")
        self._write("sys/class/hwmon/hwmon1/name", "acer")
        self._write("sys/class/hwmon/hwmon1/fan1_input", "2200")
        self._write("sys/class/hwmon/hwmon1/fan2_input", "2100")
        self._write("sys/firmware/acpi/platform_profile", "balanced")
        self.adapter.module.parent.mkdir(parents=True, exist_ok=True)
        self.adapter.module.write_bytes(b"dummy .ko for synthetic test")
        self.adapter.digest.write_text(hashlib.sha256(self.adapter.module.read_bytes()).hexdigest())

    def _write(self, path: str, contents: str):
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(contents)

    def test_exact_model_valid_prerequisites(self):
        ok, reason = self.adapter.status()
        self.assertTrue(ok, reason)
        self.assertEqual(RGBHardware(self.root).probe().backend, "native-wmi")

    def test_non_acer_model_rejected(self):
        self._write("sys/class/dmi/id/product_name", "Nitro AN999-99")
        self.assertFalse(self.adapter.status()[0])
        self.assertFalse(RGBHardware(self.root).probe().available)

    def test_incomplete_fan_monitoring_rejected(self):
        (self.root / "sys/class/hwmon/hwmon1/fan2_input").unlink()
        self.assertFalse(self.adapter.status()[0])

    def test_missing_current_kernel_module_rejected(self):
        self.adapter.module.unlink()
        self.assertFalse(self.adapter.status()[0])

    def test_alternative_caps_rejected(self):
        self._write("proc/cmdline", "quiet acer_wmi.force_caps=1024")
        self.assertFalse(self.adapter.status()[0])

    def test_conflicting_driver_rejected(self):
        (self.root / "sys/module/linuwu_sense").mkdir()
        self.assertFalse(self.adapter.status()[0])

    def test_missing_guid_rejected(self):
        (self.root / "sys/bus/wmi/devices" / f"{RGB_WMI_GUID}-8").rmdir()
        self.assertFalse(self.adapter.status()[0])

    def test_requires_admin_for_physical_apply(self):
        with patch("nitro_control.native_rgb.os.geteuid", return_value=1000):
            with self.assertRaisesRegex(NativeRGBError, "administrator"):
                self.adapter.apply(COLORS)

    def test_helper_invokes_fixed_module_and_unloads(self):
        calls = []
        def runner(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        lock = self.root / "native-rgb.lock"
        with (patch("nitro_control.native_rgb.os.geteuid", return_value=0),
              patch("nitro_control.native_rgb._LOCK", lock),
              patch("nitro_control.native_rgb.os.fstat",
                    return_value=SimpleNamespace(st_uid=0, st_mode=0o600)),
              patch.object(self.adapter, "_verify_install"),
              patch("nitro_control.native_rgb._run", side_effect=runner)):
            message = self.adapter.apply(COLORS)
        self.assertIn("visually", message)
        self.assertEqual(calls[0][0], "/usr/sbin/insmod")
        self.assertEqual(calls[0][1], str(self.adapter.module))
        self.assertEqual(calls[0][2:], [
            "apply=1", "colors=FF0000,00FF00,0000FF,ABCDEF", "brightness=25"])
        self.assertEqual(calls[1], ["/usr/sbin/rmmod", "nitro_rgb_enable_poc"])

    def test_write_failure_does_not_report_success(self):
        def runner(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="firmware refused")
        with (patch("nitro_control.native_rgb.os.geteuid", return_value=0),
              patch("nitro_control.native_rgb._LOCK", self.root / "lock"),
              patch("nitro_control.native_rgb.os.fstat",
                    return_value=SimpleNamespace(st_uid=0, st_mode=0o600)),
              patch.object(self.adapter, "_verify_install"),
              patch("nitro_control.native_rgb._run", side_effect=runner)):
            with self.assertRaisesRegex(NativeRGBError, "firmware refused"):
                self.adapter.apply(COLORS)

    def test_rgb_hardware_refuses_unprivileged_native_apply(self):
        with patch("nitro_control.native_rgb.os.geteuid", return_value=1000):
            with self.assertRaises(RGBHardwareError):
                RGBHardware(self.root).apply(COLORS)


if __name__ == "__main__":
    unittest.main()

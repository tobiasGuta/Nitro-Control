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
from contextlib import contextmanager

from nitro_control import RGB_WMI_GUID
from nitro_control.native_rgb import NativeRGB, NativeRGBError
from nitro_control.rgb import LightingPlan
from nitro_control.rgb_hardware import RGBHardware, RGBHardwareError


COLORS = LightingPlan(("#FF0000", "#00FF00", "#0000FF", "#ABCDEF"), 100)


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

    @contextmanager
    def _pretend_installed_root_owned(self):
        """Only fake file owner metadata; keep real hashes and file contents."""
        actual_stat = Path.stat
        owned = {self.adapter.module, self.adapter.digest}

        def fake_stat(path, *args, **kwargs):
            original = actual_stat(path, *args, **kwargs)
            if path in owned:
                fields = list(original)
                fields[4] = 0  # st_uid, as with the root-owned installed files
                fields[0] = (fields[0] & ~0o777) | (
                    0o644 if path == self.adapter.module else 0o600)
                return os.stat_result(fields)
            return original

        with patch("nitro_control.native_rgb.Path.stat", autospec=True,
                   side_effect=fake_stat):
            yield

    def test_checksum_tampering_refused_before_any_module_command(self):
        self.adapter.module.write_bytes(b"tampered development module")
        with (self._pretend_installed_root_owned(),
              patch("nitro_control.native_rgb._run") as run):
            with self.assertRaisesRegex(NativeRGBError, "digest mismatch"):
                self.adapter._verify_install()
            run.assert_not_called()

    def test_digest_symlink_refused(self):
        self.adapter.digest.unlink()
        target = self.root / "fake-digest"
        target.write_text("0" * 64)
        self.adapter.digest.symlink_to(target)
        with (self._pretend_installed_root_owned(),
              patch("nitro_control.native_rgb._run") as run):
            with self.assertRaisesRegex(NativeRGBError, "symlink"):
                self.adapter._verify_install()
            run.assert_not_called()

    def test_different_kernel_vermagic_refused(self):
        answers = [
            subprocess.CompletedProcess(["modinfo"], 0, stdout="nitro_rgb_enable_poc\n"),
            subprocess.CompletedProcess(["modinfo"], 0, stdout="unrelated-kernel SMP\n"),
        ]
        with (self._pretend_installed_root_owned(),
              patch("nitro_control.native_rgb._run", side_effect=answers) as run):
            with self.assertRaisesRegex(NativeRGBError, "kernel version mismatch"):
                self.adapter._verify_install()
            self.assertEqual(run.call_count, 2)

    def test_corrupted_module_denied_before_insmod_even_for_root(self):
        self.adapter.module.write_bytes(b"corrupted after install")
        with (self._pretend_installed_root_owned(),
              patch("nitro_control.native_rgb.os.geteuid", return_value=0),
              patch("nitro_control.native_rgb._run") as run):
            with self.assertRaisesRegex(NativeRGBError, "digest mismatch"):
                self.adapter.apply(COLORS)
            run.assert_not_called()

    def test_unsafe_lock_aborts_without_module_operation(self):
        with (patch("nitro_control.native_rgb.os.geteuid", return_value=0),
              patch("nitro_control.native_rgb._LOCK", self.root / "lock"),
              patch.object(self.adapter, "_verify_install"),
              patch("nitro_control.native_rgb.os.open",
                    side_effect=PermissionError("lock denied")),
              patch("nitro_control.native_rgb._run") as run):
            with self.assertRaisesRegex(NativeRGBError, "lock unavailable"):
                self.adapter.apply(COLORS)
            run.assert_not_called()

    def test_busy_lock_prevents_wmi_operation(self):
        with (patch("nitro_control.native_rgb.os.geteuid", return_value=0),
              patch("nitro_control.native_rgb._LOCK", self.root / "lock"),
              patch("nitro_control.native_rgb.os.fstat",
                    return_value=SimpleNamespace(st_uid=0, st_mode=0o600)),
              patch("nitro_control.native_rgb.fcntl.flock",
                    side_effect=BlockingIOError("already locked")),
              patch.object(self.adapter, "_verify_install"),
              patch("nitro_control.native_rgb._run") as run):
            with self.assertRaisesRegex(NativeRGBError, "already active"):
                self.adapter.apply(COLORS)
            run.assert_not_called()

    def test_failed_module_insert_with_partial_load_still_unloads(self):
        commands = []
        def runner(argv, **kwargs):
            commands.append(argv[0])
            if argv[0].endswith("/insmod"):
                (self.root / "sys/module/nitro_rgb_enable_poc").mkdir()
                return subprocess.CompletedProcess(argv, 1, stderr="firmware rejected")
            (self.root / "sys/module/nitro_rgb_enable_poc").rmdir()
            return subprocess.CompletedProcess(argv, 0)

        with (patch("nitro_control.native_rgb.os.geteuid", return_value=0),
              patch("nitro_control.native_rgb._LOCK", self.root / "lock"),
              patch("nitro_control.native_rgb.os.fstat",
                    return_value=SimpleNamespace(st_uid=0, st_mode=0o600)),
              patch.object(self.adapter, "_verify_install"),
              patch("nitro_control.native_rgb._run", side_effect=runner)):
            with self.assertRaisesRegex(NativeRGBError, "firmware rejected"):
                self.adapter.apply(COLORS)
        self.assertEqual(commands, ["/usr/sbin/insmod", "/usr/sbin/rmmod"])

    def test_unload_failure_blocks_success(self):
        commands = []
        def runner(argv, **kwargs):
            commands.append(argv[0])
            return subprocess.CompletedProcess(
                argv, 0 if argv[0].endswith("/insmod") else 1,
                stderr="unload failed" if argv[0].endswith("/rmmod") else "")

        with (patch("nitro_control.native_rgb.os.geteuid", return_value=0),
              patch("nitro_control.native_rgb._LOCK", self.root / "lock"),
              patch("nitro_control.native_rgb.os.fstat",
                    return_value=SimpleNamespace(st_uid=0, st_mode=0o600)),
              patch.object(self.adapter, "_verify_install"),
              patch("nitro_control.native_rgb._run", side_effect=runner)):
            with self.assertRaisesRegex(NativeRGBError, "CRITICAL"):
                self.adapter.apply(COLORS)
        self.assertEqual(commands, ["/usr/sbin/insmod", "/usr/sbin/rmmod"])

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

    def test_non_100_brightness_rejected_before_module_operations(self):
        # Confirm an unverified firmware brightness never reaches insmod, even
        # for a root caller and a valid staged module.
        for value in (0, 25, 65, 75, 99):
            with self.subTest(brightness=value):
                plan = LightingPlan(COLORS.zones, value)
                with (patch("nitro_control.native_rgb.os.geteuid", return_value=0),
                      patch("nitro_control.native_rgb._run") as run):
                    with self.assertRaisesRegex(NativeRGBError, "requires 100%"):
                        self.adapter.apply(plan)
                    run.assert_not_called()

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
            "apply=1", "colors=FF0000,00FF00,0000FF,ABCDEF", "brightness=100"])
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

"""Stock-driver fan changes against disposable simulated sysfs."""
import errno
import fcntl
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from nitro_control import fan_control
from nitro_control.fan_client import FanClient
from nitro_control.fan_control import FanControlError, FanPlan


class FanControlTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.platform = self.root / "sys/devices/platform/acer-wmi"
        self.hwmon = self.platform / "hwmon/hwmon11"
        self.hwmon.mkdir(parents=True)
        driver = self.root / "sys/bus/platform/drivers/acer-wmi"
        driver.mkdir(parents=True)
        stock = self.root / "sys/module/acer_wmi"
        stock.mkdir(parents=True)
        (driver / "module").symlink_to(stock)
        (self.platform / "driver").symlink_to(driver)
        self.dmi = self.root / "sys/class/dmi/id"
        self.dmi.mkdir(parents=True)
        (self.dmi / "sys_vendor").write_text("Acer\n")
        (self.dmi / "product_name").write_text("Nitro AN515-58\n")
        (self.hwmon / "name").write_text("acer\n")
        for channel in (1, 2):
            (self.hwmon / f"pwm{channel}").write_text("0\n")
            (self.hwmon / f"pwm{channel}_enable").write_text("2\n")
        self.firmware = self.root / "sys/firmware/acpi/platform_profile"
        self.firmware.parent.mkdir(parents=True)
        self.firmware.write_text("balanced\n")

    def test_auto_max_and_independent_custom(self):
        for plan in (FanPlan("Max"), FanPlan("Custom", 45, None),
                     FanPlan("Custom", None, 62), FanPlan("Custom", 55, 70), FanPlan("Auto")):
            with self.subTest(plan=plan):
                fan_control.apply_fan_plan(plan, self.root)
                self.assertEqual(fan_control.read_fan_plan(self.hwmon), plan)
        self.assertEqual(self.firmware.read_text(), "balanced\n")

    def test_readback_handles_truncation_at_each_supported_percentage(self):
        original_write = fan_control._write

        def kernel_write(path, value):
            if path.name == "pwm1":
                value = (value * 100 // 255) * 255 // 100
            original_write(path, value)

        with patch.object(fan_control, "_write", side_effect=kernel_write):
            for percent in range(45, 101):
                plan = FanPlan("Custom", percent, None)
                fan_control.apply_fan_plan(plan, self.root)
                self.assertEqual(fan_control.read_fan_plan(self.hwmon), plan)

    def test_unknown_mode_can_be_recovered_by_auto(self):
        (self.hwmon / "pwm1_enable").write_text("unknown\n")
        self.assertIsNone(fan_control.read_fan_plan(self.hwmon))
        fan_control.apply_fan_plan(FanPlan("Auto"), self.root)
        self.assertEqual(fan_control.read_fan_plan(self.hwmon), FanPlan("Auto"))

    def test_partial_failure_restores_both_fans(self):
        original_write = fan_control._write

        def reject_gpu_target(path, value):
            if path.name == "pwm2":
                raise OSError("test write failed")
            original_write(path, value)

        with patch.object(fan_control, "_write", side_effect=reject_gpu_target):
            with self.assertRaisesRegex(FanControlError, "Both fans returned to Auto"):
                fan_control.apply_fan_plan(FanPlan("Custom", 45, 55), self.root)
        self.assertEqual(fan_control.read_fan_plan(self.hwmon), FanPlan("Auto"))

    def test_restore_attempts_other_fan_when_first_one_fails(self):
        original_write = fan_control._write
        fan_control.apply_fan_plan(FanPlan("Max"), self.root)

        def reject_cpu_auto(path, value):
            if path.name == "pwm1_enable":
                raise OSError("test write failed")
            original_write(path, value)

        with patch.object(fan_control, "_write", side_effect=reject_cpu_auto):
            with self.assertRaisesRegex(FanControlError, "Could not verify Auto for CPU"):
                fan_control.apply_fan_plan(FanPlan("Auto"), self.root)
        self.assertEqual((self.hwmon / "pwm2_enable").read_text(), "2\n")

    def test_requires_stock_driver_exact_model_and_real_attributes(self):
        (self.dmi / "product_name").write_text("Nitro AN515-57\n")
        self.assertIsNone(fan_control.find_fan_controls(self.root))
        (self.dmi / "product_name").write_text("Nitro AN515-58\n")
        linuwu = self.root / "sys/module/linuwu_sense"
        linuwu.mkdir()
        self.assertIsNone(fan_control.find_fan_controls(self.root))
        linuwu.rmdir()
        target = self.hwmon / "pwm2"
        target.unlink()
        target.symlink_to(self.firmware)
        self.assertIsNone(fan_control.find_fan_controls(self.root))

    def test_overlapping_change_is_rejected(self):
        with fan_control._lock(self.root):
            with self.assertRaisesRegex(FanControlError, "Another fan change"):
                fan_control.apply_fan_plan(FanPlan("Max"), self.root)
        self.assertEqual(fan_control.read_fan_plan(self.hwmon), FanPlan("Auto"))

    def test_sysfs_directory_flock_does_not_block_fan_control_or_auto_recovery(self):
        fan_control.apply_fan_plan(FanPlan("Custom", 55, 65), self.root)
        hwmon_fd = os.open(self.hwmon, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(hwmon_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fan_control.apply_fan_plan(FanPlan("Max"), self.root)
            self.assertEqual(fan_control.read_fan_plan(self.hwmon), FanPlan("Max"))
            fan_control.apply_fan_plan(FanPlan("Auto"), self.root)
            self.assertEqual(fan_control.read_fan_plan(self.hwmon), FanPlan("Auto"))
        finally:
            os.close(hwmon_fd)

    def test_private_lock_retains_stable_inode_and_blocks_concurrent_process(self):
        fan_control.apply_fan_plan(FanPlan("Max"), self.root)
        lock_dir = self.root / "run/nitro-control"
        lock_file = lock_dir / "fan.lock"
        self.assertEqual(stat.S_IMODE(lock_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(lock_file.stat().st_mode), 0o600)
        inode_before = lock_file.stat().st_ino

        holder = subprocess.Popen(
            [
                sys.executable, "-c",
                "import fcntl, os, sys; "
                "fd = os.open(sys.argv[1], os.O_RDWR); "
                "fcntl.flock(fd, fcntl.LOCK_EX); "
                "print('locked', flush=True); "
                "sys.stdin.read()",
                str(lock_file),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(holder.stdout.readline().strip(), "locked")
            with self.assertRaisesRegex(FanControlError, "Another fan change"):
                fan_control.apply_fan_plan(FanPlan("Auto"), self.root)
        finally:
            holder.communicate(input="release", timeout=5)

        fan_control.apply_fan_plan(FanPlan("Auto"), self.root)
        self.assertEqual(lock_file.stat().st_ino, inode_before)

    def test_unsafe_lock_filesystem_metadata_rejected_without_sysfs_writes(self):
        fan_control.apply_fan_plan(FanPlan("Max"), self.root)
        run_dir = self.root / "run"
        lock_dir = run_dir / "nitro-control"
        lock_file = lock_dir / "fan.lock"

        # Permissive file mode rejected.
        lock_file.chmod(0o644)
        with self.assertRaisesRegex(FanControlError, "unsafe permissions"):
            fan_control.apply_fan_plan(FanPlan("Auto"), self.root)
        lock_file.chmod(0o600)

        # Permissive lock directory rejected.
        lock_dir.chmod(0o755)
        with self.assertRaisesRegex(FanControlError, "unsafe permissions"):
            fan_control.apply_fan_plan(FanPlan("Auto"), self.root)
        lock_dir.chmod(0o700)

        # Symlink lock file rejected.
        outside = self.root / "outside-lock"
        outside.touch()
        lock_file.unlink()
        lock_file.symlink_to(outside)
        with self.assertRaisesRegex(FanControlError, "unavailable"):
            fan_control.apply_fan_plan(FanPlan("Auto"), self.root)
        lock_file.unlink()

        # FIFO lock file fails closed without blocking.
        if hasattr(os, "mkfifo"):
            os.mkfifo(lock_file, 0o600)
            with self.assertRaisesRegex(FanControlError, "unsafe ownership or file type"):
                fan_control.apply_fan_plan(FanPlan("Auto"), self.root)
            lock_file.unlink()

        # Symlink lock directory rejected.
        lock_dir.rmdir()
        target_dir = self.root / "real-dir"
        target_dir.mkdir(mode=0o700)
        lock_dir.symlink_to(target_dir)
        with self.assertRaisesRegex(FanControlError, "unavailable"):
            fan_control.apply_fan_plan(FanPlan("Auto"), self.root)
        lock_dir.unlink()

        # Group/world-writable runtime directory rejected.
        run_dir.chmod(0o777)
        with self.assertRaisesRegex(FanControlError, "unsafe permissions"):
            fan_control.apply_fan_plan(FanPlan("Auto"), self.root)
        run_dir.chmod(0o755)

        # Wrong owner on lock file rejected.
        actual_fstat = os.fstat
        def fake_wrong_uid(fd):
            res = actual_fstat(fd)
            if stat.S_ISREG(res.st_mode):
                fields = list(res)
                fields[4] = os.geteuid() + 1
                return os.stat_result(fields)
            return res

        with patch("nitro_control.fan_control.os.fstat", side_effect=fake_wrong_uid):
            with self.assertRaisesRegex(FanControlError, "unsafe ownership"):
                fan_control.apply_fan_plan(FanPlan("Auto"), self.root)

        self.assertEqual(fan_control.read_fan_plan(self.hwmon), FanPlan("Max"))

    def test_recovery_rejects_cross_channel_reset_and_unverified_write_failure(self):
        fan_control.apply_fan_plan(FanPlan("Max"), self.root)
        original_write = fan_control._write

        # Even if pwm1 already contains 2, a failed Auto write on pwm1 must fail recovery.
        original_write(self.hwmon / "pwm1_enable", 2)
        def fail_cpu_write(path, value):
            if path.name == "pwm1_enable":
                raise OSError(errno.EIO, "write error")
            original_write(path, value)

        with patch.object(fan_control, "_write", side_effect=fail_cpu_write):
            with self.assertRaisesRegex(FanControlError, "Could not verify Auto for CPU"):
                fan_control.apply_fan_plan(FanPlan("Auto"), self.root)
        self.assertEqual((self.hwmon / "pwm2_enable").read_text(), "2\n")

        # If writing pwm2_enable=2 causes pwm1_enable to drop back to 0, post-write verification catches it.
        fan_control.apply_fan_plan(FanPlan("Max"), self.root)
        def interfere_after_write(path, value):
            original_write(path, value)
            if path.name == "pwm2_enable" and value == 2:
                original_write(self.hwmon / "pwm1_enable", 0)

        with patch.object(fan_control, "_write", side_effect=interfere_after_write):
            with self.assertRaisesRegex(FanControlError, "Could not verify Auto for CPU"):
                fan_control.apply_fan_plan(FanPlan("Auto"), self.root)

    def test_enxio_read_recovers_to_auto_and_hwmon_disappearance_fails_cleanly(self):
        actual_read = fan_control._read
        def enxio_on_enable(path):
            if path.name.endswith("_enable"):
                raise OSError(errno.ENXIO, "No such device or address")
            return actual_read(path)

        with patch.object(fan_control, "_read", side_effect=enxio_on_enable):
            self.assertIsNone(fan_control.read_fan_plan(self.hwmon))
        fan_control.apply_fan_plan(FanPlan("Auto"), self.root)
        self.assertEqual(fan_control.read_fan_plan(self.hwmon), FanPlan("Auto"))

        # Disappearing hwmon between initial check and post-lock check raises cleanly.
        actual_find = fan_control.find_fan_controls
        calls = {"count": 0}
        def vanish_inside_lock(root):
            calls["count"] += 1
            return actual_find(root) if calls["count"] == 1 else None

        with patch.object(fan_control, "find_fan_controls", side_effect=vanish_inside_lock):
            with self.assertRaisesRegex(FanControlError, "unavailable"):
                fan_control.apply_fan_plan(FanPlan("Auto"), self.root)

    def test_main_cli_validates_arguments_and_requires_root(self):
        for argv, euid, expected_rc in (
            (["fan-helper", "Auto"], 1000, 1),
            (["fan-helper"], 0, 1),
            (["fan-helper", "Turbo"], 0, 1),
            (["fan-helper", "Custom", "44", "50"], 0, 1),
            (["fan-helper", "Custom", "auto", "auto"], 0, 1),
            (["fan-helper", "Custom", "45;id", "auto"], 0, 1),
        ):
            with self.subTest(argv=argv, euid=euid):
                stderr = io.StringIO()
                with (patch.object(sys, "argv", argv),
                      patch("nitro_control.fan_control.os.geteuid", return_value=euid),
                      patch("nitro_control.fan_control.sys.stderr", stderr),
                      patch("nitro_control.fan_control.apply_fan_plan") as apply_mock):
                    rc = fan_control.main()
                self.assertEqual(rc, expected_rc)
                apply_mock.assert_not_called()

        stdout = io.StringIO()
        with (patch.object(sys, "argv", ["fan-helper", "Custom", "45", "auto"]),
              patch("nitro_control.fan_control.os.geteuid", return_value=0),
              patch("nitro_control.fan_control.sys.stdout", stdout),
              patch("nitro_control.fan_control.apply_fan_plan") as apply_mock):
            rc = fan_control.main()
        self.assertEqual(rc, 0)
        self.assertEqual(stdout.getvalue().strip(), "Fan settings verified.")
        apply_mock.assert_called_once_with(FanPlan("Custom", 45, None))

    def test_out_of_range_and_wrong_mode_settings_are_rejected(self):
        for args in (("Custom", 44, None), ("Custom", 101, None), ("Custom", None, None),
                     ("Custom", True, 55), ("Max", 45, None), ("Turbo", None, None)):
            with self.subTest(args=args), self.assertRaises(FanControlError):
                FanPlan(*args)

    def test_client_invokes_only_the_fan_helper_with_validated_settings(self):
        helper = self.root / "fan-helper"
        helper.touch()

        with patch("nitro_control.fan_client.shutil.which", return_value="/usr/bin/pkexec"):
            def run(command, **kwargs):
                self.assertEqual(command, ["pkexec", "--disable-internal-agent", str(helper),
                                           "Custom", "45", "auto"])
                return subprocess.CompletedProcess(command, 0, "Fan settings verified.\n", "")

            FanClient(helper, runner=run).apply(FanPlan("Custom", 45))

    def test_client_rejects_canceled_denied_timeout_and_unverified_stdout(self):
        helper = self.root / "fan-helper"
        helper.touch()
        with patch("nitro_control.fan_client.shutil.which", return_value="/usr/bin/pkexec"):
            for rc, stdout, stderr, regex in (
                (126, "", "", "canceled or denied"),
                (127, "", "", "denied or the fan helper"),
                (1, "", "Helper failed", "Helper failed"),
                (0, "", "", "did not confirm verification"),
            ):
                with self.subTest(rc=rc, stdout=stdout):
                    client = FanClient(
                        helper,
                        runner=lambda cmd, **_kw: subprocess.CompletedProcess(cmd, rc, stdout, stderr),
                    )
                    with self.assertRaisesRegex(FanControlError, regex):
                        client.apply(FanPlan("Auto"))

            def raise_timeout(cmd, **_kw):
                raise subprocess.TimeoutExpired(cmd, 120)

            with self.assertRaisesRegex(FanControlError, "does not guarantee the privileged process stopped"):
                FanClient(helper, runner=raise_timeout).apply(FanPlan("Auto"))

            def raise_eperm(cmd, **_kw):
                raise PermissionError("cannot kill privileged pkexec child")

            with self.assertRaisesRegex(FanControlError, "unable to signal privileged child"):
                FanClient(helper, runner=raise_eperm).apply(FanPlan("Auto"))

            def raise_missing(cmd, **_kw):
                raise FileNotFoundError("pkexec missing")

            with self.assertRaisesRegex(FanControlError, "could not be started"):
                FanClient(helper, runner=raise_missing).apply(FanPlan("Auto"))

            # Symlinked fan helper refused before launching pkexec.
            sym_helper = self.root / "sym-fan-helper"
            sym_helper.symlink_to(helper)
            self.assertFalse(FanClient(sym_helper, runner=lambda *a, **k: None).ready())


if __name__ == "__main__":
    unittest.main()

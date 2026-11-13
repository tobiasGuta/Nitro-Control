"""Stock-driver fan changes against disposable simulated sysfs."""
from pathlib import Path
import subprocess
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
        with fan_control._lock(self.hwmon):
            with self.assertRaisesRegex(FanControlError, "Another fan change"):
                fan_control.apply_fan_plan(FanPlan("Max"), self.root)
        self.assertEqual(fan_control.read_fan_plan(self.hwmon), FanPlan("Auto"))

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


if __name__ == "__main__":
    unittest.main()

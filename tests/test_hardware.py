"""Offline tests exercise the exact interface shapes observed on AN515-58."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from nitro_control import RGB_WMI_GUID
from nitro_control.demo import demo_snapshot
from nitro_control.hardware import HardwareReader, _fan_mode, _gpu_from_csv, _numeric


def make_file(root: Path, location: str, data: str) -> Path:
    path = root / location.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")
    return path


class HardwareTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        make_file(self.root, "sys/class/dmi/id/product_name", "Nitro AN515-58\n")
        make_file(self.root, "sys/firmware/acpi/platform_profile", "balanced\n")
        make_file(self.root, "sys/firmware/acpi/platform_profile_choices",
                  "low-power quiet balanced balanced-performance performance\n")
        acer = "sys/class/hwmon/hwmon6"
        make_file(self.root, f"{acer}/name", "acer\n")
        make_file(self.root, f"{acer}/fan1_input", "1986\n")
        make_file(self.root, f"{acer}/fan2_input", "2112\n")
        make_file(self.root, f"{acer}/pwm1_enable", "2\n")
        make_file(self.root, f"{acer}/pwm2_enable", "2\n")
        make_file(self.root, f"{acer}/temp1_input", "48000\n")
        make_file(self.root, f"{acer}/temp2_input", "40000\n")
        make_file(self.root, f"{acer}/temp3_input", "44000\n")
        cpu = "sys/class/hwmon/hwmon7"
        make_file(self.root, f"{cpu}/name", "coretemp\n")
        make_file(self.root, f"{cpu}/temp1_label", "Package id 0\n")
        make_file(self.root, f"{cpu}/temp1_input", "45000\n")
        make_file(self.root, "sys/class/hwmon/hwmon3/name", "nvme\n")
        make_file(self.root, "sys/class/hwmon/hwmon3/temp1_input", "34900\n")
        make_file(self.root, f"sys/bus/wmi/devices/{RGB_WMI_GUID}-8/guid", RGB_WMI_GUID)

    @staticmethod
    def runner(command, **_kwargs):
        if command[0] == "nvidia-smi":
            return subprocess.CompletedProcess(command, 0, "NVIDIA GeForce RTX 3050 Ti, 40, 0, 12, 4096, 6.00\n", "")
        if command[0] == "tuned-adm":
            return subprocess.CompletedProcess(command, 0, "Current active profile: balanced\n", "")
        raise AssertionError(f"Unexpected command: {command!r}")

    def test_complete_snapshot_matches_expected_nitro_interface(self):
        snap = HardwareReader(self.root, self.runner).collect()
        self.assertEqual(snap.model, "Nitro AN515-58")
        self.assertEqual(snap.cpu_celsius, 45)
        self.assertEqual([fan.rpm for fan in snap.fans], [1986, 2112])
        self.assertEqual([fan.control_mode for fan in snap.fans], ["Automatic", "Automatic"])
        self.assertEqual([t.celsius for t in snap.acer_temperatures], [48, 40, 44])
        self.assertIn("unidentified", snap.acer_temperatures[0].name)
        self.assertEqual(snap.drives[0].celsius, 34.9)
        self.assertEqual(snap.firmware_profile, "balanced")
        self.assertEqual(len(snap.firmware_profile_choices), 5)
        self.assertEqual(snap.tuned_profile, "balanced")
        self.assertTrue(snap.rgb.present)
        self.assertIsNone(snap.rgb.driver)
        self.assertEqual(snap.gpu.memory_total_mib, 4096)

    def test_rgb_instance_suffix_is_detected(self):
        snap = HardwareReader(self.root, self.runner).collect()
        self.assertEqual(snap.rgb.interface, f"{RGB_WMI_GUID}-8")

    def test_bound_rgb_driver_name(self):
        device = self.root / f"sys/bus/wmi/devices/{RGB_WMI_GUID}-8"
        driver_dir = self.root / "sys/bus/wmi/drivers/acer-rgb"
        driver_dir.mkdir(parents=True)
        (device / "driver").symlink_to(driver_dir, target_is_directory=True)
        self.assertEqual(HardwareReader(self.root, self.runner).collect().rgb.driver, "acer-rgb")

    def test_missing_gpu_graceful(self):
        def no_gpu(command, **_kwargs):
            if command[0] == "nvidia-smi":
                raise FileNotFoundError("no NVIDIA")
            return self.runner(command, **_kwargs)
        self.assertIsNone(HardwareReader(self.root, no_gpu).collect().gpu)

    def test_gpu_command_failure_graceful(self):
        def failing(command, **_kwargs):
            if command[0] == "nvidia-smi":
                return subprocess.CompletedProcess(command, 1, "", "Driver error")
            return self.runner(command, **_kwargs)
        self.assertIsNone(HardwareReader(self.root, failing).collect().gpu)

    def test_gpu_timeout_graceful(self):
        def timeout(command, **_kwargs):
            if command[0] == "nvidia-smi":
                raise subprocess.TimeoutExpired(command, 2)
            return self.runner(command, **_kwargs)
        self.assertIsNone(HardwareReader(self.root, timeout).collect().gpu)

    def test_unlabeled_acer_temperature_not_assigned_cpu_identity(self):
        snap = HardwareReader(self.root, self.runner).collect()
        self.assertNotEqual(snap.acer_temperatures[0].name, "CPU")
        self.assertEqual(snap.cpu_celsius, 45)

    def test_unavailable_cpu_not_taken_from_unlabeled_acer(self):
        (self.root / "sys/class/hwmon/hwmon7/temp1_label").unlink()
        self.assertIsNone(HardwareReader(self.root, self.runner).collect().cpu_celsius)

    def test_missing_files_do_not_invent_readings(self):
        blank = tempfile.TemporaryDirectory()
        self.addCleanup(blank.cleanup)
        snap = HardwareReader(Path(blank.name), lambda *_a, **_k: subprocess.CompletedProcess([], 1, "", "")).collect()
        self.assertEqual(snap.model, "Unknown device")
        self.assertIsNone(snap.cpu_celsius)
        self.assertEqual(snap.fans, ())
        self.assertFalse(snap.rgb.present)

    def test_malformed_numeric_is_unavailable(self):
        make_file(self.root, "sys/class/hwmon/hwmon6/fan1_input", "not a number\n")
        self.assertIsNone(HardwareReader(self.root, self.runner).collect().fans[0].rpm)

    def test_fan_modes(self):
        self.assertEqual(_fan_mode("1"), "Manual")
        self.assertEqual(_fan_mode("2"), "Automatic")
        self.assertEqual(_fan_mode("3"), "Automatic")
        self.assertEqual(_fan_mode("0"), "Full speed / control disabled")
        self.assertEqual(_fan_mode("bad"), "Unknown")

    def test_gpu_csv_unavailable_values(self):
        gpu = _gpu_from_csv("NVIDIA GPU, N/A, 0, N/A, 4096, [Not Supported]\n")
        self.assertIsNone(gpu.celsius)
        self.assertEqual(gpu.utilization_percent, 0)
        self.assertIsNone(gpu.power_watts)

    def test_malformed_gpu_csv(self):
        self.assertIsNone(_gpu_from_csv("driver communication error\n"))

    def test_tuned_missing_graceful(self):
        def no_tuned(command, **_kwargs):
            if command[0] == "tuned-adm":
                raise FileNotFoundError("no tuned")
            return self.runner(command, **_kwargs)
        self.assertIsNone(HardwareReader(self.root, no_tuned).collect().tuned_profile)

    def test_snapshot_json(self):
        decoded = json.loads(HardwareReader(self.root, self.runner).collect().to_json())
        self.assertEqual(decoded["rgb"]["present"], True)
        self.assertFalse(decoded["demo"])
        self.assertNotIn("machine_id", decoded)

    def test_demo_is_clearly_labeled(self):
        demo = demo_snapshot()
        self.assertTrue(demo.demo)
        self.assertIn("demo", demo.model.lower())

    def test_collector_does_not_write_sysfs(self):
        with patch.object(Path, "write_text", side_effect=AssertionError("unexpected sysfs write")):
            HardwareReader(self.root, self.runner).collect()

    def test_nvidia_uses_no_shell_or_privileges(self):
        commands = []
        def capture(command, **kwargs):
            commands.append((command, kwargs))
            return self.runner(command, **kwargs)
        HardwareReader(self.root, capture).collect()
        self.assertEqual(commands[0][0][0], "nvidia-smi")
        self.assertFalse(commands[0][1].get("shell", False))
        self.assertLessEqual(commands[0][1]["timeout"], 2)
        self.assertEqual(commands[1][0][0], "tuned-adm")


class NumericTests(unittest.TestCase):
    def test_none_and_bad_values(self):
        self.assertIsNone(_numeric(None))
        self.assertIsNone(_numeric("N/A"))
        self.assertIsNone(_numeric("something else"))
        self.assertEqual(_numeric("5.5"), 5.5)


if __name__ == "__main__":
    unittest.main()

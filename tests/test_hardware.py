"""Offline tests exercise the exact interface shapes observed on AN515-58."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nitro_control import RGB_WMI_GUID
from nitro_control.demo import demo_snapshot
from nitro_control.fan_control import find_fan_controls, read_fan_plan
from nitro_control.fan_presentation import dashboard_fan_slots
from nitro_control.hardware import HardwareReader, _fan_mode, _gpu_from_csv, _numeric
from nitro_control.monitor_history import MonitorHistory
from nitro_control.tray_model import format_tray_snapshot


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
            return subprocess.CompletedProcess(command, 0, "00000000:01:00.0, NVIDIA GeForce RTX 3050 Ti, 40, 0, 12, 4096, 6.00\n", "")
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
        gpu = _gpu_from_csv("00000000:01:00.0, NVIDIA GPU, N/A, 0, N/A, 4096, [Not Supported]\n")
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

    def test_numeric_hwmon_and_sensor_sorting_above_nine(self):
        make_file(self.root, "sys/class/hwmon/hwmon10/name", "nvme\n")
        make_file(self.root, "sys/class/hwmon/hwmon10/temp1_input", "55000\n")
        make_file(self.root, "sys/class/hwmon/hwmon6/fan10_input", "9000\n")
        make_file(self.root, "sys/class/hwmon/hwmon6/temp10_input", "99000\n")
        snap = HardwareReader(self.root, self.runner).collect()
        self.assertEqual([(d.name, d.celsius) for d in snap.drives],
                         [("NVMe drive 1", 34.9), ("NVMe drive 2", 55.0)])
        self.assertEqual([(f.name, f.channel, f.rpm) for f in snap.fans],
                         [("Fan 1", 1, 1986), ("Fan 2", 2, 2112), ("Fan 10", 10, 9000)])
        self.assertEqual([(t.name, t.celsius) for t in snap.acer_temperatures],
                         [("Acer sensor 1 (unidentified)", 48.0),
                          ("Acer sensor 2 (unidentified)", 40.0),
                          ("Acer sensor 3 (unidentified)", 44.0),
                          ("Acer sensor 10 (unidentified)", 99.0)])

    def test_missing_fan1_and_genuine_labels_preserve_channel_identity(self):
        (self.root / "sys/class/hwmon/hwmon6/fan1_input").unlink()
        make_file(self.root, "sys/class/hwmon/hwmon6/fan2_label", "GPU Blower\n")
        make_file(self.root, "sys/class/hwmon/hwmon6/pwm2_enable", "1\n")
        snap = HardwareReader(self.root, self.runner).collect()
        self.assertEqual(len(snap.fans), 1)
        self.assertEqual(snap.fans[0].name, "GPU Blower")
        self.assertEqual(snap.fans[0].channel, 2)
        self.assertEqual(snap.fans[0].rpm, 2112)
        self.assertEqual(snap.fans[0].control_mode, "Manual")

    def test_negative_and_malformed_fan_values_stay_unavailable_with_channel(self):
        make_file(self.root, "sys/class/hwmon/hwmon6/fan1_input", "-50\n")
        make_file(self.root, "sys/class/hwmon/hwmon6/fan2_input", "not-rpm\n")
        snap = HardwareReader(self.root, self.runner).collect()
        self.assertEqual([(f.channel, f.rpm) for f in snap.fans], [(1, None), (2, None)])

    def test_ambiguous_fallback_acer_devices_are_not_merged(self):
        stray = "sys/class/hwmon/hwmon2"
        make_file(self.root, f"{stray}/name", "acer")
        make_file(self.root, f"{stray}/fan2_input", "990")
        self.assertEqual(HardwareReader(self.root, self.runner).collect().fans, ())
        (self.root / stray / "name").unlink()
        self.assertEqual([fan.rpm for fan in HardwareReader(self.root, self.runner).collect().fans],
                         [1986, 2112])

    def test_other_vendor_and_unrelated_generic_fan_are_excluded(self):
        make_file(self.root, "sys/class/dmi/id/sys_vendor", "Other vendor")
        make_file(self.root, "sys/class/hwmon/hwmon11/name", "nct6775")
        make_file(self.root, "sys/class/hwmon/hwmon11/fan1_input", "3333")
        snapshot = HardwareReader(self.root, self.runner).collect()
        self.assertEqual(snapshot.fans, ())
        self.assertEqual(snapshot.acer_temperatures, ())
        self.assertEqual(snapshot.cpu_celsius, 45)

    def test_intel_history_tracks_package_identity_across_hwmon_renumbering(self):
        class_path = self.root / "sys/class/hwmon"
        device = self.root / "sys/devices/platform/coretemp.0/hwmon"
        device.mkdir(parents=True)
        first = device / "hwmon7"
        (class_path / "hwmon7").rename(first)
        (class_path / "hwmon7").symlink_to(first, target_is_directory=True)
        reader = HardwareReader(self.root, self.runner)
        history = MonitorHistory()

        def record(observed_at):
            snap = reader.collect()
            self.assertEqual(snap.cpu_source, "Intel CPU package")
            history.record("test", snap.cpu_celsius, None, None,
                           observed_at=observed_at, cpu_identity=snap.cpu_identity)
            return snap.cpu_identity

        initial = record(0)
        renamed = device / "hwmon12"
        first.rename(renamed)
        (class_path / "hwmon7").unlink()
        (class_path / "hwmon12").symlink_to(renamed, target_is_directory=True)
        (renamed / "temp1_input").write_text("0", encoding="utf-8")
        self.assertEqual(record(4), initial)

        make_file(self.root, "sys/class/hwmon/hwmon13/name", "coretemp")
        make_file(self.root, "sys/class/hwmon/hwmon13/temp1_input", "75000")
        make_file(self.root, "sys/class/hwmon/hwmon13/temp1_label", "Package id 1")
        (class_path / "hwmon12").unlink()
        self.assertNotEqual(record(8), initial)
        self.assertEqual(history.segments("cpu_celsius"), [[(0, 45), (0.5, 0)], [(1, 75)]])

    def test_amd_cpu_identity_follows_the_backing_pci_device(self):
        (self.root / "sys/class/hwmon/hwmon7/temp1_label").unlink()
        hwmon = "sys/class/hwmon/hwmon10"
        make_file(self.root, f"{hwmon}/name", "k10temp")
        make_file(self.root, f"{hwmon}/temp2_label", "Tdie")
        make_file(self.root, f"{hwmon}/temp2_input", "47000")
        first = self.root / "sys/devices/pci0000:00/0000:00:18.3"
        second = self.root / "sys/devices/pci0000:00/0000:00:19.3"
        first.mkdir(parents=True)
        second.mkdir()
        link = self.root / hwmon / "device"
        link.symlink_to(first, target_is_directory=True)
        reader = HardwareReader(self.root, self.runner)
        initial = reader.collect()
        new_path = link.parent.with_name("hwmon14")
        link.parent.rename(new_path)
        self.assertEqual(reader.collect().cpu_identity, initial.cpu_identity)
        (new_path / "device").unlink()
        (new_path / "device").symlink_to(second, target_is_directory=True)
        replacement = reader.collect()
        self.assertEqual(replacement.cpu_source, initial.cpu_source)
        self.assertNotEqual(replacement.cpu_identity, initial.cpu_identity)

    def test_amd_die_temperature_wins_and_missing_input_remains_unavailable(self):
        (self.root / "sys/class/hwmon/hwmon7/temp1_label").unlink()
        cpu = "sys/class/hwmon/hwmon10"
        make_file(self.root, f"{cpu}/name", "k10temp")
        make_file(self.root, f"{cpu}/temp1_label", "Tctl")
        make_file(self.root, f"{cpu}/temp1_input", "67000")
        make_file(self.root, f"{cpu}/temp2_label", "Tdie")
        make_file(self.root, f"{cpu}/temp2_input", "47000")
        reader = HardwareReader(self.root, self.runner)
        self.assertEqual(reader.collect().cpu_celsius, 47)
        (self.root / cpu / "temp2_input").unlink()
        self.assertIsNone(reader.collect().cpu_celsius)
        (self.root / cpu / "temp2_label").unlink()
        snapshot = reader.collect()
        self.assertEqual(snapshot.cpu_celsius, 67)
        self.assertIn("Tctl", snapshot.cpu_source)

    def test_integer_sysfs_samples_distinguish_zero_and_invalid_or_missing(self):
        acer = "sys/class/hwmon/hwmon6"
        make_file(self.root, f"{acer}/fan1_input", "0")
        make_file(self.root, f"{acer}/fan2_input", "2278.6")
        make_file(self.root, "sys/class/hwmon/hwmon7/temp1_input", "0")
        make_file(self.root, f"{acer}/temp1_input", "4.8e4")
        make_file(self.root, "sys/class/hwmon/hwmon10/name", "nvme")
        make_file(self.root, "sys/class/hwmon/hwmon10/temp1_input", "54000")
        (self.root / "sys/class/hwmon/hwmon3/temp1_input").unlink()
        snap = HardwareReader(self.root, self.runner).collect()
        self.assertEqual([fan.rpm for fan in snap.fans], [0, None])
        self.assertEqual(snap.cpu_celsius, 0)
        self.assertEqual([temp.celsius for temp in snap.acer_temperatures], [40, 44])
        self.assertEqual([temp.name for temp in snap.drives], ["NVMe drive 2"])

    def _make_pci_display(self, bus, *, vendor="0x10de", runtime_status="active"):
        path = f"sys/bus/pci/devices/{bus}"
        make_file(self.root, f"{path}/vendor", vendor)
        make_file(self.root, f"{path}/class", "0x030200")
        make_file(self.root, f"{path}/power/runtime_status", runtime_status)
        return self.root / path

    def test_multiple_gpu_records_are_matched_to_pci_device(self):
        self._make_pci_display("0000:01:00.0")
        self._make_pci_display("0000:00:02.0", vendor="0x8086")
        other = "00000000:0a:00.0, External GPU, 70, 80, 100, 8192, 40\n"
        reported = "00000000:01:00.0, Laptop GPU, 0, 0, 0, 4096, 6\n"
        commands = []

        def capture(command, **kwargs):
            if command[0] == "nvidia-smi":
                commands.append(command)
                return subprocess.CompletedProcess(command, 0, other + reported, "")
            return self.runner(command, **kwargs)

        reader = HardwareReader(self.root, capture, gpu_poll_interval=3)
        gpu = reader.collect().gpu
        self.assertEqual((gpu.name, gpu.pci_bus_id, gpu.celsius, gpu.utilization_percent),
                         ("Laptop GPU", "0000:01:00.0", 0, 0))
        self.assertIn("--id=0000:01:00.0", commands[0])
        self.assertIsNone(_gpu_from_csv(other + reported))
        self.assertIsNone(_gpu_from_csv(reported + reported, "0000:01:00.0"))

        self._make_pci_display("0000:0a:00.0")
        snapshot = reader.collect()
        self.assertIsNone(snapshot.gpu)
        self.assertIn("Multiple", snapshot.gpu_status)
        self.assertEqual(len(commands), 1)

    def test_periodic_gpu_queries_skip_sleep_rate_limit_and_recover_after_disappearance(self):
        device = self._make_pci_display("0000:01:00.0", runtime_status="suspended")
        runtime_status = device / "power/runtime_status"
        time = 0
        gpu_calls = []

        def capture(command, **kwargs):
            if command[0] == "nvidia-smi":
                gpu_calls.append(time)
            return self.runner(command, **kwargs)

        reader = HardwareReader(self.root, capture, gpu_poll_interval=3, clock=lambda: time)
        snapshot = reader.collect()
        self.assertIsNone(snapshot.gpu)
        self.assertIn("suspended", snapshot.gpu_status)
        self.assertEqual(gpu_calls, [])

        runtime_status.write_text("active", encoding="utf-8")
        self.assertEqual(reader.collect().gpu.utilization_percent, 0)
        time = 2
        self.assertIsNone(reader.collect().gpu)
        time = 4
        runtime_status.unlink()
        self.assertIsNone(reader.collect().gpu)
        (device / "vendor").unlink()
        self.assertIsNone(reader.collect().gpu)
        self._make_pci_display("0000:01:00.0")
        self.assertIsNotNone(reader.collect().gpu)
        self.assertEqual(gpu_calls, [0, 4])

    def test_invalid_gpu_values_are_unavailable(self):
        gpu = _gpu_from_csv("00000000:01:00.0, GPU, Inf, 101, -4, 0, -1")
        self.assertEqual((gpu.celsius, gpu.utilization_percent, gpu.memory_used_mib,
                          gpu.memory_total_mib, gpu.power_watts), (None,) * 5)
        gpu = _gpu_from_csv("00000000:01:00.0, GPU, -5, -0.1, 0, 4096, 0")
        self.assertEqual((gpu.celsius, gpu.utilization_percent, gpu.power_watts), (-5, None, 0))

    def _build_stock_acer_wmi_tree(self, root: Path, hwmon_name: str = "hwmon6") -> Path:
        make_file(root, "sys/class/dmi/id/sys_vendor", "Acer\n")
        make_file(root, "sys/class/dmi/id/product_name", "Nitro AN515-58\n")
        (root / "sys/module/acer_wmi").mkdir(parents=True, exist_ok=True)
        driver = root / "sys/bus/platform/drivers/acer-wmi"
        driver.mkdir(parents=True, exist_ok=True)
        os.symlink(root / "sys/module/acer_wmi", driver / "module")
        platform = root / "sys/devices/platform/acer-wmi"
        platform.mkdir(parents=True, exist_ok=True)
        os.symlink(driver, platform / "driver")
        hwmon = platform / "hwmon" / hwmon_name
        hwmon.mkdir(parents=True, exist_ok=True)
        (hwmon / "name").write_text("acer\n", encoding="utf-8")
        for ch in (1, 2):
            for attr in (f"pwm{ch}", f"pwm{ch}_enable"):
                path = hwmon / attr
                path.write_text("2\n" if attr.endswith("_enable") else "0\n", encoding="utf-8")
                path.chmod(0o644)
        (hwmon / "fan1_input").write_text("2200\n", encoding="utf-8")
        (hwmon / "fan2_input").write_text("2300\n", encoding="utf-8")
        (root / "sys/class/hwmon").mkdir(parents=True, exist_ok=True)
        os.symlink(f"../../devices/platform/acer-wmi/hwmon/{hwmon_name}", root / "sys/class/hwmon" / hwmon_name)
        return hwmon

    def test_stock_acer_controller_rejects_unexpected_pwm_mode_gt_2_without_breaking_generic_hwmon(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hwmon = self._build_stock_acer_wmi_tree(root)
            (hwmon / "pwm1_enable").write_text("3\n", encoding="utf-8")
            (hwmon / "pwm2_enable").write_text("2\n", encoding="utf-8")

            control_hwmon = find_fan_controls(root)
            self.assertEqual(control_hwmon, hwmon)
            self.assertIsNone(read_fan_plan(control_hwmon))

            snap = HardwareReader(root, self.runner).collect()
            self.assertEqual([(f.channel, f.control_mode) for f in snap.fans],
                             [(1, "Unknown"), (2, "Automatic")])

        # Generic non-stock hwmon semantics preserve >= 2 as Automatic.
        make_file(self.root, "sys/class/hwmon/hwmon6/pwm1_enable", "3\n")
        generic_snap = HardwareReader(self.root, self.runner).collect()
        self.assertEqual(generic_snap.fans[0].control_mode, "Automatic")

    def test_multiple_acer_hwmon_devices_prefer_controlled_and_platform_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hwmon = self._build_stock_acer_wmi_tree(root, hwmon_name="hwmon6")
            (hwmon / "fan1_input").write_text("4200\n", encoding="utf-8")
            (hwmon / "pwm1_enable").write_text("1\n", encoding="utf-8")
            # Remove fan2_input on the controlled device so a stray second hwmon
            # device cannot backfill slot 2 with foreign fan telemetry.
            (hwmon / "fan2_input").unlink()

            stray = "sys/class/hwmon/hwmon2"
            make_file(root, f"{stray}/name", "acer\n")
            make_file(root, f"{stray}/fan1_input", "900\n")
            make_file(root, f"{stray}/fan2_input", "950\n")
            make_file(root, f"{stray}/pwm1_enable", "2\n")
            make_file(root, f"{stray}/pwm2_enable", "2\n")

            self.assertEqual(find_fan_controls(root), hwmon)
            snap = HardwareReader(root, self.runner).collect()
            self.assertEqual([(f.channel, f.rpm, f.control_mode) for f in snap.fans],
                             [(1, 4200, "Manual")])
            slot1, slot2 = dashboard_fan_slots(snap.fans)
            self.assertEqual(slot1.rpm, 4200)
            self.assertEqual(slot1.control_mode, "Manual")
            self.assertIsNone(slot2)
            tray = format_tray_snapshot(snap)
            self.assertEqual(tray.cpu_fan, "CPU fan: 4,200 RPM")
            self.assertEqual(tray.gpu_fan, "Fan 2: Unavailable")

            # Even if stock control eligibility is lost (e.g. linuwu_sense present),
            # platform acer-wmi hwmon still takes precedence over stray hwmon2.
            (root / "sys/module/linuwu_sense").mkdir()
            self.assertIsNone(find_fan_controls(root))
            snap_platform = HardwareReader(root, self.runner).collect()
            self.assertEqual([(f.channel, f.rpm) for f in snap_platform.fans], [(1, 4200)])


class NumericTests(unittest.TestCase):
    def test_none_and_bad_values(self):
        self.assertIsNone(_numeric(None))
        self.assertIsNone(_numeric("N/A"))
        self.assertIsNone(_numeric("something else"))
        self.assertEqual(_numeric("5.5"), 5.5)


if __name__ == "__main__":
    unittest.main()

"""Offline contract tests for the opt-in, read-only Acer fan inventory."""

from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
import errno
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nitro_control.fan_probe import collect_fan_probe
from nitro_control.__main__ import main


def fixture(root: Path, path: str, contents: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contents, encoding="utf-8")


class FanProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        fixture(self.root, "sys/class/dmi/id/sys_vendor", "Acer\n")
        fixture(self.root, "sys/class/dmi/id/product_name", "Nitro AN515-58\n")
        fixture(self.root, "sys/class/dmi/id/bios_version", "V1.20\n")
        fixture(self.root, "proc/sys/kernel/osrelease", "7.2.15-test\n")
        fixture(self.root, "sys/firmware/acpi/platform_profile", "balanced\n")
        (self.root / "sys/module/acer_wmi").mkdir(parents=True)

    def test_stock_acer_readable_pwm_files_are_inventory_only(self):
        d = "sys/class/hwmon/hwmon7"
        fixture(self.root, d + "/name", "acer\n")
        fixture(self.root, d + "/fan1_input", "2307\n")
        fixture(self.root, d + "/pwm1", "128\n")
        fixture(self.root, d + "/pwm1_enable", "2\n")
        fixture(self.root, d + "/fan2_input", "2205\n")
        fixture(self.root, d + "/pwm2", "230\n")
        fixture(self.root, d + "/pwm2_enable", "1\n")

        with patch.object(Path, "write_text", side_effect=AssertionError("write attempted")):
            with patch("subprocess.run", side_effect=AssertionError("subprocess attempted")):
                result = collect_fan_probe(self.root)

        self.assertTrue(result["read_only"])
        self.assertEqual(result["model"], "Nitro AN515-58")
        self.assertEqual(result["bios_version"], "V1.20")
        self.assertEqual(result["kernel_release"], "7.2.15-test")
        self.assertEqual(result["firmware_platform_profile"], "balanced")
        self.assertTrue(result["stock_acer_wmi_loaded"])
        self.assertFalse(result["linuwu_sense_loaded"])
        a, b = result["acer_hwmon_devices"][0]["channels"]
        self.assertEqual((a["fan_rpm"], a["pwm_255"], a["pwm_enable"]), (2307, 128, 2))
        self.assertEqual(a["upstream_acer_wmi_mode"], "automatic")
        self.assertIsNone(a["pwm_enable_read_errno"])
        self.assertEqual((b["pwm_readback_status"], b["upstream_acer_wmi_mode"]),
                         ("readable", "custom"))

    def test_no_acer_hwmon_does_not_invent_support(self):
        fixture(self.root, "sys/class/hwmon/hwmon4/name", "coretemp\n")
        result = collect_fan_probe(self.root)
        self.assertEqual(result["acer_hwmon_devices"], [])
        self.assertTrue(result["read_only"])

    def test_rpm_without_pwm_reports_not_exposed(self):
        d = "sys/class/hwmon/hwmon2"
        fixture(self.root, d + "/name", "acer\n")
        fixture(self.root, d + "/fan1_input", "3000\n")
        channel = collect_fan_probe(self.root)["acer_hwmon_devices"][0]["channels"][0]
        self.assertEqual(channel["fan_rpm"], 3000)
        self.assertEqual(channel["pwm_readback_status"], "not_exposed")
        self.assertFalse(channel["pwm_present"])
        self.assertIsNone(channel["upstream_acer_wmi_mode"])

    def test_malformed_and_partial_files_fail_closed(self):
        d = "sys/class/hwmon/hwmon3"
        fixture(self.root, d + "/name", "acer\n")
        fixture(self.root, d + "/fan1_input", "-10\n")
        fixture(self.root, d + "/pwm1", "9999\n")
        fixture(self.root, d + "/pwm1_enable", "n/a\n")
        channel = collect_fan_probe(self.root)["acer_hwmon_devices"][0]["channels"][0]
        self.assertIsNone(channel["fan_rpm"])
        self.assertIsNone(channel["pwm_255"])
        self.assertIsNone(channel["pwm_enable"])
        self.assertEqual(channel["pwm_readback_status"], "partial_or_unreadable")

    def test_overlapping_driver_invalidates_mode_interpretation(self):
        (self.root / "sys/module/linuwu_sense").mkdir(parents=True)
        d = "sys/class/hwmon/hwmon9"
        fixture(self.root, d + "/name", "acer\n")
        fixture(self.root, d + "/pwm1", "150\n")
        fixture(self.root, d + "/pwm1_enable", "0\n")
        result = collect_fan_probe(self.root)
        self.assertTrue(result["linuwu_sense_loaded"])
        self.assertIsNone(result["acer_hwmon_devices"][0]["channels"][0]["upstream_acer_wmi_mode"])

    def test_enxio_from_stock_fan_mode_is_reported_not_ignored(self):
        d = "sys/class/hwmon/hwmon6"
        fixture(self.root, d + "/name", "acer\n")
        fixture(self.root, d + "/fan1_input", "2238\n")
        fixture(self.root, d + "/pwm1", "0\n")
        fixture(self.root, d + "/pwm1_enable", "2\n")
        original_open = Path.open

        def reject_mode(path, *args, **kwargs):
            if path.name == "pwm1_enable":
                raise OSError(errno.ENXIO, "No such device or address")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", reject_mode):
            result = collect_fan_probe(self.root)

        channel = result["acer_hwmon_devices"][0]["channels"][0]
        self.assertEqual(channel["fan_rpm"], 2238)
        self.assertEqual(channel["pwm_255"], 0)
        self.assertTrue(channel["pwm_enable_present"])
        self.assertIsNone(channel["pwm_enable"])
        self.assertEqual(channel["pwm_enable_read_errno"], errno.ENXIO)
        self.assertIsNone(channel["upstream_acer_wmi_mode"])
        self.assertEqual(channel["pwm_readback_status"], "partial_or_unreadable")

    def test_unknown_mode_and_excess_channels_are_not_assumed(self):
        d = "sys/class/hwmon/hwmon1"
        fixture(self.root, d + "/name", "acer\n")
        fixture(self.root, d + "/pwm1_enable", "9\n")
        fixture(self.root, d + "/pwm40_enable", "1\n")
        channel = collect_fan_probe(self.root)["acer_hwmon_devices"][0]["channels"]
        self.assertEqual(len(channel), 1)
        self.assertIsNone(channel[0]["pwm_enable"])
        self.assertEqual(channel[0]["pwm_readback_status"], "partial_or_unreadable")

    def test_cli_branch_is_json_and_does_not_import_gui(self):
        with patch("nitro_control.fan_probe.collect_fan_probe", return_value={"read_only": True}):
            with patch("builtins.__import__", wraps=__import__) as importer:
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["--fan-probe"]), 0)
        self.assertEqual(json.loads(output.getvalue()), {"read_only": True})
        imports = [call.args[0] for call in importer.call_args_list if call.args]
        self.assertNotIn("nitro_control.ui", imports)
        self.assertNotIn("nitro_control.tray_indicator", imports)

    def test_cli_rejects_mixed_hardware_or_demo_modes(self):
        for combo in (["--fan-probe", "--rgb-probe"], ["--fan-probe", "--once"],
                      ["--fan-probe", "--demo"], ["--fan-probe", "--tray"]):
            with self.subTest(combo=combo):
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        main(combo)
                self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()

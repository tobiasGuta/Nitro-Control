"""Synthetic sysfs RGB tests: never open the host's real RGB interfaces."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from nitro_control.rgb import LightingPlan
from nitro_control.rgb_hardware import RGBHardware, RGBHardwareError, LED_NAMES, LINUWU_PATHS
from nitro_control.rgb_client import RGBClient
from nitro_control import RGB_WMI_GUID


COLORS = LightingPlan(("#FF0000", "#00FF00", "#0000FF", "#ABCDEF"), 50)


class RGBHardwareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.hardware = RGBHardware(self.root)
        self._file("sys/class/dmi/id/product_name", "Nitro AN515-58\n")
        (self.root / f"sys/bus/wmi/devices/{RGB_WMI_GUID}-8").mkdir(parents=True)

    def _file(self, relative, contents):
        p = self.root / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(contents, encoding="ascii")
        return p

    def _native(self, index=("red", "green", "blue")):
        for i, name in enumerate(LED_NAMES):
            base = "sys/class/leds/" + name + "/"
            self._file(base + "multi_index", " ".join(index) + "\n")
            self._file(base + "multi_intensity", "11 22 33\n")
            self._file(base + "multi_max_intensity", "255 255 255\n")
            self._file(base + "max_brightness", "255\n")
            self._file(base + "brightness", "100\n")

    def test_guid_alone_not_backend(self):
        cap = self.hardware.probe()
        self.assertFalse(cap.available)
        self.assertEqual(cap.backend, "none")
        self.assertIn("no supported RGB", cap.detail)
        with self.assertRaises(RGBHardwareError):
            self.hardware.apply(COLORS)

    def test_wrong_model_never_writes_even_if_endpoint_present(self):
        p = self._file(LINUWU_PATHS[0], "000000,000000,000000,000000,50\n")
        self._file("sys/class/dmi/id/product_name", "Not Nitro\n")
        with self.assertRaises(RGBHardwareError):
            self.hardware.apply(COLORS)
        self.assertIn("000000,", p.read_text())

    def test_native_four_zone_color_and_brightness(self):
        self._native()
        cap = self.hardware.probe()
        self.assertTrue(cap.available)
        self.assertEqual(cap.backend, "native-led")
        result = self.hardware.apply(COLORS)
        self.assertTrue(result.verified_readback)
        self.assertIn("visually", result.detail)
        expected = (("255 0 0", "128"), ("0 255 0", "128"), ("0 0 255", "128"), ("171 205 239", "128"))
        for name, (rgb, bright) in zip(LED_NAMES, expected):
            base = self.root / "sys/class/leds" / name
            self.assertEqual((base / "multi_intensity").read_text().strip(), rgb)
            self.assertEqual((base / "brightness").read_text().strip(), bright)

    def test_native_channel_order_from_sysfs(self):
        self._native(index=("blue", "red", "green"))
        self.hardware.apply(COLORS)
        self.assertEqual((self.root / "sys/class/leds" / LED_NAMES[0] / "multi_intensity").read_text().strip(), "0 255 0")

    def test_incomplete_or_ambiguous_native_rejected(self):
        self._native(index=("unknown", "red", "green"))
        self.assertFalse(self.hardware.probe().available)
        with self.assertRaises(RGBHardwareError):
            self.hardware.apply(COLORS)

    def test_native_rolls_back_on_second_zone_failure(self):
        self._native()
        from nitro_control import rgb_hardware
        actual_write = rgb_hardware._write
        state = {"raised": False}
        def fail_once(path, value):
            if LED_NAMES[1] in str(path) and not state["raised"]:
                state["raised"] = True
                raise RGBHardwareError("simulated failed zone")
            return actual_write(path, value)
        with patch("nitro_control.rgb_hardware._write", side_effect=fail_once):
            with self.assertRaisesRegex(RGBHardwareError, "previous sysfs values restored"):
                self.hardware.apply(COLORS)
        for name in LED_NAMES:
            base = self.root / "sys/class/leds" / name
            self.assertEqual((base / "multi_intensity").read_text().strip(), "11 22 33")
            self.assertEqual((base / "brightness").read_text().strip(), "100")

    def test_linuwu_four_zones_single_write(self):
        p = self._file(LINUWU_PATHS[0], "000000,000000,000000,000000,50\n")
        self.assertEqual(self.hardware.probe().backend, "linuwu-sense")
        result = self.hardware.apply(COLORS)
        self.assertTrue(result.verified_readback)
        self.assertEqual(p.read_text().strip(), "ff0000,00ff00,0000ff,abcdef,50")

    def test_missing_wmi_guid_disallows_writes(self):
        p = self._file(LINUWU_PATHS[0], "000000,000000,000000,000000,50\n")
        (self.root / f"sys/bus/wmi/devices/{RGB_WMI_GUID}-8").rmdir()
        self.assertFalse(self.hardware.probe().available)
        with self.assertRaises(RGBHardwareError):
            self.hardware.apply(COLORS)
        self.assertEqual(p.read_text().strip(), "000000,000000,000000,000000,50")

    def test_invalid_plan_is_never_written(self):
        p = self._file(LINUWU_PATHS[0], "000000,000000,000000,000000,50\n")
        with self.assertRaises(RGBHardwareError):
            self.hardware.apply(("#FF0000",) * 4)
        self.assertEqual(p.read_text().strip(), "000000,000000,000000,000000,50")

    def test_native_writes_do_not_touch_fans(self):
        self._native()
        fan = self._file("sys/class/hwmon/hwmon6/pwm1", "127\n")
        profile = self._file("sys/firmware/acpi/platform_profile", "balanced\n")
        self.hardware.apply(COLORS)
        self.assertEqual(fan.read_text(), "127\n")
        self.assertEqual(profile.read_text(), "balanced\n")


class RGBClientTests(unittest.TestCase):
    def test_client_does_not_call_helper_when_absent(self):
        calls = []
        client = RGBClient(Path("/definitely/missing/agent"), runner=lambda *a, **k: calls.append((a, k)))
        with self.assertRaisesRegex(RGBHardwareError, "not installed"):
            client.apply(COLORS)
        self.assertEqual(calls, [])

    def test_client_uses_exact_helper_path_and_structured_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = Path(tmp) / "helper"
            helper.write_text("stub")
            def runner(argv, **kwargs):
                self.assertEqual(argv, ["pkexec", str(helper)])
                self.assertEqual(json.loads(kwargs["input"])["zones"], list(COLORS.zones))
                self.assertFalse(kwargs["check"])
                return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "verified_readback": True,
                                                                          "backend": "native-led", "detail": "Sysfs confirmed"}))
            with patch("nitro_control.rgb_client.shutil.which", return_value="/usr/bin/pkexec"):
                self.assertEqual(RGBClient(helper, runner).apply(COLORS), "Sysfs confirmed")

    def test_helper_refusal_is_not_mistaken_for_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = Path(tmp) / "helper"
            helper.write_text("stub")
            runner = lambda *a, **k: SimpleNamespace(returncode=1, stdout='{"ok":false,"error":"denied"}')
            with patch("nitro_control.rgb_client.shutil.which", return_value="/usr/bin/pkexec"):
                with self.assertRaisesRegex(RGBHardwareError, "denied"):
                    RGBClient(helper, runner).apply(COLORS)


if __name__ == "__main__":
    unittest.main()

"""Preview-only lighting tests; no access to a real system is required."""

import unittest
from unittest.mock import patch
from pathlib import Path

from nitro_control.rgb import LightingPlan, PRESETS, PreviewBackend, RGBValidationError


class RGBPreviewTests(unittest.TestCase):
    def test_all_presets_have_four_rgb_colors(self):
        self.assertGreaterEqual(len(PRESETS), 3)
        for preset in PRESETS.values():
            self.assertEqual(len(preset.zones), 4)
            self.assertTrue(all(color.startswith("#") and len(color) == 7 for color in preset.zones))

    def test_hex_normalization(self):
        plan = LightingPlan(("#123abc", "#000000", "#ffffff", "#aBcDeF"), 55)
        self.assertEqual(plan.zones, ("#123ABC", "#000000", "#FFFFFF", "#ABCDEF"))

    def test_invalid_zone_count(self):
        for zones in ((), ("#000000",), ("#000000",) * 5, ["#000000"] * 4):
            with self.subTest(zones=zones), self.assertRaises(RGBValidationError):
                LightingPlan(zones, 60)

    def test_invalid_color(self):
        for value in ("#fff", "#12GG00", "red", "#000000\n", "#000000;reboot", None, 42):
            with self.subTest(value=value), self.assertRaises(RGBValidationError):
                LightingPlan(("#123456", "#123456", "#123456", value), 60)

    def test_brightness_boundaries(self):
        for level in (0, 100):
            self.assertEqual(LightingPlan(("#123456",) * 4, level).brightness, level)
        for level in (-1, 101, 4.2, "42", True, None):
            with self.subTest(level=level), self.assertRaises(RGBValidationError):
                LightingPlan(("#123456",) * 4, level)

    def test_preview_in_memory_only(self):
        backend = PreviewBackend()
        original = backend.current()
        new = LightingPlan(("#010203",) * 4, 17)
        with patch.object(Path, "write_text", side_effect=AssertionError("Unexpected file write")):
            self.assertEqual(backend.apply(new), new)
            self.assertEqual(backend.current(), new)
            self.assertEqual(backend.reset(), original)

    def test_preview_rejects_unvalidated_values(self):
        backend = PreviewBackend()
        with self.assertRaises(RGBValidationError):
            backend.apply(("#000000",) * 4)
        self.assertEqual(backend.current(), PRESETS["Midnight"])

    def test_preview_instances_are_isolated(self):
        first, second = PreviewBackend(), PreviewBackend()
        first.apply(PRESETS["Ocean"])
        self.assertEqual(second.current(), PRESETS["Midnight"])


if __name__ == "__main__":
    unittest.main()

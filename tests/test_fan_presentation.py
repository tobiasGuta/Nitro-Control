"""AN515-58 fan names and unavailable control-mode presentation only."""
from __future__ import annotations

import unittest

from nitro_control.fan_presentation import fan_display_name, fan_mode_description


class FanPresentationTests(unittest.TestCase):
    def test_an515_58_uses_upstream_acer_wmi_channel_map(self):
        # acer-wmi.c: [0] CPU fan, [1] GPU fan.
        for model in ("Nitro AN515-58", "AN515-58", "Acer Nitro AN515-58",
                      "Acer Nitro AN515-58 (demo)"):
            with self.subTest(model=model):
                self.assertEqual(fan_display_name(model, "Fan 1"), "CPU Fan")
                self.assertEqual(fan_display_name(model, "Fan 2"), "GPU Fan")

    def test_unknown_model_does_not_claim_cpu_gpu_association(self):
        for model in ("Unknown device", "Nitro AN515-57",
                      "Predator PH315-53", "Nitro AN515-58 Special Edition"):
            with self.subTest(model=model):
                self.assertEqual(fan_display_name(model, "Fan 1"), "Fan 1")
                self.assertEqual(fan_display_name(model, "Fan 2"), "Fan 2")

    def test_kernel_supplied_labels_are_never_overwritten(self):
        self.assertEqual(fan_display_name("Nitro AN515-58", "CPU blower 1"),
                         "CPU blower 1")
        self.assertEqual(fan_display_name("Nitro AN515-58", "GPU fan"),
                         "GPU fan")
        self.assertEqual(fan_display_name("Nitro AN515-58", "Fan 3"),
                         "Fan 3")

    def test_absent_pwm_mode_is_not_assumed_auto(self):
        self.assertEqual(fan_mode_description("Unknown"), "Mode not reported")
        self.assertEqual(fan_mode_description(""), "Mode not reported")
        self.assertEqual(fan_mode_description("Automatic"), "Automatic")
        self.assertEqual(fan_mode_description("Manual"), "Manual")
        self.assertEqual(fan_mode_description("Full speed / control disabled"),
                         "Full speed / control disabled")


if __name__ == "__main__":
    unittest.main()

"""Offline fan animation tests; no GTK or hardware access."""
from __future__ import annotations

import math
import unittest

from nitro_control.fan_animation import fan_angle_step


class FanAnimationTests(unittest.TestCase):
    def test_unavailable_stopped_and_invalid_rpm_do_not_rotate(self):
        for rpm in (None, 0, -100, True, 1234.5, "2500"):
            with self.subTest(rpm=rpm):
                self.assertEqual(fan_angle_step(rpm, 0.033), 0.0)

    def test_real_rpm_monotonically_sets_independent_visual_rates(self):
        slow = fan_angle_step(2000, 0.04)
        fast = fan_angle_step(4000, 0.04)
        self.assertGreater(slow, 0)
        self.assertAlmostEqual(fast, 2 * slow)
        self.assertLess(fast, math.tau)

    def test_visual_rotation_is_slower_than_physical_rotation(self):
        step = fan_angle_step(2250, 0.04)
        physical = 2250 / 60 * 0.04 * math.tau
        self.assertLess(step, physical)

    def test_pauses_and_sleep_gaps_are_safe(self):
        regular = fan_angle_step(5500, 0.1)
        self.assertAlmostEqual(fan_angle_step(5500, 1200), regular)
        for invalid in (-1, 0.0, float("nan"), float("inf")):
            with self.subTest(invalid=invalid):
                self.assertEqual(fan_angle_step(5500, invalid), 0.0)

    def test_extreme_rpm_has_visual_cap_but_does_not_change_reported_rpm(self):
        self.assertAlmostEqual(fan_angle_step(7500, 0.05), fan_angle_step(12000, 0.05))

    def test_two_rotors_do_not_depend_on_each_other(self):
        a = fan_angle_step(1800, 0.033)
        b = fan_angle_step(5000, 0.033)
        self.assertGreater(b, a)
        self.assertEqual(fan_angle_step(None, 0.033), 0.0)


if __name__ == "__main__":
    unittest.main()

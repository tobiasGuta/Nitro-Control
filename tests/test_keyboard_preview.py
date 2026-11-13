"""Display-only keyboard geometry and effect regression tests."""

import unittest

from nitro_control.keyboard_preview import (
    DECK_HEIGHT, DECK_WIDTH, EFFECTS, KEYS, SPATIAL_EFFECTS,
    lighting_frame, lighting_gradient, zone_at,
)


ZONES = ("#FF0000", "#00FF00", "#000000", "#0000FF")


class KeyboardPreviewTests(unittest.TestCase):
    def test_static_frame_respects_zone_colors_brightness_and_off(self):
        self.assertEqual(
            lighting_frame(ZONES, 50),
            ((0.5, 0.0, 0.0), (0.0, 0.5, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.5)),
        )

    def test_effects_animate_and_can_fade_to_off(self):
        for effect in EFFECTS:
            with self.subTest(effect=effect):
                if effect in SPATIAL_EFFECTS:
                    def frame(brightness, elapsed=0):
                        return lighting_gradient(effect, brightness, color="#FF475D",
                                                 speed=4, elapsed=elapsed)
                else:
                    def frame(brightness, elapsed=0):
                        return lighting_frame(ZONES, brightness, effect=effect, elapsed=elapsed)
                first = frame(100)
                second = frame(100, 0.75)
                self.assertNotEqual(first, second)
                self.assertTrue(all(0 <= value <= 1 for rgb in second for value in rgb))
                self.assertEqual(frame(0), ((0, 0, 0),) * len(first))

    def test_wave_moves_continuously_left_to_right(self):
        slow = lighting_gradient("Wave", 100, color="#FFFFFF", speed=4, elapsed=0)
        advanced = lighting_gradient("Wave", 100, color="#FFFFFF", speed=4, elapsed=0.5)
        faster = lighting_gradient("Wave", 100, color="#FFFFFF", speed=9, elapsed=0.5)
        self.assertGreater(len(set(slow)), 30)
        for left, right in zip(slow, slow[1:]):
            self.assertLess(max(abs(a - b) for a, b in zip(left, right)), 0.2)
        for a, b in zip(slow[0], advanced[9]):
            self.assertAlmostEqual(a, b)
        self.assertNotEqual(advanced, faster)

    def test_shifting_moves_the_selected_color_without_changing_hue(self):
        frames = [lighting_gradient("Shifting", 80, color="#8030C0", speed=4, elapsed=t)
                  for t in (0, 0.5)]
        for frame in frames:
            for red, green, blue in frame:
                self.assertAlmostEqual(red / blue, 128 / 192)
                self.assertAlmostEqual(green / blue, 48 / 192)
        peaks = [max(range(len(frame)), key=lambda index: frame[index][2]) for frame in frames]
        self.assertEqual(peaks, [0, 9])

    def test_zoom_expands_symmetrically_from_the_center_and_returns(self):
        contracted = lighting_gradient("Zoom", 100, color="#FF0000", speed=4, elapsed=0)
        expanded = lighting_gradient("Zoom", 100, color="#FF0000", speed=4, elapsed=1)
        returned = lighting_gradient("Zoom", 100, color="#FF0000", speed=4, elapsed=2)
        self.assertGreater(contracted[18][0], contracted[0][0])
        self.assertGreater(expanded[0][0], 0.8)
        self.assertGreater(expanded[9][0], contracted[9][0])
        self.assertEqual(returned, contracted)
        for a, b in zip(expanded, reversed(expanded)):
            self.assertAlmostEqual(a[0], b[0])

    def test_key_geometry_and_clickable_zones_stay_inside_the_deck(self):
        for x, y, width, height, _label in KEYS:
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + width, DECK_WIDTH)
            self.assertLessEqual(y + height, DECK_HEIGHT)
        for zone in range(4):
            self.assertEqual(zone_at((zone + 0.5) * DECK_WIDTH / 4, DECK_HEIGHT / 2), zone)
        self.assertIsNone(zone_at(-0.1, 0))
        self.assertIsNone(zone_at(DECK_WIDTH, 1))
        self.assertIsNone(zone_at(1, DECK_HEIGHT))


if __name__ == "__main__":
    unittest.main()

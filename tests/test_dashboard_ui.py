"""Offline dashboard tests. These never import GTK or touch real hardware."""
from __future__ import annotations

import math
from pathlib import Path
import unittest

from nitro_control.monitor_history import MonitorHistory


ROOT = Path(__file__).resolve().parents[1]


class MonitorHistoryTests(unittest.TestCase):
    def test_only_observed_values_appear_in_series(self):
        history = MonitorHistory(limit=3)
        history.record("2026-11-10T21:00:00", 51, None, None)
        history.record("2026-11-10T21:00:04", 54.5, 43, 17)
        self.assertEqual(history.series("cpu_celsius"), (51, 54.5))
        self.assertEqual(history.series("gpu_celsius"), (None, 43))
        self.assertEqual(history.series("gpu_utilization_percent"), (None, 17))

    def test_missing_measurements_never_become_zero(self):
        history = MonitorHistory()
        history.record("t0", None, None, None)
        history.record("t1", float("nan"), float("inf"), -math.inf)
        self.assertEqual(history.series("cpu_celsius"), (None, None))
        self.assertEqual(history.series("gpu_celsius"), (None, None))
        self.assertEqual(history.series("gpu_utilization_percent"), (None, None))

    def test_fixed_history_capacity_and_order(self):
        history = MonitorHistory(limit=3)
        for index in range(5):
            history.record(f"t{index}", float(index), None, None)
        self.assertEqual(tuple(s.timestamp for s in history.samples),
                         ("t2", "t3", "t4"))
        self.assertEqual(history.series("cpu_celsius"), (2, 3, 4))

    def test_invalid_limit_and_unrecognized_field_rejected(self):
        with self.assertRaises(ValueError):
            MonitorHistory(limit=1)
        with self.assertRaises(ValueError):
            MonitorHistory().series("__dict__")


class DashboardSafetyContracts(unittest.TestCase):
    def test_graphs_only_use_real_snapshot_data(self):
        source = (ROOT / "nitro_control" / "ui.py").read_text()
        self.assertIn("self._history.record(", source)
        self.assertIn("data.cpu_celsius", source)
        self.assertIn("data.gpu.utilization_percent if data.gpu else None", source)
        self.assertIn('self.fan_dials[index].update(display_name, fan.rpm, fan.control_mode)', source)
        self.assertIn('display_name = fan_display_name(data.model, fan.name)', source)

    def test_rgb_physical_permissions_and_100_guard_preserved(self):
        source = (ROOT / "nitro_control" / "ui.py").read_text()
        self.assertIn("self._rgb_client.apply(plan)", source)
        self.assertIn('self._rgb_status.backend == "native-wmi" and plan.brightness != 100', source)
        self.assertIn("self._confirm_rgb_hardware", source)
        self.assertIn("self._confirm_power_change", source)

    def test_power_column_does_not_force_wide_monitoring_pane(self):
        source = (ROOT / "nitro_control" / "ui.py").read_text()
        self.assertIn("self.power_panel.set_size_request(268, -1)", source)
        self.assertIn('self.power_combo = Gtk.DropDown.new_from_strings(["Unavailable"])', source)
        self.assertIn("monitors.set_hexpand(True)", source)
        self.assertIn("monitors.set_size_request(470, -1)", source)
        self.assertNotIn("power_group = Adw.PreferencesGroup(", source)

    def test_maximized_dashboard_fills_window_without_horizontal_clamp(self):
        source = (ROOT / "nitro_control" / "ui.py").read_text()
        page = source.split("        def page(key: str, title: str) -> Gtk.Box:", 1)[1]
        page = page.split('        content = page("dashboard", "Dashboard")', 1)[0]
        self.assertIn('if key == "dashboard":', page)
        self.assertIn("scroll.set_child(inner)", page)
        self.assertIn("inner.set_hexpand(True)", page)
        self.assertIn("inner.set_halign(Gtk.Align.FILL)", page)
        self.assertIn("Adw.Clamp(maximum_size=1100", page)
        self.assertIn("clamp.set_child(inner)", page)
        # Only the narrower settings pages use the clamp.
        self.assertLess(page.index("if key == \"dashboard\":"), page.index("Adw.Clamp("))
        self.assertIn("monitoring_section.set_hexpand(True)", source)
        self.assertIn("fans.set_hexpand(True)", source)
        self.assertIn("self.power_panel.set_size_request(268, -1)", source)

    def test_dashboard_uses_spare_window_height_without_fake_data(self):
        source = (ROOT / "nitro_control" / "ui.py").read_text()
        visuals = (ROOT / "nitro_control" / "dashboard_visuals.py").read_text()
        dashboard = source.split('if key == "dashboard":', 1)[1].split("            else:", 1)[0]
        self.assertIn("inner.set_vexpand(True)", dashboard)
        self.assertIn("inner.set_valign(Gtk.Align.FILL)", dashboard)
        self.assertIn("lower.set_valign(Gtk.Align.FILL)", source)
        self.assertIn("lower.set_vexpand(True)", source)
        self.assertIn('section("Power mode", self.power_panel, fill_height=True)', source)
        self.assertIn('section("Live monitoring", monitors, fill_height=True)', source)
        self.assertIn("self.cpu_graph.set_vexpand(True)", source)
        self.assertIn("self.gpu_graph.set_vexpand(True)", source)
        self.assertIn("if fill_height:", visuals)
        self.assertIn("body.set_vexpand(True)", visuals)
        # Decorative fan section remains fixed-height instead of stretching
        # the rotor drawing to occupy the entire screen.
        self.assertIn('section("Fan monitoring", fans, min_height=285)', source)
        # No synthetic sensor series may be introduced to fill graph space.
        self.assertIn("self._history.record(", source)

    def test_hardware_and_rgb_pages_use_the_same_panel_shell(self):
        source = (ROOT / "nitro_control" / "ui.py").read_text()
        self.assertIn("def _panel_group(", source)
        self.assertIn("group.set_title(\"\")", source)
        self.assertIn("group.set_description(\"\")", source)
        self.assertIn("return section(title, body)", source)
        for heading in ("Graphics", "Performance", "Cooling",
                        "Temperature sensors", "RGB hardware status",
                        "Four-zone RGB studio"):
            self.assertIn(f'\"{heading}\"', source)
        self.assertIn("hardware_content.append(_panel_group(", source)
        self.assertIn("lighting_content.append(_panel_group(", source)

    def test_color_strip_is_draft_only_and_rgb_guard_remains_in_place(self):
        source = (ROOT / "nitro_control" / "ui.py").read_text()
        visual = (ROOT / "nitro_control" / "dashboard_visuals.py").read_text()
        self.assertIn("class RGBZoneStrip(Gtk.DrawingArea):", visual)
        self.assertIn("DRAFT PALETTE · NOT APPLIED TO KEYBOARD", visual)
        self.assertIn("self.rgb_strip = RGBZoneStrip()", source)
        self.assertIn("self.rgb_strip.update_zones(plan.zones)", source)
        self.assertIn("self.rgb_native_hint.set_visible(native and not supported_brightness)", source)
        self.assertIn("supported_brightness = not native or self._rgb_draft().brightness == 100", source)
        self.assertIn("self._rgb_client.apply(plan)", source)
        self.assertIn("self._confirm_rgb_hardware", source)
        # The custom paint code never invokes the privileged write path.
        rgb_section = visual.split("class RGBZoneStrip", 1)[1].split("class TelemetryGraph", 1)[0]
        for unsafe in ("subprocess", "pkexec", "insmod", "modprobe", "pwm", "self._rgb_client"):
            self.assertNotIn(unsafe, rgb_section)

    def test_real_power_control_remains_select_apply_confirm(self):
        source = (ROOT / "nitro_control" / "ui.py").read_text()
        self.assertIn("self.power_combo.connect(\"notify::selected\", self._on_power_selection)", source)
        self.assertIn("self.power_apply.connect(\"clicked\", self._request_power_change)", source)
        self.assertIn("self.power_status.set_text(", source)
        self.assertIn("state.choices", source)
        self.assertIn("self._power.apply(chosen)", source)

    def test_graphs_and_turbine_are_decorative_only(self):
        source = (ROOT / "nitro_control" / "dashboard_visuals.py").read_text()
        self.assertIn("cairo.LinearGradient", source)
        self.assertIn("blades = 28", source)
        self.assertIn("max(11, int(max(right - left, 1) / 22))", source)
        for unsafe in ("subprocess.run", "modprobe ", "pwm1", "insmod ", "pkexec"):
            self.assertNotIn(unsafe, source)

    def test_fan_animation_does_not_poll_or_write_hardware(self):
        source = (ROOT / "nitro_control" / "ui.py").read_text()
        artwork = (ROOT / "nitro_control" / "dashboard_visuals.py").read_text()
        self.assertIn("GLib.timeout_add(33, self._on_fan_animation_tick)", source)
        self.assertIn("GLib.get_monotonic_time()", source)
        self.assertIn("self._page_stack.get_visible_child_name()", source)
        self.assertIn("dial.animate(elapsed_seconds)", source)
        self.assertIn("step = fan_angle_step(self._rpm, elapsed_seconds)", artwork)
        self.assertIn("ctx.rotate(self._rotation + 2 * math.pi * i / blades)", artwork)
        # The sensor-read path remains on the existing four-second cycle,
        # rather than fetching sensor values on every animation frame.
        self.assertIn("REFRESH_SECONDS = 4", source)
        self.assertNotIn("self._reader.collect()", source[source.index("    def _on_fan_animation_tick"):source.index("    def _request_refresh")])
        for dangerous in ("pwm", "insmod", "modprobe", "subprocess.run"):
            self.assertNotIn(dangerous, artwork)

    def test_no_fabricated_fan_control_buttons(self):
        source = (ROOT / "nitro_control" / "ui.py").read_text()
        self.assertIn("FAN STATUS", source)
        self.assertIn("Firmware-managed · monitoring only", source)
        self.assertIn('value.set_text(fan_mode_description(fan.control_mode))', source)
        self.assertIn('self.fan_mode_titles[index].set_text(f"{display_name.upper()} · MODE")', source)
        self.assertIn('value.add_css_class("nitro-fan-muted")', source)
        self.assertIn('fan_mode_description(fan.control_mode) + " · read-only"', source)
        self.assertNotIn('value.set_text(_number(fan.rpm, "RPM"))', source)
        self.assertIn("self.overview_firmware_value.set_text(data.firmware_profile or \"Unavailable\")", source)
        self.assertIn("self.overview_tuned_value.set_text(data.tuned_profile or \"Unavailable\")", source)
        self.assertNotIn('Gtk.Button(label="Max")', source)
        self.assertNotIn('Gtk.Button(label="Custom")', source)
        self.assertNotIn('Gtk.Button(label="CoolBoost")', source)
        self.assertNotIn('Fan Max / Custom unavailable', source)
        self.assertNotIn('No CoolBoost / Max / Custom PWM controls', source)
        self.assertIn('No manual fan speed control', source)
        self.assertIn('button.suggested-action:disabled', source)


if __name__ == "__main__":
    unittest.main()

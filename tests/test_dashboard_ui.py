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
        self.assertIn('self.fan_dials[index].update(fan.name, fan.rpm, fan.control_mode)', source)

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

    def test_no_fabricated_fan_control_buttons(self):
        source = (ROOT / "nitro_control" / "ui.py").read_text()
        self.assertIn("FIRMWARE FAN MANAGEMENT", source)
        self.assertIn("Firmware-managed · monitoring only", source)
        self.assertIn('value.set_text(fan.control_mode)', source)
        self.assertNotIn('value.set_text(_number(fan.rpm, "RPM"))', source)
        self.assertIn("self.overview_firmware_value.set_text(data.firmware_profile or \"Unavailable\")", source)
        self.assertIn("self.overview_tuned_value.set_text(data.tuned_profile or \"Unavailable\")", source)
        self.assertNotIn('Gtk.Button(label="Max")', source)
        self.assertNotIn('Gtk.Button(label="Custom")', source)
        self.assertNotIn('Gtk.Button(label="CoolBoost")', source)


if __name__ == "__main__":
    unittest.main()

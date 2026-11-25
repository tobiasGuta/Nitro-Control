"""Offline dashboard tests. These never import GTK or touch real hardware."""
from __future__ import annotations

import importlib
import math
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from nitro_control.fan_control import FanControlError, FanPlan
from nitro_control.models import GPU, RGB, Fan, Snapshot
from nitro_control.monitor_history import MonitorHistory
from nitro_control.rgb_hardware import RGBCapability

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
        history.record("t0", None, None, None, observed_at=0)
        history.record("t1", float("nan"), float("inf"), -math.inf, observed_at=1)
        self.assertEqual(history.series("cpu_celsius"), (None, None))
        self.assertEqual(history.series("gpu_celsius"), (None, None))
        self.assertEqual(history.series("gpu_utilization_percent"), (None, None))

    def test_fixed_history_capacity_and_order(self):
        history = MonitorHistory(limit=3)
        for index in range(5):
            history.record(f"t{index}", float(index), None, None, observed_at=index)
        self.assertEqual(tuple(s.timestamp for s in history.samples),
                         ("t2", "t3", "t4"))
        self.assertEqual(history.series("cpu_celsius"), (2, 3, 4))

    def test_time_spacing_and_missing_or_delayed_observations_break_segments(self):
        history = MonitorHistory()
        for time, reading in ((0, 27), (2, 28), (6, 29), (10, None), (14, 0), (24, 35), (28, 36)):
            history.record(str(time), reading, reading, reading, observed_at=time, gpu_identity="GPU A")
        history.record("late", 55, 56, 57, observed_at=16)
        history.record("invalid", 55, 56, 57)
        self.assertEqual(history.series("cpu_celsius"), (27, 28, 29, None, 0, 35, 36))
        self.assertEqual(history.segments("gpu_utilization_percent"), [
            [(0, 27), (2 / 28, 28), (6 / 28, 29)], [(0.5, 0)], [(24 / 28, 35), (1, 36)],
        ])

    def test_sensor_replacement_breaks_lines_and_old_history_expires(self):
        history = MonitorHistory(max_age=12)
        history.record("0", 30, 40, 0, observed_at=0, gpu_identity="slot 1", cpu_identity="Tctl")
        history.record("5", 25, 30, 0, observed_at=5, gpu_identity="slot 2", cpu_identity="Tdie")
        self.assertEqual(history.segments("cpu_celsius"), [[(0, 30)], [(1, 25)]])
        self.assertEqual(history.segments("gpu_celsius"), [[(0, 40)], [(1, 30)]])
        history.record("13", None, None, 101, observed_at=13)
        self.assertEqual(history.series("gpu_utilization_percent"), (0, None))

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
        self.assertIn('display_name = fan_display_name(model, fan.name)', source)

    def test_rgb_physical_permissions_and_100_guard_preserved(self):
        source = (ROOT / "nitro_control" / "ui.py").read_text()
        self.assertIn("self._rgb_client.apply(plan)", source)
        self.assertIn("not native or plan.brightness == 100", source)
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
                        "Keyboard preview"):
            self.assertIn(f'\"{heading}\"', source)
        self.assertIn("hardware_content.append(_panel_group(", source)
        self.assertIn('content.append(section("Keyboard preview", preview_panel))', source)

    def test_keyboard_preview_is_draft_only_and_rgb_guard_remains_in_place(self):
        source = (ROOT / "nitro_control" / "ui.py").read_text()
        visual = (ROOT / "nitro_control" / "dashboard_visuals.py").read_text()
        self.assertIn("class KeyboardPreview(Gtk.DrawingArea):", visual)
        self.assertIn("self.rgb_keyboard.update_lighting(self._rgb_draft())", source)
        self.assertIn('self.rgb_modes.add_titled(static, "static", "Static")', source)
        self.assertIn('self.rgb_modes.add_titled(dynamic, "dynamic", "Dynamic")', source)
        for method, mode_check in (("_request_rgb_hardware", '!= "static"'),
                                   ("_confirm_rgb_hardware", '== "static"')):
            body = source.split(f"    def {method}(", 1)[1].split("\n    def ", 1)[0]
            self.assertIn(f"self.rgb_modes.get_visible_child_name() {mode_check}", body)
            self.assertIn("self._rgb_can_apply(plan)", body)
        self.assertIn("self.rgb_native_hint.set_visible(native and draft.brightness != 100)", source)
        self.assertIn("self._rgb_client.apply(plan)", source)
        self.assertIn("self._confirm_rgb_hardware", source)
        # The custom paint code never invokes the privileged write path.
        rgb_section = visual.split("class KeyboardPreview", 1)[1].split("class TelemetryGraph", 1)[0]
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

    def test_fan_readouts_and_optional_controls(self):
        source = (ROOT / "nitro_control" / "ui.py").read_text()
        self.assertIn("FAN STATUS", source)
        self.assertIn('value.set_text(fan_mode_description(fan.control_mode))', source)
        self.assertIn('self.fan_mode_titles[index].set_text(f"{display_name.upper()} · MODE")', source)
        self.assertIn('value.add_css_class("nitro-fan-muted")', source)
        self.assertIn('fan_mode_description(fan.control_mode) + " · read-only"', source)
        self.assertNotIn('value.set_text(_number(fan.rpm, "RPM"))', source)
        self.assertIn("self.overview_firmware_value.set_text(data.firmware_profile or \"Unavailable\")", source)
        self.assertIn("self.overview_tuned_value.set_text(data.tuned_profile or \"Unavailable\")", source)
        self.assertIn('self.fan_controls.set_visible(ready)', source)
        self.assertIn('ready = not self._demo and self._fan_available and self._fan_ready', source)
        self.assertIn('dialog.set_default_response("cancel")', source)
        self.assertIn('self._fan_client.apply(plan)', source)
        self.assertIn('button.suggested-action:disabled', source)


class _FakeWidget:
    def __init__(self, label: str = "", active: bool = False, **_kwargs):
        self.label = label
        self.text = label
        self.subtitle = ""
        self.title = ""
        self.tooltip = ""
        self.visible = True
        self.sensitive = True
        self.active = active
        self.selected = 0
        self.value = 0.0
        self.css_classes: set[str] = set()
        self.children: list[object] = []
        self._callbacks: dict[str, list] = {}

    def connect(self, signal: str, callback, *extra):
        self._callbacks.setdefault(signal, []).append((callback, extra))

    def emit(self, signal: str, *args):
        for callback, extra in list(self._callbacks.get(signal, [])):
            callback(self, *args, *extra)

    def append(self, child):
        self.children.append(child)

    def add_css_class(self, name: str):
        self.css_classes.add(name)

    def remove_css_class(self, name: str):
        self.css_classes.discard(name)

    def set_visible(self, visible: bool):
        self.visible = bool(visible)

    def get_visible(self) -> bool:
        return self.visible

    def set_sensitive(self, sensitive: bool):
        self.sensitive = bool(sensitive)

    def get_sensitive(self) -> bool:
        return self.sensitive

    def set_active(self, active: bool):
        self.active = bool(active)
        self.emit("toggled")

    def get_active(self) -> bool:
        return self.active

    def set_selected(self, index: int):
        self.selected = index
        self.emit("notify::selected", None)

    def get_selected(self) -> int:
        return self.selected

    def set_value(self, value: float):
        self.value = float(value)
        self.emit("value-changed")

    def get_value(self) -> float:
        return self.value

    def set_text(self, text: str):
        self.text = text

    def set_label(self, label: str):
        self.label = label

    def set_subtitle(self, text: str):
        self.subtitle = text

    def set_title(self, text: str):
        self.title = text

    def set_tooltip_text(self, text: str):
        self.tooltip = text

    def set_from_icon_name(self, _name: str):
        pass

    def update_property(self, *_args):
        pass

    def __getattr__(self, name: str):
        if name.startswith("set_"):
            return lambda *_a, **_kw: None
        raise AttributeError(name)


class _FakeAlertDialog(_FakeWidget):
    created: list["_FakeAlertDialog"] = []

    def __init__(self, heading: str, body: str):
        super().__init__()
        self.heading = heading
        self.body = body
        self.responses: dict[str, str] = {}
        self.appearances: dict[str, object] = {}
        self.default_response: str | None = None
        self.close_response: str | None = None
        self.presented_to = None
        _FakeAlertDialog.created.append(self)

    @classmethod
    def new(cls, heading: str, body: str):
        return cls(heading, body)

    def add_response(self, key: str, label: str):
        self.responses[key] = label

    def set_response_appearance(self, key: str, appearance):
        self.appearances[key] = appearance

    def set_default_response(self, key: str):
        self.default_response = key

    def set_close_response(self, key: str):
        self.close_response = key

    def present(self, parent):
        self.presented_to = parent

    def respond(self, key: str):
        self.emit("response", key)


class _FakeDial:
    def __init__(self):
        self.updates: list[tuple[str, int | None, str]] = []

    def update(self, name: str, rpm: int | None, mode: str):
        self.updates.append((name, rpm, mode))


def _load_ui_with_fake_gtk(cleanup_fn):
    _FakeAlertDialog.created.clear()
    gi = types.ModuleType("gi")
    gi.require_version = lambda *_a, **_kw: None
    repo = types.ModuleType("gi.repository")

    class _BaseWindow:
        pass

    class _BaseApp:
        pass

    class _DrawingArea(_FakeWidget):
        pass

    gtk = types.ModuleType("gi.repository.Gtk")
    gtk.DrawingArea = _DrawingArea
    gtk.Widget = _FakeWidget
    gtk.Box = lambda **_kw: _FakeWidget()
    gtk.Label = lambda label="", **_kw: _FakeWidget(label=label)
    gtk.Button = lambda label="", **_kw: _FakeWidget(label=label)
    gtk.CheckButton = lambda label="", **_kw: _FakeWidget(label=label)
    gtk.DropDown = types.SimpleNamespace(new_from_strings=lambda _items: _FakeWidget())
    gtk.Scale = types.SimpleNamespace(new_with_range=lambda *_a: _FakeWidget())
    gtk.Orientation = types.SimpleNamespace(VERTICAL=1, HORIZONTAL=2)
    gtk.Align = types.SimpleNamespace(CENTER=1, START=2, FILL=3)
    gtk.AccessibleProperty = types.SimpleNamespace(LABEL=1)
    gtk.Window = types.SimpleNamespace(set_default_icon_name=lambda *_a: None)

    adw = types.ModuleType("gi.repository.Adw")
    adw.ApplicationWindow = _BaseWindow
    adw.Application = _BaseApp
    adw.AlertDialog = _FakeAlertDialog
    adw.ResponseAppearance = types.SimpleNamespace(DESTRUCTIVE="destructive", SUGGESTED="suggested")
    adw.ActionRow = _FakeWidget
    adw.PreferencesGroup = _FakeWidget

    glib = types.ModuleType("gi.repository.GLib")
    glib.idle_add = lambda fn, *args: fn(*args)

    gdk = types.ModuleType("gi.repository.Gdk")
    gio = types.ModuleType("gi.repository.Gio")
    pango = types.ModuleType("gi.repository.Pango")
    pango.EllipsizeMode = types.SimpleNamespace(END=1)
    cairo = types.ModuleType("cairo")

    repo.Gtk = gtk
    repo.Adw = adw
    repo.GLib = glib
    repo.Gdk = gdk
    repo.Gio = gio
    repo.Pango = pango
    gi.repository = repo

    fake_modules = {
        "gi": gi,
        "gi.repository": repo,
        "gi.repository.Gtk": gtk,
        "gi.repository.Adw": adw,
        "gi.repository.GLib": glib,
        "gi.repository.Gdk": gdk,
        "gi.repository.Gio": gio,
        "gi.repository.Pango": pango,
        "cairo": cairo,
    }
    patcher = patch.dict(sys.modules, fake_modules)
    patcher.start()
    cleanup_fn(patcher.stop)
    for mod in ("nitro_control.ui", "nitro_control.dashboard_visuals"):
        sys.modules.pop(mod, None)
    cleanup_fn(lambda: [sys.modules.pop(m, None) for m in ("nitro_control.ui", "nitro_control.dashboard_visuals")])
    return importlib.import_module("nitro_control.ui")


class _ImmediateThread:
    def __init__(self, target=None, args=(), **_kwargs):
        self._target = target
        self._args = args

    def start(self):
        self._target(*self._args)


class DashboardFanBehaviorTests(unittest.TestCase):
    def _make_window(self, ui_mod, *, ready=True, demo=False):
        win = ui_mod.NitroWindow.__new__(ui_mod.NitroWindow)
        win._demo = demo
        win._closed = False
        win._fan_available = True
        win._fan_ready = ready
        win._fan_active = FanPlan("Auto")
        win._fan_busy = False
        win._fan_editing = False
        win._fan_rendering = False
        win._fan_revision = 0
        win._fan_feedback = None
        win._last_fan_observed_at = None
        win._fan_inspection_error = None
        win._power_busy = False
        win._history = MonitorHistory()
        win._rgb_pending = None
        win._rgb_revision = 0
        win._power_revision = 0
        win._last_power_observed_at = None
        win._next_rgb_probe = 0
        win._rgb_status = RGBCapability("none", "Unavailable", False, "Not checked")
        win._rgb_hardware = types.SimpleNamespace(probe=lambda: win._rgb_status)
        win._rgb_client = types.SimpleNamespace(ready=lambda: False)
        win._sync_power_button = lambda: None
        win._sync_rgb_hardware_button = lambda: None
        win._request_refresh = lambda: None
        win._post_result = lambda callback, *args: callback(*args)
        win.applied = []

        class _StubFanClient:
            def __init__(self):
                self.fail_with = None

            def ready(self):
                return ready

            def apply(self, plan):
                if self.fail_with is not None:
                    raise self.fail_with
                win.applied.append(plan)

        win._fan_client = _StubFanClient()
        win._busy = False
        win._build_fan_controls()
        win._render_fan_controls(True, FanPlan("Auto"))
        return self._attach_dashboard_widgets(win)

    def _attach_dashboard_widgets(self, win):
        win.model_label = _FakeWidget()
        win.status_label = _FakeWidget()
        win.status_icon = _FakeWidget()
        win.cpu_value = _FakeWidget()
        win.cpu_hint = _FakeWidget()
        win.gpu_value = _FakeWidget()
        win.gpu_hint = _FakeWidget()
        win.fan1_value = _FakeWidget()
        win.fan1_hint = _FakeWidget()
        win.fan2_value = _FakeWidget()
        win.fan2_hint = _FakeWidget()
        win.fan_mode_titles = [_FakeWidget(), _FakeWidget()]
        win.fan_dials = (_FakeDial(), _FakeDial())
        win.cpu_graph = types.SimpleNamespace(set_history=lambda *_a: None)
        win.gpu_graph = types.SimpleNamespace(set_history=lambda *_a: None)
        win.gpu_rows = {k: _FakeWidget() for k in ("name", "util", "memory", "power")}
        win.overview_firmware_value = _FakeWidget()
        win.overview_tuned_value = _FakeWidget()
        win.profile_rows = {"firmware": _FakeWidget(), "tuned": _FakeWidget()}
        win.firmware_choices_row = _FakeWidget()
        win.fan_rows = [(_FakeWidget(), _FakeWidget()) for _ in range(4)]
        win.thermal_rows = [(_FakeWidget(), _FakeWidget()) for _ in range(10)]
        win.rgb_value = _FakeWidget()
        win.rgb_row = _FakeWidget()
        win.driver_value = _FakeWidget()
        win.rgb_capability = _FakeWidget()
        win.rgb_hardware_status = _FakeWidget()
        win._render_power = lambda *_a: None
        return win

    def test_custom_requires_confirmation_warns_user_and_cancel_writes_nothing(self):
        ui_mod = _load_ui_with_fake_gtk(self.addCleanup)
        win = self._make_window(ui_mod)

        # Switch to Custom (index 2), set CPU 45%, GPU Auto.
        win.fan_mode.set_selected(2)
        win.fan_settings[0][1].set_value(45)
        win.fan_settings[1][0].set_active(True)
        self.assertTrue(win.fan_custom_warning.get_visible())
        self.assertTrue(win.fan_apply.get_sensitive())

        with patch.object(ui_mod, "Thread", _ImmediateThread):
            win._request_fan_change()
            self.assertEqual(win.applied, [])
            self.assertEqual(len(_FakeAlertDialog.created), 1)
            dialog = _FakeAlertDialog.created[0]
            self.assertEqual(dialog.default_response, "cancel")
            self.assertEqual(dialog.close_response, "cancel")
            self.assertIn("45%", dialog.body)
            self.assertIn("GPU fan: Auto", dialog.body)
            self.assertIn("closing Nitro Control", dialog.body)
            self.assertIn("Restore Auto", dialog.body)

            # Cancel leaves hardware untouched.
            dialog.respond("cancel")
            self.assertEqual(win.applied, [])

            # Confirm applies the exact validated Custom plan.
            win._request_fan_change()
            _FakeAlertDialog.created[-1].respond("apply")
            self.assertEqual(win.applied, [FanPlan("Custom", 45, None)])
            self.assertEqual(win._fan_active, FanPlan("Custom", 45, None))

    def test_auto_and_restore_auto_stay_accessible_when_state_or_telemetry_unknown(self):
        ui_mod = _load_ui_with_fake_gtk(self.addCleanup)
        win = self._make_window(ui_mod)

        # Unknown fan plan + user previously moved sliders into Custom.
        win._render_fan_controls(True, None)
        win.fan_mode.set_selected(2)
        win.fan_settings[0][1].set_value(50)
        self.assertTrue(win._fan_editing)
        self.assertTrue(win.fan_restore_auto.get_sensitive())

        # Even when fan_active reports Auto (e.g. telemetry unknown), Restore Auto stays enabled.
        win._render_fan_controls(True, FanPlan("Auto"))
        self.assertTrue(win.fan_restore_auto.get_sensitive())

        with patch.object(ui_mod, "Thread", _ImmediateThread):
            win._request_restore_auto()
        self.assertEqual(_FakeAlertDialog.created, [])
        self.assertEqual(win.applied, [FanPlan("Auto")])
        self.assertFalse(win._fan_editing)
        self.assertEqual(win.fan_feedback.text, "Auto applied.")

    def test_failed_custom_apply_surfaces_error_and_keeps_restore_auto_available(self):
        ui_mod = _load_ui_with_fake_gtk(self.addCleanup)
        win = self._make_window(ui_mod)
        win.fan_mode.set_selected(2)
        win.fan_settings[0][1].set_value(60)
        win.fan_settings[1][1].set_value(70)
        win._fan_client.fail_with = FanControlError("Fan change failed (write error). Both fans returned to Auto.")

        with patch.object(ui_mod, "Thread", _ImmediateThread):
            win._request_fan_change()
            _FakeAlertDialog.created[-1].respond("apply")

        self.assertIsNone(win._fan_active)
        self.assertTrue(win._fan_editing)
        self.assertIn("Both fans returned to Auto", win.fan_feedback.text)
        self.assertTrue(win.fan_restore_auto.get_sensitive())

        win._fan_client.fail_with = FanControlError("Could not verify Auto for CPU.")
        with patch.object(ui_mod, "Thread", _ImmediateThread):
            win._request_restore_auto()
        self.assertIsNone(win._fan_active)
        self.assertFalse(win._fan_editing)
        self.assertEqual(win.fan_feedback.text, "Could not verify Auto for CPU.")

        win._fan_client.fail_with = None
        with patch.object(ui_mod, "Thread", _ImmediateThread):
            win._request_restore_auto()
        self.assertEqual(win._fan_active, FanPlan("Auto"))
        self.assertEqual(win.fan_feedback.text, "Auto applied.")

    def test_dashboard_render_aligns_fans_by_channel_not_list_position(self):
        ui_mod = _load_ui_with_fake_gtk(self.addCleanup)
        win = self._make_window(ui_mod)

        snap_missing_fan1 = Snapshot(
            sampled_at="2026-11-24T12:00:00-05:00",
            model="Nitro AN515-58",
            cpu_celsius=50.0,
            fans=(Fan("Fan 2", 3157, "Automatic", channel=2), Fan("Fan 10", 9000, "Unknown", channel=10)),
            acer_temperatures=(),
            drives=(),
            gpu=GPU("RTX 3050 Ti", 45.0, 5.0, 100.0, 4096.0, 10.0),
            firmware_profile="balanced",
            firmware_profile_choices=("balanced",),
            tuned_profile="balanced",
            rgb=RGB(True, None, None),
        )
        win._render(snap_missing_fan1)
        self.assertEqual(win.fan_dials[0].updates[-1], ("Fan 1", None, "Unknown"))
        self.assertEqual(win.fan_dials[1].updates[-1], ("GPU Fan", 3157, "Automatic"))
        self.assertEqual(win.fan1_value.text, "Unavailable")
        self.assertEqual(win.fan2_value.text, "Automatic")

        snap_genuine_labels = Snapshot(
            sampled_at="2026-11-24T12:00:04-05:00",
            model="Nitro AN515-58",
            cpu_celsius=51.0,
            fans=(
                Fan("Aux Blower", 3300, "Manual", channel=2),
                Fan("Main Blower", 2400, "Automatic", channel=1),
            ),
            acer_temperatures=(),
            drives=(),
            gpu=None,
            firmware_profile="balanced",
            firmware_profile_choices=("balanced",),
            tuned_profile="balanced",
            rgb=RGB(True, None, None),
        )
        win._render(snap_genuine_labels)
        self.assertEqual(win.fan_dials[0].updates[-1], ("Main Blower", 2400, "Automatic"))
        self.assertEqual(win.fan_dials[1].updates[-1], ("Aux Blower", 3300, "Manual"))

    def test_stale_in_flight_refresh_does_not_overwrite_fan_telemetry_after_fan_change(self):
        ui_mod = _load_ui_with_fake_gtk(self.addCleanup)
        win = self._make_window(ui_mod)
        refresh_calls = 0

        def record_refresh():
            nonlocal refresh_calls
            refresh_calls += 1

        win._request_refresh = record_refresh

        stale_snap = Snapshot(
            sampled_at="2026-11-24T12:00:00-05:00",
            model="Nitro AN515-58",
            cpu_celsius=49.0,
            fans=(
                Fan("Fan 1", 2100, "Automatic", channel=1),
                Fan("Fan 2", 2200, "Automatic", channel=2),
            ),
            acer_temperatures=(),
            drives=(),
            gpu=None,
            firmware_profile="balanced",
            firmware_profile_choices=("balanced",),
            tuned_profile="balanced",
            rgb=RGB(True, None, None),
        )
        post_max_snap = Snapshot(
            sampled_at="2026-11-24T12:00:01-05:00",
            model="Nitro AN515-58",
            cpu_celsius=50.0,
            fans=(
                Fan("Fan 1", 7500, "Full speed / control disabled", channel=1),
                Fan("Fan 2", 7600, "Full speed / control disabled", channel=2),
            ),
            acer_temperatures=(),
            drives=(),
            gpu=None,
            firmware_profile="balanced",
            firmware_profile_choices=("balanced",),
            tuned_profile="balanced",
            rgb=RGB(True, None, None),
        )

        def finish(snapshot, fan_plan, revision):
            observed_at = ui_mod._elapsed_time()
            win._finish_refresh(ui_mod.RefreshResult(
                observed_at=observed_at, snapshot=snapshot, error=None,
                fan_revision=revision, fan_observed_at=observed_at, fan_available=True,
                fan_ready=True, fan_plan=fan_plan, fan_error=None,
                power_revision=0, power_observed_at=None, power_state=None, power_error=None,
                rgb_revision=0, rgb_observed_at=None, rgb=None,
            ))

        # Initial poll completes at revision 0.
        finish(stale_snap, FanPlan("Auto"), 0)
        self.assertEqual(win.fan1_value.text, "Automatic")
        self.assertEqual(len(win.fan_dials[0].updates), 1)

        # A newer fan change increments _fan_revision while an older revision-0 poll was still in flight.
        with patch.object(ui_mod, "Thread", _ImmediateThread):
            win._start_fan_change(FanPlan("Max"))
        self.assertEqual(win._fan_revision, 1)
        self.assertEqual(refresh_calls, 1)

        # Simulate newer telemetry already rendered or post-change state active; now old revision-0 arrives.
        win._render(post_max_snap)
        self.assertEqual(win.fan1_value.text, "Full speed / control disabled")
        self.assertEqual(win.fan_dials[0].updates[-1], ("CPU Fan", 7500, "Full speed / control disabled"))

        win._busy = True
        finish(stale_snap, FanPlan("Auto"), 0)
        # Non-fan telemetry updates, while fan status cards, dials, rows, and controls keep newer state.
        self.assertEqual(win.cpu_value.text, "49 °C")
        self.assertEqual(win.fan1_value.text, "Full speed / control disabled")
        self.assertEqual(win.fan2_value.text, "Full speed / control disabled")
        self.assertEqual(win.fan_dials[0].updates[-1], ("CPU Fan", 7500, "Full speed / control disabled"))
        self.assertEqual(win._fan_active, FanPlan("Max"))
        self.assertEqual(win.fan_feedback.text, "Max applied.")
        # Because _fan_busy is False, stale completion schedules a fresh follow-up refresh once.
        self.assertEqual(refresh_calls, 2)

        # Current revision-1 poll updates normally without extra follow-up refresh.
        win._busy = True
        finish(post_max_snap, FanPlan("Max"), 1)
        self.assertEqual(win.cpu_value.text, "50 °C")
        self.assertEqual(win.fan_dials[0].updates[-1], ("CPU Fan", 7500, "Full speed / control disabled"))
        self.assertEqual(refresh_calls, 2)

    def test_unknown_control_mode_displays_mode_not_reported_in_ui(self):
        ui_mod = _load_ui_with_fake_gtk(self.addCleanup)
        win = self._make_window(ui_mod)
        snap = Snapshot(
            sampled_at="2026-11-24T12:00:00-05:00",
            model="Nitro AN515-58",
            cpu_celsius=48.0,
            fans=(
                Fan("Fan 1", 2200, "Unknown", channel=1),
                Fan("Fan 2", 2300, "Automatic", channel=2),
            ),
            acer_temperatures=(),
            drives=(),
            gpu=None,
            firmware_profile="balanced",
            firmware_profile_choices=("balanced",),
            tuned_profile="balanced",
            rgb=RGB(True, None, None),
        )
        win._render(snap)
        self.assertEqual(win.fan1_value.text, "Mode not reported")
        self.assertIn("nitro-fan-muted", win.fan1_value.css_classes)
        self.assertEqual(win.fan_dials[0].updates[-1], ("CPU Fan", 2200, "Unknown"))
        self.assertEqual(win.fan2_value.text, "Automatic")


if __name__ == "__main__":
    unittest.main()

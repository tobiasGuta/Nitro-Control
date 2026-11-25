"""Real GTK fixtures launched by test_gtk_lifecycle on a private session bus."""
from __future__ import annotations

import subprocess
import sys
import unittest
from dataclasses import replace
from itertools import count
from pathlib import Path
from threading import Event, get_ident
from unittest.mock import Mock, patch

try:
    from nitro_control import ui
except (ImportError, ValueError) as exc:
    print(f"GTK4/Libadwaita/PyCairo unavailable: {exc}", file=sys.stderr)
    raise SystemExit(77) from exc

from nitro_control.demo import demo_snapshot
from nitro_control.desktop_session import activate_live
from nitro_control.fan_control import FanPlan
from nitro_control.models import RGB
from nitro_control.profiles import ALLOWED, PowerState, ProfileError
from nitro_control.rgb import PRESETS
from nitro_control.rgb_client import RGBHelperCapabilities
from nitro_control.rgb_hardware import RGBCapability

Gdk, Gio, GLib, Gtk = ui.Gdk, ui.Gio, ui.GLib, ui.Gtk
BALANCED = PowerState("balanced", ALLOWED)
PERFORMANCE = PowerState("performance", ALLOWED)
AVAILABLE = RGBCapability("native-wmi", "Acer RGB", True, "Detected", effects_supported=True)
UNAVAILABLE = RGBCapability("none", "Unavailable", False, "Device disappeared")
HELPER = RGBHelperCapabilities(True, 2, True, True, False, "Static and Dynamic supported")


class Gate:
    """Park a worker at a specific call without assuming relative timing."""

    def __init__(self, value):
        self.value = value
        self.entered = Event()
        self.release = Event()
        self.thread = None

    def __call__(self, *_args):
        self.thread = get_ident()
        self.entered.set()
        if not self.release.wait(8):
            raise AssertionError("test never released worker")
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class ActivationApp(ui.NitroApplication):
    def __init__(self, *, demo):
        super().__init__(demo=demo)
        self.activations = 0

    def do_activate(self):
        self.activations += 1


class GtkLifecycleTests(unittest.TestCase):
    application_ids = count()

    def setUp(self):
        self.context = GLib.MainContext.default()
        self.clock = 100.0
        self.snapshot = replace(demo_snapshot(), demo=False)
        self.reader = Mock()
        self.reader.collect.return_value = self.snapshot
        self.power = Mock()
        self.power.read.return_value = BALANCED
        self.power.apply.return_value = PERFORMANCE
        self.fans = Mock()
        self.fans.ready.return_value = True
        self.rgb = Mock()
        self.rgb.capabilities.return_value = HELPER
        self.rgb.apply.return_value = "Static software response"
        self.rgb.apply_effect.return_value = "Dynamic software response"
        self.hardware = Mock()
        self.hardware.probe.return_value = AVAILABLE
        self.find_fans = Mock(return_value=Path("/fake/fan-control"))
        self.tray_preference = Mock(return_value=False)
        self.tray_spawn = Mock()
        overrides = patch.multiple(
            ui, HardwareReader=Mock(return_value=self.reader),
            FanClient=Mock(return_value=self.fans), PowerProfileController=Mock(return_value=self.power),
            RGBClient=Mock(return_value=self.rgb), RGBHardware=Mock(return_value=self.hardware),
            find_fan_controls=self.find_fans, read_fan_plan=Mock(return_value=FanPlan("Auto")),
            load_keep_in_tray=self.tray_preference, start_tray_process=self.tray_spawn,
            _elapsed_time=lambda: self.clock,
        )
        overrides.start()
        self.addCleanup(overrides.stop)

    def wait_until(self, predicate):
        timed_out = False

        def expire():
            nonlocal timed_out
            timed_out = True
            return False

        timeout = GLib.timeout_add_seconds(5, expire)
        try:
            while not predicate() and not timed_out:
                self.context.iteration(True)
            self.assertTrue(predicate(), "GTK callback did not complete")
        finally:
            if not timed_out:
                GLib.source_remove(timeout)

    def gate(self, value):
        gate = Gate(value)
        self.addCleanup(gate.release.set)
        return gate

    def window(self, *, demo=False, settle=True, present=False):
        app = ui.NitroApplication(demo=demo)
        app.set_application_id(f"io.github.tobiasguta.NitroControl.Test{next(self.application_ids)}")
        app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
        self.assertTrue(app.register(None))
        if present:
            app.activate()
            win = app.get_active_window()
        else:
            win = ui.NitroWindow(app, demo=demo)
        self.addCleanup(win.destroy)
        if settle:
            self.wait_until(lambda: not win._busy)
        return win

    def test_demo_and_live_activate_only_their_own_single_instance(self):
        live, demo = ActivationApp(demo=False), ActivationApp(demo=True)
        self.assertTrue(live.register(None))
        self.assertTrue(demo.register(None))
        self.assertFalse(live.get_is_remote())
        self.assertFalse(demo.get_is_remote())
        for mode, expected, other in (("live", live, demo), ("demo", demo, live)):
            with self.subTest(mode=mode):
                previous = expected.activations
                other_activations = other.activations
                with subprocess.Popen([sys.executable, __file__, "--activate", mode],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as client:
                    # Return codes alone cannot tell which primary received Activate.
                    GLib.timeout_add(10, lambda: client.poll() is None)
                    self.wait_until(lambda: client.poll() is not None)
                    stdout, stderr = client.communicate()
                self.assertEqual((client.returncode, stdout.strip()), (0, "remote"), stderr)
                self.wait_until(lambda app=expected, count=previous: app.activations == count + 1)
                self.assertEqual(other.activations, other_activations)

        replies = []
        count = live.activations
        demo_count = demo.activations
        activate_live(Gio.Cancellable(), lambda *reply: replies.append(reply))
        self.wait_until(lambda: bool(replies))
        self.assertEqual(replies, [(True, None)])
        self.assertEqual(live.activations, count + 1)
        self.assertEqual(demo.activations, demo_count)

    def test_dashboard_does_not_launch_another_tray_while_one_is_running(self):
        window = self.window()
        process = self.tray_spawn.return_value
        process.get_identifier.return_value = "1234"
        window.tray_switch.set_active(True)
        self.tray_spawn.assert_called_once()
        source = window._tray_check_source
        window.tray_switch.set_active(False)
        window.tray_switch.set_active(True)
        self.tray_spawn.assert_called_once()
        self.assertEqual(window._tray_check_source, source)
        window.destroy()
        self.assertIsNone(self.context.find_source_by_id(source))
        self.assertTrue(process.wait_async.call_args.args[0].is_cancelled())
        process.force_exit.assert_not_called()

    def test_dashboard_reports_tray_exit_before_start_timer(self):
        window = self.window()
        process = self.tray_spawn.return_value
        window.tray_switch.set_active(True)
        source = window._tray_check_source
        process.get_identifier.return_value = None
        process.get_successful.return_value = False
        process.wait_async.call_args.args[1](process, None)
        self.assertEqual(window._tray_check_source, 0)
        self.assertIsNone(self.context.find_source_by_id(source))
        self.assertIn("Tray unavailable", window.tray_feedback.get_text())

    def test_demo_queries_no_live_dependencies_and_rejects_every_write(self):
        win = self.window(demo=True, present=True)
        win._request_restore_auto()
        win._confirm_power_change(None, "apply", "performance")
        win._confirm_rgb_hardware(None, "apply", win._rgb_draft())
        win.rgb_modes.set_visible_child_name("dynamic")
        win._confirm_rgb_effect(None, "apply", win._effect_draft())
        for mock in (self.reader, self.power, self.fans, self.rgb, self.hardware):
            self.assertEqual(mock.mock_calls, [])
        self.find_fans.assert_not_called()
        self.tray_preference.assert_not_called()
        self.tray_spawn.assert_not_called()
        self.assertIn("DEMO", win.status_label.get_text())
        self.assertFalse(win.power_apply.get_sensitive())
        self.assertFalse(win.rgb_apply_effect.get_sensitive())
        self.assertFalse(win.fan_restore_auto.get_sensitive())

    def test_slow_inspection_does_not_block_gtk_and_refreshes_never_overlap(self):
        probe = self.gate(AVAILABLE)
        self.hardware.probe.side_effect = probe
        win = self.window(settle=False)
        self.assertTrue(probe.entered.wait(4))
        self.assertNotEqual(probe.thread, get_ident())
        for _ in range(5):
            win._on_timer()
            win._request_refresh()
            win.rgb_brightness.set_value(100)
            win.rgb_brightness.set_value(90)
        self.reader.collect.assert_called_once()
        self.hardware.probe.assert_called_once()
        self.rgb.capabilities.assert_not_called()
        probe.release.set()
        self.wait_until(lambda: not win._busy)
        self.assertEqual(self.rgb.capabilities.call_count, 1)
        self.clock += 4
        win._request_refresh()
        self.wait_until(lambda: not win._busy)
        self.assertEqual(self.reader.collect.call_count, 2)
        self.hardware.probe.assert_called_once()
        self.rgb.capabilities.assert_called_once()
        self.clock += 4
        self.reader.collect.return_value = replace(self.snapshot, rgb=RGB(False, None, None))
        self.hardware.probe.side_effect = None
        self.hardware.probe.return_value = UNAVAILABLE
        win.rgb_brightness.set_value(100)
        self.assertTrue(win.rgb_apply_hardware.get_sensitive())
        win._request_refresh()
        self.wait_until(lambda: not win._busy)
        self.assertEqual(self.hardware.probe.call_count, 2)
        self.assertFalse(win.rgb_apply_hardware.get_sensitive())
        self.assertIn("disappeared", win.rgb_hardware_status.get_subtitle())

    def test_old_power_poll_and_apply_completion_preserve_a_new_selection(self):
        win = self.window()
        old_read = self.gate(BALANCED)
        new_read = self.gate(PERFORMANCE)
        write = self.gate(PERFORMANCE)
        reads = iter((old_read, new_read))
        self.power.read.side_effect = lambda: next(reads)()
        self.power.apply.side_effect = write

        self.clock += 4
        win._request_refresh()
        self.assertTrue(old_read.entered.wait(4))
        win.power_combo.set_selected(ALLOWED.index("performance"))
        win._confirm_power_change(None, "apply", "performance")
        self.assertTrue(write.entered.wait(4))
        win.power_combo.set_selected(ALLOWED.index("balanced"))
        self.assertFalse(win.power_apply.get_sensitive())
        self.assertFalse(win.fan_restore_auto.get_sensitive())

        write.release.set()
        self.wait_until(lambda: not win._power_busy)
        self.assertEqual(win._power_state, PERFORMANCE)
        self.assertEqual(win._selected_power(), "balanced")
        self.assertIn("Verified: Performance", win.power_status.get_text())
        old_read.release.set()
        self.wait_until(new_read.entered.is_set)
        self.assertEqual(win._power_state, PERFORMANCE)
        self.assertEqual(win._selected_power(), "balanced")
        self.assertEqual(win.overview_firmware_value.get_text(), "Unavailable")
        new_read.release.set()
        self.wait_until(lambda: not win._busy)
        self.assertTrue(win.power_apply.get_sensitive())
        self.power.apply.assert_called_once_with("performance")

    def test_power_service_disappearance_clears_verified_status_and_preserves_draft(self):
        win = self.window()
        win.power_combo.set_selected(ALLOWED.index("performance"))
        self.power.read.return_value = PERFORMANCE
        win._confirm_power_change(None, "apply", "performance")
        self.wait_until(lambda: not win._power_busy and not win._busy)
        self.assertIn("Verified: Performance", win.power_status.get_text())
        win.power_combo.set_selected(ALLOWED.index("power-saver"))
        self.power.read.side_effect = ProfileError("Service disappeared")
        self.clock += 4
        win._request_refresh()
        self.wait_until(lambda: not win._busy)
        self.assertIsNone(win._power_state)
        self.assertFalse(win.power_apply.get_sensitive())
        self.assertEqual(win.power_status.get_text(), "Service disappeared")
        self.power.read.side_effect = None
        self.clock += 4
        win._request_refresh()
        self.wait_until(lambda: not win._busy)
        self.assertEqual(win._selected_power(), "power-saver")
        self.assertTrue(win.power_apply.get_sensitive())

    def test_power_failure_clears_unverified_active_state_then_recovers(self):
        win = self.window()
        recovery = self.gate(BALANCED)
        self.power.read.side_effect = recovery
        self.power.apply.side_effect = RuntimeError("denied")
        win.power_combo.set_selected(ALLOWED.index("performance"))
        win._confirm_power_change(None, "apply", "performance")
        self.wait_until(recovery.entered.is_set)
        self.assertIsNone(win._power_state)
        self.assertFalse(win.power_apply.get_sensitive())
        self.assertIn("could not be verified", win.power_status.get_text())
        recovery.release.set()
        self.wait_until(lambda: not win._busy)
        self.assertEqual(win._power_state, BALANCED)
        self.assertEqual(win._selected_power(), "performance")
        self.assertIn("System reports Balanced active", win.power_status.get_text())
        self.power.apply.assert_called_once_with("performance")

    def test_static_completion_describes_submitted_palette_and_keeps_new_editor_values(self):
        win = self.window()
        win.rgb_brightness.set_value(100)
        plan = win._rgb_draft()
        win.rgb_brightness.set_value(90)
        win._confirm_rgb_hardware(None, "apply", plan)
        self.rgb.apply.assert_not_called()
        win.rgb_brightness.set_value(100)
        write = self.gate("Static software response")
        self.rgb.apply.side_effect = write
        win._confirm_rgb_hardware(None, "apply", plan)
        self.assertTrue(write.entered.wait(4))
        win._set_rgb_editor(PRESETS["Midnight"])
        win.rgb_brightness.set_value(75)
        newer = win._rgb_draft()
        self.assertFalse(win.rgb_apply_hardware.get_sensitive())
        self.assertIn("selection differs", win.rgb_hardware_status.get_subtitle())
        write.release.set()
        self.wait_until(lambda: win._rgb_pending is None and not win._busy)
        self.assertEqual(win._rgb_draft(), newer)
        self.assertFalse(win.rgb_apply_hardware.get_sensitive())
        self.assertIn("Previous Static palette request", win.rgb_hardware_status.get_subtitle())
        self.rgb.apply.assert_called_once_with(plan)

    def test_dynamic_completion_and_stale_capability_cannot_overwrite_new_editor_state(self):
        win = self.window()
        win.rgb_modes.set_visible_child_name("dynamic")
        plan = win._effect_draft()
        old_probe = self.gate(AVAILABLE)
        new_probe = self.gate(UNAVAILABLE)
        write = self.gate("Dynamic software response")
        probes = iter((old_probe, new_probe))
        self.hardware.probe.side_effect = lambda: next(probes)()
        self.rgb.apply_effect.side_effect = write
        self.clock += ui.RGB_PROBE_SECONDS
        win._request_refresh()
        self.assertTrue(old_probe.entered.wait(4))
        win._confirm_rgb_effect(None, "apply", plan)
        self.assertTrue(write.entered.wait(4))
        win.rgb_effect_speed.set_value(9)
        win.rgb_modes.set_visible_child_name("static")
        win.rgb_brightness.set_value(100)
        self.assertFalse(win.rgb_apply_hardware.get_sensitive())
        write.release.set()
        self.wait_until(lambda: win._rgb_pending is None)
        old_probe.release.set()
        self.wait_until(new_probe.entered.is_set)
        self.assertFalse(win.rgb_apply_hardware.get_sensitive())
        self.assertIn("Checking", win.rgb_capability.get_subtitle())
        new_probe.release.set()
        self.wait_until(lambda: not win._busy)
        win.rgb_modes.set_visible_child_name("dynamic")
        self.assertEqual(win._effect_draft().speed, 9)
        self.assertIn("Previous Breathing request", win.rgb_effect_hardware_row.get_subtitle())
        self.assertFalse(win.rgb_apply_effect.get_sensitive())
        self.rgb.apply_effect.assert_called_once_with(plan)
        self.rgb.apply.assert_not_called()

    def test_close_policy_is_consistent_while_writes_are_pending(self):
        for operation in ("fans", "power", "static", "dynamic"):
            with self.subTest(operation=operation):
                win = self.window()
                if operation == "fans":
                    pending = self.gate(None)
                    self.fans.apply.side_effect = pending
                    win._request_restore_auto()
                elif operation == "power":
                    pending = self.gate(PERFORMANCE)
                    self.power.apply.side_effect = pending
                    win.power_combo.set_selected(ALLOWED.index("performance"))
                    win._confirm_power_change(None, "apply", "performance")
                elif operation == "static":
                    pending = self.gate("Static software response")
                    self.rgb.apply.side_effect = pending
                    win.rgb_brightness.set_value(100)
                    win._confirm_rgb_hardware(None, "apply", win._rgb_draft())
                else:
                    pending = self.gate("Dynamic software response")
                    self.rgb.apply_effect.side_effect = pending
                    win.rgb_modes.set_visible_child_name("dynamic")
                    win._confirm_rgb_effect(None, "apply", win._effect_draft())
                self.assertTrue(pending.entered.wait(4))
                self.assertTrue(win.emit("close-request"))
                self.assertFalse(win._closed)
                pending.release.set()
                self.wait_until(lambda window=win: not window._write_pending() and not window._busy)
                self.assertFalse(win.emit("close-request"))
                self.assertTrue(win._closed)

    def test_destroy_removes_timers_and_drops_queued_refresh(self):
        pending = self.gate(self.snapshot)
        self.reader.collect.side_effect = pending
        win = self.window(settle=False)
        self.assertTrue(pending.entered.wait(4))
        posted = Event()
        original = win._post_result

        def post(*args):
            original(*args)
            posted.set()

        with patch.object(win, "_post_result", side_effect=post):
            pending.release.set()
            self.assertTrue(posted.wait(4))
            self.assertTrue(win._idle_sources)
            sources = (win._refresh_source, win._animation_source, *win._idle_sources)
            win.destroy()
        self.assertTrue(win._closed)
        self.assertTrue(win._color_cancellable.is_cancelled())
        self.assertFalse(win._idle_sources)
        self.assertFalse(win._history.samples)
        for source in sources:
            self.assertIsNone(self.context.find_source_by_id(source))

    def test_destroy_does_not_cancel_authorized_write_or_deliver_late_completion(self):
        win = self.window()
        pending = self.gate(PERFORMANCE)
        self.power.apply.side_effect = pending
        win.power_combo.set_selected(ALLOWED.index("performance"))
        win._confirm_power_change(None, "apply", "performance")
        self.assertTrue(pending.entered.wait(4))
        posted = Event()
        original = win._post_result

        def post(*args):
            original(*args)
            posted.set()

        with patch.object(win, "_post_result", side_effect=post):
            win.destroy()
            self.assertTrue(win._closed)
            self.assertFalse(posted.is_set())
            pending.release.set()
            self.assertTrue(posted.wait(4))
        self.assertFalse(win._idle_sources)
        self.assertIn("Requesting Performance", win.power_status.get_text())
        self.power.apply.assert_called_once_with("performance")

    def test_application_quit_detaches_window_while_authorized_worker_continues(self):
        app = ui.NitroApplication(demo=False)
        app.set_application_id(f"io.github.tobiasguta.NitroControl.Test{next(self.application_ids)}")
        app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
        pending = self.gate(PERFORMANCE)
        self.power.apply.side_effect = pending
        windows = []
        completed = Event()

        def on_activate(application):
            win = application.get_active_window()
            windows.append(win)
            self.addCleanup(win.destroy)
            original = win._post_result

            def post(callback, *args):
                original(callback, *args)
                if callback == win._finish_power_change:
                    completed.set()

            replace_poster = patch.object(win, "_post_result", side_effect=post)
            replace_poster.start()
            self.addCleanup(replace_poster.stop)

            def begin_write():
                if win._busy:
                    return True
                win.power_combo.set_selected(ALLOWED.index("performance"))
                win._confirm_power_change(None, "apply", "performance")

                def quit_while_pending():
                    if not pending.entered.is_set():
                        return True
                    application.quit()
                    return False

                GLib.timeout_add(10, quit_while_pending)
                return False

            GLib.timeout_add(10, begin_write)

        app.connect_after("activate", on_activate)

        def abort():
            app.quit()
            return False

        fail_safe = GLib.timeout_add_seconds(5, abort)
        self.assertEqual(app.run([]), 0)
        if self.context.find_source_by_id(fail_safe) is not None:
            GLib.source_remove(fail_safe)
        self.assertTrue(pending.entered.is_set())
        win, = windows
        self.assertTrue(win._closed)
        self.assertFalse(completed.is_set())
        self.assertIsNone(self.context.find_source_by_id(win._refresh_source))
        pending.release.set()
        self.assertTrue(completed.wait(4))
        self.assertFalse(win._idle_sources)
        self.assertIn("Requesting Performance", win.power_status.get_text())

    def test_expired_refresh_cannot_restore_controls_or_extend_rgb_deadline(self):
        win = self.window()
        win.rgb_brightness.set_value(100)
        win.power_combo.set_selected(ALLOWED.index("performance"))
        self.assertTrue(win.rgb_apply_hardware.get_sensitive())
        self.assertTrue(win.fan_restore_auto.get_sensitive())
        self.assertTrue(win.power_apply.get_sensitive())

        self.clock = 131
        delayed_probe = self.gate(AVAILABLE)
        self.hardware.probe.side_effect = delayed_probe
        win._request_refresh()
        self.assertTrue(delayed_probe.entered.wait(4))
        self.clock = 145
        win._on_timer()
        self.assertEqual(win._history.samples[-1].observed_at, 145)
        self.assertTrue(win._busy)
        self.assertFalse(win.fan_restore_auto.get_sensitive())
        self.assertFalse(win.power_apply.get_sensitive())
        self.assertFalse(win.rgb_apply_hardware.get_sensitive())

        recovery = self.gate(self.snapshot)
        self.reader.collect.side_effect = recovery
        self.hardware.probe.side_effect = None
        self.clock = 146
        delayed_probe.release.set()
        self.wait_until(recovery.entered.is_set)
        self.assertEqual(win._history.samples[-1].observed_at, 145)
        self.assertIsNone(win._fan_active)
        self.assertIsNone(win._power_state)
        self.assertFalse(win._rgb_status.available)
        self.assertEqual(win._next_rgb_probe, 0)

        recovery.release.set()
        self.wait_until(lambda: not win._busy)
        self.assertTrue(win.fan_restore_auto.get_sensitive())
        self.assertEqual(win._selected_power(), "performance")
        self.assertTrue(win.power_apply.get_sensitive())
        self.assertTrue(win.rgb_apply_hardware.get_sensitive())
        self.assertEqual(win._next_rgb_probe, 176)

    def test_new_telemetry_expiry_marker_does_not_discard_current_domain_reads(self):
        win = self.window()
        win.rgb_brightness.set_value(100)
        self.clock = 131
        delayed_fans = self.gate(Path("/fake/fan-control"))
        delayed_probe = self.gate(AVAILABLE)
        self.find_fans.side_effect = delayed_fans
        self.hardware.probe.side_effect = delayed_probe
        self.power.read.return_value = PERFORMANCE
        win._request_refresh()
        self.assertTrue(delayed_fans.entered.wait(4))

        # Power and RGB start much later than the sensor and fan inspections.
        self.clock = 143
        delayed_fans.release.set()
        self.assertTrue(delayed_probe.entered.wait(4))
        self.clock = 145
        win._on_timer()
        self.assertEqual(win._history.samples[-1].observed_at, 145)
        recovery = self.gate(self.snapshot)
        self.reader.collect.side_effect = recovery
        self.find_fans.side_effect = None
        self.hardware.probe.side_effect = None
        self.clock = 149
        delayed_probe.release.set()
        self.wait_until(recovery.entered.is_set)

        self.assertEqual(win.cpu_value.get_text(), "Unavailable")
        self.assertFalse(win.fan_restore_auto.get_sensitive())
        self.assertEqual(win._power_state, PERFORMANCE)
        self.assertTrue(win.rgb_apply_hardware.get_sensitive())
        self.assertEqual(win._next_rgb_probe, 173)
        self.clock = 150
        recovery.release.set()
        self.wait_until(lambda: not win._busy)
        self.assertTrue(win.fan_restore_auto.get_sensitive())
        self.assertEqual([sample.observed_at for sample in win._history.samples], [100, 145, 149])

    def test_unexpected_fan_and_power_errors_complete_and_next_refresh_recovers(self):
        win = self.window()
        self.clock = 104
        delayed_error = self.gate(RuntimeError("discovery failed"))
        self.find_fans.side_effect = delayed_error
        self.power.read.side_effect = RuntimeError("service failed")
        self.reader.collect.return_value = replace(self.snapshot, cpu_celsius=0)
        win._next_rgb_probe = 0
        win.rgb_brightness.set_value(100)

        with patch.object(win, "_post_result", wraps=win._post_result) as post:
            win._request_refresh()
            self.assertTrue(delayed_error.entered.wait(4))
            self.assertTrue(win._busy)
            delayed_error.release.set()
            self.wait_until(lambda: not win._busy)
            post.assert_called_once()

        self.assertEqual(win.cpu_value.get_text(), "0 °C")
        self.assertIn("discovery failed", win.fan_feedback.get_text())
        self.assertFalse(win.fan_restore_auto.get_sensitive())
        self.assertIn("service failed", win.power_status.get_text())
        self.assertTrue(win.rgb_apply_hardware.get_sensitive())

        self.clock = 108
        self.find_fans.side_effect = None
        self.power.read.side_effect = None
        win._on_timer()
        self.wait_until(lambda: not win._busy)
        self.assertTrue(win.fan_restore_auto.get_sensitive())
        self.assertEqual(win._power_state, BALANCED)
        self.fans.apply.assert_not_called()
        self.power.apply.assert_not_called()
        self.rgb.apply.assert_not_called()

    def test_expired_inspections_block_confirmation_before_the_next_timer(self):
        win = self.window()
        win.rgb_brightness.set_value(100)
        win.power_combo.set_selected(ALLOWED.index("performance"))
        plan = win._rgb_draft()
        self.clock = 142
        win._confirm_power_change(None, "apply", "performance")
        win._request_restore_auto()
        win._confirm_rgb_hardware(None, "apply", plan)
        self.assertFalse(win._write_pending())
        self.assertFalse(win.power_apply.get_sensitive())
        self.assertFalse(win.fan_restore_auto.get_sensitive())
        self.assertFalse(win.rgb_apply_hardware.get_sensitive())
        self.power.apply.assert_not_called()
        self.fans.apply.assert_not_called()
        self.rgb.apply.assert_not_called()

    def test_refresh_thread_start_failure_releases_busy_flag(self):
        win = self.window()
        self.clock = 104
        with patch.object(ui, "Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("thread limit")
            win._request_refresh()
        self.assertFalse(win._busy)
        self.assertIn("thread limit", win.status_label.get_text())

        win._on_timer()
        self.wait_until(lambda: not win._busy)
        self.assertIn("Updated", win.status_label.get_text())

    def test_write_thread_start_failure_preserves_state_and_releases_controls(self):
        for operation in ("fan", "power", "static", "dynamic"):
            with self.subTest(operation=operation):
                win = self.window()
                win.rgb_brightness.set_value(100)
                win.power_combo.set_selected(ALLOWED.index("performance"))
                if operation == "dynamic":
                    win.rgb_modes.set_visible_child_name("dynamic")
                    plan = win._effect_draft()
                    rgb_button = win.rgb_apply_effect
                else:
                    plan = win._rgb_draft()
                    rgb_button = win.rgb_apply_hardware
                self.assertTrue(rgb_button.get_sensitive())
                previous_rgb = (plan, "Earlier software readback verified")
                win._rgb_result = previous_rgb
                revisions = win._fan_revision, win._power_revision, win._rgb_revision
                inspection_times = win._last_fan_observed_at, win._last_power_observed_at, win._next_rgb_probe
                fan_mode = win.fan1_value.get_text()
                firmware = win.overview_firmware_value.get_text()

                self.clock += 4
                read = self.gate(self.snapshot)
                reads_before = self.reader.collect.call_count
                self.reader.collect.side_effect = read
                win._request_refresh()
                try:
                    self.assertTrue(read.entered.wait(4))
                    with patch.object(ui, "Thread") as thread, patch.object(win._toasts, "add_toast") as toast:
                        thread.return_value.start.side_effect = RuntimeError("thread limit")
                        if operation == "fan":
                            win._request_restore_auto()
                            message = win.fan_feedback.get_text()
                        elif operation == "power":
                            win._confirm_power_change(None, "apply", "performance")
                            message = win.power_status.get_text()
                        else:
                            if operation == "dynamic":
                                win._confirm_rgb_effect(None, "apply", plan)
                            else:
                                win._confirm_rgb_hardware(None, "apply", plan)
                            toast.assert_called_once()
                            message = toast.call_args.args[0].get_title()
                        thread.return_value.start.assert_called_once_with()

                    self.assertIn("could not be started. thread limit", message)
                    self.assertFalse(win._write_pending())
                    self.assertIsNone(win._power_pending)
                    self.assertEqual((win._fan_revision, win._power_revision, win._rgb_revision), revisions)
                    self.assertEqual(
                        (win._last_fan_observed_at, win._last_power_observed_at, win._next_rgb_probe),
                        inspection_times,
                    )
                    self.assertEqual(win._fan_active, FanPlan("Auto"))
                    self.assertEqual(win.fan1_value.get_text(), fan_mode)
                    self.assertEqual(win._power_state, BALANCED)
                    self.assertEqual(win._selected_power(), "performance")
                    self.assertEqual(win.overview_firmware_value.get_text(), firmware)
                    self.assertEqual(win._rgb_result, previous_rgb)
                    self.assertTrue(win.fan_restore_auto.get_sensitive())
                    self.assertTrue(win.power_apply.get_sensitive())
                    self.assertTrue(rgb_button.get_sensitive())
                finally:
                    read.release.set()
                    self.reader.collect.side_effect = None
                self.wait_until(lambda window=win: not window._busy)

                self.assertEqual(self.reader.collect.call_count, reads_before + 1)
                self.clock += 4
                win._on_timer()
                self.wait_until(lambda window=win: not window._busy)
                self.assertEqual(self.reader.collect.call_count, reads_before + 2)
                self.assertFalse(win.emit("close-request"))
                self.assertTrue(win._closed)

        self.fans.apply.assert_not_called()
        self.power.apply.assert_not_called()
        self.rgb.apply.assert_not_called()
        self.rgb.apply_effect.assert_not_called()

    def test_stale_metrics_clear_to_a_gap_then_recover(self):
        win = self.window()
        self.assertIn("45", win.cpu_value.get_text())
        self.clock += 4
        stale = self.gate(self.snapshot)
        reads = iter((stale, lambda: replace(self.snapshot, cpu_celsius=0, gpu=None)))
        self.reader.collect.side_effect = lambda: next(reads)()
        win._request_refresh()
        self.assertTrue(stale.entered.wait(4))
        self.clock += ui.STALE_SECONDS
        win._on_timer()
        self.assertEqual(win.cpu_value.get_text(), "Unavailable")
        self.assertIn("unavailable", win.gpu_graph.get_tooltip_text())
        self.clock += 1
        stale.release.set()
        self.wait_until(lambda: not win._busy)
        self.assertEqual(win.cpu_value.get_text(), "0 °C")
        self.assertEqual(win.gpu_value.get_text(), "Unavailable")
        self.clock += 4
        self.reader.collect.side_effect = None
        win._request_refresh()
        self.wait_until(lambda: not win._busy)
        self.assertEqual(win._history.series("gpu_utilization_percent"), (0, None, None, 0))
        self.assertEqual(len(win._history.segments("gpu_utilization_percent")), 2)


if __name__ == "__main__":
    Gtk.init()
    if Gdk.Display.get_default() is None:
        print("GTK display unavailable for private-session integration tests", file=sys.stderr)
        raise SystemExit(77)
    if len(sys.argv) == 3 and sys.argv[1] == "--activate":
        app = ActivationApp(demo=sys.argv[2] == "demo")
        if not app.register(None):
            raise SystemExit(1)
        print("remote" if app.get_is_remote() else "primary", flush=True)
        app.activate()
    else:
        unittest.main(verbosity=2)

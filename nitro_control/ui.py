"""Native GNOME GTK4/Libadwaita dashboard. All hardware polling is off the UI thread."""

from __future__ import annotations

from datetime import datetime
from threading import Thread

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from . import APP_ID, __version__
from .demo import demo_snapshot
from .hardware import HardwareReader
from .models import Snapshot
from .profiles import DISPLAY, PowerProfileController, PowerState
from .rgb import LightingPlan, PRESETS, PreviewBackend
from .rgb_client import RGBClient
from .rgb_hardware import RGBHardware, RGBCapability

REFRESH_SECONDS = 4

CSS = """
window.nitro-window { background: #10141d; }
.metric-card { background: #1b2230; border: 1px solid #303c50; border-radius: 16px; }
.metric-caption { color: #a5b3c8; font-size: 12px; font-weight: 600; }
.metric-value { color: #f5f8ff; font-size: 31px; font-weight: 700; }
.metric-detail { color: #a5b3c8; font-size: 12px; }
.hero-title { color: #f5f8ff; font-size: 27px; font-weight: 750; }
.hero-subtitle { color: #9bacbf; }
.info-note { color: #9bacbf; font-size: 12px; }
.status-ok { color: #8ddcbe; }
.status-alert { color: #eacb8b; }
"""


def _label(text: str = "", *, css: str | None = None) -> Gtk.Label:
    widget = Gtk.Label(label=text, xalign=0)
    widget.set_selectable(False)
    if css:
        widget.add_css_class(css)
    return widget


def _metric_card(title: str, icon: str) -> tuple[Gtk.Widget, Gtk.Label, Gtk.Label]:
    frame = Gtk.Frame()
    frame.add_css_class("metric-card")
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    outer.set_margin_top(17)
    outer.set_margin_bottom(17)
    outer.set_margin_start(19)
    outer.set_margin_end(19)
    heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
    heading.append(Gtk.Image.new_from_icon_name(icon))
    heading.append(_label(title.upper(), css="metric-caption"))
    value = _label("—", css="metric-value")
    hint = _label("Waiting for hardware data", css="metric-detail")
    outer.append(heading)
    outer.append(value)
    outer.append(hint)
    frame.set_child(outer)
    frame.set_hexpand(True)
    return frame, value, hint


def _readout_row(title: str, subtitle: str = "") -> tuple[Adw.ActionRow, Gtk.Label]:
    row = Adw.ActionRow(title=title, subtitle=subtitle)
    row.set_use_markup(False)
    value = _label("—")
    value.set_xalign(1)
    value.add_css_class("numeric")
    value.set_max_width_chars(38)
    value.set_ellipsize(Pango.EllipsizeMode.END)
    row.add_suffix(value)
    return row, value


def _temp(value: float | None) -> str:
    return f"{value:.0f} °C" if value is not None else "Unavailable"


def _number(value: float | None, unit: str) -> str:
    return f"{value:,.0f} {unit}" if value is not None else "Unavailable"


class NitroWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application, *, demo: bool = False):
        super().__init__(application=application, title="Nitro Control", default_width=920, default_height=760)
        self.add_css_class("nitro-window")
        self._demo = demo
        self._reader = HardwareReader()
        self._rgb_preview = PreviewBackend()
        self._rgb_hardware = RGBHardware()
        self._rgb_client = RGBClient()
        self._rgb_status = RGBCapability("none", "Unavailable", False, "Not checked")
        self._rgb_hw_busy = False
        self._rgb_rendering = False
        self._power = PowerProfileController()
        self._power_state: PowerState | None = None
        self._power_choices: tuple[str, ...] = ()
        self._power_busy = False
        self._power_feedback: str | None = None
        self._power_feedback_profile: str | None = None
        self._rendering_power = False
        self._busy = False
        self._closed = False
        self.connect("close-request", self._on_close)
        self._build()
        self._request_refresh()
        GLib.timeout_add_seconds(REFRESH_SECONDS, self._on_timer)

    def _build(self) -> None:
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle.new("Nitro Control", "Fedora hardware dashboard"))
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh.set_tooltip_text("Refresh readings")
        refresh.connect("clicked", lambda *_: self._request_refresh())
        header.pack_end(refresh)
        toolbar.add_top_bar(header)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        clamp = Adw.Clamp(maximum_size=960, tightening_threshold=680)
        scroll.set_child(clamp)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        content.set_margin_top(26)
        content.set_margin_bottom(28)
        content.set_margin_start(22)
        content.set_margin_end(22)
        clamp.set_child(content)
        toolbar.set_content(scroll)
        self.set_content(toolbar)

        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        top.append(_label("Your Nitro, at a glance", css="hero-title"))
        self.model_label = _label("Detecting hardware…", css="hero-subtitle")
        top.append(self.model_label)
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        self.status_icon = Gtk.Image.new_from_icon_name("view-refresh-symbolic")
        self.status_label = _label("Reading sensors…", css="info-note")
        self.status_label.set_wrap(True)
        status_row.append(self.status_icon)
        status_row.append(self.status_label)
        top.append(status_row)
        content.append(top)

        tiles = Gtk.FlowBox()
        tiles.set_selection_mode(Gtk.SelectionMode.NONE)
        tiles.set_min_children_per_line(1)
        tiles.set_max_children_per_line(2)
        tiles.set_column_spacing(12)
        tiles.set_row_spacing(12)
        tiles.set_homogeneous(True)
        for key, title, icon in (
            ("cpu", "CPU package", "cpu-symbolic"),
            ("gpu", "NVIDIA GPU", "video-display-symbolic"),
            ("fan1", "Fan 1", "weather-windy-symbolic"),
            ("fan2", "Fan 2", "weather-windy-symbolic"),
        ):
            card, value, hint = _metric_card(title, icon)
            setattr(self, f"{key}_value", value)
            setattr(self, f"{key}_hint", hint)
            tiles.append(card)
        content.append(tiles)

        self.gpu_group = Adw.PreferencesGroup(title="Graphics", description="NVIDIA readings are optional; the dashboard remains usable without nvidia-smi.")
        self.gpu_rows = {}
        for key, title in (("name", "Device"), ("util", "GPU utilization"),
                           ("memory", "Video memory"), ("power", "Power draw")):
            row, val = _readout_row(title)
            self.gpu_group.add(row)
            self.gpu_rows[key] = val
        content.append(self.gpu_group)

        profile_group = Adw.PreferencesGroup(title="Performance", description="Read-only firmware and TuneD state. Firmware state is read-only.")
        self.profile_rows = {}
        for key, title in (("firmware", "Acer firmware profile"), ("tuned", "TuneD profile")):
            row, val = _readout_row(title)
            profile_group.add(row)
            self.profile_rows[key] = val
        # Long firmware names belong in a wrapping subtitle, not a truncated suffix.
        self.firmware_choices_row = Adw.ActionRow(title="Firmware-supported profiles")
        self.firmware_choices_row.set_subtitle_lines(0)
        self.firmware_choices_row.set_subtitle("Checking firmware capabilities…")
        profile_group.add(self.firmware_choices_row)
        content.append(profile_group)

        power_group = Adw.PreferencesGroup(
            title="Desktop power mode",
            description="Use Fedora's standard power-profile service. These three GNOME modes are not the five raw Acer firmware modes.",
        )
        self.power_combo = Adw.ComboRow(title="GNOME power mode", subtitle="Choose a mode, then apply it")
        self.power_combo.set_model(Gtk.StringList.new(["Unavailable"]))
        self.power_combo.set_sensitive(False)
        self.power_combo.connect("notify::selected", self._on_power_selection)
        power_group.add(self.power_combo)
        apply_row = Adw.ActionRow(title="Apply selected mode", subtitle="Requires confirmation; the system may ask for authorization")
        self.power_apply = Gtk.Button(label="Apply")
        self.power_apply.add_css_class("suggested-action")
        self.power_apply.set_valign(Gtk.Align.CENTER)
        self.power_apply.set_sensitive(False)
        self.power_apply.connect("clicked", self._request_power_change)
        apply_row.add_suffix(self.power_apply)
        power_group.add(apply_row)
        self.power_status = Adw.ActionRow(title="Status", subtitle="Checking power-profile service…")
        self.power_status.set_subtitle_lines(0)
        power_group.add(self.power_status)
        content.append(power_group)

        cooling_group = Adw.PreferencesGroup(title="Cooling", description="Fan readings are passive. Firmware remains in control of the fans.")
        self.fan_rows = []
        for i in range(4):
            row, val = _readout_row(f"Fan {i + 1}")
            cooling_group.add(row)
            self.fan_rows.append((row, val))
        content.append(cooling_group)

        thermal_group = Adw.PreferencesGroup(title="Temperature sensors", description="Unlabeled Acer firmware sensors are not guessed to be CPU or GPU.")
        self.thermal_rows = []
        for i in range(10):
            row, val = _readout_row(f"Sensor {i + 1}")
            thermal_group.add(row)
            self.thermal_rows.append((row, val))
        content.append(thermal_group)

        rgb_group = Adw.PreferencesGroup(title="Keyboard lighting", description="Physical Apply is available only when a compatible RGB driver and authorized helper are present.")
        rgb_row, self.rgb_value = _readout_row("Acer RGB WMI interface")
        self.rgb_row = rgb_row
        rgb_group.add(rgb_row)
        driver_row, self.driver_value = _readout_row("Bound RGB driver")
        rgb_group.add(driver_row)
        self.rgb_capability = Adw.ActionRow(title="Physical lighting", subtitle="Checking for a supported RGB control endpoint…")
        self.rgb_capability.set_subtitle_lines(0)
        rgb_group.add(self.rgb_capability)
        content.append(rgb_group)

        # The preview is in memory; physical Apply follows a separate authorized path.
        editor = Adw.PreferencesGroup(
            title="Four-zone lighting studio",
            description="Edit four colors and brightness. Apply preview is simulated; Apply to keyboard is a separate, authorized hardware operation.",
        )
        self.rgb_preset = Adw.ComboRow(title="Preset", subtitle="Load a starting palette")
        self.rgb_preset.set_model(Gtk.StringList.new(list(PRESETS)))
        editor.add(self.rgb_preset)
        load = Gtk.Button(label="Load preset")
        load.set_valign(Gtk.Align.CENTER)
        load.connect("clicked", self._load_rgb_preset)
        preset_action = Adw.ActionRow(title="Edit colors from preset")
        preset_action.add_suffix(load)
        editor.add(preset_action)
        self.rgb_colors = []
        self.rgb_swatches = []
        for index in range(4):
            row = Adw.ActionRow(title=f"Zone {index + 1}", subtitle="Choose a static color")
            dialog = Gtk.ColorDialog.new()
            dialog.set_with_alpha(False)
            picker = Gtk.ColorDialogButton.new(dialog)
            picker.set_valign(Gtk.Align.CENTER)
            picker.connect("notify::rgba", self._on_rgb_edit)
            swatch = Gtk.Label(xalign=1)
            swatch.set_selectable(False)
            row.add_suffix(swatch)
            row.add_suffix(picker)
            editor.add(row)
            self.rgb_colors.append(picker)
            self.rgb_swatches.append(swatch)
        brightness_row = Adw.ActionRow(title="Brightness", subtitle="0–100% for preview or physical Apply")
        self.rgb_brightness = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 5)
        self.rgb_brightness.set_digits(0)
        self.rgb_brightness.set_size_request(220, -1)
        self.rgb_brightness.set_valign(Gtk.Align.CENTER)
        self.rgb_brightness.connect("value-changed", self._on_rgb_edit)
        brightness_row.add_suffix(self.rgb_brightness)
        editor.add(brightness_row)
        preview_row = Adw.ActionRow(title="Simulated lighting", subtitle="No firmware, sysfs or WMI writes")
        apply_preview = Gtk.Button(label="Apply preview")
        apply_preview.add_css_class("suggested-action")
        apply_preview.set_valign(Gtk.Align.CENTER)
        apply_preview.connect("clicked", self._apply_rgb_preview)
        reset_preview = Gtk.Button(label="Reset")
        reset_preview.set_valign(Gtk.Align.CENTER)
        reset_preview.connect("clicked", self._reset_rgb_preview)
        preview_row.add_suffix(reset_preview)
        preview_row.add_suffix(apply_preview)
        editor.add(preview_row)
        self.rgb_preview_status = Adw.ActionRow(title="Preview status", subtitle="Nothing sent to the keyboard")
        self.rgb_preview_status.set_subtitle_lines(0)
        editor.add(self.rgb_preview_status)
        physical_row = Adw.ActionRow(
            title="Physical keyboard", subtitle="Optional root-owned helper; changes require separate confirmation and authentication"
        )
        self.rgb_apply_hardware = Gtk.Button(label="Apply to keyboard")
        self.rgb_apply_hardware.set_valign(Gtk.Align.CENTER)
        self.rgb_apply_hardware.set_sensitive(False)
        self.rgb_apply_hardware.connect("clicked", self._request_rgb_hardware)
        physical_row.add_suffix(self.rgb_apply_hardware)
        editor.add(physical_row)
        self.rgb_hardware_status = Adw.ActionRow(
            title="Hardware status", subtitle="Checking for a supported RGB writer…"
        )
        self.rgb_hardware_status.set_subtitle_lines(0)
        editor.add(self.rgb_hardware_status)
        content.append(editor)
        self._set_rgb_editor(self._rgb_preview.current())

        note = _label("Fans remain read-only  •  Preview is simulated; physical Apply uses a detected driver and administrator-authorized helper  •  Nitro Control v" + __version__, css="info-note")
        note.set_wrap(True)
        content.append(note)

    def _on_close(self, *_args) -> bool:
        self._closed = True
        return False

    def _on_timer(self) -> bool:
        if self._closed:
            return False
        self._request_refresh()
        return True

    def _request_refresh(self) -> None:
        if self._busy or self._closed or self._power_busy:
            return
        self._busy = True
        self.status_label.set_text("Reading sensors…" if not self._demo else "DEMO MODE • sample data")
        Thread(target=self._collect_background, daemon=True, name="nitro-hardware-reader").start()

    def _collect_background(self) -> None:
        try:
            result = demo_snapshot() if self._demo else self._reader.collect()
            error = None
        except Exception as exc:  # A sensor failure must not terminate the GUI.
            result = None
            error = f"Unable to refresh: {type(exc).__name__}: {exc}"
        try:
            state = PowerState("balanced", ("power-saver", "balanced", "performance")) if self._demo else self._power.read()
            power_error = None
        except Exception as exc:
            state = None
            power_error = str(exc)
        GLib.idle_add(self._finish_refresh, result, error, state, power_error)

    def _finish_refresh(self, snapshot: Snapshot | None, error: str | None,
                        state: PowerState | None, power_error: str | None) -> bool:
        self._busy = False
        if self._closed:
            return False
        if error:
            self.status_label.set_text(error)
            self.status_icon.set_from_icon_name("dialog-warning-symbolic")
        elif snapshot:
            self._render(snapshot)
        self._render_power(state, power_error)
        return False

    def _selected_power(self) -> str | None:
        index = self.power_combo.get_selected()
        return self._power_choices[index] if 0 <= index < len(self._power_choices) else None

    def _on_power_selection(self, *_args) -> None:
        if not self._rendering_power:
            self._power_feedback = None
            self._power_feedback_profile = None
        self._sync_power_button()

    def _sync_power_button(self) -> None:
        chosen = self._selected_power()
        self.power_apply.set_sensitive(
            not self._demo and not self._power_busy and self._power_state is not None
            and chosen is not None and chosen != self._power_state.active
        )

    def _render_power(self, state: PowerState | None, error: str | None) -> None:
        prior = self._selected_power()
        old_active = self._power_state.active if self._power_state else None
        self._power_state = state
        self._rendering_power = True
        try:
            if state is None:
                self._power_choices = ()
                self.power_combo.set_model(Gtk.StringList.new(["Unavailable"]))
                self.power_combo.set_sensitive(False)
                self.power_status.set_subtitle(error or "Power-profile service unavailable")
            else:
                if self._power_feedback_profile and state.active != self._power_feedback_profile:
                    self._power_feedback = None
                    self._power_feedback_profile = None
                if state.choices != self._power_choices:
                    self._power_choices = state.choices
                    self.power_combo.set_model(Gtk.StringList.new([DISPLAY[p] for p in state.choices]))
                # Keep a deliberate uncommitted selection through background refreshes.
                chosen = prior if prior in state.choices and prior != old_active else state.active
                self.power_combo.set_selected(state.choices.index(chosen))
                self.power_combo.set_sensitive(not self._demo and not self._power_busy)
                self.power_status.set_subtitle(
                    "Demo only — power changes disabled" if self._demo else
                    (self._power_feedback or f"System reports {DISPLAY[state.active]} active")
                )
        finally:
            self._rendering_power = False
        self._sync_power_button()

    def _request_power_change(self, *_args) -> None:
        chosen = self._selected_power()
        if (self._demo or self._power_busy or self._power_state is None or
                chosen is None or chosen == self._power_state.active):
            return
        dialog = Adw.AlertDialog.new(
            f"Switch to {DISPLAY[chosen]}?",
            "Fedora's power-profile service will handle the change. "
            "Fan speeds and RGB will remain under their existing drivers."
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("apply", "Apply")
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._confirm_power_change, chosen)
        dialog.present(self)

    def _confirm_power_change(self, _dialog, response: str, chosen: str) -> None:
        if response != "apply" or self._closed or self._power_busy:
            return
        self._power_busy = True
        self.power_combo.set_sensitive(False)
        self._sync_power_button()
        self.power_status.set_subtitle(f"Requesting {DISPLAY[chosen]}…")
        Thread(target=self._apply_power_background, args=(chosen,), daemon=True,
               name="nitro-power-mode").start()

    def _apply_power_background(self, chosen: str) -> None:
        try:
            state = self._power.apply(chosen)
            error = None
        except Exception as exc:
            state = None
            error = str(exc)
        GLib.idle_add(self._finish_power_change, state, error)

    def _finish_power_change(self, state: PowerState | None, error: str | None) -> bool:
        self._power_busy = False
        if self._closed:
            return False
        self._power_feedback = (f"Verified: {DISPLAY[state.active]} is active" if state else
                                f"Power mode unchanged or unverified: {error}")
        self._power_feedback_profile = state.active if state else None
        self._render_power(state or self._power_state, None)
        self._request_refresh()
        return False

    def _render(self, data: Snapshot) -> None:
        self.model_label.set_text(data.model)
        try:
            sampled = datetime.fromisoformat(data.sampled_at).strftime("%H:%M:%S")
        except ValueError:
            sampled = data.sampled_at
        self.status_label.set_text(("DEMO • " if data.demo else "Live • ") + f"Updated {sampled} • sensors read-only")
        self.status_icon.set_from_icon_name("media-playback-start-symbolic" if data.demo else "emblem-ok-symbolic")
        self.cpu_value.set_text(_temp(data.cpu_celsius))
        self.cpu_hint.set_text("Identified CPU package sensor" if data.cpu_celsius is not None else "No identified CPU package sensor")
        self.gpu_value.set_text(_temp(data.gpu.celsius) if data.gpu else "Unavailable")
        self.gpu_hint.set_text(data.gpu.name if data.gpu else "nvidia-smi unavailable or GPU not detected")
        for index in (0, 1):
            value = getattr(self, f"fan{index + 1}_value")
            hint = getattr(self, f"fan{index + 1}_hint")
            if index < len(data.fans):
                fan = data.fans[index]
                value.set_text(_number(fan.rpm, "RPM"))
                hint.set_text(fan.control_mode + " control")
            else:
                value.set_text("Unavailable")
                hint.set_text("No fan reading")

        if data.gpu:
            gpu = data.gpu
            self.gpu_rows["name"].set_text(gpu.name)
            self.gpu_rows["util"].set_text(_number(gpu.utilization_percent, "%"))
            self.gpu_rows["memory"].set_text(
                f"{gpu.memory_used_mib:,.0f} / {gpu.memory_total_mib:,.0f} MiB"
                if gpu.memory_used_mib is not None and gpu.memory_total_mib is not None else "Unavailable"
            )
            self.gpu_rows["power"].set_text(f"{gpu.power_watts:.1f} W" if gpu.power_watts is not None else "Unavailable")
        else:
            for val in self.gpu_rows.values():
                val.set_text("Unavailable")

        self.profile_rows["firmware"].set_text(data.firmware_profile or "Unavailable")
        choices = "  •  ".join(data.firmware_profile_choices) or "Unavailable"
        self.firmware_choices_row.set_subtitle(choices)
        self.profile_rows["tuned"].set_text(data.tuned_profile or "Unavailable")
        self._update_sensor_rows(self.fan_rows, [
            (fan.name, fan.control_mode + " control", _number(fan.rpm, "RPM")) for fan in data.fans
        ])
        self._update_sensor_rows(self.thermal_rows, [
            (sensor.name, sensor.source, _temp(sensor.celsius))
            for sensor in (*data.acer_temperatures, *data.drives)
        ])
        self.rgb_value.set_text("Detected" if data.rgb.present else "Not detected")
        self.rgb_row.set_subtitle(data.rgb.interface or "No matching WMI instance exposed")
        self.driver_value.set_text(data.rgb.driver or "None bound")
        self._rgb_status = self._rgb_hardware.probe() if not self._demo else RGBCapability(
            "none", "Demo only", False, "Demo mode cannot make physical keyboard changes"
        )
        self.rgb_capability.set_subtitle(self._rgb_status.detail)
        if not self._rgb_status.available:
            hw_detail = self._rgb_status.detail
        elif not self._rgb_client.ready():
            hw_detail = (f"{self._rgb_status.description} detected; optional root-owned helper not installed. "
                         "See README for explicit installation.")
        else:
            hw_detail = f"{self._rgb_status.description} available; Apply requires confirmation and authentication"
        if not self._rgb_hw_busy:
            self.rgb_hardware_status.set_subtitle(hw_detail)
        self._sync_rgb_hardware_button()

    def _set_rgb_editor(self, plan: LightingPlan) -> None:
        self._rgb_rendering = True
        try:
            for picker, color in zip(self.rgb_colors, plan.zones):
                rgba = Gdk.RGBA()
                if not rgba.parse(color):
                    raise ValueError("Invalid RGB plan")
                picker.set_rgba(rgba)
            self.rgb_brightness.set_value(plan.brightness)
        finally:
            self._rgb_rendering = False
        self._on_rgb_edit()

    def _rgb_draft(self) -> LightingPlan:
        values = []
        for picker in self.rgb_colors:
            rgba = picker.get_rgba()
            components = [round(max(0.0, min(1.0, channel)) * 255)
                          for channel in (rgba.red, rgba.green, rgba.blue)]
            values.append("#" + "".join(f"{channel:02X}" for channel in components))
        return LightingPlan(tuple(values), round(self.rgb_brightness.get_value()))

    def _on_rgb_edit(self, *_args) -> None:
        if self._rgb_rendering:
            return
        plan = self._rgb_draft()
        for label, color in zip(self.rgb_swatches, plan.zones):
            label.set_markup(f'<span foreground="{color}">●</span>  {color}')
        self.rgb_preview_status.set_subtitle(
            f"Draft: {plan.brightness}% brightness • not applied to hardware"
        )
        self._sync_rgb_hardware_button()

    def _load_rgb_preset(self, *_args) -> None:
        names = list(PRESETS)
        index = self.rgb_preset.get_selected()
        if 0 <= index < len(names):
            self._set_rgb_editor(PRESETS[names[index]])

    def _apply_rgb_preview(self, *_args) -> None:
        plan = self._rgb_preview.apply(self._rgb_draft())
        self.rgb_preview_status.set_subtitle(
            f"Simulated only: {plan.brightness}% • {', '.join(plan.zones)}. No keyboard writes."
        )

    def _reset_rgb_preview(self, *_args) -> None:
        self._set_rgb_editor(self._rgb_preview.reset())
        self.rgb_preview_status.set_subtitle("Preview reset to Midnight; hardware unchanged")

    def _sync_rgb_hardware_button(self) -> None:
        self.rgb_apply_hardware.set_sensitive(
            not self._demo and not self._rgb_hw_busy and
            self._rgb_status.available and self._rgb_client.ready()
        )

    def _request_rgb_hardware(self, *_args) -> None:
        if self._demo or self._rgb_hw_busy or not self._rgb_status.available or not self._rgb_client.ready():
            return
        plan = self._rgb_draft()
        dialog = Adw.AlertDialog.new(
            "Apply these colors to your physical keyboard?",
            "This makes an actual hardware change through the detected RGB driver. "
            "Administrator authentication may be required. Sysfs readback does not prove the LEDs changed: "
            "visually inspect the keyboard afterward. No fans or power settings will be changed."
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("apply", "Apply to keyboard")
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._confirm_rgb_hardware, plan)
        dialog.present(self)

    def _confirm_rgb_hardware(self, _dialog, response: str, plan: LightingPlan) -> None:
        if response != "apply" or self._closed or self._rgb_hw_busy:
            return
        self._rgb_hw_busy = True
        self._sync_rgb_hardware_button()
        self.rgb_hardware_status.set_subtitle("Authorizing one keyboard RGB change…")
        Thread(target=self._apply_rgb_hardware_background, args=(plan,), daemon=True,
               name="nitro-rgb-apply").start()

    def _apply_rgb_hardware_background(self, plan: LightingPlan) -> None:
        try:
            message = self._rgb_client.apply(plan)
            error = None
        except Exception as exc:
            message, error = None, str(exc)
        GLib.idle_add(self._finish_rgb_hardware, message, error)

    def _finish_rgb_hardware(self, message: str | None, error: str | None) -> bool:
        self._rgb_hw_busy = False
        if self._closed:
            return False
        self.rgb_hardware_status.set_subtitle(
            f"Hardware change failed or unverified: {error}" if error else message or "No verified sysfs response"
        )
        self._rgb_status = self._rgb_hardware.probe()
        self._sync_rgb_hardware_button()
        return False

    @staticmethod
    def _update_sensor_rows(rows: list[tuple[Adw.ActionRow, Gtk.Label]],
                            entries: list[tuple[str, str, str]]) -> None:
        for index, (row, value) in enumerate(rows):
            visible = index < len(entries)
            row.set_visible(visible)
            if visible:
                title, subtitle, reading = entries[index]
                row.set_title(title)
                row.set_subtitle(subtitle)
                value.set_text(reading)


class NitroApplication(Adw.Application):
    def __init__(self, *, demo: bool):
        super().__init__(application_id=APP_ID)
        self.demo = demo

    def do_activate(self) -> None:
        window = self.get_active_window()
        if window is None:
            self.get_style_manager().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
            display = Gdk.Display.get_default()
            if display is not None:
                provider = Gtk.CssProvider()
                provider.load_from_data(CSS.encode("utf-8"))
                Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            window = NitroWindow(self, demo=self.demo)
        window.present()


def run_ui(*, demo: bool) -> int:
    return NitroApplication(demo=demo).run([])

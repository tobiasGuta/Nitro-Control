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

        profile_group = Adw.PreferencesGroup(title="Performance", description="Read-only firmware and TuneD state. No profile switching in v0.1.")
        self.profile_rows = {}
        for key, title in (("firmware", "Acer firmware profile"), ("choices", "Available profiles"),
                           ("tuned", "TuneD profile")):
            row, val = _readout_row(title)
            if key == "choices":
                row.set_subtitle("Profiles exposed by your system firmware")
            profile_group.add(row)
            self.profile_rows[key] = val
        content.append(profile_group)

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

        rgb_group = Adw.PreferencesGroup(title="Keyboard lighting", description="Capability detection only. RGB controls are reserved for a later, validated version.")
        rgb_row, self.rgb_value = _readout_row("Acer RGB WMI interface")
        self.rgb_row = rgb_row
        rgb_group.add(rgb_row)
        driver_row, self.driver_value = _readout_row("Bound RGB driver")
        rgb_group.add(driver_row)
        content.append(rgb_group)

        note = _label("READ-ONLY  •  No fan, firmware, RGB, or kernel writes  •  Nitro Control v" + __version__, css="info-note")
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
        if self._busy or self._closed:
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
        GLib.idle_add(self._finish_refresh, result, error)

    def _finish_refresh(self, snapshot: Snapshot | None, error: str | None) -> bool:
        self._busy = False
        if self._closed:
            return False
        if error:
            self.status_label.set_text(error)
            self.status_icon.set_from_icon_name("dialog-warning-symbolic")
        elif snapshot:
            self._render(snapshot)
        return False

    def _render(self, data: Snapshot) -> None:
        self.model_label.set_text(data.model)
        try:
            sampled = datetime.fromisoformat(data.sampled_at).strftime("%H:%M:%S")
        except ValueError:
            sampled = data.sampled_at
        self.status_label.set_text(("DEMO • " if data.demo else "Live • ") + f"Updated {sampled} • read-only")
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
        choices = ", ".join(data.firmware_profile_choices) or "Unavailable"
        self.profile_rows["choices"].set_text(choices)
        self.profile_rows["choices"].set_tooltip_text(choices)
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

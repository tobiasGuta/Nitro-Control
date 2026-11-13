"""Native GNOME GTK4/Libadwaita dashboard. All hardware polling is off the UI thread."""

from __future__ import annotations

from datetime import datetime
from threading import Thread

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from . import APP_ID, __version__
from .demo import demo_snapshot
from .hardware import HardwareReader
from .fan_client import FanClient
from .fan_control import MIN_PERCENT, MODES, FanPlan, find_fan_controls, read_fan_plan
from .fan_presentation import fan_display_name, fan_mode_description
from .monitor_history import MonitorHistory
from .dashboard_visuals import FanDial, KeyboardPreview, TelemetryGraph, section
from .keyboard_preview import COLOR_EFFECTS, EFFECTS
from .models import Snapshot
from .native_rgb import NativeRGB
from .profiles import DISPLAY, PowerProfileController, PowerState
from .rgb import EffectPlan, LightingPlan, PHYSICAL_EFFECT_MODES, PRESETS
from .rgb_client import RGBClient
from .rgb_hardware import RGBHardware, RGBCapability
from .tray_preferences import load_keep_in_tray, save_keep_in_tray, start_tray_process

REFRESH_SECONDS = 4

CSS = """
/* Original Nitro-Control interpretation of the supplied NitroSense reference.
   All dashboard indicators represent real data. */
window.nitro-window { background: #09090d; color: #eeeeef; }
headerbar { background: #15151a; border-bottom: 1px solid #3a2026; }
.nitro-brand { color: #f0f0f0; font-size: 20px; font-weight: 800; letter-spacing: 5px; }
.nitro-section {
    background-color: #101013;
    /* Repeated 4px steel/scanline shading layered above graphite. These
       are decorative CSS backgrounds, not any hardware measurement. */
    background-image:
        linear-gradient(to bottom, rgba(255,255,255,0.026), transparent 55%, rgba(0,0,0,0.07)),
        linear-gradient(to bottom, #252529, #111115 14%, #080809 67%, #101014);
    background-size: 100% 4px, 100% 100%;
    background-repeat: repeat-y, no-repeat;
    border: 1px solid #555358;
    border-radius: 6px 7px 18px 18px;
    box-shadow: inset 0 1px rgba(255,255,255,0.16), 0 4px 15px rgba(0,0,0,0.45);
    padding: 0px 5px 7px;
}
.metric-card {
    background: #141419;
    border: 1px solid #333339;
    border-radius: 6px;
}
.metric-caption { color: #afafb3; font-size: 11px; font-weight: 700; }
.metric-value { color: #f7f7f7; font-size: 25px; font-weight: 800; }
.metric-detail { color: #88888e; font-size: 11px; }
.hero-title { color: #e5e5e8; font-size: 17px; font-weight: 700; letter-spacing: 2px; }
.hero-subtitle { color: #a5a5ad; font-size: 12px; }
.info-note { color: #aaaab1; font-size: 11px; }
.status-ok { color: #b6d5bf; }
.status-alert { color: #f0a36d; }
.nitro-power-picker { background: #222226; border-radius: 5px; padding: 8px; }
.nitro-power-status { color: #a9a9b2; font-size: 12px; }
.nitro-panel-hint { color: #b5b5bc; font-size: 12px; }
.nitro-divider { background: #38383e; min-height: 1px; }
.nitro-telemetry { color: #ef3b48; font-weight: 750; font-size: 21px; }
.nitro-fan-mode { color: #f04450; font-size: 16px; font-weight: 750; }
.nitro-fan-mode.nitro-fan-muted { color: #a7a7ae; font-size: 14px; }
.nitro-fan-status { background: rgba(43,43,48,0.30); border-bottom: 1px solid #35353a; padding: 7px 9px; }
.nitro-panel-value { color: #e9e9ea; font-size: 14px; font-weight: 650; }
.nitro-section-note { color: #b2b2b8; font-size: 12px; }
.nitro-safety-hint { color: #ffba7d; font-size: 12px; font-weight: 700; }
.nitro-panel-caption { color: #cfced3; font-size: 12px; font-weight: 750; letter-spacing: 1px; }
.nitro-panel-group { margin: 0; }
.nitro-panel-group row { background-color: #19191e; }
.nitro-panel-group row:hover { background-color: #222228; }

.nitro-readonly { color: #8e8e95; font-size: 11px; font-weight: 600; }
.nitro-accent { color: #eb3546; font-size: 12px; font-weight: 700; }
stack { background: #09090d; }
stackswitcher button:checked { color: #ef4654; }
.nitro-rgb-zones > box {
    background-color: #18181e;
    border: 1px solid #35353e;
    border-radius: 6px;
    padding: 7px 8px;
}
.nitro-rgb-zones switch:checked { background-color: #a6202d; }
.nitro-rgb-modes { background: transparent; }
.nitro-rgb-modes button { min-width: 80px; }
button.suggested-action { background: #a6202d; color: #fff; }
button.suggested-action:disabled {
    background: #28282d;
    color: #777780;
    box-shadow: none;
}
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


def _panel_group(title: str, description: str,
                 group: Adw.PreferencesGroup) -> Gtk.Widget:
    """Present existing Libadwaita rows inside the dashboard's steel panel.

    The wrapped rows preserve their original widgets, signals and permissions.
    Move titles/descriptions into the panel shell to avoid doubled headings.
    """
    group.set_title("")
    group.set_description("")
    group.add_css_class("nitro-panel-group")
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=11)
    if description:
        note = _label(description, css="nitro-section-note")
        note.set_wrap(True)
        body.append(note)
    body.append(group)
    return section(title, body)


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
        super().__init__(application=application, title="Nitro Control", default_width=1260, default_height=850)
        self.add_css_class("nitro-window")
        self._demo = demo
        self._reader = HardwareReader()
        self._fan_client = FanClient()
        self._fan_available = False
        self._fan_active: FanPlan | None = None
        self._fan_busy = False
        self._fan_editing = False
        self._fan_rendering = False
        self._fan_revision = 0
        self._fan_feedback: str | None = None
        self._history = MonitorHistory()
        self._rgb_hardware = RGBHardware()
        self._rgb_client = RGBClient()
        self._rgb_status = RGBCapability("none", "Unavailable", False, "Not checked")
        self._rgb_hw_busy = False
        self._rgb_effect_result = False
        self._effects_available = not demo and NativeRGB().effects_supported()
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
        self._tray_setting_updating = False
        self._keep_in_tray = load_keep_in_tray()
        self.connect("close-request", self._on_close)
        self._build()
        # Never open live hardware monitoring in a --demo session.
        if self._keep_in_tray and not self._demo:
            self._start_tray()
        self._request_refresh()
        self._fan_last_tick_us = GLib.get_monotonic_time()
        # ~30 FPS is solely for decorative Cairo drawing; actual sensor RPM
        # is still read on the separate four-second hardware refresh cycle.
        GLib.timeout_add(33, self._on_fan_animation_tick)
        GLib.timeout_add_seconds(REFRESH_SECONDS, self._on_timer)

    def _build(self) -> None:
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        title = _label("NITRO CONTROL", css="nitro-brand")
        title.set_xalign(0.5)
        header.set_title_widget(title)
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh.set_tooltip_text("Refresh real sensor readings")
        refresh.connect("clicked", lambda *_: self._request_refresh())
        header.pack_end(refresh)

        # The tray preference is intentionally visible but off by default.
        # The GTK3 indicator always runs in another Python process.
        settings_button = Gtk.MenuButton()
        settings_button.set_icon_name("preferences-system-symbolic")
        settings_button.set_tooltip_text("Nitro Control preferences")
        settings_popup = Gtk.Popover()
        settings_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        settings_box.set_margin_top(12)
        settings_box.set_margin_bottom(12)
        settings_box.set_margin_start(12)
        settings_box.set_margin_end(12)
        settings_box.set_size_request(340, -1)
        tray_group = Adw.PreferencesGroup(title="System tray")
        self.tray_switch = Adw.SwitchRow(
            title="Keep Nitro-Control in the system tray",
            subtitle="Start the tray when opening the dashboard; it stays after closing",
        )
        self.tray_switch.set_active(self._keep_in_tray)
        self.tray_switch.set_sensitive(not self._demo)
        tray_group.add(self.tray_switch)
        settings_box.append(tray_group)
        self.tray_feedback = _label(
            "Off by default. Requires GNOME AppIndicator support and "
            "the optional GTK3 Ayatana package." if not self._keep_in_tray else
            "Enabled. Quit tray from its menu to stop background monitoring.",
            css="nitro-section-note",
        )
        self.tray_feedback.set_wrap(True)
        settings_box.append(self.tray_feedback)
        if self._demo:
            self.tray_feedback.set_text("Demo mode never starts live tray monitoring.")
        self.tray_switch.connect("notify::active", self._on_tray_setting_changed)
        settings_popup.set_child(settings_box)
        settings_button.set_popover(settings_popup)
        header.pack_end(settings_button)

        # Keep the existing controls, with their original permission checks,
        # on separate pages. The reference-inspired landing page is monitoring.
        stack = Gtk.Stack()
        stack.set_vexpand(True)
        stack.set_vhomogeneous(False)
        self._page_stack = stack
        switcher = Gtk.StackSwitcher()
        switcher.set_stack(stack)
        header.pack_start(switcher)
        toolbar.add_top_bar(header)
        toolbar.set_content(stack)
        self.set_content(toolbar)

        def page(key: str, title: str) -> Gtk.Box:
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_hexpand(True)
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
            inner.set_hexpand(True)
            inner.set_halign(Gtk.Align.FILL)
            for side in ("top", "bottom", "start", "end"):
                getattr(inner, f"set_margin_{side}")(20)
            if key == "dashboard":
                # Let the dashboard content grow to fill spare *vertical*
                # space as well as horizontal space. Its graph panels take
                # extra height; the status header and fan dials stay compact.
                inner.set_vexpand(True)
                inner.set_valign(Gtk.Align.FILL)
                # A max-width Adw.Clamp centered the dashboard on a maximized
                # desktop and left wide unused black gutters at both edges.
                # The dashboard's fixed-width left status column and expanding
                # monitoring pane can fill the actual available viewport.
                scroll.set_child(inner)
            else:
                # Long settings and RGB edit rows remain comfortably readable.
                clamp = Adw.Clamp(maximum_size=1100, tightening_threshold=750)
                clamp.set_hexpand(True)
                clamp.set_child(inner)
                scroll.set_child(clamp)
            stack.add_titled(scroll, key, title)
            return inner

        content = page("dashboard", "Dashboard")
        hardware_content = page("hardware", "Hardware details")
        lighting_content = page("lighting", "Keyboard RGB")
        stack.set_visible_child_name("dashboard")

        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        top.append(_label("SYSTEM MONITORING", css="hero-title"))
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

        fans = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        fans.set_hexpand(True)
        fan_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        fan_info.set_size_request(245, -1)
        fan_info.append(_label("FAN STATUS", css="nitro-accent"))
        # Show the *reported* fan modes here. The large dial is the sole
        # numerical RPM readout; repeating it at the left obscured the
        # reference's control/status hierarchy.
        self.fan1_value = _label("—", css="nitro-fan-mode")
        self.fan1_hint = _label("Waiting for Fan 1", css="metric-detail")
        self.fan2_value = _label("—", css="nitro-fan-mode")
        self.fan2_hint = _label("Waiting for Fan 2", css="metric-detail")
        self.fan_mode_titles = []
        for name, value, hint in (
            ("FAN 1 · MODE", self.fan1_value, self.fan1_hint),
            ("FAN 2 · MODE", self.fan2_value, self.fan2_hint),
        ):
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            row.add_css_class("nitro-fan-status")
            mode_title = _label(name, css="metric-caption")
            self.fan_mode_titles.append(mode_title)
            row.append(mode_title)
            row.append(value)
            hint.set_wrap(True)
            row.append(hint)
            fan_info.append(row)
        fan_info.append(self._build_fan_controls())
        fans.append(fan_info)
        self.fan_dials = (FanDial("Fan 1"), FanDial("Fan 2"))
        for dial, slider in zip(self.fan_dials, self.fan_custom):
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            column.set_hexpand(True)
            column.append(dial)
            column.append(slider)
            fans.append(column)
        content.append(section("Fan monitoring", fans, min_height=285))

        lower = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        lower.set_valign(Gtk.Align.FILL)
        lower.set_hexpand(True)
        lower.set_vexpand(True)
        content.append(lower)

        # A compact left column is important to match the reference: broad
        # monitoring graphs belong on the RIGHT. Adw.PreferencesGroup's long
        # descriptions previously forced this column to more than half width.
        self.power_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.power_panel.set_size_request(268, -1)
        power_section = section("Power mode", self.power_panel, fill_height=True)
        power_section.set_hexpand(False)
        lower.append(power_section)

        monitors = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        monitors.set_hexpand(True)
        monitors.set_vexpand(True)
        monitors.set_size_request(470, -1)
        # The reference uses the graphs as the dominant elements, not
        # oversized dashboard metric tiles. Keep readable values in a slim
        # header strip; the full series remains in the two large charts.
        metric_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=25)
        for name, title in (("cpu", "CPU PACKAGE"), ("gpu", "NVIDIA GPU")):
            metric = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            metric.set_hexpand(True)
            metric.append(_label(title, css="metric-caption"))
            value = _label("—", css="nitro-telemetry")
            hint = _label("Waiting for sensors", css="metric-detail")
            hint.set_ellipsize(Pango.EllipsizeMode.END)
            metric.append(value)
            metric.append(hint)
            metric_row.append(metric)
            setattr(self, f"{name}_value", value)
            setattr(self, f"{name}_hint", hint)
        monitors.append(metric_row)
        self.cpu_graph = TelemetryGraph("CPU · package temperature", "cpu_celsius")
        self.gpu_graph = TelemetryGraph(
            "GPU · temperature / utilization", "gpu_celsius", "gpu_utilization_percent")
        self.cpu_graph.set_vexpand(True)
        self.gpu_graph.set_vexpand(True)
        monitors.append(self.cpu_graph)
        monitors.append(self.gpu_graph)
        monitoring_section = section("Live monitoring", monitors, fill_height=True)
        monitoring_section.set_hexpand(True)
        lower.append(monitoring_section)

        footer = _label(
            "Real hardware readings only  •  "
            "Power mode changes require confirmation  •  Nitro Control v" + __version__,
            css="info-note")
        footer.set_wrap(True)
        content.append(footer)

        hardware_content.append(_label("HARDWARE DETAILS", css="hero-title"))
        hardware_content.append(_label(
            "Real telemetry only · unidentified sensors stay unidentified",
            css="hero-subtitle"))
        lighting_content.append(_label("KEYBOARD LIGHTING", css="hero-title"))
        lighting_content.append(_label(
            "Preview updates as you edit. Apply to keyboard sends your Static colors.",
            css="hero-subtitle"))

        self.gpu_group = Adw.PreferencesGroup(title="Graphics", description="NVIDIA readings are optional; the dashboard remains usable without nvidia-smi.")
        self.gpu_rows = {}
        for key, title in (("name", "Device"), ("util", "GPU utilization"),
                           ("memory", "Video memory"), ("power", "Power draw")):
            row, val = _readout_row(title)
            self.gpu_group.add(row)
            self.gpu_rows[key] = val
        hardware_content.append(_panel_group(
            "Graphics", "Optional NVIDIA information, read-only. Missing GPU data is not estimated.",
            self.gpu_group))

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
        hardware_content.append(_panel_group(
            "Performance", "Acer firmware and TuneD state are read-only; no raw firmware-mode controls.",
            profile_group))

        # These are the SAME advertised GNOME profile options and the SAME
        # explicit apply/confirm path. Only the wide Adw row is replaced with
        # a compact native GTK selector and status label.
        self.power_panel.append(_label("DESKTOP POWER PROFILE", css="nitro-accent"))
        self.power_panel.append(_label(
            "Only modes offered by Fedora's power-profile service.",
            css="nitro-panel-hint"))
        power_picker = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        power_picker.add_css_class("nitro-power-picker")
        power_picker.append(_label("Select system profile", css="metric-caption"))
        self.power_combo = Gtk.DropDown.new_from_strings(["Unavailable"])
        self.power_combo.set_hexpand(True)
        self.power_combo.set_sensitive(False)
        self.power_combo.connect("notify::selected", self._on_power_selection)
        power_picker.append(self.power_combo)
        self.power_apply = Gtk.Button(label="Apply selected mode")
        self.power_apply.add_css_class("suggested-action")
        self.power_apply.set_sensitive(False)
        self.power_apply.connect("clicked", self._request_power_change)
        power_picker.append(self.power_apply)
        self.power_panel.append(power_picker)
        self.power_panel.append(_label("STATUS", css="metric-caption"))
        self.power_status = _label(
            "Checking power-profile service…", css="nitro-power-status")
        self.power_status.set_wrap(True)
        self.power_status.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.power_status.set_max_width_chars(32)
        self.power_panel.append(self.power_status)
        self.power_panel.append(_label(
            "Fan RPM: read-only · Acer firmware profiles: read-only",
            css="nitro-readonly"))
        # Real, read-only values fill the panel's previously blank lower area.
        # Neither the firmware profile nor TuneD is changed by this UI.
        self.power_panel.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        self.power_panel.append(_label("ACER FIRMWARE PROFILE", css="metric-caption"))
        self.overview_firmware_value = _label("Checking…", css="nitro-panel-value")
        self.overview_firmware_value.set_wrap(True)
        self.power_panel.append(self.overview_firmware_value)
        self.power_panel.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        self.power_panel.append(_label("TUNED PROFILE", css="metric-caption"))
        self.overview_tuned_value = _label("Checking…", css="nitro-panel-value")
        self.overview_tuned_value.set_wrap(True)
        self.power_panel.append(self.overview_tuned_value)

        cooling_group = Adw.PreferencesGroup(title="Cooling")
        self.fan_rows = []
        for i in range(4):
            row, val = _readout_row(f"Fan {i + 1}")
            cooling_group.add(row)
            self.fan_rows.append((row, val))
        hardware_content.append(_panel_group(
            "Cooling", "Live fan speed and control mode.",
            cooling_group))

        thermal_group = Adw.PreferencesGroup(title="Temperature sensors", description="Unlabeled Acer firmware sensors are not guessed to be CPU or GPU.")
        self.thermal_rows = []
        for i in range(10):
            row, val = _readout_row(f"Sensor {i + 1}")
            thermal_group.add(row)
            self.thermal_rows.append((row, val))
        hardware_content.append(_panel_group(
            "Temperature sensors", "Unknown Acer sensors remain unidentified; drive temperatures use reported labels.",
            thermal_group))

        self._build_rgb(lighting_content)

    def _build_rgb(self, content: Gtk.Box) -> None:
        preview_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        self.rgb_modes = Gtk.Stack()
        self.rgb_modes.set_vhomogeneous(False)
        self.rgb_modes.set_hhomogeneous(False)
        mode_switcher = Gtk.StackSwitcher()
        mode_switcher.add_css_class("nitro-rgb-modes")
        mode_switcher.set_stack(self.rgb_modes)
        mode_switcher.set_hexpand(True)
        mode_switcher.set_halign(Gtk.Align.START)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.append(mode_switcher)
        self.rgb_reset = Gtk.Button(label="Reset", valign=Gtk.Align.CENTER)
        self.rgb_reset.set_tooltip_text("Restore the default static palette")
        self.rgb_reset.connect("clicked", self._reset_rgb_preview)
        toolbar.append(self.rgb_reset)
        preview_panel.append(toolbar)
        self.rgb_keyboard = KeyboardPreview(self._choose_rgb_zone)
        preview_panel.append(self.rgb_keyboard)
        preview_panel.append(self.rgb_modes)
        content.append(section("Keyboard preview", preview_panel))

        static = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        zones = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, homogeneous=True)
        zones.add_css_class("nitro-rgb-zones")
        self.rgb_colors = []
        self.rgb_zone_switches = []
        for index in range(4):
            zone = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            title = _label(f"Zone {index + 1}", css="metric-caption")
            title.set_halign(Gtk.Align.CENTER)
            zone.append(title)
            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
            actions.set_halign(Gtk.Align.CENTER)
            dialog = Gtk.ColorDialog.new()
            dialog.set_title(f"Zone {index + 1} color")
            dialog.set_with_alpha(False)
            picker = Gtk.ColorDialogButton.new(dialog)
            picker.set_tooltip_text(f"Choose Zone {index + 1} color")
            picker.connect("notify::rgba", self._on_rgb_edit)
            toggle = Gtk.Switch(active=True, valign=Gtk.Align.CENTER)
            toggle.update_property([Gtk.AccessibleProperty.LABEL], [f"Zone {index + 1} lighting"])
            toggle.set_tooltip_text(f"Toggle Zone {index + 1}")
            toggle.connect("notify::active", self._on_rgb_edit)
            actions.append(picker)
            actions.append(toggle)
            zone.append(actions)
            zones.append(zone)
            self.rgb_colors.append(picker)
            self.rgb_zone_switches.append(toggle)
        static.append(zones)

        editor = Adw.PreferencesGroup(css_classes=["nitro-panel-group"])
        self.rgb_preset = Adw.ComboRow(title="Preset")
        self.rgb_preset.set_model(Gtk.StringList.new(list(PRESETS)))
        load = Gtk.Button(label="Load preset", valign=Gtk.Align.CENTER)
        load.connect("clicked", self._load_rgb_preset)
        self.rgb_preset.add_suffix(load)
        editor.add(self.rgb_preset)
        brightness_row = Adw.ActionRow(title="Brightness")
        self.rgb_brightness = self._rgb_scale(0, 100, 5, "Static brightness", "%")
        self.rgb_brightness.connect("value-changed", self._on_rgb_edit)
        brightness_row.add_suffix(self.rgb_brightness)
        editor.add(brightness_row)

        self.rgb_native_hint = Adw.ActionRow(
            title="Native WMI brightness",
            subtitle="Set brightness to 100% to apply or restore colors. "
                     "Use Fn+F9/Fn+F10 to dim the physical keyboard."
        )
        self.rgb_native_hint.set_subtitle_lines(0)
        self.rgb_native_hint.set_visible(False)
        editor.add(self.rgb_native_hint)
        self.rgb_physical_row = Adw.ActionRow(title="Physical keyboard")
        self.rgb_apply_hardware = Gtk.Button(label="Apply to keyboard", valign=Gtk.Align.CENTER)
        self.rgb_apply_hardware.add_css_class("suggested-action")
        self.rgb_apply_hardware.connect("clicked", self._request_rgb_hardware)
        self.rgb_restore_hardware = Gtk.Button(label="Restore lighting", valign=Gtk.Align.CENTER)
        self.rgb_restore_hardware.set_tooltip_text(
            "Reinitialize native Acer lighting using the selected four colors and brightness")
        self.rgb_restore_hardware.connect("clicked", self._request_rgb_hardware)
        self.rgb_physical_row.add_suffix(self.rgb_restore_hardware)
        self.rgb_physical_row.add_suffix(self.rgb_apply_hardware)
        editor.add(self.rgb_physical_row)
        self.rgb_hardware_status = Adw.ActionRow(title="Hardware status", subtitle="Checking RGB support…")
        self.rgb_hardware_status.set_subtitle_lines(0)
        editor.add(self.rgb_hardware_status)
        static.append(editor)
        self.rgb_modes.add_titled(static, "static", "Static")

        dynamic = Adw.PreferencesGroup(
            description="Preview any effect. Supported effects can also run on the physical keyboard.",
            css_classes=["nitro-panel-group"],
        )
        self.rgb_effect = Adw.ComboRow(title="Effect")
        self.rgb_effect.set_model(Gtk.StringList.new(list(EFFECTS)))
        self.rgb_effect.connect("notify::selected", self._on_effect_selected)
        dynamic.add(self.rgb_effect)

        self.rgb_effect_color_row = Adw.ActionRow(title="Effect color")
        dialog = Gtk.ColorDialog.new()
        dialog.set_with_alpha(False)
        self.rgb_effect_color = Gtk.ColorDialogButton.new(dialog)
        self.rgb_effect_color.set_valign(Gtk.Align.CENTER)
        color = Gdk.RGBA()
        color.parse("#FF475D")
        self.rgb_effect_color.set_rgba(color)
        self.rgb_effect_color.connect("notify::rgba", self._sync_keyboard_preview)
        self.rgb_effect_color_row.add_suffix(self.rgb_effect_color)
        dynamic.add(self.rgb_effect_color_row)

        speed_row = Adw.ActionRow(title="Speed")
        self.rgb_effect_speed = self._rgb_scale(1, 9, 1, "Effect speed")
        self.rgb_effect_speed.set_value(4)
        self.rgb_effect_speed.connect("value-changed", self._sync_keyboard_preview)
        speed_row.add_suffix(self.rgb_effect_speed)
        dynamic.add(speed_row)

        effect_brightness = Adw.ActionRow(title="Brightness")
        self.rgb_effect_brightness = self._rgb_scale(0, 100, 5, "Effect brightness", "%")
        self.rgb_effect_brightness.set_value(80)
        self.rgb_effect_brightness.connect("value-changed", self._sync_keyboard_preview)
        effect_brightness.add_suffix(self.rgb_effect_brightness)
        dynamic.add(effect_brightness)

        self.rgb_effect_playing = Adw.SwitchRow(title="Animate preview", active=True)
        self.rgb_effect_playing.connect("notify::active", self._sync_keyboard_preview)
        dynamic.add(self.rgb_effect_playing)
        self.rgb_effect_hardware_row = Adw.ActionRow(title="Physical keyboard")
        self.rgb_effect_hardware_row.set_subtitle_lines(0)
        self.rgb_apply_effect = Gtk.Button(label="Apply effect to keyboard", valign=Gtk.Align.CENTER)
        self.rgb_apply_effect.add_css_class("suggested-action")
        self.rgb_apply_effect.connect("clicked", self._request_rgb_effect)
        self.rgb_effect_hardware_row.add_suffix(self.rgb_apply_effect)
        dynamic.add(self.rgb_effect_hardware_row)
        self.rgb_modes.add_titled(dynamic, "dynamic", "Dynamic")
        self.rgb_modes.connect("notify::visible-child-name", self._on_rgb_mode_changed)

        rgb_group = Adw.PreferencesGroup()
        self.rgb_row, self.rgb_value = _readout_row("Acer RGB WMI interface")
        rgb_group.add(self.rgb_row)
        driver_row, self.driver_value = _readout_row("Bound RGB driver")
        rgb_group.add(driver_row)
        self.rgb_capability = Adw.ActionRow(title="Physical lighting", subtitle="Checking RGB support…")
        self.rgb_capability.set_subtitle_lines(0)
        rgb_group.add(self.rgb_capability)
        content.append(_panel_group("RGB hardware status", "", rgb_group))

        self._set_rgb_editor(PRESETS["Midnight"])

    @staticmethod
    def _rgb_scale(lower: int, upper: int, step: int, label: str, suffix: str = "") -> Gtk.Scale:
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lower, upper, step)
        scale.set_digits(0)
        scale.set_draw_value(True)
        scale.set_size_request(190, -1)
        scale.set_valign(Gtk.Align.CENTER)
        scale.set_format_value_func(lambda _scale, value: f"{value:.0f}{suffix}")
        scale.update_property([Gtk.AccessibleProperty.LABEL], [label])
        return scale

    def _on_tray_setting_changed(self, row, _param) -> None:
        if self._tray_setting_updating or self._demo:
            return
        requested = row.get_active()
        try:
            save_keep_in_tray(requested)
        except (OSError, ValueError) as exc:
            self._tray_setting_updating = True
            try:
                row.set_active(self._keep_in_tray)
            finally:
                self._tray_setting_updating = False
            self.tray_feedback.set_text(f"Could not save tray preference: {exc}")
            return
        self._keep_in_tray = requested
        if requested:
            self._start_tray()
        else:
            self.tray_feedback.set_text(
                "Disabled for future dashboard launches. "
                "To stop an already running indicator, use its Quit tray action."
            )

    def _start_tray(self) -> None:
        if self._demo or self._closed:
            return
        try:
            process = start_tray_process()
        except OSError as exc:
            self.tray_feedback.set_text(f"Could not launch system tray: {exc}")
            return
        self.tray_feedback.set_text("Starting indicator… Look for it in the GNOME top bar.")
        GLib.timeout_add(800, self._check_tray_started, process)

    def _check_tray_started(self, process) -> bool:
        # Ignore late launch feedback when the user has already switched off.
        if self._closed or not self._keep_in_tray:
            return False
        result = process.poll()
        if result is None:
            self.tray_feedback.set_text(
                "Tray process running. Requires the GNOME AppIndicator extension; "
                "Quit tray to stop monitoring."
            )
        elif result == 0:
            self.tray_feedback.set_text("Tray already running; no duplicate icon started.")
        else:
            self.tray_feedback.set_text(
                "Tray unavailable. Install Fedora libayatana-appindicator-gtk3 "
                "and enable GNOME AppIndicator support. "
                f"(Process exit: {result})"
            )
        return False

    def _on_close(self, *_args) -> bool:
        # Do not terminate the opt-in, separate GTK3 tray process.
        if self._fan_busy:
            return True
        self._closed = True
        return False

    def _on_timer(self) -> bool:
        if self._closed:
            return False
        self._request_refresh()
        return True

    def _on_fan_animation_tick(self) -> bool:
        if self._closed:
            return False
        now_us = GLib.get_monotonic_time()
        elapsed_seconds = max(0.0, (now_us - self._fan_last_tick_us) / 1_000_000)
        self._fan_last_tick_us = now_us
        # Hidden pages and unmapped windows do not need frame redraws.
        # Keeping the clock current avoids a big rotation jump on return.
        if self.get_mapped() and self._page_stack.get_visible_child_name() == "dashboard":
            for dial in self.fan_dials:
                dial.animate(elapsed_seconds)
        return True

    def _request_refresh(self) -> None:
        if self._busy or self._closed or self._power_busy or self._fan_busy:
            return
        self._busy = True
        self.status_label.set_text("Reading sensors…" if not self._demo else "DEMO MODE • sample data")
        Thread(target=self._collect_background, args=(self._fan_revision,), daemon=True,
               name="nitro-hardware-reader").start()

    def _collect_background(self, fan_revision: int) -> None:
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
        hwmon = None if self._demo else find_fan_controls()
        fan_plan = read_fan_plan(hwmon) if hwmon is not None else None
        GLib.idle_add(self._finish_refresh, result, error, state, power_error,
                      hwmon is not None, fan_plan, fan_revision)

    def _finish_refresh(self, snapshot: Snapshot | None, error: str | None,
                        state: PowerState | None, power_error: str | None,
                        fan_available: bool, fan_plan: FanPlan | None,
                        fan_revision: int) -> bool:
        self._busy = False
        if self._closed:
            return False
        if error:
            self.status_label.set_text(error)
            self.status_icon.set_from_icon_name("dialog-warning-symbolic")
        elif snapshot:
            self._render(snapshot)
        if fan_revision == self._fan_revision:
            if self._fan_active is not None and fan_plan != self._fan_active:
                self._fan_feedback = None
            self._render_fan_controls(fan_available, fan_plan)
        self._render_power(state, power_error)
        return False

    def _build_fan_controls(self) -> Gtk.Box:
        controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.fan_controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.fan_controls.set_visible(False)
        controls.append(self.fan_controls)
        self.fan_mode = Gtk.DropDown.new_from_strings(list(MODES))
        self.fan_mode.update_property([Gtk.AccessibleProperty.LABEL], ["Fan mode"])
        self.fan_mode.connect("notify::selected", self._on_fan_edit)
        self.fan_controls.append(self.fan_mode)

        self.fan_custom = []
        self.fan_settings = []
        for name in ("CPU fan", "GPU fan"):
            slider = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            slider.set_visible(False)
            slider.set_halign(Gtk.Align.CENTER)
            auto = Gtk.CheckButton(label="Auto")
            auto.update_property([Gtk.AccessibleProperty.LABEL], [f"{name} Auto"])
            auto.connect("toggled", self._on_fan_edit)
            speed = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, MIN_PERCENT, 100, 1)
            speed.set_size_request(135, -1)
            speed.update_property([Gtk.AccessibleProperty.LABEL], [f"{name} speed"])
            speed.set_value(MIN_PERCENT)
            speed.set_draw_value(True)
            speed.set_format_value_func(lambda _scale, value: f"{value:.0f}%")
            speed.connect("value-changed", self._on_fan_edit)
            slider.append(speed)
            slider.append(auto)
            self.fan_custom.append(slider)
            self.fan_settings.append((auto, speed))

        self.fan_apply = Gtk.Button(label="Apply fan settings")
        self.fan_apply.add_css_class("suggested-action")
        self.fan_apply.set_sensitive(False)
        self.fan_apply.connect("clicked", self._request_fan_change)
        self.fan_controls.append(self.fan_apply)
        self.fan_feedback = _label("Checking fan controls…", css="nitro-readonly")
        self.fan_feedback.set_wrap(True)
        self.fan_feedback.set_max_width_chars(29)
        controls.append(self.fan_feedback)
        return controls

    def _render_fan_controls(self, available: bool, active: FanPlan | None) -> None:
        self._fan_available = available
        self._fan_active = active
        if not self._fan_editing:
            self._fan_rendering = True
            try:
                self.fan_mode.set_selected(MODES.index(active.mode) if active else 0)
                for (auto, scale), speed in zip(self.fan_settings, active.channels if active else (None, None)):
                    auto.set_active(active is not None and active.mode == "Custom" and speed is None)
                    scale.set_value(speed if speed is not None else MIN_PERCENT)
            finally:
                self._fan_rendering = False
        self._sync_fan_controls()

    def _fan_draft(self) -> FanPlan:
        mode = MODES[self.fan_mode.get_selected()]
        if mode == "Custom":
            speeds = [None if auto.get_active() else round(scale.get_value())
                      for auto, scale in self.fan_settings]
            if any(speed is not None for speed in speeds):
                return FanPlan(mode, *speeds)
            return FanPlan("Auto")
        return FanPlan(mode)

    def _on_fan_edit(self, *_args) -> None:
        if not self._fan_rendering:
            self._fan_editing = True
            self._fan_feedback = None
            self._sync_fan_controls()

    def _sync_fan_controls(self) -> None:
        ready = self._fan_available and self._fan_client.ready()
        self.fan_controls.set_visible(ready)
        editable = not self._fan_busy and not self._power_busy
        self.fan_controls.set_sensitive(editable)
        custom = MODES[self.fan_mode.get_selected()] == "Custom"
        for slider, (auto, scale) in zip(self.fan_custom, self.fan_settings):
            slider.set_visible(ready and custom)
            slider.set_sensitive(editable)
            scale.set_sensitive(not auto.get_active())
        self.fan_apply.set_sensitive(ready and self._fan_draft() != self._fan_active)
        message = self._fan_feedback
        if self._demo:
            message = "Fan controls are disabled in Demo."
        elif not self._fan_available:
            message = "Fan controls require stock Acer PWM support on the AN515-58."
        elif not ready:
            message = "Install the optional fan helper to change speeds."
        elif not message and self._fan_active is None:
            message = "Fan mode unverified. Apply Auto to restore firmware control."
        self.fan_feedback.set_text(message or "")
        self.fan_feedback.set_visible(bool(message))

    def _request_fan_change(self, *_args) -> None:
        if self._demo or self._fan_busy or self._power_busy or not self._fan_available:
            return
        plan = self._fan_draft()
        if plan.mode != "Max":
            self._start_fan_change(plan)
            return
        dialog = Adw.AlertDialog.new(
            "Run both fans at full speed?",
            "The fans will get loud. Select Auto and apply it to return control to the firmware."
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("apply", "Run fans at Max")
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._confirm_max_fans, plan)
        dialog.present(self)

    def _confirm_max_fans(self, _dialog, response: str, plan: FanPlan) -> None:
        if response == "apply":
            self._start_fan_change(plan)

    def _start_fan_change(self, plan: FanPlan) -> None:
        if (self._closed or self._demo or self._fan_busy or self._power_busy
                or not self._fan_available or not self._fan_client.ready()):
            return
        self._fan_busy = True
        self._fan_revision += 1
        self._fan_feedback = "Authorizing fan settings…"
        self._sync_fan_controls()
        self._sync_power_button()
        Thread(target=self._apply_fans_background, args=(plan,), daemon=True,
               name="nitro-fan-control").start()

    def _apply_fans_background(self, plan: FanPlan) -> None:
        try:
            self._fan_client.apply(plan)
            error = None
        except Exception as exc:
            error = str(exc)
        GLib.idle_add(self._finish_fan_change, plan, error)

    def _finish_fan_change(self, plan: FanPlan, error: str | None) -> bool:
        self._fan_busy = False
        self._fan_editing = error is not None
        self._fan_feedback = error or f"{plan.mode} applied."
        self._render_fan_controls(self._fan_available, None if error else plan)
        self._sync_power_button()
        self._request_refresh()
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
            not self._demo and not self._power_busy and not self._fan_busy and self._power_state is not None
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
                self.power_status.set_text(error or "Power-profile service unavailable")
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
                self.power_status.set_text(
                    "Demo only — power changes disabled" if self._demo else
                    (self._power_feedback or f"System reports {DISPLAY[state.active]} active")
                )
        finally:
            self._rendering_power = False
        self._sync_power_button()

    def _request_power_change(self, *_args) -> None:
        chosen = self._selected_power()
        if (self._demo or self._power_busy or self._fan_busy or self._power_state is None or
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
        if response != "apply" or self._closed or self._power_busy or self._fan_busy:
            return
        self._power_busy = True
        self.power_combo.set_sensitive(False)
        self._sync_power_button()
        self._sync_fan_controls()
        self.power_status.set_text(f"Requesting {DISPLAY[chosen]}…")
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
        self._sync_fan_controls()
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
                display_name = fan_display_name(data.model, fan.name)
                self.fan_mode_titles[index].set_text(f"{display_name.upper()} · MODE")
                value.set_text(fan_mode_description(fan.control_mode))
                if fan.control_mode == "Unknown":
                    value.add_css_class("nitro-fan-muted")
                else:
                    value.remove_css_class("nitro-fan-muted")
                hint.set_text(f"{fan.name} · read-only sensor")
                self.fan_dials[index].update(display_name, fan.rpm, fan.control_mode)
            else:
                self.fan_mode_titles[index].set_text(f"FAN {index + 1} · MODE")
                value.set_text("Unavailable")
                value.add_css_class("nitro-fan-muted")
                hint.set_text("No fan reading")
                self.fan_dials[index].update(f"Fan {index + 1}", None, "Unknown")

        # Time series are built only from actual snapshots and keep missing
        # sensors as gaps rather than synthetic zeros or illustrative curves.
        self._history.record(
            data.sampled_at,
            data.cpu_celsius,
            data.gpu.celsius if data.gpu else None,
            data.gpu.utilization_percent if data.gpu else None,
        )
        self.cpu_graph.set_history(self._history)
        self.gpu_graph.set_history(self._history)

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

        self.overview_firmware_value.set_text(data.firmware_profile or "Unavailable")
        self.overview_tuned_value.set_text(data.tuned_profile or "Unavailable")
        self.profile_rows["firmware"].set_text(data.firmware_profile or "Unavailable")
        choices = "  •  ".join(data.firmware_profile_choices) or "Unavailable"
        self.firmware_choices_row.set_subtitle(choices)
        self.profile_rows["tuned"].set_text(data.tuned_profile or "Unavailable")
        self._update_sensor_rows(self.fan_rows, [
            (fan_display_name(data.model, fan.name),
             fan_mode_description(fan.control_mode) + " · read-only",
             _number(fan.rpm, "RPM")) for fan in data.fans
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
            for picker, toggle, color in zip(self.rgb_colors, self.rgb_zone_switches, plan.zones):
                rgba = Gdk.RGBA()
                if not rgba.parse(color):
                    raise ValueError("Invalid RGB plan")
                picker.set_rgba(rgba)
                toggle.set_active(True)
            self.rgb_brightness.set_value(plan.brightness)
        finally:
            self._rgb_rendering = False
        self._on_rgb_edit()

    @staticmethod
    def _rgb_hex(rgba: Gdk.RGBA) -> str:
        channels = (rgba.red, rgba.green, rgba.blue)
        return "#" + "".join(f"{round(max(0.0, min(1.0, channel)) * 255):02X}" for channel in channels)

    def _rgb_draft(self) -> LightingPlan:
        zones = tuple(self._rgb_hex(picker.get_rgba()) if toggle.get_active() else "#000000"
                      for picker, toggle in zip(self.rgb_colors, self.rgb_zone_switches))
        return LightingPlan(zones, round(self.rgb_brightness.get_value()))

    def _choose_rgb_zone(self, index: int) -> None:
        picker = self.rgb_colors[index]
        dialog = picker.get_dialog()

        def finish(source: Gtk.ColorDialog, result: Gio.AsyncResult) -> None:
            try:
                rgba = source.choose_rgba_finish(result)
            except GLib.Error as exc:
                if exc.matches(Gtk.dialog_error_quark(), Gtk.DialogError.FAILED):
                    self.rgb_hardware_status.set_subtitle("Unable to open the color picker.")
                return
            if rgba is not None and not self._closed:
                picker.set_rgba(rgba)

        dialog.choose_rgba(self, picker.get_rgba(), None, finish)

    def _sync_keyboard_preview(self, *_args) -> None:
        if self.rgb_modes.get_visible_child_name() == "static":
            self.rgb_keyboard.update_lighting(self._rgb_draft())
            return

        selected = self.rgb_effect.get_selected()
        effect = EFFECTS[selected] if selected < len(EFFECTS) else EFFECTS[0]
        self.rgb_effect_color_row.set_visible(effect in COLOR_EFFECTS)
        self._sync_effect_button()
        preview = LightingPlan(self._rgb_draft().zones, round(self.rgb_effect_brightness.get_value()))
        self.rgb_keyboard.update_lighting(
            preview, effect=effect, color=self._rgb_hex(self.rgb_effect_color.get_rgba()),
            speed=round(self.rgb_effect_speed.get_value()), playing=self.rgb_effect_playing.get_active())

    def _on_rgb_mode_changed(self, *_args) -> None:
        self.rgb_reset.set_visible(self.rgb_modes.get_visible_child_name() == "static")
        self._sync_keyboard_preview()
        self._sync_rgb_hardware_button()

    def _on_rgb_edit(self, *_args) -> None:
        if self._rgb_rendering:
            return
        self._sync_keyboard_preview()
        self._sync_rgb_hardware_button()

    def _load_rgb_preset(self, *_args) -> None:
        names = list(PRESETS)
        index = self.rgb_preset.get_selected()
        if 0 <= index < len(names):
            self._set_rgb_editor(PRESETS[names[index]])

    def _reset_rgb_preview(self, *_args) -> None:
        self._set_rgb_editor(PRESETS["Midnight"])

    def _on_effect_selected(self, *_args) -> None:
        self._rgb_effect_result = False
        self._sync_keyboard_preview()

    def _sync_effect_button(self) -> None:
        selected = self.rgb_effect.get_selected()
        supported = selected < len(EFFECTS) and EFFECTS[selected] in PHYSICAL_EFFECT_MODES
        self.rgb_apply_effect.set_visible(supported)
        ready = (self._effects_available and self._rgb_status.backend == "native-wmi"
                 and self._rgb_status.available and self._rgb_client.ready())
        self.rgb_apply_effect.set_sensitive(ready and not self._rgb_hw_busy)
        if not self._rgb_hw_busy and (not self._rgb_effect_result or not supported):
            if self._demo or not supported:
                detail = "On-screen preview only."
            elif not ready:
                detail = "Physical effects require the updated native RGB module and helper."
            else:
                detail = "Uses 100% firmware brightness. The preview brightness slider is on-screen only."
            self.rgb_effect_hardware_row.set_subtitle(detail)

    def _request_rgb_effect(self, *_args) -> None:
        selected = self.rgb_effect.get_selected()
        if (self._demo or self._closed or self._rgb_hw_busy
                or self.rgb_modes.get_visible_child_name() != "dynamic"
                or selected >= len(EFFECTS) or EFFECTS[selected] not in PHYSICAL_EFFECT_MODES
                or not self._effects_available or self._rgb_status.backend != "native-wmi"
                or not self._rgb_status.available or not self._rgb_client.ready()):
            return
        effect = EFFECTS[selected]
        color = self._rgb_hex(self.rgb_effect_color.get_rgba()) if effect in COLOR_EFFECTS else "#000000"
        plan = EffectPlan(effect, round(self.rgb_effect_speed.get_value()), color)
        dialog = Adw.AlertDialog.new(
            f"Apply {effect} to the physical keyboard?",
            "Physical effects use 100% firmware brightness. To switch back, open Static and apply your palette.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("apply", f"Apply {effect}")
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._confirm_rgb_effect, plan)
        dialog.present(self)

    def _confirm_rgb_effect(self, _dialog, response: str, plan: EffectPlan) -> None:
        if (response != "apply" or self._closed or self._rgb_hw_busy
                or self.rgb_modes.get_visible_child_name() != "dynamic"):
            return
        self._rgb_hw_busy = True
        self._rgb_effect_result = False
        self._sync_rgb_hardware_button()
        self.rgb_effect_hardware_row.set_subtitle(f"Authorizing {plan.effect}…")
        Thread(target=self._apply_effect_background, args=(plan,), daemon=True,
               name="nitro-rgb-effect").start()

    def _apply_effect_background(self, plan: EffectPlan) -> None:
        try:
            detail = self._rgb_client.apply_effect(plan)
        except Exception as exc:
            detail = f"{plan.effect} could not be verified: {exc}"
        GLib.idle_add(self._finish_effect, detail)

    def _finish_effect(self, detail: str) -> bool:
        self._rgb_hw_busy = False
        if not self._closed:
            self._rgb_effect_result = True
            self.rgb_effect_hardware_row.set_subtitle(detail)
            self._sync_rgb_hardware_button()
        return False

    def _sync_rgb_hardware_button(self) -> None:
        self._sync_effect_button()
        ready = (self.rgb_modes.get_visible_child_name() == "static"
                 and not self._demo and not self._rgb_hw_busy and
                 self._rgb_status.available and self._rgb_client.ready())
        native = self._rgb_status.backend == "native-wmi"
        # The AN515-58 native backend currently permits only verified 100%
        # WMI initialization. The physical Fn keys still control dimming.
        supported_brightness = not native or self._rgb_draft().brightness == 100
        self.rgb_native_hint.set_visible(native and not supported_brightness)
        self.rgb_apply_hardware.set_sensitive(ready and supported_brightness)
        self.rgb_restore_hardware.set_sensitive(ready and native and supported_brightness)
        if native:
            self.rgb_physical_row.set_subtitle(
                "Native WMI: set slider to 100% to Apply/Restore; dim afterward with Fn+F9/Fn+F10")
        else:
            self.rgb_physical_row.set_subtitle(
                "Optional root-owned helper; changes require confirmation and authentication")

    def _request_rgb_hardware(self, *_args) -> None:
        if (self.rgb_modes.get_visible_child_name() != "static" or self._demo or
                self._rgb_hw_busy or not self._rgb_status.available or not self._rgb_client.ready()):
            return
        plan = self._rgb_draft()
        if self._rgb_status.backend == "native-wmi" and plan.brightness != 100:
            self.rgb_hardware_status.set_subtitle(
                "Native Acer WMI requires 100% initialization; use Fn keys for dimming")
            return
        dialog = Adw.AlertDialog.new(
            "Apply these colors to your physical keyboard?",
            "This changes physical RGB lighting through an optional authorized backend. "
            "On native Acer WMI this runs a one-shot kernel module that initializes all zones. "
            "Administrator authentication may be required. Firmware/sysfs readback "
            "does not prove the LEDs changed: inspect the keyboard. "
            "No direct fan or power control is requested."
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("apply", "Apply to keyboard")
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._confirm_rgb_hardware, plan)
        dialog.present(self)

    def _confirm_rgb_hardware(self, _dialog, response: str, plan: LightingPlan) -> None:
        if (response != "apply" or self._closed or self._rgb_hw_busy
                or self.rgb_modes.get_visible_child_name() != "static"):
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
            f"Hardware change failed or unverified: {error}" if error else message or "No confirmed software response"
        )
        self._rgb_status = self._rgb_hardware.probe()
        self._rgb_effect_result = False
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

"""NitroSense-inspired, original GTK4/Cairo read-only dashboard artwork.

Drawing functions never read hardware or make writes. A missing reading is
rendered as unavailable, not a fabricated value or an assumed fan limit.
"""
from __future__ import annotations

import math

import cairo  # PyGObject's Cairo draw-function foreign type adapter
from gi.repository import Gtk

from .fan_animation import fan_angle_step
from .monitor_history import MonitorHistory


RED = (0.88, 0.13, 0.19)
ORANGE = (0.99, 0.50, 0.12)


def _color(ctx, rgb: tuple[float, float, float], alpha: float = 1.0) -> None:
    ctx.set_source_rgba(*rgb, alpha)


def _text(ctx, label: str, x: float, y: float, size: float,
          color: tuple[float, float, float] = (0.94, 0.94, 0.94),
          *, center: bool = False, bold: bool = False) -> None:
    # Cairo's built-in font API is sufficient for decorative labels. GTK
    # accessibility labels are supplied separately in the actual UI.
    ctx.save()
    ctx.select_font_face("Sans", 0, 1 if bold else 0)
    ctx.set_font_size(size)
    _color(ctx, color)
    if center:
        extent = ctx.text_extents(label)
        x -= extent.width / 2 + extent.x_bearing
    ctx.move_to(x, y)
    ctx.show_text(label)
    ctx.restore()


class Ribbon(Gtk.DrawingArea):
    """Machined gray section tab with an angled right edge."""

    def __init__(self, title: str):
        super().__init__()
        self.title = title
        self.set_content_width(255)
        self.set_content_height(38)
        self.set_halign(Gtk.Align.START)
        self.set_draw_func(self._draw)
        self.set_tooltip_text(title)
        # GTK's tooltip provides readable text alongside the Cairo label.

    def _draw(self, _area, ctx, width, height):
        w = min(width, 258)
        # Angled machined-metal header with a gentle silver bevel rather than
        # a generic GTK group label. The drawing is original Nitro-Control art.
        ctx.move_to(0, height - 4)
        ctx.line_to(0, 10)
        ctx.line_to(11, 1)
        ctx.line_to(w, 1)
        ctx.line_to(w - 32, height - 4)
        ctx.close_path()
        steel = cairo.LinearGradient(0, 0, 0, height)
        steel.add_color_stop_rgb(0.0, 0.46, 0.46, 0.48)
        steel.add_color_stop_rgb(0.28, 0.33, 0.34, 0.36)
        steel.add_color_stop_rgb(0.79, 0.25, 0.26, 0.28)
        steel.add_color_stop_rgb(1.0, 0.15, 0.15, 0.17)
        ctx.set_source(steel)
        ctx.fill_preserve()
        ctx.set_line_width(1)
        ctx.set_source_rgba(0.8, 0.8, 0.8, 0.30)
        ctx.stroke()

        ctx.move_to(11, 3)
        ctx.line_to(w - 7, 3)
        ctx.set_source_rgba(1, 1, 1, 0.20)
        ctx.set_line_width(1)
        ctx.stroke()
        for y in (12, 19, 26):
            ctx.move_to(7, y)
            ctx.line_to(w - 40, y)
        ctx.set_source_rgba(0.07, 0.07, 0.08, 0.09)
        ctx.set_line_width(0.6)
        ctx.stroke()
        _text(ctx, self.title.upper(), 16, 25, 13, bold=True)

class FanDial(Gtk.DrawingArea):
    """Animated display turbine with an *unscaled* measured RPM label."""

    def __init__(self, caption: str):
        super().__init__()
        self.caption = caption
        self._rpm: int | None = None
        self._mode = "Unknown"
        self._name = caption
        self._rotation = 0.0
        self.set_content_width(220)
        self.set_content_height(223)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)
        self._accessible()

    def _accessible(self):
        value = f"{self._rpm:,} RPM" if self._rpm is not None else "Unavailable"
        self.set_tooltip_text(
            f"{self._name}: {value}; {self._mode} fan mode; read-only. "
            "Visual rotation follows measured RPM at a slowed display rate.")

    def update(self, name: str, rpm: int | None, mode: str) -> None:
        self._name = name
        self._rpm = rpm
        self._mode = mode
        self._accessible()
        self.queue_draw()

    def animate(self, elapsed_seconds: float) -> None:
        """Advance only this rotor's painted blade angle; never write hardware."""
        step = fan_angle_step(self._rpm, elapsed_seconds)
        if step > 0:
            self._rotation = (self._rotation + step) % math.tau
            self.queue_draw()

    def _draw(self, _area, ctx, width, height):
        cx = width / 2
        cy = min(height * 0.43, 100)
        radius = min(width * 0.375, 85)

        # Thirty swept, narrow blades make the reference's turbine silhouette.
        # Their gray color is a decorative material treatment, NOT a speed or
        # PWM indicator. No fan maximum is assumed or guessed.
        blades = 28
        for i in range(blades):
            ctx.save()
            ctx.translate(cx, cy)
            ctx.rotate(self._rotation + 2 * math.pi * i / blades)
            ctx.move_to(radius * 0.69, -1)
            ctx.curve_to(radius * 0.84, -radius * 0.21,
                         radius * 1.13, -radius * 0.27,
                         radius * 1.22, -radius * 0.13)
            ctx.curve_to(radius * 1.18, -radius * 0.085,
                         radius * 1.14, -radius * 0.09,
                         radius * 1.04, -radius * 0.105)
            ctx.curve_to(radius * 0.91, -radius * 0.10,
                         radius * 0.80, radius * 0.09,
                         radius * 0.70, radius * 0.10)
            ctx.close_path()
            metal = cairo.LinearGradient(radius * 0.7, 0, radius * 1.2, 0)
            metal.add_color_stop_rgba(0, 0.19, 0.19, 0.21, 0.86)
            metal.add_color_stop_rgba(0.5, 0.41, 0.41, 0.43, 0.86)
            metal.add_color_stop_rgba(1, 0.29, 0.29, 0.31, 0.74)
            ctx.set_source(metal)
            ctx.fill_preserve()
            ctx.set_source_rgba(0, 0, 0, 0.5)
            ctx.set_line_width(0.6)
            ctx.stroke()
            ctx.restore()

        ctx.arc(cx, cy, radius * 0.89, 0, 2 * math.pi)
        _color(ctx, RED, 0.47)
        ctx.set_line_width(2.8)
        ctx.stroke_preserve()
        ctx.set_source_rgb(0.040, 0.041, 0.048)
        ctx.fill()

        rpm = f"{self._rpm:,}" if self._rpm is not None else "—"
        _text(ctx, rpm, cx, cy + 9, 30, center=True, bold=True)
        _text(ctx, "RPM" if self._rpm is not None else "NO SENSOR",
              cx, cy + 30, 10, (0.65, 0.65, 0.68), center=True)
        _text(ctx, self._name[:24], cx, height - 34, 13, center=True, bold=True)
        _text(ctx, "READ-ONLY · FIRMWARE CONTROL", cx, height - 16, 9,
              (0.54, 0.54, 0.58), center=True)

class RGBZoneStrip(Gtk.DrawingArea):
    """Four **draft** colors, never a claim about the physical keyboard.

    This visualization owns no drivers or write APIs. It displays the same
    validated in-memory palette already used by the existing RGB editor.
    """

    def __init__(self):
        super().__init__()
        self._zones: tuple[str, ...] = ("#000000",) * 4
        self.set_content_width(470)
        self.set_content_height(112)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)
        self.set_tooltip_text("Draft RGB colors only; not applied to hardware")

    def update_zones(self, zones: tuple[str, ...]) -> None:
        import re
        if len(zones) != 4 or any(
            re.fullmatch(r"#[0-9A-Fa-f]{6}", zone) is None for zone in zones
        ):
            raise ValueError("Expected four validated RGB hex colors")
        self._zones = zones
        self.set_tooltip_text(
            "Unapplied draft colors: " + ", ".join(zones))
        self.queue_draw()

    def _draw(self, _area, ctx, width, height):
        ctx.set_source_rgb(0.047, 0.048, 0.058)
        ctx.paint()
        left, right = 17, max(width - 17, 18)
        gap = 7
        zone_width = max(1, (right - left - gap * 3) / 4)
        for i, value in enumerate(self._zones):
            r, g, b = (int(value[j:j + 2], 16) / 255 for j in (1, 3, 5))
            x = left + i * (zone_width + gap)
            # Original beveled key-zone tile, not a hardware top-view model.
            ctx.move_to(x + 6, 24)
            ctx.line_to(x + zone_width - 3, 24)
            ctx.line_to(x + zone_width, 28)
            ctx.line_to(x + zone_width - 5, 73)
            ctx.line_to(x + 4, 73)
            ctx.line_to(x, 68)
            ctx.close_path()
            ctx.set_source_rgb(r, g, b)
            ctx.fill_preserve()
            ctx.set_line_width(1.2)
            ctx.set_source_rgba(1, 1, 1, 0.30)
            ctx.stroke()
            _text(ctx, f"ZONE {i + 1}", x + zone_width / 2, 94, 10,
                  (0.70, 0.70, 0.75), center=True, bold=True)
        _text(ctx, "DRAFT PALETTE · NOT APPLIED TO KEYBOARD",
              left + 1, 15, 10, (0.70, 0.70, 0.75))


class TelemetryGraph(Gtk.DrawingArea):
    """Actual timestamped samples, with breaks for absent sensor readings."""

    def __init__(self, title: str, field: str, extra: str | None = None):
        super().__init__()
        self.title = title
        self.field = field
        self.extra = extra
        self.history = MonitorHistory()
        self.set_content_height(145)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)
        self.set_tooltip_text(f"{title} monitoring history")

    def set_history(self, history: MonitorHistory):
        self.history = history
        samples = history.samples
        if samples:
            temp = getattr(samples[-1], self.field)
            util = getattr(samples[-1], self.extra) if self.extra else None
            detail = (f"{temp:.0f} degrees Celsius" if temp is not None else
                      "temperature unavailable")
            if self.extra:
                detail += (f", {util:.0f}% utilization" if util is not None else
                           ", utilization unavailable")
            self.set_tooltip_text(f"{self.title}: {detail}; recent real readings")
        self.queue_draw()

    def _plot(self, ctx, values: tuple[float | None, ...], start: float,
              end: float, top: float, bottom: float, left: float, right: float,
              rgb: tuple[float, float, float]):
        if not values:
            return
        count = len(values)
        last = False
        ctx.new_path()
        for index, value in enumerate(values):
            if value is None:
                if last:
                    _color(ctx, rgb, 0.96)
                    ctx.set_line_width(2)
                    ctx.stroke()
                last = False
                ctx.new_path()
                continue
            x = left + (index / max(count - 1, 1)) * (right - left)
            y = bottom - (value - start) / max(end - start, 1) * (bottom - top)
            if last:
                ctx.line_to(x, y)
            else:
                ctx.move_to(x, y)
            last = True
        if last:
            _color(ctx, rgb, 0.96)
            ctx.set_line_width(2)
            ctx.stroke()
        # A single sample has no segment; draw an actual point.
        if count == 1 and values[0] is not None:
            x, y = left, bottom - (values[0] - start) / max(end - start, 1) * (bottom - top)
            ctx.arc(x, y, 2.5, 0, 2 * math.pi)
            _color(ctx, rgb)
            ctx.fill()

    def _draw(self, _area, ctx, width, height):
        left, right = 44, max(44, width - 108)
        top, bottom = 31, max(31, height - 20)
        ctx.set_source_rgb(0.041, 0.043, 0.050)
        ctx.rectangle(0, 0, width, height)
        ctx.fill()

        # Fine red graph-paper grid, matching the dense monitoring panels
        # without inventing sensor readings.
        lines = max(11, int(max(right - left, 1) / 22))
        for i in range(lines + 1):
            x = left + (right - left) * i / lines
            ctx.move_to(x, top)
            ctx.line_to(x, bottom)
        for i in range(6):
            y = top + (bottom - top) * i / 5
            ctx.move_to(left, y)
            ctx.line_to(right, y)
        _color(ctx, RED, 0.15)
        ctx.set_line_width(0.55)
        ctx.stroke()
        _text(ctx, self.title.upper(), 12, 21, 13, bold=True)

        series = self.history.series(self.field)
        available = [v for v in series if v is not None]
        if available:
            lo = math.floor((min(available) - 5) / 5) * 5
            hi = math.ceil((max(available) + 5) / 5) * 5
            if hi <= lo:
                hi = lo + 10
            self._plot(ctx, series, lo, hi, top, bottom, left, right, RED)
            _text(ctx, f"MIN {min(available):.0f}°  MAX {max(available):.0f}°",
                  right - 132, 21, 10, (0.58, 0.58, 0.62))
            _text(ctx, f"{hi:.0f}°", left + 2, top + 14, 10, (0.57, 0.57, 0.59))
            _text(ctx, f"{lo:.0f}°", left + 2, bottom - 6, 10, (0.57, 0.57, 0.59))
            last = series[-1]
            if last is not None:
                _text(ctx, f"{last:.0f}°C", right + 14, top + 38, 22, RED, bold=True)
        else:
            _text(ctx, "Temperature unavailable", left + 22,
                  (top + bottom) / 2, 12, (0.56, 0.56, 0.59))

        if self.extra:
            secondary = self.history.series(self.extra)
            if any(v is not None for v in secondary):
                self._plot(ctx, secondary, 0, 100, top, bottom, left, right, ORANGE)
                last = secondary[-1]
                if last is not None:
                    _text(ctx, f"{last:.0f}%", right + 14, top + 77, 19, ORANGE)
        _text(ctx, "LIVE HISTORY · READ ONLY", left + 5, height - 5,
              9, (0.47, 0.47, 0.48))


def section(title: str, content: Gtk.Widget, *,
            min_height: int = 0, fill_height: bool = False) -> Gtk.Widget:
    """Panel shell. Fill height only on panels designed to absorb spare room."""
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    outer.add_css_class("nitro-section")
    if min_height:
        outer.set_size_request(-1, min_height)
    if fill_height:
        outer.set_vexpand(True)
    outer.append(Ribbon(title))
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    if fill_height:
        body.set_vexpand(True)
        content.set_vexpand(True)
    body.set_margin_start(15)
    body.set_margin_end(15)
    body.set_margin_bottom(13)
    body.append(content)
    outer.append(body)
    return outer

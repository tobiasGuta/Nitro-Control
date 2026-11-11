"""NitroSense-inspired, original GTK4/Cairo read-only dashboard artwork.

Drawing functions never read hardware or make writes. A missing reading is
rendered as unavailable, not a fabricated value or an assumed fan limit.
"""
from __future__ import annotations

import math

from gi.repository import Gtk

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
        self.update_property([Gtk.AccessibleProperty.LABEL], [title])

    def _draw(self, _area, ctx, width, height):
        w = min(width, 258)
        ctx.move_to(0, height - 4)
        ctx.line_to(0, 11)
        ctx.line_to(12, 1)
        ctx.line_to(w, 1)
        ctx.line_to(w - 34, height - 4)
        ctx.close_path()
        ctx.set_source_rgb(0.29, 0.30, 0.32)
        ctx.fill_preserve()
        ctx.set_line_width(1)
        ctx.set_source_rgba(0.75, 0.75, 0.75, 0.30)
        ctx.stroke()
        ctx.move_to(7, 5)
        ctx.line_to(w - 24, 5)
        ctx.set_line_width(1)
        ctx.set_source_rgba(0.88, 0.88, 0.88, 0.10)
        ctx.stroke()
        _text(ctx, self.title.upper(), 17, 25, 13, bold=True)


class FanDial(Gtk.DrawingArea):
    """A decorative turbine dial with an *unscaled* real RPM readout."""

    def __init__(self, caption: str):
        super().__init__()
        self.caption = caption
        self._rpm: int | None = None
        self._mode = "Unknown"
        self._name = caption
        self.set_content_width(220)
        self.set_content_height(223)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)
        self._accessible()

    def _accessible(self):
        value = f"{self._rpm:,} RPM" if self._rpm is not None else "Unavailable"
        self.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [f"{self._name}: {value}; {self._mode} fan mode; read-only"],
        )

    def update(self, name: str, rpm: int | None, mode: str) -> None:
        self._name = name
        self._rpm = rpm
        self._mode = mode
        self._accessible()
        self.queue_draw()

    def _draw(self, _area, ctx, width, height):
        cx = width / 2
        cy = min(height * 0.43, 100)
        radius = min(width * 0.35, 76)

        # Geometric fan blades echo the turbine motif in the reference.
        for i in range(20):
            ctx.save()
            ctx.translate(cx, cy)
            ctx.rotate(2 * math.pi * i / 20)
            ctx.move_to(radius * 0.75, -2)
            ctx.curve_to(radius * 1.08, -14, radius * 1.17, -13, radius * 1.19, -1)
            ctx.curve_to(radius * 1.09, 8, radius * 0.91, 12, radius * 0.75, 7)
            ctx.close_path()
            _color(ctx, (0.38, 0.38, 0.40), 0.64)
            ctx.fill()
            ctx.restore()

        ctx.arc(cx, cy, radius * 0.89, 0, 2 * math.pi)
        _color(ctx, RED, 0.45)
        ctx.set_line_width(3)
        ctx.stroke_preserve()
        ctx.set_source_rgb(0.044, 0.044, 0.052)
        ctx.fill()

        rpm = f"{self._rpm:,}" if self._rpm is not None else "—"
        _text(ctx, rpm, cx, cy + 10, 28, center=True, bold=True)
        _text(ctx, "RPM" if self._rpm is not None else "NO SENSOR",
              cx, cy + 31, 10, (0.65, 0.65, 0.68), center=True)
        _text(ctx, self._name[:24], cx, height - 34, 13, center=True, bold=True)
        _text(ctx, "READ-ONLY FAN TELEMETRY", cx, height - 16, 9,
              (0.53, 0.53, 0.56), center=True)


class TelemetryGraph(Gtk.DrawingArea):
    """Actual timestamped samples, with breaks for absent sensor readings."""

    def __init__(self, title: str, field: str, extra: str | None = None):
        super().__init__()
        self.title = title
        self.field = field
        self.extra = extra
        self.history = MonitorHistory()
        self.set_content_height(180)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)
        self.update_property([Gtk.AccessibleProperty.LABEL], [f"{title} monitoring history"])

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
            self.update_property([Gtk.AccessibleProperty.LABEL],
                                 [f"{self.title}: {detail}; recent real readings"])
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

        # Quiet industrial grid; line density remains independent of values.
        for i in range(11):
            x = left + (right - left) * i / 10
            ctx.move_to(x, top)
            ctx.line_to(x, bottom)
        for i in range(5):
            y = top + (bottom - top) * i / 4
            ctx.move_to(left, y)
            ctx.line_to(right, y)
        _color(ctx, RED, 0.13)
        ctx.set_line_width(0.65)
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


def section(title: str, content: Gtk.Widget, *, min_height: int = 0) -> Gtk.Widget:
    """Shared clipped-panel composition; children remain normal GTK controls."""
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    outer.add_css_class("nitro-section")
    if min_height:
        outer.set_size_request(-1, min_height)
    outer.append(Ribbon(title))
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    body.set_margin_start(15)
    body.set_margin_end(15)
    body.set_margin_bottom(13)
    body.append(content)
    outer.append(body)
    return outer

"""Pure, read-only presentation of an observed hardware snapshot in a tray.

No GUI imports, external commands, device I/O, hardware writes or guessed
measurements. The standalone tray process uses the same HardwareReader as the
main dashboard, but samples it at a slower interval.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from .fan_presentation import fan_display_name
from .models import Snapshot


@dataclass(frozen=True, slots=True)
class TrayReadout:
    cpu_temp: str
    gpu_temp: str
    cpu_fan: str
    gpu_fan: str
    updated: str


def _temp(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "Unavailable"
    return f"{value:.0f} °C"


def _rpm(value: int | None) -> str:
    if value is None or value < 0:
        return "Unavailable"
    return f"{value:,} RPM"


def format_tray_snapshot(snapshot: Snapshot) -> TrayReadout:
    """Show reported channels only; never infer a missing fan or sensor."""
    fans = {
        fan_display_name(snapshot.model, fan.name): fan.rpm
        for fan in snapshot.fans
    }
    return TrayReadout(
        cpu_temp=f"CPU temperature: {_temp(snapshot.cpu_celsius)}",
        gpu_temp=f"GPU temperature: {_temp(snapshot.gpu.celsius if snapshot.gpu else None)}",
        cpu_fan=f"CPU fan: {_rpm(fans.get('CPU Fan'))}" if "CPU Fan" in fans
                else f"Fan 1: {_rpm(fans.get('Fan 1'))}",
        gpu_fan=f"GPU fan: {_rpm(fans.get('GPU Fan'))}" if "GPU Fan" in fans
                else f"Fan 2: {_rpm(fans.get('Fan 2'))}",
        updated=f"Last reading: {snapshot.sampled_at}",
    )

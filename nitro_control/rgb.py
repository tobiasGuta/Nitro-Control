"""Four-zone keyboard lighting plan and deliberately in-memory preview backend.

No real hardware adapter is provided in v0.3.0-preview. Never infer a
writable RGB endpoint from the presence of the Acer WMI GUID alone.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

_HEX = re.compile(r"#[0-9A-Fa-f]{6}\Z", re.ASCII)


class RGBValidationError(ValueError):
    """Invalid brightness, zone count, or RGB color."""


@dataclass(frozen=True, slots=True)
class LightingPlan:
    zones: tuple[str, str, str, str]
    brightness: int

    def __post_init__(self) -> None:
        if not isinstance(self.zones, tuple) or len(self.zones) != 4:
            raise RGBValidationError("Exactly four RGB zones are required")
        if any(not isinstance(color, str) or not _HEX.fullmatch(color) for color in self.zones):
            raise RGBValidationError("Each zone must be an #RRGGBB color")
        if type(self.brightness) is not int or not 0 <= self.brightness <= 100:
            raise RGBValidationError("Brightness must be an integer between 0 and 100")
        object.__setattr__(self, "zones", tuple(color.upper() for color in self.zones))


PRESETS: dict[str, LightingPlan] = {
    "Midnight": LightingPlan(("#5931B5", "#4536B6", "#245A9B", "#16869B"), 65),
    "Ocean": LightingPlan(("#174EA6", "#126BB7", "#1598B7", "#29B6A8"), 80),
    "Purple": LightingPlan(("#6225A9", "#7C3AC6", "#A64BC3", "#CC62A1"), 70),
    "Warm": LightingPlan(("#CC3D36", "#D66A32", "#DA9730", "#E1B84A"), 70),
}


class PreviewBackend:
    """In-memory example only: no filesystem, subprocess, D-Bus or WMI writes."""

    def __init__(self, initial: LightingPlan | None = None) -> None:
        self._applied = initial or PRESETS["Midnight"]

    def apply(self, plan: LightingPlan) -> LightingPlan:
        if not isinstance(plan, LightingPlan):
            raise RGBValidationError("Expected a validated lighting plan")
        self._applied = plan
        return self._applied

    def current(self) -> LightingPlan:
        return self._applied

    def reset(self) -> LightingPlan:
        self._applied = PRESETS["Midnight"]
        return self._applied

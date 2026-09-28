"""Constrained AN515-58 RGB adapters for documented kernel sysfs interfaces.

Presence of a WMI GUID is *not* a writable RGB endpoint. We never call ACPI/WMI,
load modules, or touch fan controls. A privileged, root-owned one-shot helper is
required for real writes; the unprivileged GUI only probes capabilities.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from . import RGB_WMI_GUID
from .rgb import LightingPlan

MODEL = "Nitro AN515-58"
LED_NAMES = tuple(f"acer-wmi::kbd_backlight_{i}" for i in range(1, 5))
LINUWU_PATHS = (
    "sys/devices/platform/acer-wmi/four_zoned_kb/per_zone_mode",
    "sys/module/linuwu_sense/drivers/platform:acer-wmi/acer-wmi/four_zoned_kb/per_zone_mode",
)
_HEX = re.compile(r"[0-9a-fA-F]{6}\Z", re.ASCII)


class RGBHardwareError(RuntimeError):
    """No supported endpoint, invalid metadata, denied write or failed verification."""


@dataclass(frozen=True, slots=True)
class RGBCapability:
    backend: str  # none, native-led, linuwu-sense
    description: str
    available: bool
    detail: str


@dataclass(frozen=True, slots=True)
class RGBWriteResult:
    backend: str
    verified_readback: bool
    detail: str


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise RGBHardwareError(f"Cannot read expected RGB attribute {path.name}: {exc}") from exc


def _numbers(path: Path) -> tuple[int, ...]:
    try:
        result = tuple(int(item) for item in _read(path).split())
    except ValueError as exc:
        raise RGBHardwareError(f"Invalid RGB attribute {path.name}") from exc
    if not result:
        raise RGBHardwareError(f"Empty RGB attribute {path.name}")
    return result


def _channels(path: Path) -> tuple[int, int, int]:
    names = _read(path).lower().split()
    if sorted(names) == ["blue", "green", "red"]:
        return (names.index("red"), names.index("green"), names.index("blue"))
    # Standard LED_COLOR_ID_RED/GREEN/BLUE values, if the kernel exposes IDs.
    if sorted(names) == ["1", "2", "3"]:
        return (names.index("1"), names.index("2"), names.index("3"))
    raise RGBHardwareError("Unsupported LED color-channel order")


def _write(path: Path, value: str) -> None:
    try:
        # Only whitelisted, already-discovered sysfs attributes reach here.
        with path.open("w", encoding="ascii") as target:
            target.write(value + "\n")
    except OSError as exc:
        raise RGBHardwareError(f"RGB write refused for {path.name}: {exc}") from exc


def _linuwu_value(plan: LightingPlan) -> str:
    return ",".join([*(color[1:].lower() for color in plan.zones), str(plan.brightness)])


def _parse_linuwu(value: str) -> tuple[tuple[str, ...], int]:
    chunks = value.strip().split(",")
    if len(chunks) != 5 or not all(_HEX.fullmatch(c) for c in chunks[:4]):
        raise RGBHardwareError("Linuwu returned an unrecognized RGB value")
    try:
        brightness = int(chunks[4])
    except ValueError as exc:
        raise RGBHardwareError("Linuwu returned invalid brightness") from exc
    if not 0 <= brightness <= 100:
        raise RGBHardwareError("Linuwu returned brightness out of range")
    return tuple(c.lower() for c in chunks[:4]), brightness


class RGBHardware:
    """Probe the exact model and interface; apply only a validated LightingPlan."""

    def __init__(self, root: Path = Path("/")):
        self.root = Path(root)

    def path(self, relative: str) -> Path:
        return self.root / relative

    def _eligible(self) -> bool:
        try:
            model = _read(self.path("sys/class/dmi/id/product_name"))
        except RGBHardwareError:
            return False
        return (model == MODEL and any(self.path("sys/bus/wmi/devices").glob(f"{RGB_WMI_GUID}-*")))

    def _native(self) -> tuple[Path, ...] | None:
        base = self.path("sys/class/leds")
        zones = tuple(base / name for name in LED_NAMES)
        if not all(p.is_dir() for p in zones):
            return None
        for zone in zones:
            for name in ("multi_index", "multi_intensity", "brightness", "max_brightness"):
                if not (zone / name).is_file():
                    raise RGBHardwareError(f"Incomplete native RGB zone: {name}")
            _channels(zone / "multi_index")
            maxima = _numbers(zone / "multi_max_intensity") if (zone / "multi_max_intensity").is_file() else (255, 255, 255)
            if len(maxima) != 3 or any(n < 1 or n > 65535 for n in maxima):
                raise RGBHardwareError("Unsupported native RGB channel maxima")
            if len(_numbers(zone / "multi_intensity")) != 3:
                raise RGBHardwareError("Invalid native RGB intensity count")
            try:
                maximum = int(_read(zone / "max_brightness"))
            except ValueError as exc:
                raise RGBHardwareError("Invalid native RGB brightness metadata") from exc
            if not 1 <= maximum <= 65535:
                raise RGBHardwareError("Invalid native RGB brightness maximum")
        return zones

    def _linuwu(self) -> Path | None:
        for name in LINUWU_PATHS:
            candidate = self.path(name)
            if candidate.is_file():
                _parse_linuwu(_read(candidate))
                return candidate
        return None

    def probe(self) -> RGBCapability:
        if not self._eligible():
            return RGBCapability("none", "Unavailable", False, "Exact AN515-58 model and numbered RGB WMI interface required")
        try:
            if self._native():
                return RGBCapability("native-led", "Native Linux multicolor LEDs", True, "Four named Acer keyboard zones detected")
            if self._linuwu():
                return RGBCapability("linuwu-sense", "Linuwu Sense", True, "Documented four-zone sysfs control detected")
        except RGBHardwareError as exc:
            return RGBCapability("none", "Unavailable", False, str(exc))
        return RGBCapability("none", "Unavailable", False, "WMI GUID present, but no supported RGB sysfs writer is exposed")

    def apply(self, plan: LightingPlan) -> RGBWriteResult:
        if not isinstance(plan, LightingPlan):
            raise RGBHardwareError("A validated four-zone lighting plan is required")
        cap = self.probe()  # Recheck current hardware immediately before writing.
        if not cap.available:
            raise RGBHardwareError(cap.detail)
        if cap.backend == "native-led":
            return self._apply_native(plan)
        if cap.backend == "linuwu-sense":
            return self._apply_linuwu(plan)
        raise RGBHardwareError("Unsupported RGB backend")

    def _apply_native(self, plan: LightingPlan) -> RGBWriteResult:
        zones = self._native()
        if zones is None:
            raise RGBHardwareError("Native RGB endpoint disappeared")
        backup: list[tuple[Path, str, str]] = []
        try:
            for zone, color in zip(zones, plan.zones):
                intensity_path, brightness_path = zone / "multi_intensity", zone / "brightness"
                old_intensity, old_brightness = _read(intensity_path), _read(brightness_path)
                backup.append((zone, old_intensity, old_brightness))
                indices = _channels(zone / "multi_index")
                maxima = _numbers(zone / "multi_max_intensity") if (zone / "multi_max_intensity").is_file() else (255, 255, 255)
                wanted = [0, 0, 0]
                for component, channel_index in zip((int(color[n:n + 2], 16) for n in (1, 3, 5)), indices):
                    wanted[channel_index] = round(component * maxima[channel_index] / 255)
                brightness = round(int(_read(zone / "max_brightness")) * plan.brightness / 100)
                _write(intensity_path, " ".join(map(str, wanted)))
                _write(brightness_path, str(brightness))  # Triggers the patch's brightness callback.
                if _numbers(intensity_path) != tuple(wanted) or _numbers(brightness_path) != (brightness,):
                    raise RGBHardwareError("Native RGB readback differs from the requested values")
        except (RGBHardwareError, OSError) as exc:
            # Best-effort rollback of all touched zones, including a partial final one.
            rollback_errors = []
            for zone, old_intensity, old_brightness in reversed(backup):
                try:
                    _write(zone / "multi_intensity", old_intensity)
                    _write(zone / "brightness", old_brightness)
                except RGBHardwareError as error:
                    rollback_errors.append(str(error))
            extra = " (rollback incomplete: " + "; ".join(rollback_errors) + ")" if rollback_errors else " (previous sysfs values restored)"
            raise RGBHardwareError(str(exc) + extra) from exc
        return RGBWriteResult("native-led", True, "Four sysfs zones accepted and returned the requested values; verify the actual keyboard visually")

    def _apply_linuwu(self, plan: LightingPlan) -> RGBWriteResult:
        path = self._linuwu()
        if path is None:
            raise RGBHardwareError("Linuwu RGB endpoint disappeared")
        previous = _read(path)
        wanted = _linuwu_value(plan)
        _write(path, wanted)
        try:
            found = _parse_linuwu(_read(path))
            expected = (tuple(c[1:].lower() for c in plan.zones), plan.brightness)
            if found != expected:
                raise RGBHardwareError("Linuwu RGB readback differs from requested colors")
        except RGBHardwareError as exc:
            try:
                _write(path, previous)
            except RGBHardwareError as rollback:
                raise RGBHardwareError(f"{exc}; rollback also failed: {rollback}") from exc
            raise RGBHardwareError(f"{exc}; previous sysfs value restored") from exc
        return RGBWriteResult("linuwu-sense", True, "Linuwu sysfs accepted and returned requested values; verify the actual keyboard visually")

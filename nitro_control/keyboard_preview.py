"""Four-zone keyboard geometry and software-only lighting frames."""

from __future__ import annotations

from colorsys import hsv_to_rgb
import math

EFFECTS = ("Breathing", "Wave", "Zoom", "Shifting", "Neon")
COLOR_EFFECTS = frozenset({"Breathing", "Zoom", "Shifting"})
SPATIAL_EFFECTS = frozenset({"Wave", "Zoom", "Shifting"})

# Key coordinates are in key-width units. The four lighting zones divide the
# deck from left to right. Wide keys can cross a zone boundary.
DECK_WIDTH = 19.4
DECK_HEIGHT = 6.03
KEY_GAP = 0.10


def _keyboard_keys():
    rows = (
        ((1, "Esc"), *((1, f"F{i}") for i in range(1, 13)),
         (1, "PrtSc"), (1, "Del")),
        (*((1, key) for key in "`1234567890-="), (2, "Backspace")),
        ((1.5, "Tab"), *((1, key) for key in "QWERTYUIOP[]"), (1.5, "\\")),
        ((1.75, "Caps"), *((1, key) for key in "ASDFGHJKL;'"), (2.25, "Enter")),
        ((2.25, "Shift"), *((1, key) for key in "ZXCVBNM,./"), (1.75, "Shift"), (1, "Up")),
        ((1.25, "Ctrl"), (1, "Fn"), (1.25, "Super"), (1.25, "Alt"), (5.5, ""),
         (1, "Alt"), (0.75, "Ctrl"), (1, "Left"), (1, "Down"), (1, "Right")),
    )
    for row, keys in enumerate(rows):
        x = 0.0
        y = row + 0.13 if row else 0.0
        for width, label in keys:
            yield x, y, width - KEY_GAP, (0.80 if row == 0 else 1.0) - KEY_GAP, label
            x += width

    numpad = (
        ((1, "Home"), (1, "End"), (1, "PgUp"), (1, "PgDn")),
        ((1, "Num"), (1, "/"), (1, "*"), (1, "−")),
        ((1, "7"), (1, "8"), (1, "9"), (1, "+")),
        ((1, "4"), (1, "5"), (1, "6")),
        ((1, "1"), (1, "2"), (1, "3"), (1, "Enter")),
        ((2, "0"), (1, ".")),
    )
    for row, keys in enumerate(numpad):
        x = 15.5
        y = row + 0.13 if row else 0.0
        for width, label in keys:
            height = 0.80 if row == 0 else 1.0
            if x == 18.5 and row in (2, 4):
                height = 2.0
            yield x, y, width - KEY_GAP, height - KEY_GAP, label
            x += width


KEYS = tuple(_keyboard_keys())


def zone_at(x: float, y: float) -> int | None:
    if 0 <= x < DECK_WIDTH and 0 <= y < DECK_HEIGHT:
        return int(x * 4 / DECK_WIDTH)
    return None


def lighting_frame(zones: tuple[str, ...], brightness: int, *,
                   effect: str | None = None, color: str = "#FF475D",
                   speed: int = 4, elapsed: float = 0) -> tuple[tuple[float, ...], ...]:
    """Calculate four display colors. Dynamic timing is an approximation."""
    level = brightness / 100
    if effect is None:
        return tuple(tuple(component * level for component in _rgb(zone)) for zone in zones)

    phase = _phase(speed, elapsed)
    base = _rgb(color)
    match effect:
        case "Breathing":
            rgb = tuple(component * (0.20 + 0.80 * _pulse(phase)) for component in base)
        case "Neon":
            rgb = hsv_to_rgb(phase, 0.93, 1)
        case _:
            raise ValueError(f"Unknown uniform lighting effect: {effect}")
    return (tuple(component * level for component in rgb),) * 4


def lighting_gradient(effect: str, brightness: int, *, color: str,
                      speed: int, elapsed: float) -> tuple[tuple[float, ...], ...]:
    """Sample a continuous light field across the preview for Cairo."""
    phase = _phase(speed, elapsed)
    level = brightness / 100
    stops = tuple(index / 36 for index in range(37))
    if effect == "Wave":
        # Each hue moves left to right through the same continuous spectrum.
        return tuple(tuple(component * level for component in hsv_to_rgb((x - phase) % 1, 0.93, 1))
                     for x in stops)
    if effect == "Shifting":
        base = _rgb(color)
        # Move a broad band of the chosen color across the deck. Varying
        # intensity alone keeps the same hue at every position.
        intensities = ((0.12 + 0.88 * _pulse(x - phase + 0.5) ** 3) * level for x in stops)
        return tuple(tuple(component * intensity for component in base)
                     for intensity in intensities)
    if effect == "Zoom":
        # Ease a symmetric beam out from the center and back. A soft edge
        # stays visible between keys as the beam crosses the deck.
        radius = 0.32 + 0.92 * _pulse(phase)
        base = _rgb(color)
        intensities = (_zoom_intensity(x, radius) * level for x in stops)
        return tuple(tuple(component * intensity for component in base)
                     for intensity in intensities)
    raise ValueError(f"Unknown spatial lighting effect: {effect}")


def _phase(speed: int, elapsed: float) -> float:
    return (elapsed * (0.16 + 0.085 * speed)) % 1


def _zoom_intensity(position: float, radius: float) -> float:
    distance = abs(2 * position - 1)
    edge = min(1, max(0, (radius - distance) / 0.32))
    return 0.08 + 0.92 * edge * edge * (3 - 2 * edge)


def _rgb(color: str) -> tuple[float, float, float]:
    return tuple(int(color[index:index + 2], 16) / 255 for index in (1, 3, 5))


def _pulse(phase: float) -> float:
    return (1 - math.cos(math.tau * phase)) / 2

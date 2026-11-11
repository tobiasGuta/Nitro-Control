"""Presentation-only fan motion derived from *observed* RPM.

This is intentionally NOT 1:1 physical rotation: at 2,000 RPM the actual
fan rotates more than 30 times a second, which a GUI cannot meaningfully
depict at desktop refresh rates. Instead the on-screen turbine is slowed
down consistently while preserving relative speeds between fan readings.

There is no hardware I/O, PWM control, or fan RPM limit in this module.
"""
from __future__ import annotations

import math

# Readability and anti-aliasing settings for the 28-blade Cairo turbine:
# 2,250 RPM -> 0.15 visible turns/second; 6,000 RPM -> 0.4.
# The ceiling is purely a VISUAL animation bound, not a hardware limit.
_VISUAL_DIVISOR = 15_000
_MAX_ANIMATED_RPM = 7_500
_MAX_FRAME_SECONDS = 0.10  # Avoid fast-forward after sleep / a paused UI.


def fan_angle_step(rpm: int | None, elapsed_seconds: float) -> float:
    """Return the clockwise rotor angle increment in radians.

    Stationary or unavailable fans don't animate. Clamp unusually large
    frame gaps to prevent a sudden jump when resuming after suspend.
    """
    if not isinstance(rpm, int) or isinstance(rpm, bool) or rpm <= 0:
        return 0.0
    if not isinstance(elapsed_seconds, (int, float)):
        return 0.0
    if not math.isfinite(elapsed_seconds) or elapsed_seconds <= 0:
        return 0.0
    duration = min(float(elapsed_seconds), _MAX_FRAME_SECONDS)
    visual_turns = min(rpm, _MAX_ANIMATED_RPM) / _VISUAL_DIVISOR * duration
    return visual_turns * math.tau

"""Read-only fan captions derived from upstream acer-wmi channel identities.

The mainline acer-wmi hwmon channel table maps fan channel 0 to
ACER_WMID_SENSOR_CPU_FAN_SPEED (fan1_input) and channel 1 to
ACER_WMID_SENSOR_GPU_FAN_SPEED (fan2_input).

Only apply these names to *unlabeled* Acer HWMON fans on the reviewed Nitro
AN515-58. Preserve explicit kernel labels and keep unknown models generic.
No fan control or sensor collection occurs in this module.

Source: linux/drivers/platform/x86/acer-wmi.c
  acer_wmi_fan_channel_to_sensor_id[]
"""
from __future__ import annotations


_AN515_58_MODELS = frozenset({
    "an515-58",
    "nitro an515-58",
    "acer nitro an515-58",
    "acer nitro an515-58 (demo)",
})
_CHANNEL_NAMES = {"Fan 1": "CPU Fan", "Fan 2": "GPU Fan"}


def fan_display_name(model: str, sensor_name: str) -> str:
    """Map only the known AN515-58 generic hwmon channels to CPU/GPU."""
    if model.strip().casefold() in _AN515_58_MODELS:
        return _CHANNEL_NAMES.get(sensor_name, sensor_name)
    return sensor_name


def fan_mode_description(raw_mode: str) -> str:
    """Never imply Auto/Manual/Turbo when PWM mode telemetry is missing."""
    if not raw_mode or raw_mode == "Unknown":
        return "Mode not reported"
    return raw_mode

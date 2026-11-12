"""Read-only inventory of stock Acer hwmon fan interfaces.

This module never writes sysfs, invokes WMI setters, loads modules, or starts
hardware services. It only opens existing kernel-exported files for reading.

A present pwm attribute is NOT proof that an AN515-58 firmware accepts writes
or that speed settings are independent of the active power profile.
"""

from __future__ import annotations

from pathlib import Path
import re

_FAN_INPUT = re.compile(r"fan([1-9][0-9]*)_input\Z")
_PWM_VALUE = re.compile(r"pwm([1-9][0-9]*)\Z")
_PWM_ENABLE = re.compile(r"pwm([1-9][0-9]*)_enable\Z")
_MAX_CHANNEL = 32


def _read_text(path: Path) -> str | None:
    """Bounded, unprivileged text read; an unreadable attribute stays unknown."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            value = handle.read(256).strip()
        return value or None
    except (OSError, ValueError):
        return None


def _nonnegative_integer(value: str | None, maximum: int) -> int | None:
    if value is None or not value.isascii() or not value.isdecimal():
        return None
    number = int(value)
    return number if 0 <= number <= maximum else None


def _read_number_with_errno(path: Path, maximum: int) -> tuple[int | None, int | None]:
    """Read a numeric hwmon attribute, preserving only its POSIX error number.

    Error message/path details are deliberately not included in JSON output.
    The numeric value remains unknown on any read failure.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            raw = handle.read(256).strip()
    except OSError as exc:
        return None, exc.errno
    except ValueError:
        return None, None
    return _nonnegative_integer(raw, maximum), None


def _channel_numbers(hwmon: Path) -> list[int]:
    numbers = set()
    try:
        children = list(hwmon.iterdir())
    except OSError:
        return []
    for child in children:
        for pattern in (_FAN_INPUT, _PWM_VALUE, _PWM_ENABLE):
            match = pattern.fullmatch(child.name)
            if match:
                number = int(match.group(1))
                if number <= _MAX_CHANNEL:
                    numbers.add(number)
                break
    return sorted(numbers)


def collect_fan_probe(root: Path = Path("/")) -> dict:
    """Inspect only existing sysfs/proc files, without creating or changing any."""
    root = Path(root)
    sys = root / "sys"
    dmi = sys / "class/dmi/id"
    hwmon_root = sys / "class/hwmon"

    stock_driver_loaded = (sys / "module/acer_wmi").is_dir()
    linuwu_driver_loaded = (sys / "module/linuwu_sense").is_dir()
    devices: list[dict] = []

    for hwmon in sorted(hwmon_root.glob("hwmon*")):
        if _read_text(hwmon / "name") != "acer":
            continue
        channels: list[dict] = []
        for number in _channel_numbers(hwmon):
            fan_path = hwmon / f"fan{number}_input"
            pwm_path = hwmon / f"pwm{number}"
            enable_path = hwmon / f"pwm{number}_enable"
            fan_present = fan_path.exists()
            pwm_present = pwm_path.exists()
            enable_present = enable_path.exists()
            rpm = _nonnegative_integer(_read_text(fan_path), 100_000) if fan_present else None
            pwm = _nonnegative_integer(_read_text(pwm_path), 255) if pwm_present else None
            mode, mode_read_errno = _read_number_with_errno(enable_path, 2) if enable_present else (None, None)

            if not pwm_present and not enable_present:
                status = "not_exposed"
            elif pwm_present and enable_present and pwm is not None and mode is not None:
                status = "readable"
            else:
                status = "partial_or_unreadable"

            # This is the stock upstream acer-wmi interpretation, NOT a
            # promise about the live firmware state or other hwmon drivers.
            mode_label = None
            if stock_driver_loaded and not linuwu_driver_loaded:
                mode_label = {0: "turbo", 1: "custom", 2: "automatic"}.get(mode)

            channels.append({
                "channel": number,
                "fan_rpm_present": fan_present,
                "fan_rpm": rpm,
                "pwm_present": pwm_present,
                "pwm_255": pwm,
                "pwm_enable_present": enable_present,
                "pwm_enable": mode,
                "pwm_enable_read_errno": mode_read_errno,
                "upstream_acer_wmi_mode": mode_label,
                "pwm_readback_status": status,
            })

        devices.append({
            "hwmon": hwmon.name,
            "name": "acer",
            "channels": channels,
        })

    return {
        "schema_version": 1,
        "read_only": True,
        "model": _read_text(dmi / "product_name"),
        "vendor": _read_text(dmi / "sys_vendor"),
        "bios_version": _read_text(dmi / "bios_version"),
        "kernel_release": _read_text(root / "proc/sys/kernel/osrelease"),
        "stock_acer_wmi_loaded": stock_driver_loaded,
        "linuwu_sense_loaded": linuwu_driver_loaded,
        "firmware_platform_profile": _read_text(sys / "firmware/acpi/platform_profile"),
        "acer_hwmon_devices": devices,
        "interpretation": (
            "Inventory only. PWM file presence/readback does not verify write "
            "support, safe auto restoration, or independence from the power profile."
        ),
    }

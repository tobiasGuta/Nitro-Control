"""Read-only hardware discovery using stable kernel sysfs interfaces.

No sysfs write, modprobe, sudo, hardware method call, or network access occurs here.
Sensor identities not provided by a driver are intentionally not guessed.
"""

from __future__ import annotations

import csv
import math
from datetime import datetime
from pathlib import Path
import re
import subprocess
from collections.abc import Callable

from . import RGB_WMI_GUID
from .models import Fan, GPU, RGB, Snapshot, Temperature

Runner = Callable[..., subprocess.CompletedProcess[str]]
_SENSOR_NAME = re.compile(r"^(?:temp|fan)(\d+)_input$")


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None


def _numeric(text: str | None) -> float | None:
    if text is None or text.strip().lower() in {"", "n/a", "[not supported]"}:
        return None
    try:
        number = float(text)
        return number if math.isfinite(number) else None
    except (ValueError, OverflowError):
        return None


def _degrees(raw: str | None) -> float | None:
    value = _numeric(raw)
    return value / 1000 if value is not None else None


def _fan_mode(raw: str | None) -> str:
    if raw is None:
        return "Unknown"
    try:
        mode = int(raw)
    except ValueError:
        return "Unknown"
    if mode == 0:
        return "Full speed / control disabled"
    if mode == 1:
        return "Manual"
    if mode >= 2:
        return "Automatic"
    return "Unknown"


def _gpu_from_csv(output: str) -> GPU | None:
    """Parse NVIDIA's six requested CSV fields; ignore malformed output safely."""
    for row in csv.reader(output.splitlines()):
        if len(row) != 6:
            continue
        name = row[0].strip()
        if not name or name.lower() == "name":
            continue
        values = [_numeric(value) for value in row[1:]]
        return GPU(name, *values)
    return None


class HardwareReader:
    """Injectable sysfs root and command runner keep collection testable offline."""

    def __init__(self, root: Path = Path("/"), runner: Runner = subprocess.run):
        self.root = Path(root)
        self.runner = runner

    def path(self, name: str) -> Path:
        return self.root / name.lstrip("/")

    def _hwmon(self) -> list[tuple[str, Path]]:
        devices = []
        for path in sorted(self.path("sys/class/hwmon").glob("hwmon*")):
            name = _read(path / "name")
            if name:
                devices.append((name, path))
        return devices

    def _cpu(self, devices: list[tuple[str, Path]]) -> float | None:
        # Prefer an explicitly identified CPU-package sensor over an ACPI reading.
        for name, path in devices:
            if name == "coretemp":
                for file in sorted(path.glob("temp*_input")):
                    label = _read(file.with_name(file.name.replace("_input", "_label")))
                    if label and label.lower().startswith("package id"):
                        temp = _degrees(_read(file))
                        if temp is not None:
                            return temp
        for name, path in devices:
            if name == "k10temp":
                for file in sorted(path.glob("temp*_input")):
                    label = _read(file.with_name(file.name.replace("_input", "_label")))
                    if label in {"Tctl", "Tdie"}:
                        temp = _degrees(_read(file))
                        if temp is not None:
                            return temp
        return None

    def _acer(self, devices: list[tuple[str, Path]]) -> tuple[tuple[Fan, ...], tuple[Temperature, ...]]:
        fans: list[Fan] = []
        temperatures: list[Temperature] = []
        for name, path in devices:
            if name != "acer":
                continue
            for file in sorted(path.glob("fan*_input")):
                match = _SENSOR_NAME.fullmatch(file.name)
                if not match:
                    continue
                number = match.group(1)
                label = _read(path / f"fan{number}_label") or f"Fan {number}"
                value = _numeric(_read(file))
                fans.append(Fan(label, int(value) if value is not None and value >= 0 else None,
                                _fan_mode(_read(path / f"pwm{number}_enable"))))
            for file in sorted(path.glob("temp*_input")):
                match = _SENSOR_NAME.fullmatch(file.name)
                if not match:
                    continue
                number = match.group(1)
                temp = _degrees(_read(file))
                if temp is not None:
                    label = _read(path / f"temp{number}_label") or f"Acer sensor {number} (unidentified)"
                    temperatures.append(Temperature(label, temp, "acer"))
        return tuple(fans), tuple(temperatures)

    def _drives(self, devices: list[tuple[str, Path]]) -> tuple[Temperature, ...]:
        drives = []
        for name, path in devices:
            if name != "nvme":
                continue
            temp = _degrees(_read(path / "temp1_input"))
            if temp is not None:
                drives.append(Temperature(f"NVMe drive {len(drives) + 1}", temp, "nvme"))
        return tuple(drives)

    def _gpu(self) -> GPU | None:
        try:
            process = self.runner(
                ["nvidia-smi", "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw",
                 "--format=csv,noheader,nounits"],
                check=False, capture_output=True, text=True, timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if process.returncode != 0:
            return None
        return _gpu_from_csv(process.stdout)

    def _tuned(self) -> str | None:
        try:
            process = self.runner(["tuned-adm", "active"], check=False, capture_output=True, text=True, timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if process.returncode != 0:
            return None
        result = process.stdout.strip()
        prefix = "Current active profile:"
        if result.startswith(prefix):
            return result[len(prefix):].strip() or None
        return None

    def _rgb(self) -> RGB:
        devices = sorted(self.path("sys/bus/wmi/devices").glob(f"{RGB_WMI_GUID}-*"))
        if not devices:
            return RGB(False, None, None)
        device = devices[0]
        driver = device / "driver"
        # WMI device names include an instance suffix; an unbound device has no driver symlink.
        if driver.is_symlink():
            try:
                driver_name = driver.resolve().name
            except OSError:
                driver_name = None
        else:
            driver_name = None
        return RGB(True, driver_name, device.name)

    def collect(self) -> Snapshot:
        devices = self._hwmon()
        fans, acer_temperatures = self._acer(devices)
        firmware_profile = _read(self.path("sys/firmware/acpi/platform_profile"))
        choices = _read(self.path("sys/firmware/acpi/platform_profile_choices"))
        model = _read(self.path("sys/class/dmi/id/product_name")) or "Unknown device"
        return Snapshot(
            sampled_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            model=model,
            cpu_celsius=self._cpu(devices),
            fans=fans,
            acer_temperatures=acer_temperatures,
            drives=self._drives(devices),
            gpu=self._gpu(),
            firmware_profile=firmware_profile,
            firmware_profile_choices=tuple(choices.split()) if choices else (),
            tuned_profile=self._tuned(),
            rgb=self._rgb(),
        )

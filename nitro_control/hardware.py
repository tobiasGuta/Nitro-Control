"""Read-only hardware discovery using stable kernel sysfs interfaces.

No sysfs write, modprobe, sudo, hardware method call, or network access occurs here.
Sensor identities not provided by a driver are intentionally not guessed.
"""

from __future__ import annotations

import csv
import math
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from time import monotonic

from . import RGB_WMI_GUID
from .fan_control import find_fan_controls
from .models import GPU, RGB, Fan, Snapshot, Temperature

Runner = Callable[..., subprocess.CompletedProcess[str]]
_SENSOR_NAME = re.compile(r"^(?:temp|fan)(\d+)_input$")
_INDEX_PART = re.compile(r"(\d+)")
_PCI_ADDRESS = re.compile(r"([0-9a-f]{4,8}):([0-9a-f]{2}:[0-9a-f]{2}\.[0-7])", re.IGNORECASE)


def _sensor_sort_key(path: Path) -> tuple[int, str]:
    """Order hwmonN, fanN_input, and tempN_input numerically before fallback names."""
    match = _INDEX_PART.search(path.name)
    return (int(match.group(1)), path.name) if match else (sys.maxsize, path.name)


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
    if raw is None or not re.fullmatch(r"[+-]?[0-9]{1,9}", raw):
        return None
    value = int(raw) / 1000
    return value if -273.15 <= value <= 1000 else None


def _fan_mode(raw: str | None, *, max_mode: int | None = None) -> str:
    if raw is None:
        return "Unknown"
    try:
        mode = int(raw)
    except ValueError:
        return "Unknown"
    if max_mode is not None and mode > max_mode:
        return "Unknown"
    if mode == 0:
        return "Full speed / control disabled"
    if mode == 1:
        return "Manual"
    if mode >= 2:
        return "Automatic"
    return "Unknown"


def _pci_address(text: str) -> str | None:
    match = _PCI_ADDRESS.fullmatch(text.strip())
    if match is None:
        return None
    return f"{int(match[1], 16):04x}:{match[2].lower()}"


def _gpu_from_csv(output: str, address: str | None = None) -> GPU | None:
    """Accept just one matching record, regardless of NVIDIA's enumeration order."""
    candidates = []
    for row in csv.reader(output.splitlines()):
        if len(row) != 7:
            continue
        bus = _pci_address(row[0])
        name = row[1].strip()
        if not bus or not name or (address is not None and bus != address):
            continue
        temp, utilization, used, total, power = (_numeric(value) for value in row[2:])
        candidates.append(GPU(
            name,
            temp if temp is not None and -273.15 <= temp <= 1000 else None,
            utilization if utilization is not None and 0 <= utilization <= 100 else None,
            used if used is not None and used >= 0 else None,
            total if total is not None and total > 0 else None,
            power if power is not None and power >= 0 else None,
            pci_bus_id=bus,
        ))
    return candidates[0] if len(candidates) == 1 else None


class HardwareReader:
    """Injectable sysfs root and command runner keep collection testable offline."""

    def __init__(self, root: Path = Path("/"), runner: Runner = subprocess.run, *,
                 gpu_poll_interval: float = 0, clock: Callable[[], float] = monotonic):
        self.root = Path(root)
        self.runner = runner
        self._gpu_poll_interval = gpu_poll_interval
        self._clock = clock
        self._last_gpu_query: float | None = None

    def path(self, name: str) -> Path:
        return self.root / name.lstrip("/")

    def _hwmon(self) -> list[tuple[str, Path]]:
        devices = []
        for path in sorted(self.path("sys/class/hwmon").glob("hwmon*"), key=_sensor_sort_key):
            name = _read(path / "name")
            if name:
                devices.append((name, path))
        return devices

    def _cpu_sensor_identity(self, driver: str, sensor: Path, label: str) -> str:
        hwmon = sensor.parent
        devices = self.root.resolve() / "sys/devices"
        try:
            backing = (hwmon / "device").resolve(strict=True)
        except (OSError, RuntimeError):
            # Class entries normally point to <device>/hwmon/hwmonN.
            try:
                resolved = hwmon.resolve(strict=True)
                backing = resolved.parent.parent if resolved.parent.name == "hwmon" else resolved
            except (OSError, RuntimeError):
                backing = hwmon
        if backing.is_relative_to(devices) and backing != devices / "virtual":
            return f"{driver}:{backing.relative_to(devices)}:{label}"
        if driver == "coretemp":
            # coretemp's package label includes the package number.
            return f"{driver}:{label}"
        # Without a backing device, keep identically labeled AMD slots apart.
        return f"{driver}:{hwmon.name}:{label}"

    def _cpu(self, devices: list[tuple[str, Path]]) -> tuple[float | None, str | None, str | None]:
        for name, path in devices:
            if name == "coretemp":
                for file in sorted(path.glob("temp*_input"), key=_sensor_sort_key):
                    label = _read(file.with_name(file.name.replace("_input", "_label")))
                    if label and label.lower().startswith("package id"):
                        temp = _degrees(_read(file))
                        if temp is not None:
                            return temp, "Intel CPU package", self._cpu_sensor_identity(name, file, label.casefold())
        amd_sources = {}
        for name, path in devices:
            if name == "k10temp":
                for file in sorted(path.glob("temp*_label"), key=_sensor_sort_key):
                    label = _read(file)
                    if label in {"Tctl", "Tdie"} and label not in amd_sources:
                        amd_sources[label] = file.with_name(file.name.replace("_label", "_input"))
        for label, description in (("Tdie", "AMD CPU die (Tdie)"),
                                   ("Tctl", "AMD CPU control temperature (Tctl)")):
            if label in amd_sources:
                sensor = amd_sources[label]
                return _degrees(_read(sensor)), description, self._cpu_sensor_identity("k10temp", sensor, label)
        return None, None, None

    def _acer_hwmon_devices(self, devices: list[tuple[str, Path]]) -> tuple[list[Path], Path | None]:
        """Prefer the verified stock acer-wmi hwmon device before fallback class scans."""
        control_hwmon = find_fan_controls(self.root)
        if control_hwmon is not None:
            return [control_hwmon], control_hwmon
        if (_read(self.path("sys/class/dmi/id/sys_vendor")) or "Acer").casefold() != "acer":
            return [], None
        platform = self.path("sys/devices/platform/acer-wmi")
        if platform.is_dir():
            candidates = [path for path in (platform / "hwmon").glob("hwmon*")
                          if _read(path / "name") == "acer"]
        else:
            candidates = [path for name, path in devices if name == "acer"]
        # A class entry may alias the platform device. Multiple physical
        # candidates leave channel identity ambiguous.
        targets = set()
        for path in candidates:
            try:
                targets.add(path.resolve(strict=True))
            except (OSError, RuntimeError):
                continue
        return (list(targets) if len(targets) == 1 else []), None

    def _acer(self, devices: list[tuple[str, Path]]) -> tuple[tuple[Fan, ...], tuple[Temperature, ...]]:
        fans: list[Fan] = []
        temperatures: list[Temperature] = []
        targets, control_hwmon = self._acer_hwmon_devices(devices)
        for path in targets:
            max_mode = 2 if control_hwmon is not None and path == control_hwmon else None
            for file in sorted(path.glob("fan*_input"), key=_sensor_sort_key):
                match = _SENSOR_NAME.fullmatch(file.name)
                if not match:
                    continue
                number = match.group(1)
                label = _read(path / f"fan{number}_label") or f"Fan {number}"
                raw = _read(file)
                rpm = int(raw) if raw and re.fullmatch(r"[0-9]{1,10}", raw) else None
                fans.append(Fan(label, rpm,
                                _fan_mode(_read(path / f"pwm{number}_enable"), max_mode=max_mode),
                                channel=int(number)))
            for file in sorted(path.glob("temp*_input"), key=_sensor_sort_key):
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
        for index, path in enumerate((path for name, path in devices if name == "nvme"), 1):
            temp = _degrees(_read(path / "temp1_input"))
            if temp is not None:
                drives.append(Temperature(f"NVMe drive {index}", temp, "nvme"))
        return tuple(drives)

    def _gpu(self) -> tuple[GPU | None, str | None]:
        pci_devices = self.path("sys/bus/pci/devices")
        try:
            candidates = [
                path for path in pci_devices.iterdir()
                if _pci_address(path.name) and _read(path / "vendor") == "0x10de"
                and (_read(path / "class") or "").startswith("0x03")
            ]
            inventory_available = True
        except OSError:
            candidates = []
            inventory_available = False
        if len(candidates) > 1:
            return None, "Multiple NVIDIA display adapters detected"
        if inventory_available and not candidates:
            return None, "No NVIDIA display adapter detected"

        args = ["nvidia-smi"]
        address = None
        if candidates:
            device = candidates[0]
            runtime_status = _read(device / "power/runtime_status")
            if runtime_status in {"suspended", "suspending", "resuming"}:
                return None, f"NVIDIA GPU runtime power state: {runtime_status}"
            if self._gpu_poll_interval and runtime_status not in {"active", "unsupported"}:
                return None, "NVIDIA GPU runtime power state unavailable"
            address = _pci_address(device.name)
            args.append(f"--id={address}")
        elif self._gpu_poll_interval:
            return None, "PCI device inventory unavailable"
        now = self._clock()
        if self._last_gpu_query is not None and now - self._last_gpu_query < self._gpu_poll_interval:
            return None, "Waiting for the next NVIDIA reading"
        self._last_gpu_query = now
        try:
            process = self.runner(
                [*args, "--query-gpu=pci.bus_id,name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw",
                 "--format=csv,noheader,nounits"],
                check=False, capture_output=True, text=True, timeout=2,
            )
        except FileNotFoundError:
            return None, "nvidia-smi unavailable"
        except (OSError, subprocess.TimeoutExpired):
            return None, "NVIDIA telemetry query failed"
        if process.returncode != 0:
            return None, "NVIDIA telemetry query failed"
        gpu = _gpu_from_csv(process.stdout, address)
        return gpu, None if gpu is not None else "No unambiguous NVIDIA telemetry record"

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
        sampled_at = datetime.now().astimezone().isoformat(timespec="seconds")
        devices = self._hwmon()
        fans, acer_temperatures = self._acer(devices)
        firmware_profile = _read(self.path("sys/firmware/acpi/platform_profile"))
        choices = _read(self.path("sys/firmware/acpi/platform_profile_choices"))
        model = _read(self.path("sys/class/dmi/id/product_name")) or "Unknown device"
        cpu, cpu_source, cpu_identity = self._cpu(devices)
        drives = self._drives(devices)
        gpu, gpu_status = self._gpu()
        return Snapshot(
            sampled_at=sampled_at,
            model=model,
            cpu_celsius=cpu,
            cpu_source=cpu_source,
            cpu_identity=cpu_identity,
            fans=fans,
            acer_temperatures=acer_temperatures,
            drives=drives,
            gpu=gpu,
            gpu_status=gpu_status,
            firmware_profile=firmware_profile,
            firmware_profile_choices=tuple(choices.split()) if choices else (),
            tuned_profile=self._tuned(),
            rgb=self._rgb(),
        )

"""Opt-in, kernel-pinned, one-shot native Acer WMI RGB backend.

This does NOT replace acer_wmi. A root-owned, verified development module runs
once per authorized request and is unloaded immediately. Software success is
NOT proof that the physical LEDs changed; inspect the keyboard.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess

from . import RGB_WMI_GUID
from .rgb import LightingPlan

MOD_NAME = "nitro_rgb_enable_poc"
MODULE_SUBPATH = "extra/nitro-control/nitro_rgb_enable_poc.ko"
_LOCK = Path("/run/lock/nitro-control-native-rgb.lock")
_HEX_SHA = re.compile(r"[a-f0-9]{64}\Z")


class NativeRGBError(RuntimeError):
    """A native RGB preflight, write or verification failed."""


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise NativeRGBError(f"Cannot read {path.name}: {exc}") from exc


def _run(argv: list[str], *, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NativeRGBError(f"RGB module operation failed: {type(exc).__name__}") from exc


class NativeRGB:
    """No kernel operation is performed by status(); apply() requires root."""

    def __init__(self, root: Path = Path("/"), kernel: str | None = None):
        self.root = Path(root)
        self.kernel = kernel or os.uname().release
        self.module = self.root / "usr/lib/modules" / self.kernel / MODULE_SUBPATH
        self.digest = Path(str(self.module) + ".sha256")

    def path(self, relative: str) -> Path:
        return self.root / relative.lstrip("/")

    def _fans_available(self) -> bool:
        for hwmon in self.path("sys/class/hwmon").glob("hwmon*"):
            try:
                if (_read(hwmon / "name") == "acer" and
                        (hwmon / "fan1_input").is_file() and
                        (hwmon / "fan2_input").is_file()):
                    return True
            except NativeRGBError:
                continue
        return False

    def status(self) -> tuple[bool, str]:
        try:
            if (_read(self.path("sys/class/dmi/id/sys_vendor")) != "Acer" or
                    _read(self.path("sys/class/dmi/id/product_name")) != "Nitro AN515-58"):
                return False, "Native WMI is restricted to Acer Nitro AN515-58"
            if not any(self.path("sys/bus/wmi/devices").glob(f"{RGB_WMI_GUID}-*")):
                return False, "Required numbered Acer gaming WMI GUID is missing"
            if not self.path("sys/module/acer_wmi").is_dir():
                return False, "Stock acer_wmi must be loaded"
            if self.path("sys/module/linuwu_sense").exists():
                return False, "Conflicting Linuwu driver is loaded"
            if self.path("sys/module/nitro_rgb_enable_poc").exists():
                return False, "Native RGB test module is already loaded"
            if "acer_wmi.force_caps=" in _read(self.path("proc/cmdline")):
                return False, "Non-default acer_wmi.force_caps is not supported"
            if not self._fans_available():
                return False, "Acer HWMON fan1/fan2 telemetry is unavailable"
            if not self.path("sys/firmware/acpi/platform_profile").is_file():
                return False, "Firmware platform profile interface is unavailable"
            if not self.module.is_file() or self.module.is_symlink() or not self.digest.is_file():
                return False, "Install the opt-in native RGB module for this kernel"
            return True, "Kernel-pinned native WMI helper present; requires administrator authorization"
        except NativeRGBError as exc:
            return False, str(exc)

    def _verify_install(self) -> None:
        try:
            for path, expected_mode in ((self.module, 0o644), (self.digest, 0o600)):
                if path.is_symlink() or not path.is_file():
                    raise NativeRGBError("Native RGB installed file is missing or a symlink")
                info = path.stat()
                if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != expected_mode:
                    raise NativeRGBError("Native RGB installed file ownership or mode mismatch")
            expected = _read(self.digest)
            if not _HEX_SHA.fullmatch(expected):
                raise NativeRGBError("Invalid native RGB module digest")
            actual = hashlib.sha256(self.module.read_bytes()).hexdigest()
            if actual != expected:
                raise NativeRGBError("Native RGB module digest mismatch")
        except OSError as exc:
            raise NativeRGBError("Native RGB module integrity check could not read installed files") from exc
        mod_name = _run(["/usr/sbin/modinfo", "-F", "name", str(self.module)])
        vermagic = _run(["/usr/sbin/modinfo", "-F", "vermagic", str(self.module)])
        if (mod_name.returncode != 0 or mod_name.stdout.strip() != MOD_NAME or
                vermagic.returncode != 0 or
                vermagic.stdout.split()[:1] != [self.kernel]):
            raise NativeRGBError("Native RGB module identity or kernel version mismatch")

    def apply(self, plan: LightingPlan) -> str:
        if not isinstance(plan, LightingPlan):
            raise NativeRGBError("A validated four-zone lighting plan is required")
        # Fedora 45 AN515-58 testing: 65% WMI brightness made Fn+F9
        # change visible colors; 100% preserved normal Fn dimming.
        # This is a temporary hardware-specific guard, not a protocol limit.
        if plan.brightness != 100:
            raise NativeRGBError(
                "Native Acer WMI currently requires 100% apply brightness; "
                "use Fn+F9/Fn+F10 for physical dimming")
        if os.geteuid() != 0:
            raise NativeRGBError("Native RGB changes require administrator authorization")
        ready, reason = self.status()
        if not ready:
            raise NativeRGBError(reason)
        self._verify_install()

        # Only one native apply at a time, with an exclusively root-owned lock.
        try:
            fd = os.open(_LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        except OSError as exc:
            raise NativeRGBError("Native RGB lock unavailable; no firmware writes attempted") from exc
        try:
            info = os.fstat(fd)
            if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
                raise NativeRGBError("Native RGB lock has unsafe permissions")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Re-check after acquiring the lock, before privileged module insertion.
            ready, reason = self.status()
            if not ready:
                raise NativeRGBError(reason)
            colors = ",".join(zone[1:] for zone in plan.zones)
            inserted = False
            try:
                try:
                    outcome = _run(["/usr/sbin/insmod", str(self.module), "apply=1",
                                    f"colors={colors}", f"brightness={plan.brightness}"])
                except NativeRGBError as exc:
                    raise NativeRGBError(f"{exc}. Keyboard lighting may have changed.") from exc
                inserted = outcome.returncode == 0
                if not inserted:
                    detail = outcome.stderr.strip()[:240] or "no module diagnostic"
                    raise NativeRGBError(
                        f"Native RGB module insertion failed ({detail}). Keyboard lighting may have changed.")
            finally:
                # Even a failed kernel module insert must not leave a module behind.
                if inserted or self.path("sys/module/nitro_rgb_enable_poc").exists():
                    try:
                        unload = _run(["/usr/sbin/rmmod", MOD_NAME])
                    except NativeRGBError as exc:
                        raise NativeRGBError(
                            f"CRITICAL: could not confirm native RGB module unload ({exc}). "
                            "Keyboard lighting may have changed. Inspect lsmod.") from exc
                    if unload.returncode != 0:
                        raise NativeRGBError(
                            "CRITICAL: native RGB module could not be unloaded. "
                            "Keyboard lighting may have changed. Inspect lsmod.")
            if not self._fans_available() or not self.path("sys/firmware/acpi/platform_profile").is_file():
                raise NativeRGBError("RGB was applied, but Acer monitoring disappeared; inspect driver state")
            return "Native WMI firmware confirmed four zone color readbacks; verify the keyboard visually"
        except BlockingIOError as exc:
            raise NativeRGBError("Another native RGB operation is already active") from exc
        finally:
            os.close(fd)

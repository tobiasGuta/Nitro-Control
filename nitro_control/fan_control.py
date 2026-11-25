"""Independent fan control through the stock AN515-58 acer-wmi hwmon ABI."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import re
import stat
import sys

MIN_PERCENT = 45
CHANNELS = (1, 2)
MODES = ("Auto", "Max", "Custom")
_MODE_VALUES = {"Auto": 2, "Max": 0, "Custom": 1}


class FanControlError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class FanPlan:
    mode: str
    cpu: int | None = None
    gpu: int | None = None

    def __post_init__(self):
        speeds = (self.cpu, self.gpu)
        if self.mode not in MODES:
            raise FanControlError("Choose Auto, Max, or Custom.")
        if self.mode != "Custom" and speeds != (None, None):
            raise FanControlError("Individual speeds require Custom mode.")
        if self.mode == "Custom":
            if speeds == (None, None):
                raise FanControlError("Choose Auto to return both fans to firmware control.")
            if any(speed is not None and (type(speed) is not int or not MIN_PERCENT <= speed <= 100)
                   for speed in speeds):
                raise FanControlError(f"Choose a fan setting between {MIN_PERCENT}% and 100%.")

    @property
    def channels(self) -> tuple[int | None, int | None]:
        return self.cpu, self.gpu


def _read(path: Path) -> str:
    return path.read_text(encoding="ascii").strip()


def _number(path: Path, maximum: int) -> int:
    value = _read(path)
    if not re.fullmatch(r"[0-9]{1,3}", value) or int(value) > maximum:
        raise FanControlError(f"Unexpected value from {path.name}.")
    return int(value)


def find_fan_controls(root: Path = Path("/")) -> Path | None:
    """Find one exact-model, stock-driver device with both control channels."""
    sysfs = root / "sys"
    platform = sysfs / "devices/platform/acer-wmi"
    dmi = sysfs / "class/dmi/id"
    try:
        if (_read(dmi / "sys_vendor"), _read(dmi / "product_name")) != ("Acer", "Nitro AN515-58"):
            return None
        driver = sysfs / "bus/platform/drivers/acer-wmi"
        stock = sysfs / "module/acer_wmi"
        if ((sysfs / "module/linuwu_sense").exists()
                or (platform / "driver").resolve(strict=True) != driver.resolve(strict=True)
                or (driver / "module").resolve(strict=True) != stock.resolve(strict=True)):
            return None
        devices = [path for path in (platform / "hwmon").glob("hwmon*")
                   if re.fullmatch(r"hwmon[0-9]+", path.name)
                   and path.is_dir() and not path.is_symlink() and _read(path / "name") == "acer"]
        if len(devices) != 1:
            return None
        hwmon = devices[0]
        for channel in CHANNELS:
            for attr in (f"pwm{channel}", f"pwm{channel}_enable"):
                info = (hwmon / attr).lstat()
                if not stat.S_ISREG(info.st_mode) or not info.st_mode & stat.S_IWUSR:
                    return None
        return hwmon
    except (OSError, ValueError):
        return None


def read_fan_plan(hwmon: Path) -> FanPlan | None:
    """Read the active controls. Unknown or externally set combinations stay unknown."""
    try:
        modes = tuple(_number(hwmon / f"pwm{channel}_enable", 2) for channel in CHANNELS)
        if modes == (2, 2):
            return FanPlan("Auto")
        if modes == (0, 0):
            return FanPlan("Max")
        if 0 not in modes:
            speeds = [round(_number(hwmon / f"pwm{channel}", 255) * 100 / 255) if mode == 1 else None
                      for channel, mode in zip(CHANNELS, modes)]
            return FanPlan("Custom", *speeds)
    except (OSError, ValueError, FanControlError):
        pass
    return None


def _write(path: Path, value: int) -> None:
    path.write_text(f"{value}\n", encoding="ascii")


_LOCK_SUBDIR = "nitro-control"
_LOCK_NAME = "fan.lock"


def _verify_lock_stat(info: os.stat_result, *, directory: bool, expected_uid: int,
                      exact_mode: int | None = None,
                      no_group_world_write: bool = False) -> None:
    is_kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not is_kind or info.st_uid != expected_uid:
        raise FanControlError("Fan control lock has unsafe ownership or file type.")
    if exact_mode is not None and stat.S_IMODE(info.st_mode) != exact_mode:
        raise FanControlError("Fan control lock has unsafe permissions.")
    if no_group_world_write and (info.st_mode & 0o022):
        raise FanControlError("Fan control runtime directory has unsafe permissions.")


def _restore_auto(hwmon: Path) -> None:
    write_failed: set[int] = set()
    for channel in CHANNELS:
        path = hwmon / f"pwm{channel}_enable"
        try:
            _write(path, 2)
        except OSError:
            write_failed.add(channel)
    verify_failed: set[int] = set()
    for channel in CHANNELS:
        path = hwmon / f"pwm{channel}_enable"
        try:
            if _number(path, 2) != 2:
                verify_failed.add(channel)
        except (OSError, ValueError, FanControlError):
            verify_failed.add(channel)
    failed = sorted(write_failed | verify_failed)
    if failed:
        names = ", ".join("CPU" if channel == 1 else "GPU" for channel in failed)
        verified = [channel for channel in CHANNELS
                    if channel not in write_failed and channel not in verify_failed]
        if verified:
            ok_names = ", ".join("CPU" if channel == 1 else "GPU" for channel in verified)
            raise FanControlError(f"Could not verify Auto for {names} ({ok_names} verified Auto).")
        raise FanControlError(f"Could not verify Auto for {names}.")


@contextmanager
def _lock(root: Path = Path("/")):
    """Serialize fan writes using a private runtime directory outside world-readable sysfs."""
    expected_uid = os.geteuid()
    run_path = root / "run"
    if root != Path("/"):
        try:
            os.mkdir(run_path, 0o755)
        except FileExistsError:
            pass
        except OSError as exc:
            raise FanControlError("Fan control lock directory is unavailable.") from exc
    run_fd = -1
    dir_fd = -1
    lock_fd = -1
    try:
        try:
            run_fd = os.open(run_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            run_info = os.fstat(run_fd)
        except OSError as exc:
            raise FanControlError("Fan control lock directory is unavailable.") from exc
        _verify_lock_stat(run_info, directory=True, expected_uid=expected_uid,
                          no_group_world_write=True)
        try:
            try:
                os.mkdir(_LOCK_SUBDIR, 0o700, dir_fd=run_fd)
            except FileExistsError:
                pass
            dir_fd = os.open(_LOCK_SUBDIR,
                             os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                             dir_fd=run_fd)
            dir_info = os.fstat(dir_fd)
        except OSError as exc:
            raise FanControlError("Fan control lock directory is unavailable.") from exc
        finally:
            os.close(run_fd)
            run_fd = -1

        _verify_lock_stat(dir_info, directory=True, expected_uid=expected_uid, exact_mode=0o700)
        try:
            lock_fd = os.open(
                _LOCK_NAME,
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                0o600,
                dir_fd=dir_fd,
            )
            lock_info = os.fstat(lock_fd)
        except OSError as exc:
            raise FanControlError("Fan control lock file is unavailable.") from exc
        finally:
            os.close(dir_fd)
            dir_fd = -1

        _verify_lock_stat(lock_info, directory=False, expected_uid=expected_uid, exact_mode=0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FanControlError("Another fan change is in progress.") from exc
        yield
    finally:
        if run_fd != -1:
            os.close(run_fd)
        if dir_fd != -1:
            os.close(dir_fd)
        if lock_fd != -1:
            os.close(lock_fd)


def apply_fan_plan(plan: FanPlan, root: Path = Path("/")) -> None:
    """Serialize writes and restore firmware control if a change fails partway."""
    if find_fan_controls(root) is None:
        raise FanControlError("Stock Acer fan controls are unavailable.")

    with _lock(root):
        hwmon = find_fan_controls(root)
        if hwmon is None:
            raise FanControlError("Stock Acer fan controls are unavailable.")
        if plan.mode == "Auto":
            _restore_auto(hwmon)
            return
        try:
            for channel, speed in zip(CHANNELS, plan.channels):
                path = hwmon / f"pwm{channel}_enable"
                mode = "Auto" if plan.mode == "Custom" and speed is None else plan.mode
                _write(path, _MODE_VALUES[mode])
                if speed is not None:
                    # acer-wmi truncates the 0–255 input to a whole firmware percent.
                    _write(hwmon / f"pwm{channel}", (speed * 255 + 99) // 100)
            if read_fan_plan(hwmon) != plan:
                raise FanControlError("Fan settings did not match the requested values.")
        except (OSError, ValueError, FanControlError) as exc:
            try:
                _restore_auto(hwmon)
            except FanControlError as restore_error:
                raise FanControlError(f"Fan change failed ({exc}). {restore_error}") from exc
            raise FanControlError(f"Fan change failed ({exc}). Both fans returned to Auto.") from exc


def _parse_speed(value: str) -> int | None:
    if value == "auto":
        return None
    if not re.fullmatch(r"[0-9]{2,3}", value):
        raise FanControlError("Invalid Custom fan setting.")
    return int(value)


def main() -> int:
    """Fixed root-owned entry point. Arguments never supply a device path."""
    try:
        args = sys.argv[1:]
        if args in (["Auto"], ["Max"]):
            plan = FanPlan(args[0])
        elif len(args) == 3 and args[0] == "Custom":
            plan = FanPlan("Custom", _parse_speed(args[1]), _parse_speed(args[2]))
        else:
            raise FanControlError("Expected Auto, Max, or Custom with two fan settings.")
        if os.geteuid() != 0:
            raise FanControlError("Administrator authentication is required.")
        apply_fan_plan(plan)
    except (OSError, FanControlError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print("Fan settings verified.")
    return 0

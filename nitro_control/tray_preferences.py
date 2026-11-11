"""Per-user, opt-in dashboard-to-tray preference and process launcher.

Pure Python: safe to import into GTK4. It must never import GTK3/Ayatana,
invoke root helpers or alter login autostart. Preferences only affect normal
live-dashboard launches; the tray process remains separately killable.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile


_MAX_CONFIG_BYTES = 4096
_FILENAME = "tray-preferences.json"


def _settings_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", "")
    if not base or not Path(base).is_absolute():
        base = str(Path.home() / ".config")
    return Path(base) / "nitro-control"


def _verify_directory(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise OSError("Nitro Control settings directory must be owned by this user")
    if info.st_mode & 0o022:
        raise OSError("Nitro Control settings directory must not be group/world writable")


def load_keep_in_tray() -> bool:
    """Default OFF; reject unsafe, malformed and non-boolean preference data."""
    path = _settings_dir() / _FILENAME
    try:
        _verify_directory(path.parent)
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or
                    info.st_uid != os.getuid() or info.st_size > _MAX_CONFIG_BYTES):
                return False
            raw = os.read(fd, _MAX_CONFIG_BYTES + 1)
        finally:
            os.close(fd)
        if len(raw) > _MAX_CONFIG_BYTES:
            return False
        data = json.loads(raw)
        return data.get("keep_in_tray") is True if isinstance(data, dict) else False
    except (OSError, ValueError, TypeError, UnicodeDecodeError):
        return False


def save_keep_in_tray(enabled: bool) -> None:
    """Atomically save one user's boolean preference, with no system changes."""
    if type(enabled) is not bool:
        raise ValueError("Tray preference must be boolean")
    directory = _settings_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    _verify_directory(directory)
    target = directory / _FILENAME
    try:
        info = target.lstat()
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise OSError("Unsafe Nitro Control tray preference file")
    fd, temporary = tempfile.mkstemp(prefix=".tray-preferences-", dir=str(directory))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump({"keep_in_tray": enabled}, output)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def start_tray_process() -> subprocess.Popen:
    """Spawn the existing standalone GTK3 tray; never import GTK3 into GTK4."""
    source_root = Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    # Avoid stale checkouts shadowing a tray imported from the app's source.
    env["PYTHONPATH"] = str(source_root)
    return subprocess.Popen(
        [sys.executable, "-P", "-m", "nitro_control", "--tray"],
        cwd=str(source_root),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        # Keep terminal/journal stderr available for Ayatana/GTK diagnostics.
        start_new_session=True,
    )

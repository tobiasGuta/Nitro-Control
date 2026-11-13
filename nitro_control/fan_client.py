"""Unprivileged client for the optional fan-control helper."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

from .fan_control import FanControlError, FanPlan

HELPER = Path("/usr/local/libexec/nitro-control-fan-helper")


class FanClient:
    def __init__(self, helper: Path = HELPER, runner=subprocess.run):
        self.helper = helper
        self.runner = runner

    def ready(self) -> bool:
        return self.helper.is_file() and shutil.which("pkexec") is not None

    def apply(self, plan: FanPlan) -> None:
        if not self.ready():
            raise FanControlError("The optional fan helper is unavailable.")
        args = [plan.mode]
        if plan.mode == "Custom":
            args.extend("auto" if speed is None else str(speed) for speed in plan.channels)
        try:
            result = self.runner(["pkexec", "--disable-internal-agent", str(self.helper), *args],
                                 capture_output=True, text=True, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FanControlError("Fan helper did not complete. Check the reported fan modes.") from exc
        if result.returncode in (126, 127):
            raise FanControlError("Fan authorization was canceled or denied.")
        if result.returncode != 0:
            raise FanControlError(result.stderr.strip() or "Fan settings could not be verified.")

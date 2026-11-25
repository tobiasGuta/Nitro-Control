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
        return (
            not self.helper.is_symlink()
            and self.helper.is_file()
            and shutil.which("pkexec") is not None
        )

    def apply(self, plan: FanPlan) -> None:
        if not self.ready():
            raise FanControlError("The optional fan helper is unavailable.")
        args = [plan.mode]
        if plan.mode == "Custom":
            args.extend("auto" if speed is None else str(speed) for speed in plan.channels)
        try:
            result = self.runner(["pkexec", "--disable-internal-agent", str(self.helper), *args],
                                 capture_output=True, text=True, timeout=120, check=False)
        except subprocess.TimeoutExpired as exc:
            raise FanControlError(
                "Fan helper reached the 120s timeout and did not complete; reporting timeout "
                "does not guarantee the privileged process stopped, so check the reported fan modes."
            ) from exc
        except PermissionError as exc:
            raise FanControlError(
                "Fan helper did not complete cleanly (unable to signal privileged child; it may "
                "continue past the deadline). Check the reported fan modes."
            ) from exc
        except OSError as exc:
            raise FanControlError(
                f"Fan helper could not be started ({type(exc).__name__}). Check the reported fan modes."
            ) from exc
        if result.returncode == 126:
            raise FanControlError(
                "Fan authorization was canceled or denied by polkit (exit 126). Check the reported fan modes."
            )
        if result.returncode == 127:
            raise FanControlError(
                result.stderr.strip() or "Fan authorization was denied or the fan helper could not run (exit 127)."
            )
        if result.returncode != 0:
            raise FanControlError(result.stderr.strip() or "Fan settings could not be verified.")
        if result.stdout.strip() != "Fan settings verified.":
            raise FanControlError("Fan helper did not confirm verification. Check the reported fan modes.")

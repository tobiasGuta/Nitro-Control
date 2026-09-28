"""Unprivileged GUI -> opt-in root-owned Polkit helper for a single RGB apply."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

from .rgb import LightingPlan
from .rgb_hardware import RGBHardwareError

HELPER = Path("/usr/local/libexec/nitro-control-rgb-helper")


class RGBClient:
    def __init__(self, helper: Path = HELPER, runner=subprocess.run):
        self.helper = Path(helper)
        self.runner = runner

    def ready(self) -> bool:
        return self.helper.is_file() and shutil.which("pkexec") is not None

    def apply(self, plan: LightingPlan) -> str:
        if not isinstance(plan, LightingPlan):
            raise RGBHardwareError("A validated RGB plan is required")
        if not self.ready():
            raise RGBHardwareError("The optional root-owned RGB helper is not installed")
        request = json.dumps({"zones": list(plan.zones), "brightness": plan.brightness}, separators=(",", ":"))
        try:
            proc = self.runner(["pkexec", str(self.helper)], input=request, text=True,
                               capture_output=True, timeout=45, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RGBHardwareError(f"RGB authorization/helper failed: {type(exc).__name__}") from exc
        try:
            reply = json.loads(proc.stdout)
        except (ValueError, TypeError) as exc:
            raise RGBHardwareError("RGB authorization denied or helper returned no valid response") from exc
        if proc.returncode != 0 or not isinstance(reply, dict) or reply.get("ok") is not True or reply.get("verified_readback") is not True:
            message = reply.get("error", "RGB helper did not confirm the write") if isinstance(reply, dict) else "Invalid response"
            raise RGBHardwareError(message)
        if reply.get("backend") not in {"native-led", "linuwu-sense"}:
            raise RGBHardwareError("RGB helper returned an unrecognized backend")
        return str(reply.get("detail", "Sysfs readback verified; inspect the physical keyboard"))

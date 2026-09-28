"""One-shot root-owned RGB helper. No shell, paths, module controls or fan writes."""
from __future__ import annotations

import json
import os
import sys

from .rgb import LightingPlan
from .rgb_hardware import RGBHardware, RGBHardwareError

MAX_REQUEST_BYTES = 1024


def main() -> int:
    # Structured stdout is parsed by the GUI; errors are never claimed as success.
    if os.geteuid() != 0:
        print(json.dumps({"ok": False, "error": "RGB helper requires administrator authorization"}))
        return 1
    try:
        data = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(data) > MAX_REQUEST_BYTES:
            raise RGBHardwareError("RGB request exceeds maximum length")
        payload = json.loads(data.decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"zones", "brightness"}:
            raise RGBHardwareError("Unexpected RGB request fields")
        if not isinstance(payload["zones"], list) or len(payload["zones"]) != 4:
            raise RGBHardwareError("Exactly four RGB zones are required")
        plan = LightingPlan(tuple(payload["zones"]), payload["brightness"])
        result = RGBHardware().apply(plan)
        print(json.dumps({"ok": True, "backend": result.backend, "verified_readback": result.verified_readback,
                          "detail": result.detail}))
        return 0
    except (RGBHardwareError, ValueError, UnicodeError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""One-shot root-owned RGB helper. No shell or fan writes.

The opt-in native WMI backend temporarily loads a verified, kernel-pinned
module and always attempts to unload it after a firmware transaction.
"""
from __future__ import annotations

from contextlib import nullcontext
import json
import os
from pathlib import Path
import sys

from .module_staging import (
    ModuleStagingError,
    RGB_HELPER_LOCK,
    acquire_private_locks,
    verify_privileged_rgb_helper,
)
from .rgb import EffectPlan, LightingPlan
from .rgb_hardware import RGBHardware, RGBHardwareError

MAX_REQUEST_BYTES = 1024
HELPER_PROTOCOL = 2
HELPER_CAPABILITIES = ("static", "effect")
HELPER_LOCK_ROOT = Path("/")


def main(argv: list[str] | None = None, *, _lock_held: bool = False) -> int:
    """Run privileged RGB helper dispatch or read-only protocol capability discovery.

    Note: ``--capabilities`` reports only protocol features supported by the helper code
    (``scope="protocol-only"``, ``installation_verified=False``). It does not certify
    host installation coherence or readiness; callers must check ``RGBClient.capabilities()``
    and privileged operations enforce ``verify_privileged_rgb_helper()`` under lock.
    """
    # Structured stdout is parsed by the GUI; errors are never claimed as success.
    if argv == ["--capabilities"]:
        print(json.dumps({
            "ok": True,
            "component": "rgb-helper",
            "schema_version": 1,
            "helper_protocol": HELPER_PROTOCOL,
            "capabilities": list(HELPER_CAPABILITIES),
            "max_request_bytes": MAX_REQUEST_BYTES,
            "scope": "protocol-only",
            "installation_verified": False,
            "note": (
                "Reports helper code protocol capabilities only; does not verify host "
                "installation coherence (checked under lock before execution and via RGBClient.capabilities())."
            ),
        }))
        return 0
    if os.geteuid() != 0:
        print(json.dumps({"ok": False, "error": "RGB helper requires administrator authorization"}))
        return 1
    try:
        lock_cm = (
            nullcontext()
            if _lock_held
            else acquire_private_locks(
                HELPER_LOCK_ROOT,
                (RGB_HELPER_LOCK,),
                "Another RGB helper operation or installation is active; retry after it finishes.",
            )
        )
        with lock_cm:
            verify_privileged_rgb_helper(HELPER_LOCK_ROOT)
            data = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
            if len(data) > MAX_REQUEST_BYTES:
                raise RGBHardwareError("RGB request exceeds maximum length")
            payload = json.loads(data.decode("utf-8"))
            if isinstance(payload, dict) and set(payload) == {"effect", "speed", "color"}:
                plan_kind = "effect"
                parsed = EffectPlan(payload["effect"], payload["speed"], payload["color"])
            else:
                if not isinstance(payload, dict) or set(payload) != {"zones", "brightness"}:
                    raise RGBHardwareError("Unexpected RGB request fields")
                if not isinstance(payload["zones"], list) or len(payload["zones"]) != 4:
                    raise RGBHardwareError("Exactly four RGB zones are required")
                plan_kind = "static"
                parsed = LightingPlan(tuple(payload["zones"]), payload["brightness"])

            if plan_kind == "effect":
                result = RGBHardware().apply_effect(parsed)  # type: ignore[arg-type]
            else:
                result = RGBHardware().apply(parsed)  # type: ignore[arg-type]
        print(json.dumps({"ok": True, "backend": result.backend, "verified_readback": result.verified_readback,
                          "detail": result.detail}))
        return 0
    except (RGBHardwareError, ModuleStagingError, ValueError, UnicodeError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

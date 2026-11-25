"""Unprivileged GUI -> opt-in root-owned Polkit helper for a single RGB apply."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

from .rgb import EffectPlan, LightingPlan
from .rgb_hardware import RGBHardwareError

HELPER = Path("/usr/local/libexec/nitro-control-rgb-helper")
HELPER_TIMEOUT = 45
MAX_SUPPORTED_HELPER_PROTOCOL = 2
_VALID_BACKENDS = frozenset({"native-led", "linuwu-sense", "native-wmi"})
_HEX_SHA = re.compile(r"[a-f0-9]{64}\Z")
_EXPECTED_HELPER_FILES = (
    "__init__.py",
    "rgb.py",
    "rgb_hardware.py",
    "module_staging.py",
    "native_rgb.py",
    "rgb_privileged.py",
)
_LEGACY_REQUIRED_FILES = (
    "__init__.py",
    "rgb.py",
    "rgb_hardware.py",
    "native_rgb.py",
    "rgb_privileged.py",
)
_EXPECTED_MANIFEST_KEYS = frozenset({
    "nitro-control-rgb-helper",
    "io.github.tobiasguta.NitroControl.rgb.policy",
    *(f"nitro_control/{f}" for f in _EXPECTED_HELPER_FILES),
})


@dataclass(frozen=True, slots=True)
class RGBHelperCapabilities:
    installed: bool
    protocol: int | None
    static_supported: bool
    effect_supported: bool
    legacy_manifest_missing: bool
    detail: str
    state: str = "coherent"


class RGBClient:
    def __init__(
        self,
        helper: Path = HELPER,
        runner=subprocess.run,
        *,
        manifest: Path | None = None,
        policy: Path | None = None,
    ):
        self.helper = Path(helper)
        self.runner = runner
        self.manifest = Path(manifest) if manifest is not None else (
            self.helper.parent / "nitro-control-rgb" / "manifest.json"
        )
        self._explicit_policy = Path(policy) if policy is not None else None

    def ready(self) -> bool:
        return (
            not self.helper.is_symlink()
            and self.helper.is_file()
            and shutil.which("pkexec") is not None
        )

    def _policy_path(self) -> tuple[Path, tuple[Path, ...]] | None:
        if self._explicit_policy is not None:
            return self._explicit_policy, (self._explicit_policy.parent,)
        # Any production-layout helper (<root>/usr/local/libexec/nitro-control-rgb-helper)
        # MUST have its corresponding Polkit policy even if the directory was removed.
        if (
            self.helper.parent.name == "libexec"
            and self.helper.parent.parent.name == "local"
            and self.helper.parent.parent.parent.name == "usr"
        ):
            usr_dir = self.helper.parent.parent.parent
            share_dir = usr_dir / "share"
            polkit_base = share_dir / "polkit-1"
            actions_dir = polkit_base / "actions"
            return (
                actions_dir / "io.github.tobiasguta.NitroControl.rgb.policy",
                (usr_dir, share_dir, polkit_base, actions_dir),
            )
        return None

    def capabilities(self) -> RGBHelperCapabilities:
        """Read-only helper capability and coherence discovery without invoking pkexec."""
        if self.helper.is_symlink():
            return RGBHelperCapabilities(
                False, None, False, False, False,
                "RGB helper path is a symlink and cannot be trusted",
                state="unsafe_helper",
            )
        if not self.helper.is_file():
            return RGBHelperCapabilities(
                False, None, False, False, False,
                "The optional root-owned RGB helper is not installed",
                state="absent",
            )
        if shutil.which("pkexec") is None:
            return RGBHelperCapabilities(
                False, None, False, False, False,
                "pkexec is unavailable; administrator authorization cannot run",
                state="pkexec_unavailable",
            )

        base_dir = self.manifest.parent
        pkg_dir = base_dir / "nitro_control"
        tx_path = base_dir / ".tx.rgb-helper.json"
        man = self.manifest

        if base_dir.is_symlink() or pkg_dir.is_symlink() or tx_path.is_symlink() or man.is_symlink():
            return RGBHelperCapabilities(
                True, None, False, False, False,
                "RGB helper staging path or manifest is a symlink; inspect and reinstall safely",
                state="unsafe_helper",
            )
        if tx_path.exists():
            return RGBHelperCapabilities(
                True, None, False, False, False,
                "Interrupted RGB helper installation detected; re-run sudo ./scripts/install-rgb-helper-fedora.sh",
                state="interrupted",
            )

        pol_info = self._policy_path()
        if pol_info is not None:
            pol_path, pol_ancestors = pol_info
            for p_anc in pol_ancestors:
                if p_anc.is_symlink():
                    return RGBHelperCapabilities(
                        True, None, False, False, False,
                        f"Polkit directory ancestor {p_anc} is a symlink and cannot be trusted",
                        state="unsafe_helper",
                    )
        else:
            pol_path = None

        if not man.exists():
            # Distinguish legitimate legacy helper (e.g., preview.4) from incomplete installation or stub helper.
            has_layout = base_dir.exists() or self.helper.name == "nitro-control-rgb-helper"
            if has_layout:
                try:
                    launcher_text = self.helper.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    return RGBHelperCapabilities(
                        True, None, False, False, False,
                        "RGB helper launcher is unreadable; reinstall with sudo ./scripts/install-rgb-helper-fedora.sh",
                        state="incomplete",
                    )
                if (
                    "/usr/local/libexec/nitro-control-rgb" not in launcher_text
                    or "from nitro_control.rgb_privileged import main" not in launcher_text
                ):
                    return RGBHelperCapabilities(
                        True, None, False, False, False,
                        "Unrecognized RGB helper executable without manifest; inspect before use",
                        state="inconsistent_manifest",
                    )
                if not pkg_dir.is_dir():
                    return RGBHelperCapabilities(
                        True, None, False, False, False,
                        "Incomplete legacy RGB helper installation (missing nitro_control dependencies)",
                        state="missing_dependency",
                    )
                for dep in _LEGACY_REQUIRED_FILES:
                    dp = pkg_dir / dep
                    if dp.is_symlink() or not dp.is_file():
                        return RGBHelperCapabilities(
                            True, None, False, False, False,
                            f"Missing legacy RGB helper dependency ({dep}); re-run sudo ./scripts/install-rgb-helper-fedora.sh --adopt-legacy",
                            state="missing_dependency",
                        )
                if pol_path is not None and (pol_path.is_symlink() or not pol_path.is_file()):
                    return RGBHelperCapabilities(
                        True, None, False, False, False,
                        f"Missing Polkit authorization policy ({pol_path.name}); re-run sudo ./scripts/install-rgb-helper-fedora.sh --adopt-legacy",
                        state="missing_dependency",
                    )
            return RGBHelperCapabilities(
                True, 1, True, False, True,
                "Installed RGB helper supports Static only; re-run sudo ./scripts/install-rgb-helper-fedora.sh [--adopt-legacy] for Dynamic effects",
                state="legacy_static",
            )

        if not man.is_file():
            return RGBHelperCapabilities(
                True, None, False, False, False,
                "RGB helper capability manifest is unsafe; reinstall with sudo ./scripts/install-rgb-helper-fedora.sh",
                state="invalid_manifest",
            )
        try:
            data = json.loads(man.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return RGBHelperCapabilities(
                True, None, False, False, False,
                "RGB helper capability manifest is unreadable or corrupt; reinstall with sudo ./scripts/install-rgb-helper-fedora.sh",
                state="invalid_manifest",
            )
        if (
            not isinstance(data, dict)
            or data.get("managed_by") != "nitro-control"
            or data.get("component") != "rgb-helper"
            or type(data.get("schema_version")) is not int
            or type(data.get("helper_protocol")) is not int
            or not isinstance(data.get("capabilities"), list)
            or not all(isinstance(c, str) for c in data["capabilities"])
        ):
            return RGBHelperCapabilities(
                True, None, False, False, False,
                "RGB helper capability manifest has invalid fields; reinstall with sudo ./scripts/install-rgb-helper-fedora.sh",
                state="invalid_manifest",
            )
        if data["schema_version"] != 1 or not (1 <= data["helper_protocol"] <= MAX_SUPPORTED_HELPER_PROTOCOL):
            return RGBHelperCapabilities(
                True, data["helper_protocol"], False, False, False,
                f"RGB helper protocol/schema ({data['helper_protocol']}/v{data['schema_version']}) is unsupported by this application",
                state="unsupported_protocol",
            )

        files_map = data.get("files")
        if not isinstance(files_map, dict) or not files_map:
            return RGBHelperCapabilities(
                True, None, False, False, False,
                "RGB helper manifest lacks file digests; cannot verify coherent helper installation",
                state="inconsistent_manifest",
            )
        missing_keys = _EXPECTED_MANIFEST_KEYS - set(files_map)
        if missing_keys:
            return RGBHelperCapabilities(
                True, None, False, False, False,
                "RGB helper manifest is missing required artifact entries; reinstall safely",
                state="inconsistent_manifest",
            )

        key_paths: dict[str, Path] = {
            "nitro-control-rgb-helper": self.helper,
            **{f"nitro_control/{f}": pkg_dir / f for f in _EXPECTED_HELPER_FILES},
        }
        if pol_path is not None:
            key_paths["io.github.tobiasguta.NitroControl.rgb.policy"] = pol_path

        for key, path in key_paths.items():
            expected_sha = files_map.get(key)
            if not isinstance(expected_sha, str) or not _HEX_SHA.fullmatch(expected_sha):
                return RGBHelperCapabilities(
                    True, None, False, False, False,
                    f"RGB helper manifest has invalid digest entry for {key}",
                    state="inconsistent_manifest",
                )
            if path.is_symlink() or not path.is_file():
                return RGBHelperCapabilities(
                    True, None, False, False, False,
                    f"Incomplete RGB helper installation: missing dependency or artifact {path.name}",
                    state="missing_dependency",
                )
            try:
                actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                return RGBHelperCapabilities(
                    True, None, False, False, False,
                    f"Unable to read installed RGB helper file {path.name}",
                    state="incomplete",
                )
            if actual_sha != expected_sha:
                return RGBHelperCapabilities(
                    True, None, False, False, False,
                    f"RGB helper artifact {path.name} does not match manifest digest (stale or interrupted install); re-run sudo ./scripts/install-rgb-helper-fedora.sh",
                    state="inconsistent_manifest",
                )

        caps = set(data["capabilities"])
        static_ok = "static" in caps
        effect_ok = "effect" in caps and data["helper_protocol"] >= 2
        if effect_ok:
            try:
                priv_src = (pkg_dir / "rgb_privileged.py").read_text(encoding="utf-8", errors="replace")
            except OSError:
                priv_src = ""
            if "EffectPlan" not in priv_src:
                return RGBHelperCapabilities(
                    True, None, False, False, False,
                    "RGB helper manifest declares Dynamic effects, but installed rgb_privileged.py lacks EffectPlan support",
                    state="inconsistent_manifest",
                )
        if not static_ok and not effect_ok:
            return RGBHelperCapabilities(
                True, data["helper_protocol"], False, False, False,
                "RGB helper declares no supported RGB operations; reinstall with sudo ./scripts/install-rgb-helper-fedora.sh",
                state="inconsistent_manifest",
            )
        if not effect_ok:
            return RGBHelperCapabilities(
                True, data["helper_protocol"], static_ok, False, False,
                "Installed RGB helper supports Static only; re-run sudo ./scripts/install-rgb-helper-fedora.sh for Dynamic effects",
                state="coherent",
            )
        return RGBHelperCapabilities(
            True, data["helper_protocol"], static_ok, True, False,
            "RGB helper supports Static palettes and Dynamic effects",
            state="coherent",
        )

    def apply(self, plan: LightingPlan) -> str:
        if not isinstance(plan, LightingPlan):
            raise RGBHardwareError("A validated RGB plan is required")
        caps = self.capabilities()
        if not caps.installed or not caps.static_supported:
            raise RGBHardwareError(caps.detail)
        return self._send({"zones": list(plan.zones), "brightness": plan.brightness})

    def apply_effect(self, plan: EffectPlan) -> str:
        if not isinstance(plan, EffectPlan):
            raise RGBHardwareError("A validated lighting effect is required")
        caps = self.capabilities()
        if not caps.installed or not caps.effect_supported:
            raise RGBHardwareError(caps.detail)
        return self._send({"effect": plan.effect, "speed": plan.speed, "color": plan.color})

    def _send(self, payload: dict) -> str:
        request = json.dumps(payload, separators=(",", ":"))
        try:
            proc = self.runner(
                ["pkexec", str(self.helper)],
                input=request,
                text=True,
                capture_output=True,
                timeout=HELPER_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RGBHardwareError(
                f"RGB helper call reached {HELPER_TIMEOUT}s timeout before completion; reporting "
                "timeout does not guarantee the privileged process stopped, so lighting state is "
                "uncertain. Inspect the keyboard."
            ) from exc
        except PermissionError as exc:
            raise RGBHardwareError(
                "Privileged pkexec process could not be signaled after spawning (PermissionError); "
                "operation may continue past client deadline and lighting state is uncertain. "
                "Inspect the keyboard."
            ) from exc
        except OSError as exc:
            raise RGBHardwareError(
                f"RGB helper could not be started ({type(exc).__name__}); helper did not execute"
            ) from exc

        try:
            reply = json.loads(proc.stdout)
        except (ValueError, TypeError) as exc:
            if proc.returncode == 126:
                raise RGBHardwareError(
                    "RGB administrator authorization was canceled or denied by polkit (exit 126); "
                    "no structured helper confirmation was received."
                ) from exc
            if proc.returncode == 127:
                detail = (getattr(proc, "stderr", "") or "").strip()
                msg = detail[:160] if detail else "RGB authorization failed or helper could not be executed"
                raise RGBHardwareError(
                    f"{msg} (exit 127); no structured helper confirmation was received."
                ) from exc
            raise RGBHardwareError(
                f"RGB helper exited ({proc.returncode}) without a valid confirmation response; "
                "hardware state may be uncertain. Inspect the keyboard."
            ) from exc

        if not isinstance(reply, dict) or type(reply.get("ok")) is not bool:
            raise RGBHardwareError(
                "RGB helper returned a malformed response payload; hardware state may be uncertain."
            )
        if reply["ok"] is False:
            err = reply.get("error")
            if isinstance(err, str) and err.strip():
                raise RGBHardwareError(err)
            raise RGBHardwareError("RGB helper rejected the request or returned a malformed error diagnostic")
        if proc.returncode != 0:
            raise RGBHardwareError(
                f"RGB helper returned ok=true with non-zero exit status {proc.returncode}; "
                "hardware state is uncertain. Inspect the keyboard."
            )
        if type(reply.get("verified_readback")) is not bool or reply["verified_readback"] is not True:
            raise RGBHardwareError("RGB helper did not confirm verified hardware readback")
        backend = reply.get("backend")
        if type(backend) is not str or backend not in _VALID_BACKENDS:
            raise RGBHardwareError(
                "RGB helper returned an unrecognized or malformed backend; hardware state may be uncertain."
            )
        if "detail" in reply and (type(reply["detail"]) is not str or not reply["detail"].strip()):
            raise RGBHardwareError(
                "RGB helper returned malformed status detail; hardware state may be uncertain. Inspect the keyboard."
            )
        return reply["detail"] if "detail" in reply else "Sysfs readback verified; inspect the physical keyboard"

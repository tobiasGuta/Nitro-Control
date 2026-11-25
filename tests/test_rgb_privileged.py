"""Privileged helper rejects unsafe requests before reaching any hardware writer."""
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from nitro_control import rgb_privileged
from nitro_control.module_staging import (
    RGB_HELPER_LOCK,
    acquire_private_locks,
    install_rgb_helper,
)
from nitro_control.rgb import EffectPlan

REPO = Path(__file__).resolve().parents[1]


class HelperTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.lock_root = Path(self._td.name)
        (self.lock_root / "usr/local/libexec").mkdir(parents=True, exist_ok=True)
        (self.lock_root / "usr/share/polkit-1/actions").mkdir(parents=True, exist_ok=True)
        (self.lock_root / "run").mkdir(parents=True, exist_ok=True)
        install_rgb_helper(
            self.lock_root,
            REPO,
            runner=lambda *a, **k: subprocess.CompletedProcess([], 0, "", ""),
        )
        self._lock_patch = patch("nitro_control.rgb_privileged.HELPER_LOCK_ROOT", self.lock_root)
        self._lock_patch.start()
        self.addCleanup(self._lock_patch.stop)

    def invoke(self, request, euid=0):
        stdout = io.StringIO()
        stdin = SimpleNamespace(buffer=io.BytesIO(request))
        with patch("nitro_control.rgb_privileged.os.geteuid", return_value=euid), \
             patch("nitro_control.rgb_privileged.sys.stdin", stdin), \
             patch("nitro_control.rgb_privileged.sys.stdout", stdout), \
             patch("nitro_control.rgb_privileged.RGBHardware") as backend:
            code = rgb_privileged.main()
        return code, json.loads(stdout.getvalue()), backend

    def test_requires_root(self):
        code, reply, backend = self.invoke(b'{}', euid=1000)
        self.assertEqual(code, 1)
        self.assertFalse(reply["ok"])
        backend.assert_not_called()

    def test_rejects_extra_or_unknown_fields(self):
        for payload in (b'{}', b'{"zones": [], "brightness": 20, "path": "/etc/passwd"}', b'[]'):
            code, reply, backend = self.invoke(payload)
            self.assertEqual(code, 1)
            self.assertFalse(reply["ok"])
            backend.assert_not_called()

    def test_rejects_unsupported_effects_and_invalid_parameters(self):
        for overrides in ({"effect": "Meteor"}, {"speed": True}, {"speed": 10},
                          {"color": "red"}, {"brightness": 65}):
            payload = {"effect": "Wave", "speed": 4, "color": "#000000"} | overrides
            code, reply, backend = self.invoke(json.dumps(payload).encode())
            self.assertEqual(code, 1)
            self.assertFalse(reply["ok"])
            backend.assert_not_called()

    def test_routes_effect_request_to_validated_effect_plan(self):
        request = b'{"effect":"Breathing","speed":4,"color":"#22CAFF"}'
        result = SimpleNamespace(backend="native-wmi", verified_readback=True, detail="verified")
        with patch("nitro_control.rgb_privileged.RGBHardware") as constructor:
            constructor.return_value.apply_effect.return_value = result
            with patch("nitro_control.rgb_privileged.os.geteuid", return_value=0), \
                 patch("nitro_control.rgb_privileged.sys.stdin", SimpleNamespace(buffer=io.BytesIO(request))), \
                 patch("nitro_control.rgb_privileged.sys.stdout", io.StringIO()):
                self.assertEqual(rgb_privileged.main(), 0)
            constructor.return_value.apply_effect.assert_called_once_with(EffectPlan("Breathing", 4, "#22CAFF"))
            constructor.return_value.apply.assert_not_called()

    def test_rejects_oversized_request(self):
        code, reply, backend = self.invoke(b' ' * (rgb_privileged.MAX_REQUEST_BYTES + 1))
        self.assertEqual(code, 1)
        self.assertIn("maximum", reply["error"])
        backend.assert_not_called()

    def test_valid_request_uses_validated_plan(self):
        payload = json.dumps({"zones": ["#ff0000"] * 4, "brightness": 30}).encode()
        stdout = io.StringIO()
        stdin = SimpleNamespace(buffer=io.BytesIO(payload))
        with patch("nitro_control.rgb_privileged.os.geteuid", return_value=0), \
             patch("nitro_control.rgb_privileged.sys.stdin", stdin), \
             patch("nitro_control.rgb_privileged.sys.stdout", stdout), \
             patch("nitro_control.rgb_privileged.RGBHardware") as constructor:
            constructor.return_value.apply.return_value = SimpleNamespace(
                backend="native-led", verified_readback=True, detail="sysfs accepted")
            code = rgb_privileged.main()
            plan = constructor.return_value.apply.call_args.args[0]
        self.assertEqual(code, 0)
        self.assertEqual(plan.zones, ("#FF0000",) * 4)
        self.assertEqual(plan.brightness, 30)
        self.assertTrue(json.loads(stdout.getvalue())["ok"])

    def test_capabilities_query_is_read_only_and_succeeds_without_root(self):
        stdout = io.StringIO()
        with patch("nitro_control.rgb_privileged.os.geteuid", return_value=1000), \
             patch("nitro_control.rgb_privileged.sys.stdout", stdout), \
             patch("nitro_control.rgb_privileged.RGBHardware") as backend:
            code = rgb_privileged.main(["--capabilities"])
        self.assertEqual(code, 0)
        backend.assert_not_called()
        info = json.loads(stdout.getvalue())
        self.assertTrue(info["ok"])
        self.assertEqual(info["helper_protocol"], 2)
        self.assertEqual(info["capabilities"], ["static", "effect"])

    def test_preview4_and_current_payload_compatibility(self):
        # Current helper preserves v0.4.0-preview.4 Static schema {"zones", "brightness"}
        # while rejecting unknown mixed schemas cleanly before touching hardware.
        static_req = json.dumps({"zones": ["#112233"] * 4, "brightness": 100}).encode()
        stdout = io.StringIO()
        with patch("nitro_control.rgb_privileged.os.geteuid", return_value=0), \
             patch("nitro_control.rgb_privileged.sys.stdin", SimpleNamespace(buffer=io.BytesIO(static_req))), \
             patch("nitro_control.rgb_privileged.sys.stdout", stdout), \
             patch("nitro_control.rgb_privileged.RGBHardware") as constructor:
            constructor.return_value.apply.return_value = SimpleNamespace(
                backend="native-led", verified_readback=True, detail="sysfs accepted"
            )
            code = rgb_privileged.main()
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(stdout.getvalue())["ok"])
        constructor.return_value.apply.assert_called_once()
        constructor.return_value.apply_effect.assert_not_called()

        mixed = json.dumps({"zones": ["#112233"] * 4, "brightness": 100, "effect": "Wave"}).encode()
        m_code, m_reply, m_backend = self.invoke(mixed, euid=0)
        self.assertEqual(m_code, 1)
        self.assertFalse(m_reply["ok"])
        m_backend.assert_not_called()

    def test_helper_acquires_rgb_helper_lock_and_fails_cleanly_under_contention(self):
        request = b'{"zones":["#FF0000","#00FF00","#0000FF","#FFFFFF"],"brightness":100}'
        with acquire_private_locks(self.lock_root, (RGB_HELPER_LOCK,), "busy"):
            code, reply, backend = self.invoke(request, euid=0)
        self.assertEqual(code, 1)
        self.assertFalse(reply["ok"])
        self.assertIn("active", reply["error"].lower())
        backend.assert_not_called()

    def test_interrupted_or_inconsistent_helper_refused_before_hardware_adapter(self):
        request = b'{"zones":["#FF0000","#FF0000","#FF0000","#FF0000"],"brightness":100}'
        with tempfile.TemporaryDirectory() as r2:
            repo2 = Path(r2)
            (repo2 / "nitro_control").mkdir()
            for fname in ("__init__.py", "rgb.py", "rgb_hardware.py", "module_staging.py", "native_rgb.py", "rgb_privileged.py"):
                txt = (REPO / "nitro_control" / fname).read_text(encoding="utf-8")
                if fname == "rgb.py":
                    txt += "\n# gen B\n"
                (repo2 / "nitro_control" / fname).write_text(txt, encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "crash"):
                install_rgb_helper(
                    self.lock_root,
                    repo2,
                    runner=lambda *a, **k: subprocess.CompletedProcess([], 0, "", ""),
                    on_stage=lambda s: (_ for _ in ()).throw(RuntimeError("crash")) if s == "after_partial_py" else None,
                )

        # H-P4-R01: Even if dispatched directly as root, helper fails closed BEFORE calling RGBHardware!
        code, reply, backend = self.invoke(request, euid=0)
        self.assertEqual(code, 1)
        self.assertFalse(reply["ok"])
        self.assertIn("interrupted", reply["error"].lower())
        backend.assert_not_called()

        # Recover cleanly, then test missing policy file or tampered module refusal.
        install_rgb_helper(self.lock_root, REPO, runner=lambda *a, **k: subprocess.CompletedProcess([], 0, "", ""))
        (self.lock_root / "usr/share/polkit-1/actions/io.github.tobiasguta.NitroControl.rgb.policy").unlink()
        code2, reply2, backend2 = self.invoke(request, euid=0)
        self.assertEqual(code2, 1)
        self.assertFalse(reply2["ok"])
        self.assertIn("incomplete", reply2["error"].lower())
        backend2.assert_not_called()

    def test_h_p4_r05_capabilities_marks_protocol_only_even_during_interrupted_install(self):
        tx_path = self.lock_root / "usr/local/libexec/nitro-control-rgb/.tx.rgb-helper.json"
        tx_path.write_text(json.dumps({"state": "installing"}), encoding="utf-8")
        stdout_cap = io.StringIO()
        with (
            patch("nitro_control.rgb_privileged.sys.stdout", stdout_cap),
            patch("nitro_control.rgb_privileged.RGBHardware") as backend,
        ):
            code = rgb_privileged.main(["--capabilities"])
        cap = json.loads(stdout_cap.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(cap["ok"])
        self.assertEqual(cap["capabilities"], ["static", "effect"])
        self.assertEqual(cap["scope"], "protocol-only")
        self.assertFalse(cap["installation_verified"])
        backend.assert_not_called()

        code_exec, exec_reply, backend_exec = self.invoke(
            json.dumps({"mode": "static", "color": [255, 0, 0]}).encode("utf-8")
        )
        self.assertEqual(code_exec, 1)
        self.assertFalse(exec_reply["ok"])
        self.assertIn("interrupted", exec_reply["error"].lower())
        backend_exec.assert_not_called()


if __name__ == "__main__":
    unittest.main()

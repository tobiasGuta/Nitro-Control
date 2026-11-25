"""Synthetic sysfs RGB tests: never open the host's real RGB interfaces."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from nitro_control.module_staging import install_rgb_helper
from nitro_control.rgb import EffectPlan, LightingPlan
from nitro_control.rgb_hardware import RGBHardware, RGBHardwareError, LED_NAMES, LINUWU_PATHS
from nitro_control.rgb_client import RGBClient
from nitro_control import RGB_WMI_GUID

REPO = Path(__file__).resolve().parents[1]


COLORS = LightingPlan(("#FF0000", "#00FF00", "#0000FF", "#ABCDEF"), 50)


class RGBHardwareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.hardware = RGBHardware(self.root)
        self._file("sys/class/dmi/id/product_name", "Nitro AN515-58\n")
        (self.root / f"sys/bus/wmi/devices/{RGB_WMI_GUID}-8").mkdir(parents=True)

    def _file(self, relative, contents):
        p = self.root / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(contents, encoding="ascii")
        return p

    def _native(self, index=("red", "green", "blue")):
        for i, name in enumerate(LED_NAMES):
            base = "sys/class/leds/" + name + "/"
            self._file(base + "multi_index", " ".join(index) + "\n")
            self._file(base + "multi_intensity", "11 22 33\n")
            self._file(base + "multi_max_intensity", "255 255 255\n")
            self._file(base + "max_brightness", "255\n")
            self._file(base + "brightness", "100\n")

    def test_guid_alone_not_backend(self):
        cap = self.hardware.probe()
        self.assertFalse(cap.available)
        self.assertEqual(cap.backend, "none")
        self.assertIn("no supported RGB", cap.detail)
        with self.assertRaises(RGBHardwareError):
            self.hardware.apply(COLORS)

    def test_wrong_model_never_writes_even_if_endpoint_present(self):
        p = self._file(LINUWU_PATHS[0], "000000,000000,000000,000000,50\n")
        self._file("sys/class/dmi/id/product_name", "Not Nitro\n")
        with self.assertRaises(RGBHardwareError):
            self.hardware.apply(COLORS)
        self.assertIn("000000,", p.read_text())

    def test_native_four_zone_color_and_brightness(self):
        self._native()
        cap = self.hardware.probe()
        self.assertTrue(cap.available)
        self.assertEqual(cap.backend, "native-led")
        result = self.hardware.apply(COLORS)
        self.assertTrue(result.verified_readback)
        self.assertIn("visually", result.detail)
        expected = (("255 0 0", "128"), ("0 255 0", "128"), ("0 0 255", "128"), ("171 205 239", "128"))
        for name, (rgb, bright) in zip(LED_NAMES, expected):
            base = self.root / "sys/class/leds" / name
            self.assertEqual((base / "multi_intensity").read_text().strip(), rgb)
            self.assertEqual((base / "brightness").read_text().strip(), bright)

    def test_native_channel_order_from_sysfs(self):
        self._native(index=("blue", "red", "green"))
        self.hardware.apply(COLORS)
        self.assertEqual((self.root / "sys/class/leds" / LED_NAMES[0] / "multi_intensity").read_text().strip(), "0 255 0")

    def test_incomplete_or_ambiguous_native_rejected(self):
        self._native(index=("unknown", "red", "green"))
        self.assertFalse(self.hardware.probe().available)
        with self.assertRaises(RGBHardwareError):
            self.hardware.apply(COLORS)

    def test_native_rolls_back_on_second_zone_failure(self):
        self._native()
        from nitro_control import rgb_hardware
        actual_write = rgb_hardware._write
        state = {"raised": False}
        def fail_once(path, value):
            if LED_NAMES[1] in str(path) and not state["raised"]:
                state["raised"] = True
                raise RGBHardwareError("simulated failed zone")
            return actual_write(path, value)
        with patch("nitro_control.rgb_hardware._write", side_effect=fail_once):
            with self.assertRaisesRegex(RGBHardwareError, "previous sysfs values restored"):
                self.hardware.apply(COLORS)
        for name in LED_NAMES:
            base = self.root / "sys/class/leds" / name
            self.assertEqual((base / "multi_intensity").read_text().strip(), "11 22 33")
            self.assertEqual((base / "brightness").read_text().strip(), "100")

    def test_probe_and_apply_effect_preserve_specific_native_refusal_detail(self):
        self._file("sys/class/dmi/id/sys_vendor", "Acer\n")
        (self.root / "sys/module/acer_wmi").mkdir(parents=True)
        (self.root / "sys/module/linuwu_sense").mkdir(parents=True)
        self._file("proc/cmdline", "quiet\n")
        cap = self.hardware.probe()
        self.assertFalse(cap.available)
        self.assertIn("no supported RGB", cap.detail)
        self.assertIn("Conflicting Linuwu driver is loaded", cap.detail)
        with self.assertRaisesRegex(RGBHardwareError, "Conflicting Linuwu driver is loaded"):
            self.hardware.apply_effect(EffectPlan("Breathing", 4, "#22CAFF"))

        # Legacy native-led backend still reports backend requirement rather than unavailable message.
        (self.root / "sys/module/linuwu_sense").rmdir()
        self._native()
        with self.assertRaisesRegex(RGBHardwareError, "Physical effects require the native Acer WMI backend"):
            self.hardware.apply_effect(EffectPlan("Wave", 3, "#112233"))

    def test_linuwu_four_zones_single_write(self):
        p = self._file(LINUWU_PATHS[0], "000000,000000,000000,000000,50\n")
        self.assertEqual(self.hardware.probe().backend, "linuwu-sense")
        result = self.hardware.apply(COLORS)
        self.assertTrue(result.verified_readback)
        self.assertEqual(p.read_text().strip(), "ff0000,00ff00,0000ff,abcdef,50")

    def test_missing_wmi_guid_disallows_writes(self):
        p = self._file(LINUWU_PATHS[0], "000000,000000,000000,000000,50\n")
        (self.root / f"sys/bus/wmi/devices/{RGB_WMI_GUID}-8").rmdir()
        self.assertFalse(self.hardware.probe().available)
        with self.assertRaises(RGBHardwareError):
            self.hardware.apply(COLORS)
        self.assertEqual(p.read_text().strip(), "000000,000000,000000,000000,50")

    def test_invalid_plan_is_never_written(self):
        p = self._file(LINUWU_PATHS[0], "000000,000000,000000,000000,50\n")
        with self.assertRaises(RGBHardwareError):
            self.hardware.apply(("#FF0000",) * 4)
        self.assertEqual(p.read_text().strip(), "000000,000000,000000,000000,50")

    def test_native_writes_do_not_touch_fans(self):
        self._native()
        fan = self._file("sys/class/hwmon/hwmon6/pwm1", "127\n")
        profile = self._file("sys/firmware/acpi/platform_profile", "balanced\n")
        self.hardware.apply(COLORS)
        self.assertEqual(fan.read_text(), "127\n")
        self.assertEqual(profile.read_text(), "balanced\n")


class RGBClientTests(unittest.TestCase):
    def test_client_does_not_call_helper_when_absent(self):
        calls = []
        client = RGBClient(Path("/definitely/missing/agent"), runner=lambda *a, **k: calls.append((a, k)))
        with self.assertRaisesRegex(RGBHardwareError, "not installed"):
            client.apply(COLORS)
        self.assertEqual(calls, [])

    def test_client_uses_exact_helper_path_and_structured_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = Path(tmp) / "helper"
            helper.write_text("stub")
            def runner(argv, **kwargs):
                self.assertEqual(argv, ["pkexec", str(helper)])
                self.assertEqual(json.loads(kwargs["input"])["zones"], list(COLORS.zones))
                self.assertFalse(kwargs["check"])
                return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "verified_readback": True,
                                                                          "backend": "native-led", "detail": "Sysfs confirmed"}))
            with patch("nitro_control.rgb_client.shutil.which", return_value="/usr/bin/pkexec"):
                self.assertEqual(RGBClient(helper, runner).apply(COLORS), "Sysfs confirmed")

    def test_helper_refusal_is_not_mistaken_for_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = Path(tmp) / "helper"
            helper.write_text("stub")
            runner = lambda *a, **k: SimpleNamespace(returncode=1, stdout='{"ok":false,"error":"denied"}')
            with patch("nitro_control.rgb_client.shutil.which", return_value="/usr/bin/pkexec"):
                with self.assertRaisesRegex(RGBHardwareError, "denied"):
                    RGBClient(helper, runner).apply(COLORS)

    def test_pkexec_authorization_denied_without_json_is_failure(self):
        # Real pkexec denial can return no structured helper stdout.
        with tempfile.TemporaryDirectory() as tmp:
            helper = Path(tmp) / "helper"
            helper.write_text("stub")
            def runner(argv, **kwargs):
                self.assertEqual(argv, ["pkexec", str(helper)])
                return SimpleNamespace(returncode=126, stdout="", stderr="Not authorized")
            with patch("nitro_control.rgb_client.shutil.which", return_value="/usr/bin/pkexec"):
                with self.assertRaisesRegex(RGBHardwareError, "denied"):
                    RGBClient(helper, runner).apply(COLORS)

    def test_helper_must_confirm_readback_and_valid_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = Path(tmp) / "helper"
            helper.write_text("stub")
            for payload in (
                {"ok": True, "verified_readback": False, "backend": "native-wmi"},
                {"ok": True, "verified_readback": True, "backend": "unknown"},
            ):
                with self.subTest(reply=payload):
                    def runner(argv, **kwargs):
                        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))
                    with patch("nitro_control.rgb_client.shutil.which", return_value="/usr/bin/pkexec"):
                        with self.assertRaises(RGBHardwareError):
                            RGBClient(helper, runner).apply(COLORS)

    def test_capability_discovery_preserves_static_and_blocks_incompatible_dynamic_before_pkexec(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "usr/local/libexec").mkdir(parents=True)
            (root / "usr/share/polkit-1/actions").mkdir(parents=True)
            (root / "run").mkdir(parents=True)
            helper = root / "usr/local/libexec/nitro-control-rgb-helper"
            pkg_dir = root / "usr/local/libexec/nitro-control-rgb/nitro_control"
            manifest = root / "usr/local/libexec/nitro-control-rgb/manifest.json"
            policy = root / "usr/share/polkit-1/actions/io.github.tobiasguta.NitroControl.rgb.policy"
            effect = EffectPlan("Breathing", 4, "#22CAFF")
            calls = []

            def runner(argv, **kwargs):
                calls.append((argv, kwargs))
                return SimpleNamespace(returncode=0, stdout=json.dumps({
                    "ok": True, "verified_readback": True, "backend": "native-wmi", "detail": "ok",
                }), stderr="")

            with patch("nitro_control.rgb_client.shutil.which", return_value="/usr/bin/pkexec"):
                client = RGBClient(helper, runner)

                # 1. Legitimate legacy preview.4 helper (complete files without manifest): Static works, Dynamic blocked pre-pkexec.
                pkg_dir.mkdir(parents=True)
                helper.write_text(
                    '#!/usr/bin/python3 -I\nimport sys\nsys.path.insert(0, "/usr/local/libexec/nitro-control-rgb")\n'
                    "from nitro_control.rgb_privileged import main\nraise SystemExit(main())\n",
                    encoding="utf-8",
                )
                policy.write_text(
                    '<policyconfig><action id="io.github.tobiasguta.NitroControl.rgb">'
                    '<annotate key="org.freedesktop.policykit.exec.path">/usr/local/libexec/nitro-control-rgb-helper</annotate>'
                    "</action></policyconfig>",
                    encoding="utf-8",
                )
                for dep in ("__init__.py", "rgb.py", "rgb_hardware.py", "native_rgb.py", "rgb_privileged.py"):
                    (pkg_dir / dep).write_text((REPO / "nitro_control" / dep).read_text(encoding="utf-8"), encoding="utf-8")

                caps_legacy = client.capabilities()
                self.assertEqual(caps_legacy.state, "legacy_static")
                self.assertTrue(caps_legacy.installed and caps_legacy.static_supported)
                self.assertFalse(caps_legacy.effect_supported)
                self.assertTrue(caps_legacy.legacy_manifest_missing)
                self.assertEqual(client.apply(COLORS), "ok")
                self.assertEqual(len(calls), 1)
                with self.assertRaisesRegex(RGBHardwareError, "install-rgb-helper-fedora.sh"):
                    client.apply_effect(effect)
                self.assertEqual(len(calls), 1)  # pkexec NOT invoked!

                # Legacy helper missing a dependency -> fail closed before pkexec!
                (pkg_dir / "rgb_hardware.py").unlink()
                caps_missing_dep = client.capabilities()
                self.assertEqual(caps_missing_dep.state, "missing_dependency")
                self.assertFalse(caps_missing_dep.static_supported)
                with self.assertRaisesRegex(RGBHardwareError, "Missing legacy RGB helper dependency"):
                    client.apply(COLORS)
                self.assertEqual(len(calls), 1)

                # 2. Full coherent Phase 4 upgrade -> Dynamic & Static both enabled.
                install_rgb_helper(root, REPO, adopt_legacy=True, runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
                caps_new = client.capabilities()
                self.assertEqual(caps_new.state, "coherent")
                self.assertTrue(caps_new.static_supported and caps_new.effect_supported)
                self.assertEqual(client.apply_effect(effect), "ok")
                self.assertEqual(len(calls), 2)

                # 3. Stale/inconsistent manifest cases: modified file, missing dependency, missing digest entry, EffectPlan mismatch, unsupported protocol.
                saved_man = manifest.read_text(encoding="utf-8")
                saved_rgb = (pkg_dir / "rgb.py").read_text(encoding="utf-8")
                (pkg_dir / "rgb.py").write_text(saved_rgb + "\n# stale\n", encoding="utf-8")
                self.assertEqual(client.capabilities().state, "inconsistent_manifest")
                self.assertFalse(client.capabilities().static_supported)
                with self.assertRaisesRegex(RGBHardwareError, "does not match manifest digest"):
                    client.apply(COLORS)
                (pkg_dir / "rgb.py").write_text(saved_rgb, encoding="utf-8")

                # Manifest claiming static-only with valid matching digests allows Static, blocks Dynamic pre-pkexec.
                man_obj = json.loads(saved_man)
                man_obj["capabilities"] = ["static"]
                manifest.write_text(json.dumps(man_obj), encoding="utf-8")
                self.assertFalse(client.capabilities().effect_supported)
                self.assertTrue(client.capabilities().static_supported)
                with self.assertRaisesRegex(RGBHardwareError, "Dynamic effects"):
                    client.apply_effect(effect)

                # Manifest with unsupported future protocol -> fails closed before pkexec.
                man_obj["capabilities"] = ["static", "effect"]
                man_obj["helper_protocol"] = 99
                manifest.write_text(json.dumps(man_obj), encoding="utf-8")
                self.assertEqual(client.capabilities().state, "unsupported_protocol")
                self.assertFalse(client.capabilities().static_supported)

                # Bare manifest omitting files digest map is rejected (never trusts bare manifest declarations!).
                manifest.write_text(json.dumps({
                    "managed_by": "nitro-control", "schema_version": 1,
                    "component": "rgb-helper", "helper_protocol": 2, "capabilities": ["static", "effect"],
                }), encoding="utf-8")
                self.assertEqual(client.capabilities().state, "inconsistent_manifest")
                self.assertFalse(client.capabilities().static_supported)

                # Corrupt or symlinked manifest refuses before pkexec.
                manifest.write_text("{corrupt", encoding="utf-8")
                self.assertEqual(client.capabilities().state, "invalid_manifest")
                self.assertFalse(client.capabilities().static_supported)
                manifest.unlink()
                other = root / "other.json"
                other.write_text("{}", encoding="utf-8")
                manifest.symlink_to(other)
                with self.assertRaisesRegex(RGBHardwareError, "symlink"):
                    client.apply(COLORS)
                self.assertEqual(len(calls), 2)

    def test_helper_subprocess_timeouts_permission_denial_malformed_json_and_cancel(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "usr/local/libexec").mkdir(parents=True)
            (root / "usr/share/polkit-1/actions").mkdir(parents=True)
            (root / "run").mkdir(parents=True)
            install_rgb_helper(root, REPO, runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
            helper = root / "usr/local/libexec/nitro-control-rgb-helper"

            with patch("nitro_control.rgb_client.shutil.which", return_value="/usr/bin/pkexec"):
                # Real harmless sleeping subprocess exceeds short client timeout -> uncertain state, zero retries, honest non-guarantee!
                attempts = []

                def slow_subproc(argv, **kwargs):
                    attempts.append(argv)
                    return subprocess.run(
                        [sys.executable, "-c", "import time; time.sleep(0.5)"],
                        capture_output=True, text=True, timeout=kwargs["timeout"], check=False,
                    )

                with patch("nitro_control.rgb_client.HELPER_TIMEOUT", 0.05):
                    client = RGBClient(helper, slow_subproc)
                    with self.assertRaises(RGBHardwareError) as ctx_to:
                        client.apply(COLORS)
                self.assertEqual(len(attempts), 1)
                msg_to = str(ctx_to.exception).lower()
                self.assertIn("timeout", msg_to)
                self.assertIn("does not guarantee the privileged process stopped", msg_to)
                self.assertIn("uncertain", msg_to)

                # Simulated PermissionError when caller cannot signal privileged pkexec child -> uncertain & deadline note!
                client_perm = RGBClient(helper, lambda *a, **k: (_ for _ in ()).throw(PermissionError("EPERM")))
                with self.assertRaises(RGBHardwareError) as ctx_perm:
                    client_perm.apply(COLORS)
                self.assertIn("continue past client deadline", str(ctx_perm.exception).lower())
                self.assertIn("uncertain", str(ctx_perm.exception).lower())

                # Pre-exec OSError (e.g. FileNotFoundError) -> helper did not execute!
                client_fnf = RGBClient(helper, lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no pkexec")))
                with self.assertRaisesRegex(RGBHardwareError, "helper did not execute"):
                    client_fnf.apply(COLORS)

                # Exit 126/127 without JSON NEVER falsely assert "no firmware writes were attempted".
                for rc in (126, 127):
                    c_auth = RGBClient(helper, lambda *a, _rc=rc, **k: SimpleNamespace(returncode=_rc, stdout="", stderr=""))
                    with self.assertRaises(RGBHardwareError) as ctx_a:
                        c_auth.apply(COLORS)
                    self.assertNotIn("no firmware writes were attempted", str(ctx_a.exception).lower())
                    self.assertIn("no structured helper confirmation", str(ctx_a.exception).lower())

                # H-P4-03: Malformed JSON payload field types (lists, dicts, null, numbers, bools, empty/bad strings) NEVER raise TypeError!
                bad_payloads = (
                    (1, "not-json", "uncertain"),
                    (0, json.dumps([]), "malformed"),
                    (0, json.dumps({"ok": 1, "verified_readback": True, "backend": "native-wmi"}), "malformed"),
                    (0, json.dumps({"ok": "true", "verified_readback": True, "backend": "native-wmi"}), "malformed"),
                    (0, json.dumps({"ok": True, "verified_readback": True, "backend": []}), "unrecognized or malformed backend"),
                    (0, json.dumps({"ok": True, "verified_readback": True, "backend": {"b": 1}}), "unrecognized or malformed backend"),
                    (0, json.dumps({"ok": True, "verified_readback": True, "backend": None}), "unrecognized or malformed backend"),
                    (0, json.dumps({"ok": True, "verified_readback": True, "backend": 42}), "unrecognized or malformed backend"),
                    (0, json.dumps({"ok": True, "verified_readback": True, "backend": True}), "unrecognized or malformed backend"),
                    (0, json.dumps({"ok": True, "verified_readback": True, "backend": "unknown"}), "unrecognized or malformed backend"),
                    (0, json.dumps({"ok": True, "verified_readback": [], "backend": "native-wmi"}), "verified hardware readback"),
                    (0, json.dumps({"ok": True, "verified_readback": True, "backend": "native-wmi", "detail": 123}), "malformed status detail"),
                    (0, json.dumps({"ok": True, "verified_readback": True, "backend": "native-wmi", "detail": ""}), "malformed status detail"),
                    (0, json.dumps({"ok": True, "verified_readback": True, "backend": "native-wmi", "detail": []}), "malformed status detail"),
                    (0, json.dumps({"ok": False, "error": ["bad"]}), "malformed error"),
                    (1, json.dumps({"ok": True, "verified_readback": True, "backend": "native-wmi"}), "non-zero exit status 1"),
                )
                for rc, out_txt, regex in bad_payloads:
                    with self.subTest(rc=rc, out_txt=out_txt):
                        c = RGBClient(helper, lambda *a, _rc=rc, _o=out_txt, **k: SimpleNamespace(returncode=_rc, stdout=_o, stderr=""))
                        with self.assertRaisesRegex(RGBHardwareError, regex):
                            c.apply(COLORS)

    def test_h_p4_r03_missing_policy_file_or_directory_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "usr/local/libexec").mkdir(parents=True)
            (root / "usr/share/polkit-1/actions").mkdir(parents=True)
            (root / "run").mkdir(parents=True)
            install_rgb_helper(root, REPO, runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
            helper = root / "usr/local/libexec/nitro-control-rgb-helper"
            pol = root / "usr/share/polkit-1/actions/io.github.tobiasguta.NitroControl.rgb.policy"

            calls = []
            with patch("nitro_control.rgb_client.shutil.which", return_value="/usr/bin/pkexec"):
                client = RGBClient(helper, lambda *a, **k: calls.append(a))
                self.assertEqual(client.capabilities().state, "coherent")

                # Remove policy file -> missing_dependency, Static & Dynamic disabled!
                pol.unlink()
                c_no_file = client.capabilities()
                self.assertEqual(c_no_file.state, "missing_dependency")
                self.assertFalse(c_no_file.static_supported or c_no_file.effect_supported)
                with self.assertRaises(RGBHardwareError):
                    client.apply(COLORS)

                # Remove entire policy directory -> STILL missing_dependency, NEVER coherent!
                pol.parent.rmdir()
                c_no_dir = client.capabilities()
                self.assertEqual(c_no_dir.state, "missing_dependency")
                self.assertFalse(c_no_dir.static_supported or c_no_dir.effect_supported)
                with self.assertRaises(RGBHardwareError):
                    client.apply(COLORS)

                # Same missing policy directory check applies for unmanifested preview.4 helper!
                (root / "usr/local/libexec/nitro-control-rgb/manifest.json").unlink()
                c_leg_no_dir = client.capabilities()
                self.assertEqual(c_leg_no_dir.state, "missing_dependency")
                self.assertFalse(c_leg_no_dir.static_supported)
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()

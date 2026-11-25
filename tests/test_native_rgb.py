"""Synthetic native-WMI tests. No WMI, EC, modprobe, or real sysfs access."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from nitro_control import RGB_WMI_GUID
from nitro_control.native_rgb import NativeRGB, NativeRGBError
from nitro_control.rgb import EffectPlan, LightingPlan
from nitro_control.rgb_hardware import RGBHardware, RGBHardwareError


COLORS = LightingPlan(("#FF0000", "#00FF00", "#0000FF", "#ABCDEF"), 100)


class NativeRGBTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.kernel = os.uname().release
        self.adapter = NativeRGB(self.root, self.kernel)
        self._write("sys/class/dmi/id/sys_vendor", "Acer")
        self._write("sys/class/dmi/id/product_name", "Nitro AN515-58")
        (self.root / "sys/bus/wmi/devices" / f"{RGB_WMI_GUID}-8").mkdir(parents=True)
        (self.root / "sys/module/acer_wmi").mkdir(parents=True)
        self._write("proc/cmdline", "quiet rhgb")
        self._write("sys/class/hwmon/hwmon1/name", "acer")
        self._write("sys/class/hwmon/hwmon1/fan1_input", "2200")
        self._write("sys/class/hwmon/hwmon1/fan2_input", "2100")
        self._write("sys/firmware/acpi/platform_profile", "balanced")
        self.adapter.module.parent.mkdir(parents=True, exist_ok=True)
        self.adapter.module.write_bytes(b"dummy .ko for synthetic test")
        sha = hashlib.sha256(self.adapter.module.read_bytes()).hexdigest()
        self.adapter.digest.write_text(sha)
        self.adapter.manifest.write_text(json.dumps({
            "managed_by": "nitro-control",
            "schema_version": 1,
            "component": "native-rgb",
            "module_name": "nitro_rgb_enable_poc",
            "kernel": self.kernel,
            "sha256": sha,
            "state": "committed",
        }))
        real_subproc_run = subprocess.run

        def fake_staging_run(argv, **kwargs):
            if argv and Path(argv[0]).name == "modinfo" and "-p" in argv:
                return subprocess.CompletedProcess(argv, 0, stdout="apply:bool\n", stderr="")
            return real_subproc_run(argv, **kwargs)

        self._modinfo_patch = patch("nitro_control.module_staging.subprocess.run", side_effect=fake_staging_run)
        self._modinfo_patch.start()
        self.addCleanup(self._modinfo_patch.stop)

    def _write(self, path: str, contents: str):
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(contents)

    @contextmanager
    def _pretend_installed_root_owned(self):
        """Only fake file owner metadata; keep real hashes and file contents."""
        actual_stat = Path.stat
        owned = {self.adapter.module, self.adapter.digest, self.adapter.manifest}

        def fake_stat(path, *args, **kwargs):
            original = actual_stat(path, *args, **kwargs)
            if path in owned:
                fields = list(original)
                fields[4] = 0  # st_uid, as with the root-owned installed files
                fields[0] = (fields[0] & ~0o777) | (
                    0o644 if path == self.adapter.module else 0o600)
                return os.stat_result(fields)
            return original

        with patch("nitro_control.native_rgb.Path.stat", autospec=True,
                   side_effect=fake_stat):
            yield

    def test_checksum_tampering_refused_before_any_module_command(self):
        self.adapter.module.write_bytes(b"tampered development module")
        with (self._pretend_installed_root_owned(),
              patch("nitro_control.native_rgb._run") as run):
            with self.assertRaisesRegex(NativeRGBError, "digest mismatch"):
                self.adapter._verify_install()
            run.assert_not_called()

    def test_digest_symlink_refused(self):
        self.adapter.digest.unlink()
        target = self.root / "fake-digest"
        target.write_text("0" * 64)
        self.adapter.digest.symlink_to(target)
        with (self._pretend_installed_root_owned(),
              patch("nitro_control.native_rgb._run") as run):
            with self.assertRaisesRegex(NativeRGBError, "symlink"):
                self.adapter._verify_install()
            run.assert_not_called()

    def test_different_kernel_vermagic_refused(self):
        answers = [
            subprocess.CompletedProcess(["modinfo"], 0, stdout="nitro_rgb_enable_poc\n"),
            subprocess.CompletedProcess(["modinfo"], 0, stdout="unrelated-kernel SMP\n"),
        ]
        with (self._pretend_installed_root_owned(),
              patch("nitro_control.native_rgb._run", side_effect=answers) as run):
            with self.assertRaisesRegex(NativeRGBError, "kernel version mismatch"):
                self.adapter._verify_install()
            self.assertEqual(run.call_count, 2)

    def test_corrupted_module_denied_before_insmod_even_for_root(self):
        self.adapter.module.write_bytes(b"corrupted after install")
        with (self._pretend_installed_root_owned(),
              patch("nitro_control.native_rgb.os.geteuid", return_value=0),
              patch("nitro_control.native_rgb._run") as run):
            with self.assertRaisesRegex(NativeRGBError, "digest mismatch"):
                self.adapter.apply(COLORS)
            run.assert_not_called()

    def test_unsafe_lock_aborts_without_module_operation(self):
        with (patch("nitro_control.native_rgb.os.geteuid", return_value=0),
              patch("nitro_control.native_rgb._LOCK", self.root / "lock"),
              patch.object(self.adapter, "_verify_install"),
              patch("nitro_control.native_rgb.os.open",
                    side_effect=PermissionError("lock denied")),
              patch("nitro_control.native_rgb._run") as run):
            with self.assertRaisesRegex(NativeRGBError, "lock unavailable"):
                self.adapter.apply(COLORS)
            run.assert_not_called()

    def test_busy_lock_prevents_wmi_operation(self):
        with (patch("nitro_control.native_rgb.os.geteuid", return_value=0),
              patch("nitro_control.native_rgb._LOCK", self.root / "lock"),
              patch("nitro_control.native_rgb.os.fstat",
                    return_value=SimpleNamespace(st_uid=0, st_mode=0o600)),
              patch("nitro_control.native_rgb.fcntl.flock",
                    side_effect=BlockingIOError("already locked")),
              patch.object(self.adapter, "_verify_install"),
              patch("nitro_control.native_rgb._run") as run):
            with self.assertRaisesRegex(NativeRGBError, "already active"):
                self.adapter.apply(COLORS)
            run.assert_not_called()

    def test_failed_module_insert_with_partial_load_still_unloads(self):
        commands = []
        def runner(argv, **kwargs):
            commands.append(argv[0])
            if argv[0].endswith("/insmod"):
                (self.root / "sys/module/nitro_rgb_enable_poc").mkdir()
                return subprocess.CompletedProcess(argv, 1, stderr="firmware rejected")
            (self.root / "sys/module/nitro_rgb_enable_poc").rmdir()
            return subprocess.CompletedProcess(argv, 0)

        with (patch("nitro_control.native_rgb.os.geteuid", return_value=0),
              patch("nitro_control.native_rgb._LOCK", self.root / "lock"),
              patch("nitro_control.native_rgb.os.fstat",
                    return_value=SimpleNamespace(st_uid=0, st_mode=0o600)),
              patch.object(self.adapter, "_verify_install"),
              patch("nitro_control.native_rgb._run", side_effect=runner)):
            with self.assertRaisesRegex(NativeRGBError, "firmware rejected"):
                self.adapter.apply(COLORS)
        self.assertEqual(commands, ["/usr/sbin/insmod", "/usr/sbin/rmmod"])

    def test_unload_failure_blocks_success(self):
        commands = []
        def runner(argv, **kwargs):
            commands.append(argv[0])
            return subprocess.CompletedProcess(
                argv, 0 if argv[0].endswith("/insmod") else 1,
                stderr="unload failed" if argv[0].endswith("/rmmod") else "")

        with (patch("nitro_control.native_rgb.os.geteuid", return_value=0),
              patch("nitro_control.native_rgb._LOCK", self.root / "lock"),
              patch("nitro_control.native_rgb.os.fstat",
                    return_value=SimpleNamespace(st_uid=0, st_mode=0o600)),
              patch.object(self.adapter, "_verify_install"),
              patch("nitro_control.native_rgb._run", side_effect=runner)):
            with self.assertRaisesRegex(NativeRGBError, "CRITICAL"):
                self.adapter.apply(COLORS)
        self.assertEqual(commands, ["/usr/sbin/insmod", "/usr/sbin/rmmod"])

    def test_exact_model_valid_prerequisites(self):
        ok, reason = self.adapter.status()
        self.assertTrue(ok, reason)
        self.assertEqual(RGBHardware(self.root).probe().backend, "native-wmi")

    def test_non_acer_model_rejected(self):
        self._write("sys/class/dmi/id/product_name", "Nitro AN999-99")
        self.assertFalse(self.adapter.status()[0])
        self.assertFalse(RGBHardware(self.root).probe().available)

    def test_incomplete_fan_monitoring_rejected(self):
        (self.root / "sys/class/hwmon/hwmon1/fan2_input").unlink()
        self.assertFalse(self.adapter.status()[0])

    def test_missing_current_kernel_module_rejected(self):
        self.adapter.module.unlink()
        self.assertFalse(self.adapter.status()[0])

    def test_alternative_caps_rejected(self):
        self._write("proc/cmdline", "quiet acer_wmi.force_caps=1024")
        self.assertFalse(self.adapter.status()[0])

    def test_conflicting_driver_rejected(self):
        (self.root / "sys/module/linuwu_sense").mkdir()
        self.assertFalse(self.adapter.status()[0])

    def test_missing_guid_rejected(self):
        (self.root / "sys/bus/wmi/devices" / f"{RGB_WMI_GUID}-8").rmdir()
        self.assertFalse(self.adapter.status()[0])

    def test_non_100_brightness_rejected_before_module_operations(self):
        # Confirm an unverified firmware brightness never reaches insmod, even
        # for a root caller and a valid staged module.
        for value in (0, 25, 65, 75, 99):
            with self.subTest(brightness=value):
                plan = LightingPlan(COLORS.zones, value)
                with (patch("nitro_control.native_rgb.os.geteuid", return_value=0),
                      patch("nitro_control.native_rgb._run") as run):
                    with self.assertRaisesRegex(NativeRGBError, "requires 100%"):
                        self.adapter.apply(plan)
                    run.assert_not_called()

    def test_requires_admin_for_physical_apply(self):
        with patch("nitro_control.native_rgb.os.geteuid", return_value=1000):
            with self.assertRaisesRegex(NativeRGBError, "administrator"):
                self.adapter.apply(COLORS)

    def test_helper_invokes_fixed_module_and_unloads(self):
        calls = []
        def runner(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        lock = self.root / "native-rgb.lock"
        with (patch("nitro_control.native_rgb.os.geteuid", return_value=0),
              patch("nitro_control.native_rgb._LOCK", lock),
              patch("nitro_control.native_rgb.os.fstat",
                    return_value=SimpleNamespace(st_uid=0, st_mode=0o600)),
              patch.object(self.adapter, "_verify_install"),
              patch("nitro_control.native_rgb._run", side_effect=runner)):
            message = self.adapter.apply(COLORS)
        self.assertIn("visually", message)
        self.assertEqual(calls[0][0], "/usr/sbin/insmod")
        self.assertEqual(calls[0][1], str(self.adapter.module))
        self.assertEqual(calls[0][2:], [
            "apply=1", "colors=FF0000,00FF00,0000FF,ABCDEF", "brightness=100"])
        self.assertEqual(calls[1], ["/usr/sbin/rmmod", "nitro_rgb_enable_poc"])

    def test_write_failure_does_not_report_success(self):
        def runner(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="firmware refused")
        with (patch("nitro_control.native_rgb.os.geteuid", return_value=0),
              patch("nitro_control.native_rgb._LOCK", self.root / "lock"),
              patch("nitro_control.native_rgb.os.fstat",
                    return_value=SimpleNamespace(st_uid=0, st_mode=0o600)),
              patch.object(self.adapter, "_verify_install"),
              patch("nitro_control.native_rgb._run", side_effect=runner)):
            with self.assertRaisesRegex(NativeRGBError, "firmware refused"):
                self.adapter.apply(COLORS)

    def test_rgb_hardware_refuses_unprivileged_native_apply(self):
        with patch("nitro_control.native_rgb.os.geteuid", return_value=1000):
            with self.assertRaises(RGBHardwareError):
                RGBHardware(self.root).apply(COLORS)

    def test_native_rgb_stages_in_private_path_outside_usr_lib_modules(self):
        self.assertNotIn("/usr/lib/modules", str(self.adapter.module))
        self.assertIn("/usr/local/lib/nitro-control/modules/", str(self.adapter.module))
        with self.assertRaises(ValueError):
            NativeRGB(self.root, "../escape")

    def test_legacy_indexed_native_module_without_private_staging_requires_migration(self):
        self.adapter.module.unlink()
        self.adapter.legacy_module.parent.mkdir(parents=True, exist_ok=True)
        self.adapter.legacy_module.write_bytes(b"legacy ko")
        ok, reason = self.adapter.status()
        self.assertFalse(ok)
        self.assertIn("migrate", reason.lower())
        self.assertNotIn("Install the opt-in", reason)

    def test_verify_install_executes_inside_private_lock(self):
        events = []
        actual_flock = __import__("fcntl").flock

        def trace_flock(fd, op):
            events.append("flock")
            return actual_flock(fd, op)

        def trace_verify():
            events.append("verify")

        def runner(argv, **_kwargs):
            events.append(Path(argv[0]).name)
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        with (patch("nitro_control.native_rgb.os.geteuid", return_value=0),
              patch("nitro_control.native_rgb.fcntl.flock", side_effect=trace_flock),
              patch.object(self.adapter, "_verify_install", side_effect=trace_verify),
              patch("nitro_control.native_rgb._run", side_effect=runner)):
            self.adapter.apply(COLORS)
        self.assertEqual(events, ["flock", "verify", "insmod", "rmmod"])
        self.assertTrue((self.root / "run/nitro-control/native-rgb.lock").is_file())

    def test_unsafe_staging_directory_symlink_or_mode_rejected(self):
        with (self._pretend_installed_root_owned(),
              patch("nitro_control.native_rgb.os.geteuid", return_value=0),
              patch("nitro_control.native_rgb._run") as run):
            self.adapter.module.parent.chmod(0o777)
            with self.assertRaisesRegex(NativeRGBError, "staging directory ownership or mode"):
                self.adapter._verify_install()
            self.adapter.module.parent.chmod(0o755)

            real_dir = self.adapter.module.parent
            alt_dir = self.root / "alt-dir"
            alt_dir.mkdir()
            real_dir.rename(alt_dir / "real")
            real_dir.symlink_to(alt_dir / "real")
            self.assertFalse(self.adapter.status()[0])
            with self.assertRaisesRegex(NativeRGBError, "symlink"):
                self.adapter._verify_install()
            run.assert_not_called()

    def test_non_root_module_ownership_rejected_by_verify_install(self):
        # Without _pretend_installed_root_owned(), test runner uid != 0 must fail closed.
        with (patch("nitro_control.native_rgb.os.geteuid", return_value=0),
              patch("nitro_control.native_rgb._run") as run):
            with self.assertRaisesRegex(NativeRGBError, "ownership or mode mismatch"):
                self.adapter.apply(COLORS)
            run.assert_not_called()

    def test_lock_contention_fails_before_verify_install_and_concurrent_flock_blocks(self):
        # Simulate another installer/remover holding <root>/run/nitro-control/native-rgb.lock.
        import fcntl
        lock_dir = self.root / "run/nitro-control"
        lock_dir.mkdir(parents=True, mode=0o700)
        lock_file = lock_dir / "native-rgb.lock"
        fd = os.open(lock_file, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with (patch("nitro_control.native_rgb.os.geteuid", return_value=0),
              patch.object(self.adapter, "_verify_install") as verify,
              patch("nitro_control.native_rgb._run") as run):
            with self.assertRaisesRegex(NativeRGBError, "already active"):
                self.adapter.apply(COLORS)
            verify.assert_not_called()
            run.assert_not_called()

    def test_coexisting_legacy_and_stale_depmod_index_block_readiness_and_verify(self):
        # Even when a valid private triple exists, an unresolved legacy module blocks status() and _verify_install().
        self.adapter.legacy_module.parent.mkdir(parents=True, exist_ok=True)
        self.adapter.legacy_module.write_bytes(b"stale legacy")
        ok, reason = self.adapter.status()
        self.assertFalse(ok)
        self.assertIn("legacy indexed", reason.lower())
        with (self._pretend_installed_root_owned(),
              patch("nitro_control.native_rgb._run") as run):
            with self.assertRaisesRegex(NativeRGBError, "Unresolved legacy"):
                self.adapter._verify_install()
            run.assert_not_called()
        self.adapter.legacy_module.unlink()

        # Stale modules.dep reference also blocks readiness and _verify_install().
        dep = self.root / f"usr/lib/modules/{self.kernel}/modules.dep"
        dep.parent.mkdir(parents=True, exist_ok=True)
        dep.write_text("extra/nitro-control/nitro_rgb_enable_poc.ko:\n")
        ok2, reason2 = self.adapter.status()
        self.assertFalse(ok2)
        self.assertIn("stale", reason2.lower())
        with (self._pretend_installed_root_owned(),
              patch("nitro_control.native_rgb._run") as run):
            with self.assertRaisesRegex(NativeRGBError, "Stale module index"):
                self.adapter._verify_install()
            run.assert_not_called()

    def test_missing_or_foreign_manifest_and_hardlinks_rejected(self):
        with (self._pretend_installed_root_owned(),
              patch("nitro_control.native_rgb._run") as run):
            self.adapter.manifest.write_text("{}")
            with self.assertRaisesRegex(NativeRGBError, "provenance manifest"):
                self.adapter._verify_install()

            self.adapter.manifest.unlink()
            self.assertFalse(self.adapter.status()[0])
            with self.assertRaisesRegex(NativeRGBError, "missing or a symlink"):
                self.adapter._verify_install()
            run.assert_not_called()

        # Restore valid manifest, then test hardlink rejection on module file.
        sha = self.adapter.digest.read_text().strip()
        self.adapter.manifest.write_text(json.dumps({
            "managed_by": "nitro-control",
            "schema_version": 1,
            "component": "native-rgb",
            "module_name": "nitro_rgb_enable_poc",
            "kernel": self.kernel,
            "sha256": sha,
            "state": "committed",
        }))
        extra_link = self.root / "extra-hardlink.ko"
        os.link(self.adapter.module, extra_link)
        with (self._pretend_installed_root_owned(),
              patch("nitro_control.native_rgb._run") as run):
            with self.assertRaisesRegex(NativeRGBError, "ownership or mode mismatch"):
                self.adapter._verify_install()
            run.assert_not_called()

    def test_status_never_requires_reading_0600_secrets(self):
        # Even if digest and manifest are chmod 0000 (simulating unprivileged user vs root 0600),
        # NativeRGB.status() checks existence/type via 0755 dir metadata without reading contents.
        self.adapter.digest.chmod(0o000)
        self.adapter.manifest.chmod(0o000)
        self.addCleanup(lambda: (self.adapter.digest.chmod(0o600), self.adapter.manifest.chmod(0o600)))
        with patch("pathlib.Path.read_bytes", side_effect=AssertionError("unexpected read_bytes")):
            ok, _reason = self.adapter.status()
        self.assertTrue(ok)

    def test_experimental_diagnostic_params_rejected_before_insmod(self):
        for bad_param in ("backlight_test:bool", "red_test:bool", "enable:bool", "effect_test:bool"):
            with self.subTest(bad_param=bad_param):
                answers = [
                    subprocess.CompletedProcess(["modinfo"], 0, stdout="nitro_rgb_enable_poc\n"),
                    subprocess.CompletedProcess(["modinfo"], 0, stdout=f"{self.kernel} SMP\n"),
                    subprocess.CompletedProcess(["modinfo"], 0, stdout=f"apply:bool\n{bad_param}\n"),
                ]
                with (self._pretend_installed_root_owned(),
                      patch("nitro_control.native_rgb.os.geteuid", return_value=0),
                      patch("nitro_control.native_rgb._run", side_effect=answers) as run):
                    with self.assertRaisesRegex(NativeRGBError, "Experimental diagnostic"):
                        self.adapter._verify_install()
                    self.assertEqual(run.call_count, 3)

    def test_apply_and_effect_accuracy_and_empty_stderr_diagnostics(self):
        with (patch("nitro_control.native_rgb.os.geteuid", return_value=0),
              patch("nitro_control.native_rgb._LOCK", self.root / "native-rgb.lock"),
              patch("nitro_control.native_rgb.os.fstat", return_value=SimpleNamespace(st_uid=0, st_mode=0o600)),
              patch.object(self.adapter, "_verify_install"),
              patch.object(self.adapter, "_has_effect_parameter", return_value=True)):
            with patch("nitro_control.native_rgb._run", return_value=subprocess.CompletedProcess(["x"], 0, "", "")):
                msg_static = self.adapter.apply(COLORS)
                self.assertIn("static mode", msg_static.lower())
                self.assertIn("brightness 100%", msg_static.lower())
                self.assertIn("visually", msg_static.lower())

                msg_effect = self.adapter.apply_effect(EffectPlan("Breathing", 4, "#22CAFF"))
                self.assertIn("mode=1, speed=4, brightness=100", msg_effect)
                self.assertIn("effect color and physical LEDs are not firmware-verified", msg_effect)

            # Empty stderr on failed insmod preserves exit code diagnostic and truthful recovery disclosure
            def fail_empty_stderr(argv, **kwargs):
                if argv[0].endswith("/insmod"):
                    return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")
                return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

            with patch("nitro_control.native_rgb._run", side_effect=fail_empty_stderr):
                with self.assertRaises(NativeRGBError) as ctx_static:
                    self.adapter.apply(COLORS)
                msg_fail_s = str(ctx_static.exception)
                self.assertIn("insmod exited 1", msg_fail_s)
                self.assertIn("may have changed", msg_fail_s)
                self.assertIn("recovery could not be confirmed", msg_fail_s)
                self.assertNotIn("left unchanged", msg_fail_s.lower())

                with self.assertRaises(NativeRGBError) as ctx_eff:
                    self.adapter.apply_effect(EffectPlan("Breathing", 4, "#22CAFF"))
                msg_fail_e = str(ctx_eff.exception)
                self.assertIn("insmod exited 1", msg_fail_e)
                self.assertIn("may have changed", msg_fail_e)
                self.assertIn("recovery could not be confirmed", msg_fail_e)
                self.assertIn("journalctl -k", msg_fail_e)

    def test_insmod_timeout_uncertainty_and_conditional_rmmod_cleanup(self):
        import sys
        from nitro_control.native_rgb import NativeRGBTimeoutError

        # 1. Timeout during insmod triggers conditional rmmod when sysfs module node exists,
        #    and raises uncertain lighting state without retrying.
        calls = []

        def slow_insmod(argv, **_kwargs):
            calls.append(Path(argv[0]).name)
            if argv[0].endswith("/insmod"):
                (self.root / "sys/module/nitro_rgb_enable_poc").mkdir(parents=True, exist_ok=True)
                subprocess.run([sys.executable, "-c", "import time; time.sleep(0.3)"], timeout=0.02, check=False)
            return subprocess.CompletedProcess(argv, 0, "", "")

        with (self._pretend_installed_root_owned(),
              patch("nitro_control.native_rgb.os.geteuid", return_value=0),
              patch("nitro_control.native_rgb._LOCK", self.root / "native-rgb.lock"),
              patch("nitro_control.native_rgb.os.fstat", return_value=SimpleNamespace(st_uid=0, st_mode=0o600)),
              patch.object(self.adapter, "_verify_install"),
              patch("nitro_control.native_rgb._run") as mock_run):
            # Forward to NativeRGBTimeoutError via actual TimeoutExpired conversion check:
            def run_wrap(argv, **kwargs):
                try:
                    return slow_insmod(argv, **kwargs)
                except subprocess.TimeoutExpired as exc:
                    raise NativeRGBTimeoutError("RGB module operation timed out after 20s") from exc

            mock_run.side_effect = run_wrap
            with self.assertRaises(NativeRGBError) as ctx_to:
                self.adapter.apply(COLORS)
        self.assertEqual(calls, ["insmod", "rmmod"])
        self.assertIn("uncertain", str(ctx_to.exception).lower())

        # 2. Failed insmod WITHOUT /sys/module entry MUST NOT invoke rmmod (conditional removal).
        if (self.root / "sys/module/nitro_rgb_enable_poc").exists():
            (self.root / "sys/module/nitro_rgb_enable_poc").rmdir()
        calls_no_rmmod = []

        def fail_without_insert(argv, **_kwargs):
            calls_no_rmmod.append(Path(argv[0]).name)
            return subprocess.CompletedProcess(argv, 1, "", "firmware rejected")

        with (patch("nitro_control.native_rgb.os.geteuid", return_value=0),
              patch("nitro_control.native_rgb._LOCK", self.root / "native-rgb.lock"),
              patch("nitro_control.native_rgb.os.fstat", return_value=SimpleNamespace(st_uid=0, st_mode=0o600)),
              patch.object(self.adapter, "_verify_install"),
              patch("nitro_control.native_rgb._run", side_effect=fail_without_insert)):
            with self.assertRaisesRegex(NativeRGBError, "recovery could not be confirmed"):
                self.adapter.apply(COLORS)
        self.assertEqual(calls_no_rmmod, ["insmod"])

        # 3. Cleanup failure AFTER successful insmod preserves success uncertainty context explicitly.
        def insert_then_rmmod_fail(argv, **_kwargs):
            if argv[0].endswith("/insmod"):
                return subprocess.CompletedProcess(argv, 0, "", "")
            return subprocess.CompletedProcess(argv, 1, "", "rmmod EBUSY")

        with (patch("nitro_control.native_rgb.os.geteuid", return_value=0),
              patch("nitro_control.native_rgb._LOCK", self.root / "native-rgb.lock"),
              patch("nitro_control.native_rgb.os.fstat", return_value=SimpleNamespace(st_uid=0, st_mode=0o600)),
              patch.object(self.adapter, "_verify_install"),
              patch("nitro_control.native_rgb._run", side_effect=insert_then_rmmod_fail)):
            with self.assertRaisesRegex(NativeRGBError, "firmware write may have succeeded but module remains loaded"):
                self.adapter.apply(COLORS)

    def test_single_metadata_probe_and_h_p3_r01_fail_closed_in_native_rgb(self):
        # Stop dummy setUp modinfo mock so real readiness failure modes are exercised.
        self._modinfo_patch.stop()
        modinfo_calls = []

        def count_modinfo(argv, **kwargs):
            modinfo_calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "apply:bool\neffect_mode:int\n", "")

        with patch("nitro_control.module_staging.subprocess.run", side_effect=count_modinfo):
            cap = RGBHardware(self.root).probe()
        self.assertTrue(cap.available)
        self.assertEqual(cap.backend, "native-wmi")
        self.assertTrue(cap.effects_supported)
        self.assertEqual(len(modinfo_calls), 1)  # status + effect capability in single modinfo -p pass!

        # Failing modinfo -p (exit 1) or timeout refuses readiness in NativeRGB.status().
        with patch("nitro_control.module_staging.subprocess.run",
                   return_value=subprocess.CompletedProcess(["modinfo"], 1, "", "err")):
            ok, reason = self.adapter.status()
            self.assertFalse(ok)
            self.assertIn("failed", reason.lower())

        def timeout_modinfo(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 2))

        with patch("nitro_control.module_staging.subprocess.run", side_effect=timeout_modinfo):
            ok_t, reason_t = self.adapter.status()
            self.assertFalse(ok_t)
            self.assertIn("timed out", reason_t.lower())


if __name__ == "__main__":
    unittest.main()

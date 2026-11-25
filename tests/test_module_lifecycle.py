"""Behavioral tests for kernel module staging, provenance, locking, transactions, and migration.

`StagingUnitLifecycleTests` runs pure-Python against synthetic roots everywhere.
`IsolatedModuleLifecycleTests` runs actual shell scripts inside bubblewrap (`bwrap`)
with an isolated temporary root filesystem; set `NITRO_REQUIRE_ISOLATED_ROOT_TESTS=1`
in CI to fail instead of skipping when `bwrap` is unavailable.
"""
from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
from pathlib import Path
import py_compile
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from nitro_control import RGB_WMI_GUID
from nitro_control.module_staging import (
    CONTROLLER_MARKER,
    DRIVER_LIB_MANIFEST,
    ModuleStagingError,
    RGB_HELPER_MANIFEST,
    RGB_HELPER_TX_FILE,
    SERVICE_MARKER,
    _render_rgb_helper_launcher,
    acquire_private_locks,
    check_artifact_compatibility,
    check_installation_qualification,
    check_unprivileged_readiness,
    clearance_sentinel_name,
    install_rgb_helper,
    manifest_name,
    migration_state_name,
    stage_install,
    tx_dir_name,
    tx_json_name,
    uninstall_modules,
    uninstall_rgb_helper,
    verify_privileged_rgb_helper,
    verify_privileged_runtime,
    verify_dir_chain,
)
from nitro_control.native_rgb import NativeRGB
from nitro_control.rgb_client import RGBClient


REPO = Path(__file__).resolve().parents[1]
KVER_CUR = "7.3.4-300.fc45.x86_64"
KVER_OLD = "7.2.15-300.fc45.x86_64"
KVER_BAD = "7.1.0-300.fc45.x86_64"
WMI_ALIAS = "wmi:676AA15E-6A47-4D9F-A2CC-1E6D18D14026"


def _make_dummy_ko(path: Path, name: str, vermagic: str, *, alias: str = "", params: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = f"FAKE_KO\nNAME={name}\nVERMAGIC={vermagic} SMP preempt mod_unload\nALIAS={alias}\nPARAMS={params}\n"
    path.write_text(body, encoding="utf-8")
    path.chmod(0o644)
    return path


def _write_sha256(ko: Path, *, mode: int = 0o600) -> str:
    digest = hashlib.sha256(ko.read_bytes()).hexdigest()
    sha = Path(f"{ko}.sha256")
    sha.write_text(digest + "\n", encoding="utf-8")
    sha.chmod(mode)
    return digest


def _write_manifest(ko: Path, component: str, module_name: str, kernel: str, sha: str) -> Path:
    man = ko.parent / manifest_name(ko.name)
    man.write_text(json.dumps({
        "managed_by": "nitro-control",
        "schema_version": 1,
        "component": component,
        "module_name": module_name,
        "kernel": kernel,
        "sha256": sha,
        "state": "committed",
    }), encoding="utf-8")
    man.chmod(0o600)
    return man


def _fake_modinfo_runner(root: Path, *, force_mod_name: str | None = None):
    """Pure-Python mock runner for modinfo/depmod/systemctl/modprobe/restorecon."""
    depmod_calls: list[str] = []

    def runner(argv: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        cmd = Path(argv[0]).name
        if cmd == "modinfo":
            path = Path(argv[-1])
            text = path.read_text(encoding="utf-8", errors="replace")
            kv = {}
            for line in text.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    kv[k] = v
            if "-F" in argv:
                field = argv[argv.index("-F") + 1]
                if field == "name":
                    return subprocess.CompletedProcess(argv, 0, stdout=(force_mod_name or kv.get("NAME", "")) + "\n", stderr="")
                if field == "vermagic":
                    return subprocess.CompletedProcess(argv, 0, stdout=kv.get("VERMAGIC", "") + "\n", stderr="")
            if "-p" in argv:
                params = kv.get("PARAMS", "")
                out = "".join(f"{item.strip()}\n" for item in params.split(";") if item.strip())
                return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if cmd == "depmod":
            kver = argv[-1]
            depmod_calls.append(kver)
            kdir = root / "usr/lib/modules" / kver
            if kdir.is_dir():
                deps = []
                aliases = []
                for ko in sorted(kdir.rglob("*.ko")):
                    rel = ko.relative_to(kdir).as_posix()
                    deps.append(f"{rel}:")
                    txt = ko.read_text(encoding="utf-8", errors="ignore")
                    kv = dict(line.split("=", 1) for line in txt.splitlines() if "=" in line)
                    if kv.get("ALIAS") and kv.get("NAME"):
                        aliases.append(f"alias {kv['ALIAS']} {kv['NAME']}")
                (kdir / "modules.dep").write_text("\n".join(deps) + "\n", encoding="utf-8")
                (kdir / "modules.alias").write_text("\n".join(aliases) + "\n", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if cmd == "modprobe":
            if "-R" in argv:
                return subprocess.CompletedProcess(argv, 0, stdout="acer_wmi\n", stderr="")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if cmd == "systemctl":
            return subprocess.CompletedProcess(argv, 0 if argv[1] != "is-enabled" else 1, stdout="disabled\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    return runner, depmod_calls


def _prepare_native_sysfs(root: Path) -> None:
    for rel in (
        "usr/local/lib",
        f"usr/lib/modules/{KVER_CUR}",
        f"usr/lib/modules/{KVER_OLD}",
        "run",
        "src",
        "sys/class/dmi/id",
        "sys/module/acer_wmi",
        f"sys/bus/wmi/devices/{RGB_WMI_GUID}-8",
        "sys/class/hwmon/hwmon1",
        "sys/firmware/acpi",
        "proc",
    ):
        (root / rel).mkdir(parents=True, exist_ok=True)
    (root / "sys/class/dmi/id/sys_vendor").write_text("Acer\n", encoding="utf-8")
    (root / "sys/class/dmi/id/product_name").write_text("Nitro AN515-58\n", encoding="utf-8")
    (root / "sys/class/hwmon/hwmon1/name").write_text("acer\n", encoding="utf-8")
    (root / "sys/class/hwmon/hwmon1/fan1_input").write_text("2200\n", encoding="utf-8")
    (root / "sys/class/hwmon/hwmon1/fan2_input").write_text("2100\n", encoding="utf-8")
    (root / "sys/firmware/acpi/platform_profile").write_text("balanced\n", encoding="utf-8")
    (root / "proc/cmdline").write_text("quiet rhgb\n", encoding="utf-8")
    (root / "proc/modules").write_text("acer_wmi 45056 0 - Live 0x0\n", encoding="utf-8")


class StagingUnitLifecycleTests(unittest.TestCase):
    def test_simultaneous_first_use_lock_initialization_uses_single_inode(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "run").mkdir()
            lock_path = root / "run/nitro-control/native-rgb.lock"
            barrier = threading.Barrier(2)
            entered = threading.Event()
            release = threading.Event()

            def worker() -> str:
                barrier.wait(timeout=5)
                try:
                    with acquire_private_locks(root, ("native-rgb.lock",), "busy"):
                        entered.set()
                        release.wait(timeout=5)
                        return "acquired"
                except ModuleStagingError as exc:
                    return str(exc)

            with ThreadPoolExecutor(max_workers=2) as pool:
                f1 = pool.submit(worker)
                f2 = pool.submit(worker)
                self.assertTrue(entered.wait(timeout=5))
                ino_while_held = lock_path.stat().st_ino
                # Wait briefly before releasing so the contender hits flock(LOCK_NB) reliably.
                r_first_or_none = [f for f in (f1, f2) if f.done()]
                if not r_first_or_none:
                    threading.Event().wait(0.05)
                release.set()
                outcomes = sorted([f1.result(), f2.result()])

            self.assertEqual(outcomes, ["acquired", "busy"])
            st = lock_path.stat()
            self.assertEqual(st.st_ino, ino_while_held)
            self.assertEqual(st.st_nlink, 1)
            self.assertEqual(st.st_mode & 0o777, 0o600)

    def test_unmanifested_legacy_artifacts_require_adopt_legacy_and_verify_vermagic(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _prepare_native_sysfs(root)
            runner, depmod_calls = _fake_modinfo_runner(root)
            src = _make_dummy_ko(root / "src/nitro_rgb_enable_poc.ko", "nitro_rgb_enable_poc", KVER_CUR)

            # Place a valid-looking unmanifested legacy artifact on KVER_OLD.
            leg = _make_dummy_ko(
                root / f"usr/lib/modules/{KVER_OLD}/extra/nitro-control/nitro_rgb_enable_poc.ko",
                "nitro_rgb_enable_poc", KVER_OLD,
            )
            _write_sha256(leg)

            # Without --adopt-legacy: fails closed and preserves unmanifested legacy files!
            with self.assertRaisesRegex(ModuleStagingError, "adopt-legacy"):
                stage_install(root, "native-rgb", KVER_CUR, src, adopt_legacy=False, runner=runner)
            self.assertTrue(leg.is_file())

            # Uninstall without --adopt-legacy also preserves unmanifested artifact.
            with self.assertRaisesRegex(ModuleStagingError, "adopt-legacy"):
                uninstall_modules(root, "native-rgb", KVER_CUR, adopt_legacy=False, runner=runner)
            self.assertTrue(leg.is_file())

            # Even WITH --adopt-legacy, wrong vermagic in KVER_OLD dir is strictly rejected.
            bad_leg = _make_dummy_ko(
                root / f"usr/lib/modules/{KVER_BAD}/extra/nitro-control/nitro_rgb_enable_poc.ko",
                "nitro_rgb_enable_poc", KVER_CUR,
            )
            _write_sha256(bad_leg)
            with self.assertRaisesRegex(ModuleStagingError, "metadata mismatch"):
                uninstall_modules(root, "native-rgb", KVER_CUR, adopt_legacy=True, runner=runner)
            self.assertTrue(bad_leg.is_file())
            bad_leg.unlink()
            Path(f"{bad_leg}.sha256").unlink()

            # Re-seed valid unmanifested legacy KVER_OLD (the previous call removed it cleanly),
            # then test --adopt-legacy migration into private staging and depmod refresh.
            _make_dummy_ko(leg, "nitro_rgb_enable_poc", KVER_OLD)
            _write_sha256(leg)
            stage_install(root, "native-rgb", KVER_CUR, src, adopt_legacy=True, runner=runner)
            self.assertFalse(leg.exists())
            migrated = root / f"usr/local/lib/nitro-control/modules/{KVER_OLD}/nitro_rgb_enable_poc.ko"
            self.assertTrue(migrated.is_file())
            self.assertTrue((migrated.parent / manifest_name(migrated.name)).is_file())
            self.assertIn(KVER_OLD, depmod_calls)

    def test_conflicting_valid_private_and_legacy_digests_preserved_intact(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _prepare_native_sysfs(root)
            base_runner, _ = _fake_modinfo_runner(root)

            def runner(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
                if Path(argv[0]).name == "systemctl" and argv[1] == "is-active":
                    return subprocess.CompletedProcess(argv, 3, stdout="inactive\n", stderr="")
                return base_runner(argv, **kwargs)
            src_cur = _make_dummy_ko(root / "src/nitro_rgb_enable_poc.ko", "nitro_rgb_enable_poc", KVER_CUR)

            # Seed valid Nitro-managed private module on KVER_OLD (params="v1")
            priv_ko = _make_dummy_ko(
                root / f"usr/local/lib/nitro-control/modules/{KVER_OLD}/nitro_rgb_enable_poc.ko",
                "nitro_rgb_enable_poc", KVER_OLD, params="v1",
            )
            priv_sha = _write_sha256(priv_ko)
            _write_manifest(priv_ko, "native-rgb", "nitro_rgb_enable_poc", KVER_OLD, priv_sha)

            # Place a *different* valid Nitro-managed legacy module on KVER_OLD (params="v2")
            leg_ko = _make_dummy_ko(
                root / f"usr/lib/modules/{KVER_OLD}/extra/nitro-control/nitro_rgb_enable_poc.ko",
                "nitro_rgb_enable_poc", KVER_OLD, params="v2",
            )
            leg_sha = _write_sha256(leg_ko)
            _write_manifest(leg_ko, "native-rgb", "nitro_rgb_enable_poc", KVER_OLD, leg_sha)
            self.assertNotEqual(priv_sha, leg_sha)

            # Migration must fail closed and preserve BOTH valid conflicting artifacts!
            with self.assertRaisesRegex(ModuleStagingError, "Conflicting valid modules"):
                stage_install(root, "native-rgb", KVER_CUR, src_cur, adopt_legacy=True, runner=runner)
            self.assertEqual(hashlib.sha256(priv_ko.read_bytes()).hexdigest(), priv_sha)
            self.assertEqual(hashlib.sha256(leg_ko.read_bytes()).hexdigest(), leg_sha)

            # Replace legacy with IDENTICAL bytes -> conflict clears cleanly and legacy is removed.
            leg_ko.write_bytes(priv_ko.read_bytes())
            _write_sha256(leg_ko)
            _write_manifest(leg_ko, "native-rgb", "nitro_rgb_enable_poc", KVER_OLD, priv_sha)
            stage_install(root, "native-rgb", KVER_CUR, src_cur, runner=runner)
            self.assertFalse(leg_ko.exists())
            self.assertEqual(hashlib.sha256(priv_ko.read_bytes()).hexdigest(), priv_sha)

    def test_transactional_interruption_at_every_boundary_fails_closed_and_recovers(self):
        boundaries = (
            "after_tx_prepare",
            "after_manifest_unlink",
            "after_ko_replace",
            "after_sha_replace",
            "after_manifest_commit",
        )
        for boundary in boundaries:
            with self.subTest(boundary=boundary):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    _prepare_native_sysfs(root)
                    runner, _ = _fake_modinfo_runner(root)
                    src1 = _make_dummy_ko(root / "src/ko1.ko", "nitro_rgb_enable_poc", KVER_CUR, params="v1")
                    src2 = _make_dummy_ko(root / "src/ko2.ko", "nitro_rgb_enable_poc", KVER_CUR, params="v2")
                    expected_sha2 = hashlib.sha256(src2.read_bytes()).hexdigest()

                    stage_install(root, "native-rgb", KVER_CUR, src1, runner=runner)

                    def interrupt_on(stage_name: str) -> None:
                        if stage_name == boundary:
                            raise RuntimeError(f"simulated crash at {boundary}")

                    with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                        stage_install(root, "native-rgb", KVER_CUR, src2, runner=runner, on_commit_stage=interrupt_on)

                    # At EVERY interruption boundary, unprivileged status & privileged runtime fail closed!
                    self.assertFalse(NativeRGB(root, KVER_CUR).status()[0])
                    with self.assertRaises(ModuleStagingError):
                        verify_privileged_runtime(root, "native-rgb", KVER_CUR, runner=runner)

                    # Re-running installer detects the .tx journal and recovers cleanly!
                    res = stage_install(root, "native-rgb", KVER_CUR, src2, runner=runner)
                    self.assertEqual(res["sha256"], expected_sha2)
                    priv_dir = root / f"usr/local/lib/nitro-control/modules/{KVER_CUR}"
                    self.assertFalse((priv_dir / tx_json_name("nitro_rgb_enable_poc.ko")).exists())
                    self.assertFalse((priv_dir / tx_dir_name("nitro_rgb_enable_poc.ko")).exists())
                    self.assertTrue(NativeRGB(root, KVER_CUR, runner=runner).status()[0])
                    verify_privileged_runtime(root, "native-rgb", KVER_CUR, runner=runner)

    def test_ambiguous_partial_and_corrupted_tx_recovery_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _prepare_native_sysfs(root)
            base_runner, _ = _fake_modinfo_runner(root)

            def runner(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
                if Path(argv[0]).name == "systemctl" and argv[1] == "is-active":
                    return subprocess.CompletedProcess(argv, 3, stdout="inactive\n", stderr="")
                return base_runner(argv, **kwargs)
            src = _make_dummy_ko(root / "src/nitro_rgb_enable_poc.ko", "nitro_rgb_enable_poc", KVER_CUR)

            # Ambiguous stray tmp without .tx marker is never deleted automatically.
            priv_dir = root / f"usr/local/lib/nitro-control/modules/{KVER_CUR}"
            priv_dir.mkdir(parents=True, exist_ok=True)
            stray = priv_dir / ".nitro_rgb_enable_poc.ko.tmp.1234"
            stray.write_bytes(b"ambiguous")
            with self.assertRaisesRegex(ModuleStagingError, "Ambiguous temporary files"):
                stage_install(root, "native-rgb", KVER_CUR, src, runner=runner)
            self.assertTrue(stray.is_file())
            stray.unlink()

            # Interrupted .tx where live .ko was tampered to an unknown hash is rejected and preserved.
            def interrupt_mid(stage_name: str) -> None:
                if stage_name == "after_ko_replace":
                    raise RuntimeError("mid-commit")

            with self.assertRaises(RuntimeError):
                stage_install(root, "native-rgb", KVER_CUR, src, runner=runner, on_commit_stage=interrupt_mid)
            dest_ko = priv_dir / "nitro_rgb_enable_poc.ko"
            dest_ko.write_bytes(b"foreign tamper")
            with self.assertRaisesRegex(ModuleStagingError, "does not match interrupted Nitro transaction"):
                stage_install(root, "native-rgb", KVER_CUR, src, runner=runner)
            self.assertEqual(dest_ko.read_bytes(), b"foreign tamper")

    def test_hardlink_ancestor_and_source_swap_validation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _prepare_native_sysfs(root)
            runner_good, _ = _fake_modinfo_runner(root)
            runner_bad_staged, _ = _fake_modinfo_runner(root, force_mod_name="unrelated_module")
            src = _make_dummy_ko(root / "src/nitro_rgb_enable_poc.ko", "nitro_rgb_enable_poc", KVER_CUR)

            # H-P2-09: Staged byte validation catches mismatched payload even when source check passed.
            with self.assertRaisesRegex(ModuleStagingError, "Staged bytes failed verification"):
                stage_install(root, "native-rgb", KVER_CUR, src, runner=runner_bad_staged)
            self.assertFalse((root / f"usr/local/lib/nitro-control/modules/{KVER_CUR}/nitro_rgb_enable_poc.ko").exists())

            # Clean install succeeds, then hardlink or symlinked ancestor breaks readiness & runtime verification.
            stage_install(root, "native-rgb", KVER_CUR, src, runner=runner_good)
            priv_ko = root / f"usr/local/lib/nitro-control/modules/{KVER_CUR}/nitro_rgb_enable_poc.ko"
            hl = root / "hardlink.ko"
            os.link(priv_ko, hl)
            with self.assertRaisesRegex(ModuleStagingError, "hardlink"):
                verify_privileged_runtime(root, "native-rgb", KVER_CUR, runner=runner_good)
            hl.unlink()

            # Non-regular FIFO file at destination refused.
            priv_ko.unlink()
            os.mkfifo(priv_ko)
            with self.assertRaisesRegex(ModuleStagingError, "not a regular file"):
                uninstall_modules(root, "native-rgb", KVER_CUR, runner=runner_good)

    def test_h_p2_r01_residual_initramfs_persists_across_retries_until_genuinely_cleared(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _prepare_native_sysfs(root)
            (root / "usr/local/lib/nitro-control").mkdir(parents=True, exist_ok=True)
            (root / "boot").mkdir(parents=True, exist_ok=True)

            cur_src = _make_dummy_ko(root / "src/cur.ko", "nitro_rgb_enable_poc", KVER_CUR)
            legacy_old = _make_dummy_ko(
                root / f"usr/lib/modules/{KVER_OLD}/extra/nitro-control/nitro_rgb_enable_poc.ko",
                "nitro_rgb_enable_poc",
                KVER_OLD,
            )
            legacy_sha = _write_sha256(legacy_old)
            _write_manifest(legacy_old, "native-rgb", "nitro_rgb_enable_poc", KVER_OLD, legacy_sha)
            (root / f"boot/initramfs-{KVER_OLD}.img").write_bytes(b"fake-initramfs")
            (root / f"boot/initramfs-{KVER_CUR}.img").write_bytes(b"clean-initramfs")

            base_runner, _ = _fake_modinfo_runner(root)
            initramfs_state = {
                KVER_OLD: ("ok", "extra/nitro-control/nitro_rgb_enable_poc.ko\n"),
                KVER_CUR: ("ok", ""),
            }

            def runner(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
                if Path(argv[0]).name == "lsinitrd":
                    img = argv[-1]
                    for kver, (mode, output) in initramfs_state.items():
                        if kver in img:
                            if mode == "fail":
                                return subprocess.CompletedProcess(argv, 1, stdout="", stderr="lsinitrd failed\n")
                            return subprocess.CompletedProcess(argv, 0, stdout=output, stderr="")
                return base_runner(argv, **kwargs)

            with patch("nitro_control.module_staging.shutil.which", side_effect=lambda name: f"/mock/{name}"):
                # Step 1-4: Initial stage_install migrates KVER_OLD legacy files, raises RESIDUAL_INITRAMFS_EXPOSURE.
                with self.assertRaisesRegex(ModuleStagingError, "RESIDUAL_INITRAMFS_EXPOSURE"):
                    stage_install(root, "native-rgb", KVER_CUR, cur_src, runner=runner)

                # Step 5: Legacy indexed files were removed, while dirty initramfs copy remains.
                self.assertFalse(legacy_old.exists())
                self.assertFalse(Path(f"{legacy_old}.sha256").exists())

                # Step 6-7: KVER_CUR itself is runtime-ready, whereas KVER_OLD and whole install remain pending clearance.
                self.assertEqual(check_unprivileged_readiness(root, "native-rgb", KVER_CUR, runner=runner), (True, None))
                priv_ko = verify_privileged_runtime(root, "native-rgb", KVER_CUR, runner=runner)
                self.assertTrue(priv_ko.is_file())

                ok_old, msg_old = check_unprivileged_readiness(root, "native-rgb", KVER_OLD)
                self.assertFalse(ok_old)
                self.assertIn("clearance", msg_old or "")

                qualified, qdetails = check_installation_qualification(root, "native-rgb")
                self.assertFalse(qualified)
                self.assertEqual(qdetails["state"], "pending_clearance")
                self.assertEqual(qdetails["pending_kernels"], [KVER_OLD])

                sentinel_old = root / "usr/local/lib/nitro-control" / clearance_sentinel_name(
                    "nitro_rgb_enable_poc.ko"
                )
                self.assertFalse(sentinel_old.exists())
                # Persistent migration state file and per-kernel sentinel exist on KVER_OLD.
                mod_root = root / "usr/local/lib/nitro-control/modules"
                self.assertFalse((mod_root / KVER_CUR / clearance_sentinel_name("nitro_rgb_enable_poc.ko")).exists())
                self.assertTrue((mod_root / KVER_OLD / clearance_sentinel_name("nitro_rgb_enable_poc.ko")).exists())
                mfile = root / "usr/local/lib/nitro-control" / migration_state_name("native-rgb")
                self.assertTrue(mfile.is_file())

                # Step 8 (FIXED): Repeating stage_install MUST continue failing while residual initramfs remains.
                with self.assertRaisesRegex(ModuleStagingError, "RESIDUAL_INITRAMFS_EXPOSURE"):
                    stage_install(root, "native-rgb", KVER_CUR, cur_src, runner=runner)

                # Tool unavailability / lsinitrd failure MUST NOT clear pending exposure.
                initramfs_state[KVER_OLD] = ("fail", "")
                with self.assertRaisesRegex(ModuleStagingError, "failed"):
                    stage_install(root, "native-rgb", KVER_CUR, cur_src, runner=runner)

                # Genuine recovery after initramfs rebuild: stage_install clears migration state and qualifies install.
                initramfs_state[KVER_OLD] = ("ok", "")
                res = stage_install(root, "native-rgb", KVER_CUR, cur_src, runner=runner)
                self.assertIn(KVER_OLD, res["affected_kernels"])
                self.assertEqual(res["warnings"], [])
                qualified_after, qdetails_after = check_installation_qualification(root, "native-rgb")
                self.assertTrue(qualified_after)
                self.assertEqual(qdetails_after["state"], "complete")
                self.assertFalse(mfile.exists())
                self.assertFalse((mod_root / KVER_OLD / clearance_sentinel_name("nitro_rgb_enable_poc.ko")).exists())

                # Same clearance tracking applies to uninstall_modules when initramfs remains dirty on KVER_OLD.
                initramfs_state[KVER_OLD] = ("ok", "extra/nitro-control/nitro_rgb_enable_poc.ko\n")
                with self.assertRaisesRegex(ModuleStagingError, "RESIDUAL_INITRAMFS_EXPOSURE"):
                    uninstall_modules(root, "native-rgb", KVER_CUR, runner=runner)
                qualified_uninst, _ = check_installation_qualification(root, "native-rgb")
                self.assertFalse(qualified_uninst)

                initramfs_state[KVER_OLD] = ("ok", "")
                uninst_res = uninstall_modules(root, "native-rgb", KVER_CUR, runner=runner)
                self.assertIn(KVER_OLD, uninst_res["affected_kernels"])
                qualified_done, _ = check_installation_qualification(root, "native-rgb")
                self.assertTrue(qualified_done)
                self.assertFalse(mfile.exists())

    def test_h_p2_r02_service_disable_positive_verification(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _prepare_native_sysfs(root)
            (root / "etc/systemd/system").mkdir(parents=True, exist_ok=True)
            (root / "usr/local/libexec").mkdir(parents=True, exist_ok=True)
            cur_src = _make_dummy_ko(root / "src/linuwu.ko", "linuwu_sense", KVER_CUR, alias=WMI_ALIAS, params="nitro_v4=1")
            ctrl_src = root / "src/controller.sh"
            ctrl_src.write_text(f"#!/usr/bin/bash\n{CONTROLLER_MARKER}\n", encoding="utf-8")
            unit_src = root / "src/driver.service"
            unit_src.write_text(f"[Unit]\n{SERVICE_MARKER}\n", encoding="utf-8")

            base_runner, _ = _fake_modinfo_runner(root)
            behavior = {"disable_rc": 0, "is_enabled_rc": 1, "is_enabled_out": "disabled\n"}

            def runner(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
                if Path(argv[0]).name == "systemctl":
                    subcmd = argv[1]
                    if subcmd == "disable":
                        return subprocess.CompletedProcess(
                            argv, behavior["disable_rc"], stdout="", stderr="disable error\n"
                        )
                    if subcmd == "is-active":
                        return subprocess.CompletedProcess(argv, 3, stdout="inactive\n", stderr="")
                    if subcmd == "is-enabled":
                        return subprocess.CompletedProcess(
                            argv, behavior["is_enabled_rc"], stdout=behavior["is_enabled_out"], stderr=""
                        )
                    return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
                return base_runner(argv, **kwargs)

            # Missing systemctl binary fails install when unit exists.
            with patch(
                "nitro_control.module_staging.shutil.which",
                side_effect=lambda name: None if name == "systemctl" else f"/mock/{name}",
            ):
                with self.assertRaisesRegex(ModuleStagingError, "systemctl"):
                    stage_install(
                        root, "rgb-driver", KVER_CUR, cur_src,
                        controller_src=ctrl_src, unit_src=unit_src, runner=runner,
                    )

            with patch("nitro_control.module_staging.shutil.which", side_effect=lambda name: f"/mock/{name}"):
                # Non-zero systemctl disable fails install.
                behavior.update({"disable_rc": 1, "is_enabled_rc": 1, "is_enabled_out": "disabled\n"})
                with self.assertRaisesRegex(ModuleStagingError, "systemctl disable failed"):
                    stage_install(
                        root, "rgb-driver", KVER_CUR, cur_src,
                        controller_src=ctrl_src, unit_src=unit_src, runner=runner,
                    )

                # systemctl is-enabled returning enabled (rc=0) fails even if disable returned 0.
                behavior.update({"disable_rc": 0, "is_enabled_rc": 0, "is_enabled_out": "enabled\n"})
                with self.assertRaisesRegex(ModuleStagingError, "remained enabled|expected disabled state"):
                    stage_install(
                        root, "rgb-driver", KVER_CUR, cur_src,
                        controller_src=ctrl_src, unit_src=unit_src, runner=runner,
                    )

                # Ambiguous / unknown state fails.
                behavior.update({"disable_rc": 0, "is_enabled_rc": 1, "is_enabled_out": "indirect\n"})
                with self.assertRaisesRegex(ModuleStagingError, "Unable to positively verify disabled service state"):
                    stage_install(
                        root, "rgb-driver", KVER_CUR, cur_src,
                        controller_src=ctrl_src, unit_src=unit_src, runner=runner,
                    )

                # Known disabled state ("masked", rc=1) succeeds.
                behavior.update({"disable_rc": 0, "is_enabled_rc": 1, "is_enabled_out": "masked\n"})
                stage_install(
                    root, "rgb-driver", KVER_CUR, cur_src,
                    controller_src=ctrl_src, unit_src=unit_src, runner=runner,
                )

                # Uninstall MUST ALSO fail closed when systemctl disable fails instead of ignoring it.
                behavior.update({"disable_rc": 1, "is_enabled_rc": 1, "is_enabled_out": "disabled\n"})
                with self.assertRaisesRegex(ModuleStagingError, "systemctl disable failed"):
                    uninstall_modules(root, "rgb-driver", KVER_CUR, runner=runner)

                behavior.update({"disable_rc": 0, "is_enabled_rc": 1, "is_enabled_out": "disabled\n"})
                uninstall_modules(root, "rgb-driver", KVER_CUR, runner=runner)

    def test_h_p2_r04_controller_library_provenance_enforced(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _prepare_native_sysfs(root)
            (root / "etc/systemd/system").mkdir(parents=True, exist_ok=True)
            (root / "usr/local/libexec").mkdir(parents=True, exist_ok=True)
            cur_src = _make_dummy_ko(root / "src/linuwu.ko", "linuwu_sense", KVER_CUR, alias=WMI_ALIAS, params="nitro_v4=1")
            ctrl_src = root / "src/controller.sh"
            ctrl_src.write_text(f"#!/usr/bin/bash\n{CONTROLLER_MARKER}\n", encoding="utf-8")
            unit_src = root / "src/driver.service"
            unit_src.write_text(f"[Unit]\n{SERVICE_MARKER}\n", encoding="utf-8")
            base_runner, _ = _fake_modinfo_runner(root)

            def runner(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
                if Path(argv[0]).name == "systemctl" and argv[1] == "is-active":
                    return subprocess.CompletedProcess(argv, 3, stdout="inactive\n", stderr="")
                return base_runner(argv, **kwargs)

            with patch("nitro_control.module_staging.shutil.which", side_effect=lambda name: f"/mock/{name}"):
                stage_install(
                    root, "rgb-driver", KVER_CUR, cur_src,
                    controller_src=ctrl_src, unit_src=unit_src, runner=runner,
                )

                lib_dir = root / "usr/local/libexec/nitro-control-rgb-driver-lib"
                pkg_dir = lib_dir / "nitro_control"
                manifest_path = pkg_dir / DRIVER_LIB_MANIFEST
                self.assertTrue(manifest_path.is_file())
                manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                self.assertEqual(manifest_data["managed_by"], "nitro-control")
                for fname in ("__init__.py", "module_staging.py"):
                    expected = hashlib.sha256((pkg_dir / fname).read_bytes()).hexdigest()
                    self.assertEqual(manifest_data["files"][fname], expected)

                # Preserve unrelated file in driver-lib across upgrade and uninstall.
                foreign = lib_dir / "unrelated_user_file.txt"
                foreign.write_text("keep-me\n", encoding="utf-8")
                stage_install(
                    root, "rgb-driver", KVER_CUR, cur_src,
                    controller_src=ctrl_src, unit_src=unit_src, runner=runner,
                )
                self.assertTrue(foreign.is_file())

                # Tampering with staged module_staging.py violates driver-lib provenance on upgrade & uninstall.
                mod_copy = pkg_dir / "module_staging.py"
                orig_bytes = mod_copy.read_bytes()
                mod_copy.write_bytes(orig_bytes + b"# tampered\n")
                with self.assertRaisesRegex(ModuleStagingError, "Controller library digest mismatch"):
                    stage_install(
                        root, "rgb-driver", KVER_CUR, cur_src,
                        controller_src=ctrl_src, unit_src=unit_src, runner=runner,
                    )
                with self.assertRaisesRegex(ModuleStagingError, "Controller library digest mismatch"):
                    uninstall_modules(root, "rgb-driver", KVER_CUR, runner=runner)

                # Missing manifest with unmanifested files requires --adopt-legacy.
                mod_copy.write_bytes(orig_bytes)
                manifest_path.unlink()
                with self.assertRaisesRegex(ModuleStagingError, "lacks"):
                    uninstall_modules(root, "rgb-driver", KVER_CUR, runner=runner)

                # With adopt_legacy=True, uninstall removes Nitro Control files and preserves unrelated file.
                uninstall_modules(root, "rgb-driver", KVER_CUR, adopt_legacy=True, runner=runner)
                self.assertFalse((pkg_dir / "module_staging.py").exists())
                self.assertTrue(foreign.is_file())

    def test_h_p2_r05_tx_recovery_validates_tx_dir_checksum_and_manifest_objects(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _prepare_native_sysfs(root)
            base_runner, _ = _fake_modinfo_runner(root)

            def runner(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
                if Path(argv[0]).name == "systemctl" and argv[1] == "is-active":
                    return subprocess.CompletedProcess(argv, 3, stdout="inactive\n", stderr="")
                return base_runner(argv, **kwargs)
            src_good = _make_dummy_ko(root / "src/good.ko", "nitro_rgb_enable_poc", KVER_CUR)
            src_bad = _make_dummy_ko(root / "src/other.ko", "nitro_rgb_enable_poc", KVER_CUR, params="x=1")

            # Case 1: unexpected file inside .tx.<ko>.dir/ must NOT be silently deleted; fail closed.
            def inject_tx_foreign(stage: str) -> None:
                if stage == "after_tx_prepare":
                    mod_dir = root / f"usr/local/lib/nitro-control/modules/{KVER_CUR}"
                    tx_dir = mod_dir / tx_dir_name("nitro_rgb_enable_poc.ko")
                    (tx_dir / "unexpected_foreign.bin").write_bytes(b"do-not-delete")
                    raise ModuleStagingError("injected crash")

            with self.assertRaises(ModuleStagingError):
                stage_install(root, "native-rgb", KVER_CUR, src_good, runner=runner, on_commit_stage=inject_tx_foreign)
            foreign_tx = (
                root / f"usr/local/lib/nitro-control/modules/{KVER_CUR}" / tx_dir_name("nitro_rgb_enable_poc.ko") / "unexpected_foreign.bin"
            )
            self.assertTrue(foreign_tx.exists())
            with self.assertRaisesRegex(ModuleStagingError, "Unexpected entry inside transaction directory"):
                stage_install(root, "native-rgb", KVER_CUR, src_good, runner=runner)
            self.assertTrue(foreign_tx.exists())

            # Reset tx artifacts for Case 2: mismatched live .sha256 during real interrupted transaction.
            foreign_tx.unlink()
            stage_install(root, "native-rgb", KVER_CUR, src_good, runner=runner)

            def fail_after_prepare(stage: str) -> None:
                if stage == "after_tx_prepare":
                    raise ModuleStagingError("injected crash")

            with self.assertRaises(ModuleStagingError):
                stage_install(root, "native-rgb", KVER_CUR, src_bad, runner=runner, on_commit_stage=fail_after_prepare)

            mod_dir = root / f"usr/local/lib/nitro-control/modules/{KVER_CUR}"
            ko = mod_dir / "nitro_rgb_enable_poc.ko"
            tx_json = mod_dir / tx_json_name("nitro_rgb_enable_poc.ko")
            (mod_dir / f"{ko.name}.sha256").write_text(("0" * 64) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ModuleStagingError, "mismatch|does not match"):
                stage_install(root, "native-rgb", KVER_CUR, src_bad, runner=runner)
            self.assertTrue(tx_json.exists())

    def test_h_p2_r06_clean_first_time_multi_kernel_migration_finalizes_cleanly(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _prepare_native_sysfs(root)
            (root / "boot").mkdir(parents=True, exist_ok=True)
            cur_src = _make_dummy_ko(root / "src/cur.ko", "nitro_rgb_enable_poc", KVER_CUR)
            legacy_old = _make_dummy_ko(
                root / f"usr/lib/modules/{KVER_OLD}/extra/nitro-control/nitro_rgb_enable_poc.ko",
                "nitro_rgb_enable_poc", KVER_OLD,
            )
            legacy_sha = _write_sha256(legacy_old)
            _write_manifest(legacy_old, "native-rgb", "nitro_rgb_enable_poc", KVER_OLD, legacy_sha)
            (root / f"boot/initramfs-{KVER_CUR}.img").write_bytes(b"clean")
            (root / f"boot/initramfs-{KVER_OLD}.img").write_bytes(b"clean")
            runner, depmod_calls = _fake_modinfo_runner(root)

            with patch("nitro_control.module_staging.shutil.which", side_effect=lambda name: f"/mock/{name}"):
                # First-time migration with zero prior pending state and clean initramfs.
                res = stage_install(root, "native-rgb", KVER_CUR, cur_src, runner=runner)
                self.assertEqual(res["qualification_state"], "complete")
                self.assertEqual(res["warnings"], [])
                self.assertIn(KVER_OLD, res["affected_kernels"])
                self.assertIn(KVER_OLD, depmod_calls)
                self.assertFalse(legacy_old.exists())

                mfile = root / "usr/local/lib/nitro-control" / migration_state_name("native-rgb")
                self.assertFalse(mfile.exists())
                for kver in (KVER_CUR, KVER_OLD):
                    sentinel = (
                        root / f"usr/local/lib/nitro-control/modules/{kver}"
                        / clearance_sentinel_name("nitro_rgb_enable_poc.ko")
                    )
                    self.assertFalse(sentinel.exists())
                    self.assertEqual(check_unprivileged_readiness(root, "native-rgb", kver, runner=runner), (True, None))
                    self.assertTrue(verify_privileged_runtime(root, "native-rgb", kver, runner=runner).is_file())
                    self.assertTrue(NativeRGB(root, kver, runner=runner).status()[0])

                qualified, qdetails = check_installation_qualification(root, "native-rgb")
                self.assertTrue(qualified)
                self.assertEqual(qdetails["state"], "complete")
                self.assertEqual(qdetails["pending_kernels"], [])
                self.assertEqual(qdetails["unverified_kernels"], [])
                self.assertEqual(qdetails["exposed_initramfs_kernels"], [])

                uninst = uninstall_modules(root, "native-rgb", KVER_CUR, runner=runner)
                self.assertEqual(uninst["qualification_state"], "complete")
                self.assertEqual(uninst["warnings"], [])
                self.assertFalse(mfile.exists())
                self.assertEqual(check_installation_qualification(root, "native-rgb")[0], True)

    def test_h_p2_r07_repeated_and_alternating_lsinitrd_failures_preserve_known_exposure_until_cleared(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _prepare_native_sysfs(root)
            (root / "boot").mkdir(parents=True, exist_ok=True)
            cur_src = _make_dummy_ko(root / "src/cur.ko", "nitro_rgb_enable_poc", KVER_CUR)
            legacy_old = _make_dummy_ko(
                root / f"usr/lib/modules/{KVER_OLD}/extra/nitro-control/nitro_rgb_enable_poc.ko",
                "nitro_rgb_enable_poc", KVER_OLD,
            )
            _write_manifest(legacy_old, "native-rgb", "nitro_rgb_enable_poc", KVER_OLD, _write_sha256(legacy_old))
            (root / f"boot/initramfs-{KVER_CUR}.img").write_bytes(b"clean")
            (root / f"boot/initramfs-{KVER_OLD}.img").write_bytes(b"dirty")

            base_runner, _ = _fake_modinfo_runner(root)
            mode = {"lsinitrd_available": True, "old": ("ok", "extra/nitro-control/nitro_rgb_enable_poc.ko\n")}

            def which_mock(name: str) -> str | None:
                if name == "lsinitrd" and not mode["lsinitrd_available"]:
                    return None
                return f"/mock/{name}"

            def runner(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
                if Path(argv[0]).name == "lsinitrd" and KVER_OLD in argv[-1]:
                    st, out = mode["old"]
                    if st == "fail":
                        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="lsinitrd io error\n")
                    return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")
                return base_runner(argv, **kwargs)

            with patch("nitro_control.module_staging.shutil.which", side_effect=which_mock):
                # Attempt 1: Residual exposure detected.
                with self.assertRaisesRegex(ModuleStagingError, "RESIDUAL_INITRAMFS_EXPOSURE"):
                    stage_install(root, "native-rgb", KVER_CUR, cur_src, runner=runner)

                # Attempts 2-5: repeated failures & alternating unavailable lsinitrd MUST NEVER clear exposure.
                retry_sequence = (
                    (True, ("fail", "")),
                    (True, ("fail", "")),
                    (False, ("ok", "")),
                    (True, ("fail", "")),
                    (False, ("fail", "")),
                )
                for ls_avail, old_state in retry_sequence:
                    mode["lsinitrd_available"] = ls_avail
                    mode["old"] = old_state
                    with self.assertRaisesRegex(ModuleStagingError, "Cannot clear prior residual initramfs exposure"):
                        stage_install(root, "native-rgb", KVER_CUR, cur_src, runner=runner)

                    qualified, qdetails = check_installation_qualification(root, "native-rgb")
                    self.assertFalse(qualified)
                    self.assertEqual(qdetails["state"], "pending_clearance")
                    self.assertEqual(qdetails["pending_kernels"], [KVER_OLD])
                    self.assertEqual(qdetails["exposed_initramfs_kernels"], [KVER_OLD])
                    self.assertEqual(qdetails["kernels"][KVER_OLD]["initramfs_status"], "exposed")
                    self.assertFalse(check_unprivileged_readiness(root, "native-rgb", KVER_OLD)[0])
                    with self.assertRaises(ModuleStagingError):
                        verify_privileged_runtime(root, "native-rgb", KVER_OLD, runner=runner)
                    self.assertEqual(check_unprivileged_readiness(root, "native-rgb", KVER_CUR, runner=runner), (True, None))
                    self.assertTrue(verify_privileged_runtime(root, "native-rgb", KVER_CUR, runner=runner).is_file())

                # Attempt 7: ONLY positive lsinitrd verification resolving cleanly clears exposure.
                mode["lsinitrd_available"] = True
                mode["old"] = ("ok", "")
                res_ok = stage_install(root, "native-rgb", KVER_CUR, cur_src, runner=runner)
                self.assertEqual(res_ok["qualification_state"], "complete")
                self.assertEqual(check_installation_qualification(root, "native-rgb")[0], True)
                self.assertTrue(verify_privileged_runtime(root, "native-rgb", KVER_OLD, runner=runner).is_file())

                # Corresponding uninstall sequence: exposure -> consecutive failures/unavailable -> positive clearance.
                mode["old"] = ("ok", "extra/nitro-control/nitro_rgb_enable_poc.ko\n")
                with self.assertRaisesRegex(ModuleStagingError, "RESIDUAL_INITRAMFS_EXPOSURE"):
                    uninstall_modules(root, "native-rgb", KVER_CUR, runner=runner)
                for ls_avail, old_state in ((True, ("fail", "")), (False, ("ok", "")), (True, ("fail", ""))):
                    mode["lsinitrd_available"] = ls_avail
                    mode["old"] = old_state
                    with self.assertRaisesRegex(ModuleStagingError, "Cannot clear prior residual initramfs exposure"):
                        uninstall_modules(root, "native-rgb", KVER_CUR, runner=runner)
                    self.assertEqual(check_installation_qualification(root, "native-rgb")[1]["exposed_initramfs_kernels"], [KVER_OLD])

                mode["lsinitrd_available"] = True
                mode["old"] = ("ok", "")
                uninst_ok = uninstall_modules(root, "native-rgb", KVER_CUR, runner=runner)
                self.assertEqual(uninst_ok["qualification_state"], "complete")
                self.assertTrue(check_installation_qualification(root, "native-rgb")[0])

    def test_h_p2_r08_migration_interruption_boundaries_fail_closed_and_recover(self):
        boundaries = (
            "after_migration_journal_save",
            "after_clearance_sentinel_publish",
            "after_legacy_unlink",
            "after_index_refresh",
            "after_finalize_unlink_journal",
        )
        for boundary in boundaries:
            with self.subTest(boundary=boundary):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    _prepare_native_sysfs(root)
                    cur_src = _make_dummy_ko(root / "src/cur.ko", "nitro_rgb_enable_poc", KVER_CUR)
                    legacy_old = _make_dummy_ko(
                        root / f"usr/lib/modules/{KVER_OLD}/extra/nitro-control/nitro_rgb_enable_poc.ko",
                        "nitro_rgb_enable_poc", KVER_OLD,
                    )
                    _write_manifest(legacy_old, "native-rgb", "nitro_rgb_enable_poc", KVER_OLD, _write_sha256(legacy_old))
                    runner, _ = _fake_modinfo_runner(root)

                    def crash_on(stage_name: str, b=boundary) -> None:
                        if stage_name == b:
                            raise RuntimeError(f"interrupted at {b}")

                    with patch("nitro_control.module_staging.shutil.which", side_effect=lambda name: f"/mock/{name}"):
                        with self.assertRaisesRegex(RuntimeError, "interrupted at"):
                            stage_install(root, "native-rgb", KVER_CUR, cur_src, runner=runner, on_migration_stage=crash_on)

                        mfile = root / "usr/local/lib/nitro-control" / migration_state_name("native-rgb")
                        sentinel = (
                            root / f"usr/local/lib/nitro-control/modules/{KVER_OLD}"
                            / clearance_sentinel_name("nitro_rgb_enable_poc.ko")
                        )
                        if mfile.exists():
                            self.assertEqual(stat.S_IMODE(mfile.stat().st_mode), 0o600)
                        if sentinel.exists():
                            self.assertEqual(stat.S_IMODE(sentinel.stat().st_mode), 0o600)

                        # Both readiness & qualification & privileged verification fail closed together!
                        self.assertFalse(check_unprivileged_readiness(root, "native-rgb", KVER_OLD)[0])
                        self.assertFalse(NativeRGB(root, KVER_OLD).status()[0])
                        with self.assertRaises(ModuleStagingError):
                            verify_privileged_runtime(root, "native-rgb", KVER_OLD, runner=runner)
                        qualified, qdetails = check_installation_qualification(root, "native-rgb")
                        self.assertFalse(qualified)
                        self.assertEqual(qdetails["state"], "pending_clearance")
                        self.assertIn(KVER_OLD, qdetails["pending_kernels"])

                        # Retrying recovers cleanly to complete.
                        res = stage_install(root, "native-rgb", KVER_CUR, cur_src, runner=runner)
                        self.assertEqual(res["qualification_state"], "complete")
                        self.assertFalse(mfile.exists())
                        self.assertFalse(sentinel.exists())
                        self.assertTrue(check_installation_qualification(root, "native-rgb")[0])
                        self.assertTrue(verify_privileged_runtime(root, "native-rgb", KVER_OLD, runner=runner).is_file())

    def test_h_p2_r10_initial_unverified_initramfs_clearance_tracks_state_until_verified(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _prepare_native_sysfs(root)
            (root / "boot").mkdir(parents=True, exist_ok=True)
            cur_src = _make_dummy_ko(root / "src/cur.ko", "nitro_rgb_enable_poc", KVER_CUR)
            (root / f"boot/initramfs-{KVER_CUR}.img").write_bytes(b"img-cur")
            (root / f"boot/initramfs-{KVER_OLD}.img").write_bytes(b"img-old")
            legacy_old = _make_dummy_ko(
                root / f"usr/lib/modules/{KVER_OLD}/extra/nitro-control/nitro_rgb_enable_poc.ko",
                "nitro_rgb_enable_poc", KVER_OLD,
            )
            _write_manifest(legacy_old, "native-rgb", "nitro_rgb_enable_poc", KVER_OLD, _write_sha256(legacy_old))

            base_runner, _ = _fake_modinfo_runner(root)
            mode = {"lsinitrd_available": False, "cur": ("ok", ""), "old": ("ok", "")}

            def which_mock(name: str) -> str | None:
                if name == "lsinitrd" and not mode["lsinitrd_available"]:
                    return None
                return f"/mock/{name}"

            def runner(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
                if Path(argv[0]).name == "lsinitrd":
                    key = "cur" if KVER_CUR in argv[-1] else "old"
                    st, out = mode[key]
                    if st == "fail":
                        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="lsinitrd err\n")
                    return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")
                return base_runner(argv, **kwargs)

            with patch("nitro_control.module_staging.shutil.which", side_effect=which_mock):
                # Initial install with lsinitrd unavailable stages KVER_CUR cleanly with warnings,
                # preserves KVER_CUR runtime eligibility, yet MUST NOT mark install "complete".
                res1 = stage_install(root, "native-rgb", KVER_CUR, cur_src, runner=runner)
                self.assertEqual(res1["qualification_state"], "unverified_clearance")
                self.assertTrue(any("UNVERIFIED" in w for w in res1["warnings"]))
                self.assertEqual(check_unprivileged_readiness(root, "native-rgb", KVER_CUR, runner=runner), (True, None))
                self.assertTrue(verify_privileged_runtime(root, "native-rgb", KVER_CUR, runner=runner).is_file())

                qualified, qdetails = check_installation_qualification(root, "native-rgb")
                self.assertFalse(qualified)
                self.assertEqual(qdetails["state"], "unverified_clearance")
                self.assertEqual(set(qdetails["unverified_kernels"]), {KVER_CUR, KVER_OLD})
                self.assertEqual(qdetails["pending_kernels"], [])

                # Switching to failing lsinitrd on retry still never silently promotes to complete.
                mode["lsinitrd_available"] = True
                mode["cur"] = ("ok", "")
                mode["old"] = ("fail", "")
                res2 = stage_install(root, "native-rgb", KVER_CUR, cur_src, runner=runner)
                self.assertEqual(res2["qualification_state"], "unverified_clearance")
                qualified2, qdetails2 = check_installation_qualification(root, "native-rgb")
                self.assertFalse(qualified2)
                self.assertEqual(qdetails2["unverified_kernels"], [KVER_OLD])

                # Positive lsinitrd check later revealing residual exposure escalates cleanly to exposed.
                mode["old"] = ("ok", "extra/nitro-control/nitro_rgb_enable_poc.ko\n")
                with self.assertRaisesRegex(ModuleStagingError, "RESIDUAL_INITRAMFS_EXPOSURE"):
                    stage_install(root, "native-rgb", KVER_CUR, cur_src, runner=runner)
                self.assertEqual(check_installation_qualification(root, "native-rgb")[1]["state"], "pending_clearance")

                # Clean initramfs finally promotes installation qualification to complete.
                mode["old"] = ("ok", "")
                res3 = stage_install(root, "native-rgb", KVER_CUR, cur_src, runner=runner)
                self.assertEqual(res3["qualification_state"], "complete")
                self.assertTrue(check_installation_qualification(root, "native-rgb")[0])

                # Uninstall with lsinitrd initially failing keeps unverified_clearance until verified clean.
                mode["old"] = ("fail", "")
                uninst1 = uninstall_modules(root, "native-rgb", KVER_CUR, runner=runner)
                self.assertEqual(uninst1["qualification_state"], "unverified_clearance")
                qualified_u1, qdetails_u1 = check_installation_qualification(root, "native-rgb")
                self.assertFalse(qualified_u1)
                self.assertEqual(qdetails_u1["unverified_kernels"], [KVER_OLD])

                mode["old"] = ("ok", "")
                uninst2 = uninstall_modules(root, "native-rgb", KVER_CUR, runner=runner)
                self.assertEqual(uninst2["qualification_state"], "complete")
                self.assertTrue(check_installation_qualification(root, "native-rgb")[0])

    def test_h_p2_r11_unresolved_obligations_survive_unrelated_migration_and_interruption(self):
        KVER_3 = "7.2.88-300.fc45.x86_64"
        KVER_4 = "7.2.99-300.fc45.x86_64"
        crash_stages = (
            "after_migration_journal_save",
            "after_clearance_sentinel_publish",
            "after_legacy_unlink",
            "after_index_refresh",
        )
        for crash_stage in crash_stages:
            with self.subTest(crash_stage=crash_stage):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    _prepare_native_sysfs(root)
                    for kver in (KVER_3, KVER_4):
                        (root / f"usr/lib/modules/{kver}").mkdir(parents=True, exist_ok=True)
                    (root / "boot").mkdir(parents=True, exist_ok=True)
                    cur_src = _make_dummy_ko(root / "src/cur.ko", "nitro_rgb_enable_poc", KVER_CUR)

                    # Seed mixed obligations: KVER_OLD=unverified (lsinitrd fail), KVER_3=exposed.
                    old_leg = _make_dummy_ko(
                        root / f"usr/lib/modules/{KVER_OLD}/extra/nitro-control/nitro_rgb_enable_poc.ko",
                        "nitro_rgb_enable_poc", KVER_OLD,
                    )
                    _write_manifest(old_leg, "native-rgb", "nitro_rgb_enable_poc", KVER_OLD, _write_sha256(old_leg))
                    (root / f"boot/initramfs-{KVER_OLD}.img").write_bytes(b"img-old")

                    k3_leg = _make_dummy_ko(
                        root / f"usr/lib/modules/{KVER_3}/extra/nitro-control/nitro_rgb_enable_poc.ko",
                        "nitro_rgb_enable_poc", KVER_3,
                    )
                    _write_manifest(k3_leg, "native-rgb", "nitro_rgb_enable_poc", KVER_3, _write_sha256(k3_leg))
                    (root / f"boot/initramfs-{KVER_3}.img").write_bytes(b"img-k3")

                    base_runner, _ = _fake_modinfo_runner(root)
                    ls_calls: list[str] = []
                    mode = {
                        KVER_OLD: ("fail", ""),
                        KVER_3: ("ok", "extra/nitro-control/nitro_rgb_enable_poc.ko\n"),
                    }

                    def runner(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
                        if Path(argv[0]).name == "lsinitrd":
                            ls_calls.append(argv[-1])
                            for kver, (st, out) in mode.items():
                                if kver in argv[-1]:
                                    if st == "fail":
                                        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="err\n")
                                    return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")
                        return base_runner(argv, **kwargs)

                    with patch("nitro_control.module_staging.shutil.which", side_effect=lambda n: f"/mock/{n}"):
                        with self.assertRaisesRegex(ModuleStagingError, "RESIDUAL_INITRAMFS_EXPOSURE"):
                            stage_install(root, "native-rgb", KVER_CUR, cur_src, runner=runner)

                        _, q_pre = check_installation_qualification(root, "native-rgb")
                        self.assertEqual(q_pre["unverified_kernels"], [KVER_OLD])
                        self.assertEqual(q_pre["exposed_initramfs_kernels"], [KVER_3])

                        # Introduce unrelated KVER_4 legacy artifact and crash during its migration.
                        k4_leg = _make_dummy_ko(
                            root / f"usr/lib/modules/{KVER_4}/extra/nitro-control/nitro_rgb_enable_poc.ko",
                            "nitro_rgb_enable_poc", KVER_4,
                        )
                        _write_manifest(k4_leg, "native-rgb", "nitro_rgb_enable_poc", KVER_4, _write_sha256(k4_leg))

                        def crash(stage_name: str, target=crash_stage) -> None:
                            if stage_name == target:
                                raise RuntimeError(f"boom at {target}")

                        with self.assertRaisesRegex(RuntimeError, "boom at"):
                            stage_install(root, "native-rgb", KVER_CUR, cur_src, runner=runner, on_migration_stage=crash)

                        # Prior obligations on KVER_OLD (unverified) and KVER_3 (exposed) MUST survive!
                        qual_mid, q_mid = check_installation_qualification(root, "native-rgb")
                        self.assertFalse(qual_mid)
                        self.assertEqual(q_mid["state"], "pending_clearance")
                        self.assertIn(KVER_OLD, q_mid["unverified_kernels"])
                        self.assertIn(KVER_3, q_mid["exposed_initramfs_kernels"])
                        if crash_stage == "after_index_refresh":
                            self.assertEqual(q_mid["kernels"][KVER_4]["migration_status"], "complete")
                        else:
                            self.assertIn(KVER_4, q_mid["pending_kernels"])
                        self.assertEqual(check_unprivileged_readiness(root, "native-rgb", KVER_CUR, runner=runner), (True, None))
                        self.assertFalse(check_unprivileged_readiness(root, "native-rgb", KVER_3)[0])

                        # Clear KVER_3 ONLY, keep KVER_OLD failing -> retry MUST recheck KVER_OLD and stay unverified_clearance!
                        ls_calls.clear()
                        mode[KVER_3] = ("ok", "")
                        res_partial = stage_install(root, "native-rgb", KVER_CUR, cur_src, runner=runner)
                        self.assertEqual(res_partial["qualification_state"], "unverified_clearance")
                        self.assertTrue(any(KVER_OLD in p for p in ls_calls))
                        _, q_part = check_installation_qualification(root, "native-rgb")
                        self.assertEqual(q_part["unverified_kernels"], [KVER_OLD])
                        self.assertEqual(q_part["exposed_initramfs_kernels"], [])

                        # Uninstall interrupted on KVER_CUR also preserves KVER_OLD unverified obligation!
                        with self.assertRaisesRegex(RuntimeError, "boom at"):
                            uninstall_modules(
                                root, "native-rgb", KVER_CUR, runner=runner,
                                on_migration_stage=lambda s: (_ for _ in ()).throw(RuntimeError("boom at uninstall")) if s == "after_migration_journal_save" else None,
                            )
                        _, q_uninst_crash = check_installation_qualification(root, "native-rgb")
                        self.assertIn(KVER_OLD, q_uninst_crash["unverified_kernels"])

                        # Genuine KVER_OLD clearance finally brings uninstall + qualification to complete.
                        mode[KVER_OLD] = ("ok", "")
                        uninst_done = uninstall_modules(root, "native-rgb", KVER_CUR, runner=runner)
                        self.assertEqual(uninst_done["qualification_state"], "complete")
                        self.assertTrue(check_installation_qualification(root, "native-rgb")[0])

    def test_h_p2_r12_interrupted_initial_verification_blocks_qualification_and_runtime(self):
        scenarios = (
            ("after_tx_prepare", "residual"),
            ("after_index_refresh", "residual"),
            ("after_index_refresh", "clean"),
            ("after_finalize_unlink_journal", "clean"),
        )
        for crash_stage, img_mode in scenarios:
            with self.subTest(crash_stage=crash_stage, img_mode=img_mode):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    _prepare_native_sysfs(root)
                    (root / "boot").mkdir(parents=True, exist_ok=True)
                    cur_src = _make_dummy_ko(root / "src/cur.ko", "nitro_rgb_enable_poc", KVER_CUR)
                    # Running kernel only, NO legacy indexed module, initramfs exists.
                    (root / f"boot/initramfs-{KVER_CUR}.img").write_bytes(b"img-cur")

                    base_runner, _ = _fake_modinfo_runner(root)
                    img_state = {"mode": img_mode}

                    def runner(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
                        if Path(argv[0]).name == "lsinitrd":
                            out = "extra/nitro-control/nitro_rgb_enable_poc.ko\n" if img_state["mode"] == "residual" else ""
                            return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")
                        return base_runner(argv, **kwargs)

                    with patch("nitro_control.module_staging.shutil.which", side_effect=lambda n: f"/mock/{n}"):
                        if crash_stage == "after_tx_prepare":
                            hook_kw = {"on_commit_stage": lambda s: (_ for _ in ()).throw(RuntimeError("crash")) if s == crash_stage else None}
                        else:
                            hook_kw = {"on_migration_stage": lambda s: (_ for _ in ()).throw(RuntimeError("crash")) if s == crash_stage else None}

                        with self.assertRaisesRegex(RuntimeError, "crash"):
                            stage_install(root, "native-rgb", KVER_CUR, cur_src, runner=runner, **hook_kw)

                        # Interruption MUST NOT become complete qualification OR allow unprivileged/privileged runtime!
                        qual, qd = check_installation_qualification(root, "native-rgb")
                        self.assertFalse(qual)
                        self.assertEqual(qd["state"], "pending_clearance")
                        self.assertIn(KVER_CUR, qd["pending_kernels"])
                        self.assertFalse(check_unprivileged_readiness(root, "native-rgb", KVER_CUR)[0])
                        self.assertFalse(NativeRGB(root, KVER_CUR).status()[0])
                        with self.assertRaises(ModuleStagingError):
                            verify_privileged_runtime(root, "native-rgb", KVER_CUR, runner=runner)

                        # Retry behavior: residual fails closed; clean promotes to complete.
                        if img_mode == "residual":
                            with self.assertRaisesRegex(ModuleStagingError, "RESIDUAL_INITRAMFS_EXPOSURE"):
                                stage_install(root, "native-rgb", KVER_CUR, cur_src, runner=runner)
                            self.assertFalse(check_unprivileged_readiness(root, "native-rgb", KVER_CUR)[0])
                            img_state["mode"] = "clean"

                        res_ok = stage_install(root, "native-rgb", KVER_CUR, cur_src, runner=runner)
                        self.assertEqual(res_ok["qualification_state"], "complete")
                        self.assertTrue(check_installation_qualification(root, "native-rgb")[0])
                        self.assertEqual(check_unprivileged_readiness(root, "native-rgb", KVER_CUR, runner=runner), (True, None))
                        self.assertTrue(verify_privileged_runtime(root, "native-rgb", KVER_CUR, runner=runner).is_file())

                        # Equivalent uninstall path interrupted before finalization must also stay pending_clearance!
                        img_state["mode"] = "residual"
                        with self.assertRaisesRegex(RuntimeError, "crash"):
                            uninstall_modules(
                                root, "native-rgb", KVER_CUR, runner=runner,
                                on_migration_stage=lambda s: (_ for _ in ()).throw(RuntimeError("crash")) if s == "after_index_refresh" else None,
                            )
                        qual_u, qd_u = check_installation_qualification(root, "native-rgb")
                        self.assertFalse(qual_u)
                        self.assertEqual(qd_u["state"], "pending_clearance")
                        self.assertIn(KVER_CUR, qd_u["pending_kernels"])

                        with self.assertRaisesRegex(ModuleStagingError, "RESIDUAL_INITRAMFS_EXPOSURE"):
                            uninstall_modules(root, "native-rgb", KVER_CUR, runner=runner)
                        img_state["mode"] = "clean"
                        uninst_ok = uninstall_modules(root, "native-rgb", KVER_CUR, runner=runner)
                        self.assertEqual(uninst_ok["qualification_state"], "complete")
                        self.assertTrue(check_installation_qualification(root, "native-rgb")[0])

    def test_h_p2_r13_interrupted_exposure_and_mid_verify_block_retry_failures(self):
        cases = (
            ("install", "after_index_refresh", "exposed"),
            ("install", "mid_lsinitrd", "exposed"),
            ("uninstall", "after_index_refresh", "exposed"),
            ("uninstall", "mid_lsinitrd", "exposed"),
        )
        for op, crash_point, img_content in cases:
            with self.subTest(op=op, crash_point=crash_point):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    _prepare_native_sysfs(root)
                    (root / "boot").mkdir(parents=True, exist_ok=True)
                    cur_src = _make_dummy_ko(root / "src/cur.ko", "nitro_rgb_enable_poc", KVER_CUR)
                    base_runner, _ = _fake_modinfo_runner(root)

                    # Seed clean baseline for uninstall cases without dirty initramfs first.
                    if op == "uninstall":
                        with patch("nitro_control.module_staging.shutil.which", side_effect=lambda n: f"/mock/{n}"):
                            stage_install(root, "native-rgb", KVER_CUR, cur_src, runner=base_runner)

                    (root / f"boot/initramfs-{KVER_CUR}.img").write_bytes(b"img-cur")
                    mode = {"ls_avail": True, "action": "ok", "out": "extra/nitro-control/nitro_rgb_enable_poc.ko\n"}

                    def which_mock(name: str) -> str | None:
                        if name == "lsinitrd" and not mode["ls_avail"]:
                            return None
                        return f"/mock/{name}"

                    def runner(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
                        if Path(argv[0]).name == "lsinitrd":
                            if mode["action"] == "crash":
                                raise RuntimeError("mid-lsinitrd crash")
                            if mode["action"] == "fail":
                                return subprocess.CompletedProcess(argv, 1, stdout="", stderr="lsinitrd err\n")
                            return subprocess.CompletedProcess(argv, 0, stdout=mode["out"], stderr="")
                        return base_runner(argv, **kwargs)

                    with patch("nitro_control.module_staging.shutil.which", side_effect=which_mock):
                        if crash_point == "mid_lsinitrd":
                            mode["action"] = "crash"
                            hook = None
                        else:
                            hook = lambda s: (_ for _ in ()).throw(RuntimeError("crash at after_index_refresh")) if s == "after_index_refresh" else None

                        with self.assertRaises(RuntimeError):
                            if op == "install":
                                stage_install(root, "native-rgb", KVER_CUR, cur_src, runner=runner, on_migration_stage=hook)
                            else:
                                uninstall_modules(root, "native-rgb", KVER_CUR, runner=runner, on_migration_stage=hook)

                        qual_0, qd_0 = check_installation_qualification(root, "native-rgb")
                        self.assertFalse(qual_0)
                        self.assertEqual(qd_0["state"], "pending_clearance")
                        self.assertIn(KVER_CUR, qd_0["pending_kernels"])
                        self.assertFalse(check_unprivileged_readiness(root, "native-rgb", KVER_CUR)[0])
                        with self.assertRaises(ModuleStagingError):
                            verify_privileged_runtime(root, "native-rgb", KVER_CUR, runner=runner)

                        # Subsequent failed or unavailable lsinitrd retries MUST NOT clear blocking state!
                        for ls_avail, act in ((True, "fail"), (False, "ok"), (True, "fail")):
                            mode["ls_avail"] = ls_avail
                            mode["action"] = act
                            with self.assertRaises(ModuleStagingError):
                                if op == "install":
                                    stage_install(root, "native-rgb", KVER_CUR, cur_src, runner=runner)
                                else:
                                    uninstall_modules(root, "native-rgb", KVER_CUR, runner=runner)

                            qual_r, qd_r = check_installation_qualification(root, "native-rgb")
                            self.assertFalse(qual_r)
                            self.assertEqual(qd_r["state"], "pending_clearance")
                            self.assertIn(KVER_CUR, qd_r["pending_kernels"])
                            self.assertFalse(check_unprivileged_readiness(root, "native-rgb", KVER_CUR)[0])
                            self.assertFalse(NativeRGB(root, KVER_CUR).status()[0])
                            with self.assertRaises(ModuleStagingError):
                                verify_privileged_runtime(root, "native-rgb", KVER_CUR, runner=runner)

                        # ONLY genuine positive lsinitrd verification clears to complete.
                        mode["ls_avail"] = True
                        mode["action"] = "ok"
                        mode["out"] = ""
                        if op == "install":
                            res = stage_install(root, "native-rgb", KVER_CUR, cur_src, runner=runner)
                            self.assertEqual(res["qualification_state"], "complete")
                            self.assertEqual(check_unprivileged_readiness(root, "native-rgb", KVER_CUR, runner=runner), (True, None))
                            self.assertTrue(verify_privileged_runtime(root, "native-rgb", KVER_CUR, runner=runner).is_file())
                        else:
                            res = uninstall_modules(root, "native-rgb", KVER_CUR, runner=runner)
                            self.assertEqual(res["qualification_state"], "complete")
                        self.assertTrue(check_installation_qualification(root, "native-rgb")[0])

    def test_h_p2_r14_stale_index_only_non_running_and_running_kernels(self):
        table = (
            # (component, target_kver, stale_kind, crash_point, retry_outcome)
            ("native-rgb", KVER_OLD, "dep", "after_migration_journal_save", "exposed"),
            ("native-rgb", KVER_OLD, "dep", "after_index_refresh", "exposed"),
            ("native-rgb", KVER_OLD, "dep", "after_migration_journal_save", "unverified_then_clean"),
            ("native-rgb", KVER_CUR, "dep", "after_index_refresh", "exposed"),
            ("rgb-driver", KVER_OLD, "alias", "after_index_refresh", "exposed"),
        )
        for component, stale_kver, stale_kind, crash_point, outcome in table:
            with self.subTest(component=component, stale_kver=stale_kver, crash_point=crash_point, outcome=outcome):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    _prepare_native_sysfs(root)
                    (root / "boot").mkdir(parents=True, exist_ok=True)
                    (root / "etc/systemd/system").mkdir(parents=True, exist_ok=True)
                    (root / "usr/local/libexec").mkdir(parents=True, exist_ok=True)

                    if component == "native-rgb":
                        src = _make_dummy_ko(root / "src/mod.ko", "nitro_rgb_enable_poc", KVER_CUR)
                        stale_dep = "extra/nitro-control/nitro_rgb_enable_poc.ko:\n"
                        stale_alias = ""
                        img_hit = "extra/nitro-control/nitro_rgb_enable_poc.ko\n"
                        ctrl_src = None
                        unit_src = None
                    else:
                        src = _make_dummy_ko(root / "src/mod.ko", "linuwu_sense", KVER_CUR, alias=WMI_ALIAS, params="nitro_v4=1")
                        stale_dep = "extra/nitro-control/linuwu_sense.ko:\n" if stale_kind == "dep" else ""
                        stale_alias = f"alias {WMI_ALIAS} linuwu_sense\n"
                        img_hit = "/linuwu_sense.ko\n"
                        ctrl_src = root / "src/controller.sh"
                        ctrl_src.write_text(f"#!/usr/bin/bash\n{CONTROLLER_MARKER}\n", encoding="utf-8")
                        unit_src = root / "src/driver.service"
                        unit_src.write_text(f"[Unit]\n{SERVICE_MARKER}\n", encoding="utf-8")

                    # Seed stale index ONLY (no legacy module file exists on disk!).
                    if stale_dep:
                        (root / f"usr/lib/modules/{stale_kver}/modules.dep").write_text(stale_dep, encoding="utf-8")
                    if stale_alias:
                        (root / f"usr/lib/modules/{stale_kver}/modules.alias").write_text(stale_alias, encoding="utf-8")
                    # Initramfs ONLY on stale_kver (running kernel has NO initramfs when stale_kver == KVER_OLD).
                    (root / f"boot/initramfs-{stale_kver}.img").write_bytes(b"img-stale")

                    base_runner, depmod_calls = _fake_modinfo_runner(root)
                    ls_calls: list[str] = []
                    ls_state = {"fail": outcome == "unverified_then_clean", "out": img_hit if outcome == "exposed" else ""}

                    def runner(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
                        cmd = Path(argv[0]).name
                        if cmd == "lsinitrd":
                            ls_calls.append(argv[-1])
                            if ls_state["fail"]:
                                return subprocess.CompletedProcess(argv, 1, stdout="", stderr="err\n")
                            return subprocess.CompletedProcess(argv, 0, stdout=ls_state["out"], stderr="")
                        if cmd == "systemctl" and argv[1] == "is-active":
                            return subprocess.CompletedProcess(argv, 3, stdout="inactive\n", stderr="")
                        return base_runner(argv, **kwargs)

                    with patch("nitro_control.module_staging.shutil.which", side_effect=lambda n: f"/mock/{n}"):
                        with self.assertRaisesRegex(RuntimeError, "crash"):
                            stage_install(
                                root, component, KVER_CUR, src,
                                controller_src=ctrl_src, unit_src=unit_src, runner=runner,
                                on_migration_stage=lambda s: (_ for _ in ()).throw(RuntimeError(f"crash at {s}")) if s == crash_point else None,
                            )

                        # Interruption MUST leave durable pending obligation on stale_kver!
                        qual_0, qd_0 = check_installation_qualification(root, component)
                        self.assertFalse(qual_0)
                        self.assertEqual(qd_0["state"], "pending_clearance")
                        self.assertIn(stale_kver, qd_0["pending_kernels"])

                        # Retrying MUST re-verify stale_kver initramfs even after depmod refreshed modules.dep!
                        ls_calls.clear()
                        with self.assertRaises(ModuleStagingError):
                            stage_install(
                                root, component, KVER_CUR, src,
                                controller_src=ctrl_src, unit_src=unit_src, runner=runner,
                            )
                        self.assertTrue(any(stale_kver in p for p in ls_calls))

                        # Genuine positive clearance promotes cleanly to complete.
                        ls_state["fail"] = False
                        ls_state["out"] = ""
                        res_ok = stage_install(
                            root, component, KVER_CUR, src,
                            controller_src=ctrl_src, unit_src=unit_src, runner=runner,
                        )
                        self.assertEqual(res_ok["qualification_state"], "complete")
                        self.assertIn(stale_kver, res_ok["affected_kernels"])
                        self.assertIn(stale_kver, depmod_calls)
                        self.assertTrue(check_installation_qualification(root, component)[0])

                        # Corresponding uninstall starting with stale index & residual initramfs keeps pending across retry.
                        if stale_dep:
                            (root / f"usr/lib/modules/{stale_kver}/modules.dep").write_text(stale_dep, encoding="utf-8")
                        if stale_alias:
                            (root / f"usr/lib/modules/{stale_kver}/modules.alias").write_text(stale_alias, encoding="utf-8")
                        ls_state["out"] = img_hit
                        with self.assertRaisesRegex(RuntimeError, "crash"):
                            uninstall_modules(
                                root, component, KVER_CUR, runner=runner,
                                on_migration_stage=lambda s: (_ for _ in ()).throw(RuntimeError("crash")) if s == "after_index_refresh" else None,
                            )
                        self.assertEqual(check_installation_qualification(root, component)[1]["state"], "pending_clearance")
                        ls_state["out"] = ""
                        uninst_ok = uninstall_modules(root, component, KVER_CUR, runner=runner)
                        self.assertEqual(uninst_ok["qualification_state"], "complete")
                        self.assertTrue(check_installation_qualification(root, component)[0])

    def test_h_p3_01_experimental_param_gate_across_identical_migration_upgrade_and_uninstall(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _prepare_native_sysfs(root)
            (root / f"usr/lib/modules/{KVER_CUR}").mkdir(parents=True, exist_ok=True)
            (root / f"usr/lib/modules/{KVER_OLD}").mkdir(parents=True, exist_ok=True)
            runner, _ = _fake_modinfo_runner(root)

            # 1. Identical already-managed experimental module on KVER_CUR must NOT bypass validation.
            exp_src = _make_dummy_ko(
                root / "src/exp.ko", "nitro_rgb_enable_poc", KVER_CUR, params="apply:bool;backlight_test:bool"
            )
            priv_cur = root / f"usr/local/lib/nitro-control/modules/{KVER_CUR}"
            priv_cur.mkdir(parents=True, exist_ok=True)
            priv_cur_ko = priv_cur / "nitro_rgb_enable_poc.ko"
            priv_cur_ko.write_bytes(exp_src.read_bytes())
            priv_cur_ko.chmod(0o644)
            sha_cur = _write_sha256(priv_cur_ko)
            _write_manifest(priv_cur_ko, "native-rgb", "nitro_rgb_enable_poc", KVER_CUR, sha_cur)

            with self.assertRaisesRegex(ModuleStagingError, "experimental diagnostic parameters"):
                stage_install(root, "native-rgb", KVER_CUR, exp_src, runner=runner)
            ready, reason = check_unprivileged_readiness(root, "native-rgb", KVER_CUR, runner=runner)
            self.assertFalse(ready)
            self.assertIn("experimental diagnostic parameters", reason or "")
            with self.assertRaisesRegex(ModuleStagingError, "experimental diagnostic parameters"):
                verify_privileged_runtime(root, "native-rgb", KVER_CUR, runner=runner)

            # 2. Clean Phase 3 binary upgrades the valid Phase 2 experimental binary on KVER_CUR cleanly.
            clean_src = _make_dummy_ko(
                root / "src/clean.ko", "nitro_rgb_enable_poc", KVER_CUR, params="apply:bool;effect_mode:int"
            )
            up_res = stage_install(root, "native-rgb", KVER_CUR, clean_src, runner=runner)
            self.assertEqual(up_res["qualification_state"], "complete")
            self.assertEqual(up_res["incompatible_kernels"], [])
            self.assertTrue(check_unprivileged_readiness(root, "native-rgb", KVER_CUR, runner=runner)[0])
            verify_privileged_runtime(root, "native-rgb", KVER_CUR, runner=runner)

            # 3. Interrupted transaction cannot be completed using an experimental source, and succeeds with clean source.
            clean_src2 = _make_dummy_ko(
                root / "src/clean_v2.ko", "nitro_rgb_enable_poc", KVER_CUR, params="apply:bool;effect_mode:int;probe:bool"
            )
            with self.assertRaisesRegex(RuntimeError, "crash"):
                stage_install(
                    root, "native-rgb", KVER_CUR, clean_src2, runner=runner,
                    on_commit_stage=lambda s: (_ for _ in ()).throw(RuntimeError("crash")) if s == "after_tx_prepare" else None,
                )
            with self.assertRaisesRegex(ModuleStagingError, "experimental diagnostic parameters"):
                stage_install(root, "native-rgb", KVER_CUR, exp_src, runner=runner)
            rec_res = stage_install(root, "native-rgb", KVER_CUR, clean_src2, runner=runner)
            self.assertEqual(rec_res["qualification_state"], "complete")

            # 4. Legacy duplicate experimental module on KVER_OLD is refused without deleting legacy file.
            exp_old = _make_dummy_ko(
                root / f"usr/lib/modules/{KVER_OLD}/extra/nitro-control/nitro_rgb_enable_poc.ko",
                "nitro_rgb_enable_poc", KVER_OLD, params="apply:bool;red_test:bool",
            )
            sha_old = _write_sha256(exp_old)
            _write_manifest(exp_old, "native-rgb", "nitro_rgb_enable_poc", KVER_OLD, sha_old)
            priv_old = root / f"usr/local/lib/nitro-control/modules/{KVER_OLD}"
            priv_old.mkdir(parents=True, exist_ok=True)
            priv_old_ko = priv_old / "nitro_rgb_enable_poc.ko"
            priv_old_ko.write_bytes(exp_old.read_bytes())
            priv_old_ko.chmod(0o644)
            _write_sha256(priv_old_ko)
            _write_manifest(priv_old_ko, "native-rgb", "nitro_rgb_enable_poc", KVER_OLD, sha_old)

            with self.assertRaisesRegex(ModuleStagingError, "experimental diagnostic parameters"):
                stage_install(root, "native-rgb", KVER_CUR, clean_src, runner=runner)
            self.assertTrue(exp_old.is_file())

            # Remove legacy KVER_OLD artifact cleanly via uninstall (allowed even on verified Phase 2 experimental binary),
            # then re-seed KVER_OLD private experimental binary only to test cross-kernel upgrade notice & boundary.
            uninstall_modules(root, "native-rgb", KVER_OLD, runner=runner)
            priv_old.mkdir(parents=True, exist_ok=True)
            priv_old_ko.write_bytes(exp_old_bytes := (
                "FAKE_KO\nNAME=nitro_rgb_enable_poc\n"
                f"VERMAGIC={KVER_OLD} SMP preempt mod_unload\nALIAS=\nPARAMS=apply:bool;red_test:bool\n"
            ).encode("utf-8"))
            priv_old_ko.chmod(0o644)
            sha_old_priv = _write_sha256(priv_old_ko)
            _write_manifest(priv_old_ko, "native-rgb", "nitro_rgb_enable_poc", KVER_OLD, sha_old_priv)
            _ = exp_old_bytes
            res_with_old_priv = stage_install(root, "native-rgb", KVER_CUR, clean_src, runner=runner)
            self.assertEqual(res_with_old_priv["qualification_state"], "complete")
            self.assertEqual(res_with_old_priv["incompatible_kernels"], [KVER_OLD])
            self.assertTrue(any(KVER_OLD in n and "NOTICE:" in n for n in res_with_old_priv["notes"]))
            compat = check_artifact_compatibility(root, "native-rgb", runner=runner)
            self.assertEqual(compat["compatible_kernels"], [KVER_CUR])
            self.assertEqual(compat["incompatible_kernels"], [KVER_OLD])
            self.assertTrue(check_unprivileged_readiness(root, "native-rgb", KVER_CUR, runner=runner)[0])
            self.assertFalse(check_unprivileged_readiness(root, "native-rgb", KVER_OLD, runner=runner)[0])
            with self.assertRaisesRegex(ModuleStagingError, "experimental diagnostic parameters"):
                verify_privileged_runtime(root, "native-rgb", KVER_OLD, runner=runner)

            # Uninstalling verified Phase 2 experimental binary succeeds cleanly.
            un_old = uninstall_modules(root, "native-rgb", KVER_OLD, runner=runner)
            self.assertEqual(un_old["qualification_state"], "complete")
            self.assertFalse(priv_old_ko.exists())

    def test_h_p3_r01_metadata_readiness_distinguishes_missing_failed_timeout_compatible_incompatible(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _prepare_native_sysfs(root)
            (root / f"usr/lib/modules/{KVER_CUR}").mkdir(parents=True, exist_ok=True)
            base_runner, _ = _fake_modinfo_runner(root)
            clean_src = _make_dummy_ko(
                root / "src/clean.ko", "nitro_rgb_enable_poc", KVER_CUR, params="apply:bool;effect_mode:int"
            )
            stage_install(root, "native-rgb", KVER_CUR, clean_src, runner=base_runner)

            # 1. Exact H-P3-R01 reproduction: modinfo -p returns exit code 1 -> ALL THREE fail closed consistently!
            def fail_modinfo_runner(argv, **_kwargs):
                if Path(argv[0]).name == "modinfo" and "-p" in argv:
                    return subprocess.CompletedProcess(argv, 1, stdout="", stderr="Exec format error\n")
                return base_runner(argv)

            ready_f, msg_f = check_unprivileged_readiness(root, "native-rgb", KVER_CUR, runner=fail_modinfo_runner)
            self.assertFalse(ready_f)
            self.assertIn("failed", msg_f or "")
            compat_f = check_artifact_compatibility(root, "native-rgb", runner=fail_modinfo_runner)
            self.assertEqual(compat_f["unverified_kernels"], [KVER_CUR])
            self.assertEqual(compat_f["statuses"][KVER_CUR], "failed")
            with self.assertRaisesRegex(ModuleStagingError, "modinfo -p failed"):
                verify_privileged_runtime(root, "native-rgb", KVER_CUR, runner=fail_modinfo_runner)

            # 2. TimeoutExpired -> readiness & compat & runtime distinguish timeout cleanly!
            recorded_timeouts = []

            def timeout_modinfo_runner(argv, **kwargs):
                if Path(argv[0]).name == "modinfo" and "-p" in argv:
                    recorded_timeouts.append(kwargs.get("timeout"))
                    raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 2))
                return base_runner(argv)

            ready_t, msg_t = check_unprivileged_readiness(
                root, "native-rgb", KVER_CUR, runner=timeout_modinfo_runner, timeout=2
            )
            self.assertFalse(ready_t)
            self.assertEqual(recorded_timeouts, [2])
            self.assertIn("timed out", msg_t or "")
            compat_t = check_artifact_compatibility(root, "native-rgb", runner=timeout_modinfo_runner)
            self.assertEqual(compat_t["unverified_kernels"], [KVER_CUR])
            self.assertEqual(compat_t["statuses"][KVER_CUR], "timeout")
            with self.assertRaisesRegex(ModuleStagingError, "timed out"):
                verify_privileged_runtime(root, "native-rgb", KVER_CUR, runner=timeout_modinfo_runner)

            # 3. Missing modinfo tool -> explicitly reported as missing tool / unverified!
            orig_is_file = Path.is_file

            def hide_usr_sbin_modinfo(path_obj, *args, **kwargs):
                if str(path_obj) == "/usr/sbin/modinfo":
                    return False
                return orig_is_file(path_obj, *args, **kwargs)

            with (patch("nitro_control.module_staging.shutil.which", return_value=None),
                  patch("pathlib.Path.is_file", autospec=True, side_effect=hide_usr_sbin_modinfo)):
                ready_m, msg_m = check_unprivileged_readiness(root, "native-rgb", KVER_CUR)
            self.assertFalse(ready_m)
            self.assertIn("missing", msg_m or "")

    def test_nc_q02_helper_lifecycle_with_stale_listings(self):
        for initial in ("fresh", "legacy", "completed marker", "coherent", "interrupted removal"):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                (root / "run").mkdir()
                (root / "usr").mkdir()
                runner, _ = _fake_modinfo_runner(root)
                libexec = root / "usr/local/libexec"
                base = libexec / "nitro-control-rgb"
                marker = base / RGB_HELPER_TX_FILE
                manifest = base / RGB_HELPER_MANIFEST
                launcher = libexec / "nitro-control-rgb-helper"
                if initial == "legacy":
                    libexec.mkdir(parents=True)
                    launcher.write_text(
                        '#!/usr/bin/python3 -I\nimport sys\nsys.path.insert(0, "/usr/local/libexec/nitro-control-rgb")\n'
                        "from nitro_control.rgb_privileged import main\nraise SystemExit(main())\n",
                        encoding="utf-8",
                    )
                elif initial in {"completed marker", "interrupted removal"}:
                    def interrupt(stage):
                        if stage == "after_manifest":
                            raise RuntimeError("stopped after manifest")

                    with self.assertRaisesRegex(RuntimeError, "stopped after manifest"):
                        install_rgb_helper(root, REPO, runner=runner, on_stage=interrupt)
                    self.assertEqual(json.loads(marker.read_bytes())["target_files"],
                                     json.loads(manifest.read_bytes())["files"])
                elif initial == "coherent":
                    install_rgb_helper(root, REPO, runner=runner)

                listdir = os.listdir
                hidden = {RGB_HELPER_TX_FILE}

                def stale_listdir(directory):
                    return [name for name in listdir(directory) if name not in hidden]

                with patch("nitro_control.module_staging.os.listdir", side_effect=stale_listdir):
                    if initial in {"completed marker", "interrupted removal"}:
                        with self.assertRaisesRegex(ModuleStagingError, "Interrupted RGB helper"):
                            verify_privileged_rgb_helper(root)
                    if initial != "interrupted removal":
                        install_rgb_helper(root, REPO, adopt_legacy=initial == "legacy", runner=runner)
                        self.assertFalse(marker.exists())
                        with patch("nitro_control.rgb_client.shutil.which", return_value="/usr/bin/pkexec"):
                            caps = RGBClient(launcher).capabilities()
                        self.assertTrue(caps.static_supported and caps.effect_supported)
                        self.assertEqual((caps.protocol, caps.state), (2, "coherent"))
                    hidden.update((RGB_HELPER_MANIFEST, launcher.name, "rgb.py"))
                    if initial != "interrupted removal":
                        verify_privileged_rgb_helper(root)
                    else:
                        self.assertTrue(marker.is_file())
                    (base / "foreign.txt").write_text("preserve", encoding="utf-8")
                    (base / "nitro_control/custom.py").write_text("# preserve", encoding="utf-8")
                    removed = uninstall_rgb_helper(root)["removed"]
                self.assertIn(str(manifest), removed)
                self.assertIn(str(launcher), removed)
                self.assertFalse(marker.exists())
                self.assertFalse(launcher.exists())
                self.assertFalse((base / "nitro_control/rgb.py").exists())
                self.assertTrue((base / "nitro_control/custom.py").exists())
                self.assertEqual((base / "foreign.txt").read_text(encoding="utf-8"), "preserve")

    def test_nc_q02_generated_launcher_verifies_known_paths_without_listings(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "run").mkdir()
            (root / "usr").mkdir()
            runner, _ = _fake_modinfo_runner(root)
            install_rgb_helper(root, REPO, runner=runner)
            # Run the generated pre-import guard only, with every lookup rooted in the fixture.
            generated = ast.parse(_render_rgb_helper_launcher())
            functions = [node for node in generated.body if isinstance(node, ast.FunctionDef)]
            namespace = {"os": os, "stat": stat, "hashlib": hashlib, "json": json}
            exec(compile(ast.Module(body=functions, type_ignores=[]), "<helper guard>", "exec"), namespace)
            namespace["_open_dir_chain"] = lambda parts: verify_dir_chain(root, parts)
            fstat = os.fstat
            islink = os.path.islink

            def namespace_root_stat(fd):
                # Model files owned by UID 0 in the isolated fixture without changing ownership.
                info = list(fstat(fd))
                info[4] = 0
                return os.stat_result(info)

            with (patch("os.listdir", return_value=[]),
                  patch("os.path.islink", side_effect=lambda path: islink(root / path.lstrip("/"))),
                  patch("os.fstat", side_effect=namespace_root_stat)):
                namespace["_verify_helper_coherence"]()
                base = root / "usr/local/libexec/nitro-control-rgb"
                (base / RGB_HELPER_TX_FILE).touch()
                with self.assertRaisesRegex(RuntimeError, "Interrupted RGB helper"):
                    namespace["_verify_helper_coherence"]()

    def test_nc_q02_stale_listings_cannot_hide_foreign_helper_artifacts(self):
        for kind in ("foreign", "symlink", "hardlink", "untrusted owner", "modified digest"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                (root / "run").mkdir()
                libexec = root / "usr/local/libexec"
                libexec.mkdir(parents=True)
                runner, _ = _fake_modinfo_runner(root)
                launcher = libexec / "nitro-control-rgb-helper"
                messages = {
                    "foreign": "Foreign file occupies",
                    "symlink": "Cannot safely open",
                    "hardlink": "unexpected hardlink count",
                    "untrusted owner": "untrusted owner",
                    "modified digest": "locally modified manifested",
                }
                if kind == "foreign":
                    launcher.write_bytes(b"# unrelated executable\n")
                elif kind == "symlink":
                    launcher.symlink_to(root / "missing")
                elif kind == "hardlink":
                    neighbor = root / "neighbor"
                    neighbor.write_bytes(b"preserve this file\n")
                    os.link(neighbor, launcher)
                else:
                    install_rgb_helper(root, REPO, runner=runner)
                    if kind == "modified digest":
                        launcher.write_bytes(launcher.read_bytes() + b"# local change\n")

                original = launcher.lstat()
                original_contents = launcher.readlink() if launcher.is_symlink() else launcher.read_bytes()
                fstat = os.fstat

                def check_owner(fd):
                    info = fstat(fd)
                    if kind == "untrusted owner" and (info.st_dev, info.st_ino) == (original.st_dev, original.st_ino):
                        values = list(info)
                        values[4] = original.st_uid + 1
                        return os.stat_result(values)
                    return info

                with (patch("os.listdir", return_value=[]), patch("os.fstat", side_effect=check_owner)):
                    with self.assertRaisesRegex(ModuleStagingError, messages[kind]):
                        install_rgb_helper(root, REPO, adopt_legacy=True, runner=runner)
                    with self.assertRaisesRegex(ModuleStagingError, messages[kind]):
                        uninstall_rgb_helper(root, adopt_legacy=True)
                after = launcher.lstat()
                self.assertEqual((after.st_ino, after.st_mode, after.st_nlink),
                                 (original.st_ino, original.st_mode, original.st_nlink))
                self.assertEqual(launcher.readlink() if launcher.is_symlink() else launcher.read_bytes(),
                                 original_contents)

    def test_nc_q02_module_migration_and_uninstall_with_stale_listings(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _prepare_native_sysfs(root)
            runner, _ = _fake_modinfo_runner(root)
            name = "nitro_rgb_enable_poc.ko"
            source = _make_dummy_ko(root / "src" / name, "nitro_rgb_enable_poc", KVER_CUR)
            legacy = _make_dummy_ko(root / f"usr/lib/modules/{KVER_OLD}/extra/nitro-control" / name,
                                    "nitro_rgb_enable_poc", KVER_OLD)
            _write_sha256(legacy)
            private = root / "usr/local/lib/nitro-control/modules"
            hidden = {name, f"{name}.sha256", manifest_name(name), tx_json_name(name), tx_dir_name(name)}
            listdir = os.listdir

            def stale_listdir(directory):
                return [name for name in listdir(directory) if name not in hidden]

            with patch("nitro_control.module_staging.os.listdir", side_effect=stale_listdir):
                stage_install(root, "native-rgb", KVER_CUR, source, adopt_legacy=True, runner=runner)
                self.assertFalse(legacy.exists())
                self.assertTrue((private / KVER_OLD / name).exists())

                def interrupt(stage):
                    if stage == "after_tx_prepare":
                        raise RuntimeError("module interrupted after prepare")

                upgrade = _make_dummy_ko(root / "upgrade" / name, "nitro_rgb_enable_poc", KVER_CUR, params="v2")
                with self.assertRaisesRegex(RuntimeError, "module interrupted after prepare"):
                    stage_install(root, "native-rgb", KVER_CUR, upgrade, runner=runner, on_commit_stage=interrupt)
                tx_dir = private / KVER_CUR / tx_dir_name(name)
                staged = tx_dir / name
                staged.write_bytes(b"locally modified")
                with self.assertRaisesRegex(ModuleStagingError, "does not match transaction digest"):
                    stage_install(root, "native-rgb", KVER_CUR, upgrade, runner=runner)
                self.assertEqual(staged.read_bytes(), b"locally modified")
                staged.write_bytes(upgrade.read_bytes())
                stage_install(root, "native-rgb", KVER_CUR, upgrade, runner=runner)
                verify_privileged_runtime(root, "native-rgb", KVER_CUR, runner=runner)
                self.assertTrue(uninstall_modules(root, "native-rgb", KVER_CUR, runner=runner)["removed"])
                self.assertFalse((private / KVER_CUR / name).exists())
                self.assertFalse((private / KVER_OLD / name).exists())

    def test_nc_q01_documented_direct_shell_entry_points_are_executable(self):
        command = re.compile(r"^\s*(?:\|\s*`\s*)?(?:sudo\s+)?\./(scripts/[\w.-]+\.sh)\b", re.MULTILINE)
        direct = {entry for doc in (REPO / "README.md", *(REPO / "docs").rglob("*.md"))
                  for entry in command.findall(doc.read_text(encoding="utf-8"))}
        self.assertIn("scripts/uninstall-native-rgb-fedora.sh", direct)
        for entry in sorted(direct):
            with self.subTest(entry=entry):
                self.assertTrue((REPO / entry).stat().st_mode & stat.S_IXUSR)

    def test_nc_q02_helper_finalization_errors_and_recovery(self):
        for fault in ("unlink denied", "unlink ineffective", "manifest", "source", "launcher mode"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                (root / "run").mkdir()
                (root / "usr").mkdir()
                runner, _ = _fake_modinfo_runner(root)
                libexec = root / "usr/local/libexec"
                base = libexec / "nitro-control-rgb"
                marker = base / RGB_HELPER_TX_FILE
                unlink = os.unlink
                replace = os.replace
                changed = []

                def faulty_unlink(name, *, dir_fd=None):
                    if name == RGB_HELPER_TX_FILE:
                        if fault == "unlink denied":
                            raise PermissionError("simulated unlink denial")
                        if fault == "unlink ineffective":
                            return
                    unlink(name, dir_fd=dir_fd)

                def faulty_publish(src, dst, **kwargs):
                    replace(src, dst, **kwargs)
                    if dst != RGB_HELPER_MANIFEST:
                        return
                    if fault == "manifest":
                        path = base / RGB_HELPER_MANIFEST
                        changed.append((path, path.read_bytes()))
                        manifest = json.loads(path.read_bytes())
                        manifest["authorization"] = "auth_self"
                        path.write_text(json.dumps(manifest), encoding="utf-8")
                    elif fault == "source":
                        path = base / "nitro_control/rgb.py"
                        changed.append((path, path.read_bytes()))
                        path.write_bytes(b"# unexpected content\n")
                    elif fault == "launcher mode":
                        (libexec / "nitro-control-rgb-helper").chmod(0o644)

                with (patch("nitro_control.module_staging.os.unlink", side_effect=faulty_unlink),
                      patch("nitro_control.module_staging.os.replace", side_effect=faulty_publish),
                      self.assertRaises(ModuleStagingError)):
                    install_rgb_helper(root, REPO, runner=runner)
                self.assertTrue(marker.is_file())
                with self.assertRaisesRegex(ModuleStagingError, "Interrupted RGB helper"):
                    verify_privileged_rgb_helper(root)
                for path, data in changed:
                    path.write_bytes(data)
                install_rgb_helper(root, REPO, runner=runner)
                self.assertFalse(marker.exists())
                verify_privileged_rgb_helper(root)

    def test_h_p2_r09_rgb_helper_provenance_adoption_upgrade_and_preservation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "usr/local/libexec").mkdir(parents=True, exist_ok=True)
            (root / "usr/share/polkit-1/actions").mkdir(parents=True, exist_ok=True)
            (root / "run").mkdir(parents=True, exist_ok=True)

            # Seed pre-manifest preview.4-style Nitro helper + unrelated sibling note + mixed __pycache__ (Nitro + unrelated .pyc).
            libexec = root / "usr/local/libexec"
            polkit = root / "usr/share/polkit-1/actions"
            pkg = libexec / "nitro-control-rgb/nitro_control"
            pkg.mkdir(parents=True, exist_ok=True)
            (pkg / "__pycache__").mkdir()
            (pkg / "__pycache__/unrelated_plugin.cpython-315.pyc").write_bytes(b"unrelated pyc")
            (pkg / "__pycache__/rgb_hardware.cpython-315.pyc").write_bytes(b"ambiguous non-pyc text")
            (pkg / "__pycache__/cache-notes.txt").write_text("keep cache note\n", encoding="utf-8")
            (libexec / "nitro-control-rgb/custom-note.txt").write_text("admin note\n", encoding="utf-8")
            (polkit / "unrelated.policy").write_text("<policyconfig/>", encoding="utf-8")

            legacy_launcher = libexec / "nitro-control-rgb-helper"
            legacy_launcher.write_text(
                '#!/usr/bin/python3 -I\nimport sys\nsys.path.insert(0, "/usr/local/libexec/nitro-control-rgb")\n'
                "from nitro_control.rgb_privileged import main\nraise SystemExit(main())\n",
                encoding="utf-8",
            )
            legacy_policy = polkit / "io.github.tobiasguta.NitroControl.rgb.policy"
            legacy_policy.write_text(
                '<policyconfig><action id="io.github.tobiasguta.NitroControl.rgb">'
                '<annotate key="org.freedesktop.policykit.exec.path">/usr/local/libexec/nitro-control-rgb-helper</annotate>'
                "</action></policyconfig>",
                encoding="utf-8",
            )
            (pkg / "rgb.py").write_text("class LightingPlan:\n    pass\n", encoding="utf-8")
            pyc_rgb = pkg / f"__pycache__/rgb.{sys.implementation.cache_tag}.pyc"
            py_compile.compile(str(pkg / "rgb.py"), cfile=str(pyc_rgb), doraise=True)
            (pkg / "rgb_privileged.py").write_text("from .rgb_hardware import RGBHardware\n", encoding="utf-8")

            # Without --adopt-legacy, install & uninstall refuse cleanly and preserve pre-manifest artifacts.
            with self.assertRaisesRegex(ModuleStagingError, "adopt-legacy"):
                install_rgb_helper(root, REPO, runner=lambda *a, **k: subprocess.CompletedProcess([], 0, "", ""))
            with self.assertRaisesRegex(ModuleStagingError, "adopt-legacy"):
                uninstall_rgb_helper(root)
            self.assertTrue(legacy_launcher.is_file())

            # With --adopt-legacy, upgrade commits atomically, writes manifest.json, uses -IB,
            # cleans genuine PEP 552 Nitro .pyc and preserves both unrelated and ambiguous cache files!
            res = install_rgb_helper(
                root, REPO, authorization="auth_admin_keep", adopt_legacy=True,
                runner=lambda *a, **k: subprocess.CompletedProcess([], 0, "", ""),
            )
            self.assertEqual(res["authorization"], "auth_admin_keep")
            self.assertIn("-IB", legacy_launcher.read_text(encoding="utf-8"))
            self.assertIn("auth_admin_keep", legacy_policy.read_text(encoding="utf-8"))
            man = libexec / "nitro-control-rgb" / RGB_HELPER_MANIFEST
            self.assertTrue(man.is_file())
            self.assertFalse(pyc_rgb.exists())
            self.assertEqual((pkg / "__pycache__/unrelated_plugin.cpython-315.pyc").read_bytes(), b"unrelated pyc")
            self.assertEqual((pkg / "__pycache__/rgb_hardware.cpython-315.pyc").read_bytes(), b"ambiguous non-pyc text")
            self.assertEqual((pkg / "__pycache__/cache-notes.txt").read_text(encoding="utf-8"), "keep cache note\n")
            with patch("nitro_control.rgb_client.shutil.which", return_value="/usr/bin/pkexec"):
                caps = RGBClient(legacy_launcher).capabilities()
            self.assertTrue(caps.installed and caps.static_supported and caps.effect_supported)

            # H-P4-01: Locally modified manifested dependency, launcher, or policy MUST fail BOTH install AND uninstall EVEN WITH adopt_legacy=True!
            saved_rgb = (pkg / "rgb.py").read_text(encoding="utf-8")
            (pkg / "rgb.py").write_text("# modified locally\n", encoding="utf-8")
            for adopt_flag in (False, True):
                with self.assertRaisesRegex(ModuleStagingError, "locally modified manifested"):
                    install_rgb_helper(root, REPO, adopt_legacy=adopt_flag)
                with self.assertRaisesRegex(ModuleStagingError, "locally modified manifested"):
                    uninstall_rgb_helper(root, adopt_legacy=adopt_flag)
            self.assertEqual((pkg / "rgb.py").read_text(encoding="utf-8"), "# modified locally\n")
            (pkg / "rgb.py").write_text(saved_rgb, encoding="utf-8")

            saved_launcher = legacy_launcher.read_text(encoding="utf-8")
            legacy_launcher.write_text(saved_launcher + "# local tweak\n", encoding="utf-8")
            with self.assertRaisesRegex(ModuleStagingError, "locally modified manifested"):
                install_rgb_helper(root, REPO, adopt_legacy=True)
            with self.assertRaisesRegex(ModuleStagingError, "locally modified manifested"):
                uninstall_rgb_helper(root, adopt_legacy=True)
            legacy_launcher.write_text(saved_launcher, encoding="utf-8")

            # Missing manifest entry OR missing tracked dependency (incomplete install) refused on reinstall;
            # missing tracked file can still be uninstalled cleanly when remaining files match digests.
            saved_man = man.read_text(encoding="utf-8")
            bad_man = json.loads(saved_man)
            bad_man["files"].pop("nitro_control/rgb.py")
            man.write_text(json.dumps(bad_man), encoding="utf-8")
            with self.assertRaisesRegex(ModuleStagingError, "missing required entries"):
                install_rgb_helper(root, REPO, adopt_legacy=True)
            man.write_text(saved_man, encoding="utf-8")

            saved_hw = (pkg / "rgb_hardware.py").read_text(encoding="utf-8")
            (pkg / "rgb_hardware.py").unlink()
            with self.assertRaisesRegex(ModuleStagingError, "Incomplete manifested"):
                install_rgb_helper(root, REPO, adopt_legacy=True)
            (pkg / "rgb_hardware.py").write_text(saved_hw, encoding="utf-8")

            # H-P4-02: Interrupted upgrade leaves .tx.rgb-helper.json -> capabilities() fails closed; retry completes cleanly!
            with tempfile.TemporaryDirectory() as fake_repo_td:
                fake_repo = Path(fake_repo_td)
                (fake_repo / "nitro_control").mkdir()
                for fname in ("__init__.py", "rgb.py", "rgb_hardware.py", "module_staging.py", "native_rgb.py", "rgb_privileged.py"):
                    content = (REPO / "nitro_control" / fname).read_text(encoding="utf-8")
                    if fname == "rgb.py":
                        content += "\n# upgraded helper v2\n"
                    (fake_repo / "nitro_control" / fname).write_text(content, encoding="utf-8")

                with self.assertRaisesRegex(RuntimeError, "mid-upgrade crash"):
                    install_rgb_helper(
                        root, fake_repo,
                        runner=lambda *a, **k: subprocess.CompletedProcess([], 0, "", ""),
                        on_stage=lambda s: (_ for _ in ()).throw(RuntimeError("mid-upgrade crash")) if s == "after_partial_py" else None,
                    )
                with patch("nitro_control.rgb_client.shutil.which", return_value="/usr/bin/pkexec"):
                    caps_mid = RGBClient(legacy_launcher).capabilities()
                self.assertEqual(caps_mid.state, "interrupted")
                self.assertFalse(caps_mid.static_supported or caps_mid.effect_supported)
                # Retrying install recovers cleanly without --adopt-legacy and clears .tx.rgb-helper.json!
                install_rgb_helper(
                    root, fake_repo,
                    runner=lambda *a, **k: subprocess.CompletedProcess([], 0, "", ""),
                )
                with patch("nitro_control.rgb_client.shutil.which", return_value="/usr/bin/pkexec"):
                    caps_rec = RGBClient(legacy_launcher).capabilities()
                self.assertEqual(caps_rec.state, "coherent")
                self.assertTrue(caps_rec.static_supported and caps_rec.effect_supported)

            # H-P4-R02: Multi-generation repeated interrupted upgrades (A -> B partial -> C tx_prepare -> C complete, and uninstall)
            with tempfile.TemporaryDirectory() as td_seq:
                seq_root = Path(td_seq)
                (seq_root / "usr/local/libexec").mkdir(parents=True)
                (seq_root / "usr/share/polkit-1/actions").mkdir(parents=True)
                (seq_root / "run").mkdir(parents=True)
                no_restore = lambda *a, **k: subprocess.CompletedProcess([], 0, "", "")

                def make_gen_repo(target: Path, marker: str) -> Path:
                    (target / "nitro_control").mkdir(parents=True, exist_ok=True)
                    for fname in ("__init__.py", "rgb.py", "rgb_hardware.py", "module_staging.py", "native_rgb.py", "rgb_privileged.py"):
                        txt = (REPO / "nitro_control" / fname).read_text(encoding="utf-8")
                        if fname == "rgb.py":
                            txt += f"\n# {marker}\n"
                        (target / "nitro_control" / fname).write_text(txt, encoding="utf-8")
                    return target

                with tempfile.TemporaryDirectory() as g_root:
                    gp = Path(g_root)
                    gen_a = make_gen_repo(gp / "A", "gen A")
                    gen_b = make_gen_repo(gp / "B", "gen B")
                    gen_c = make_gen_repo(gp / "C", "gen C")
                    gen_d = make_gen_repo(gp / "D", "gen D")

                    # Sequence 1: A -> B(crash after_partial_py) -> C(crash after_tx_prepare) -> C(retry succeeds)
                    install_rgb_helper(seq_root, gen_a, runner=no_restore)
                    with self.assertRaisesRegex(RuntimeError, "crash-B"):
                        install_rgb_helper(
                            seq_root, gen_b, runner=no_restore,
                            on_stage=lambda s: (_ for _ in ()).throw(RuntimeError("crash-B")) if s == "after_partial_py" else None,
                        )
                    with self.assertRaisesRegex(RuntimeError, "crash-C"):
                        install_rgb_helper(
                            seq_root, gen_c, runner=no_restore,
                            on_stage=lambda s: (_ for _ in ()).throw(RuntimeError("crash-C")) if s == "after_tx_prepare" else None,
                        )
                    install_rgb_helper(seq_root, gen_c, runner=no_restore)
                    with patch("nitro_control.rgb_client.shutil.which", return_value="/usr/bin/pkexec"):
                        c_ok = RGBClient(seq_root / "usr/local/libexec/nitro-control-rgb-helper").capabilities()
                    self.assertEqual(c_ok.state, "coherent")
                    self.assertIn("# gen C", (seq_root / "usr/local/libexec/nitro-control-rgb/nitro_control/rgb.py").read_text(encoding="utf-8"))

                    # Sequence 2: C -> B(crash after_partial_py) -> D(crash after_tx_prepare) -> uninstall succeeds cleanly
                    with self.assertRaisesRegex(RuntimeError, "crash-B2"):
                        install_rgb_helper(
                            seq_root, gen_b, runner=no_restore,
                            on_stage=lambda s: (_ for _ in ()).throw(RuntimeError("crash-B2")) if s == "after_partial_py" else None,
                        )
                    with self.assertRaisesRegex(RuntimeError, "crash-D"):
                        install_rgb_helper(
                            seq_root, gen_d, runner=no_restore,
                            on_stage=lambda s: (_ for _ in ()).throw(RuntimeError("crash-D")) if s == "after_tx_prepare" else None,
                        )
                    # Genuine external edit injected mid-interrupted-chain MUST STILL be refused!
                    seq_rgb = seq_root / "usr/local/libexec/nitro-control-rgb/nitro_control/rgb.py"
                    saved_b = seq_rgb.read_bytes()
                    seq_rgb.write_text("# external unauthorized edit\n", encoding="utf-8")
                    with self.assertRaisesRegex(ModuleStagingError, "Locally modified"):
                        uninstall_rgb_helper(seq_root)
                    seq_rgb.write_bytes(saved_b)

                    uninstall_rgb_helper(seq_root)
                    self.assertFalse((seq_root / "usr/local/libexec/nitro-control-rgb-helper").exists())
                    self.assertFalse((seq_root / "usr/local/libexec/nitro-control-rgb/.tx.rgb-helper.json").exists())

            # Uninstall cleanly removes ONLY Nitro files while preserving unrelated admin & cache files!
            uninstall_rgb_helper(root)
            self.assertFalse(legacy_launcher.exists())
            self.assertFalse(legacy_policy.exists())
            self.assertFalse(man.exists())
            self.assertEqual((pkg / "__pycache__/unrelated_plugin.cpython-315.pyc").read_bytes(), b"unrelated pyc")
            self.assertEqual((pkg / "__pycache__/cache-notes.txt").read_text(encoding="utf-8"), "keep cache note\n")
            self.assertEqual((libexec / "nitro-control-rgb/custom-note.txt").read_text(encoding="utf-8"), "admin note\n")
            self.assertEqual((polkit / "unrelated.policy").read_text(encoding="utf-8"), "<policyconfig/>")

            # Foreign launcher or foreign module file is ALWAYS refused even WITH --adopt-legacy!
            legacy_launcher.write_text("#!/bin/sh\necho custom helper\n", encoding="utf-8")
            with self.assertRaisesRegex(ModuleStagingError, "Foreign file"):
                install_rgb_helper(root, REPO, adopt_legacy=True)
            with self.assertRaisesRegex(ModuleStagingError, "Foreign file"):
                uninstall_rgb_helper(root, adopt_legacy=True)
            self.assertIn("custom helper", legacy_launcher.read_text(encoding="utf-8"))
            legacy_launcher.unlink()
            (pkg / "rgb.py").write_text("# foreign module\n", encoding="utf-8")
            with self.assertRaisesRegex(ModuleStagingError, "Unrecognized file"):
                install_rgb_helper(root, REPO, adopt_legacy=True)

    def test_h_p4_r04_bytecode_ownership_checks_preserve_foreign_magic_and_same_size_caches(self):
        import importlib.util
        from nitro_control.module_staging import _pyc_matches_nitro_ownership

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "usr/share/polkit-1/actions").mkdir(parents=True)
            install_rgb_helper(root, REPO)

            pkg = root / "usr/local/libexec/nitro-control-rgb/nitro_control"
            pycache = pkg / "__pycache__"
            pycache.mkdir(exist_ok=True)
            tag = sys.implementation.cache_tag

            # 1) Foreign Python magic number (with valid CRLF header) must be preserved, not treated as Nitro-owned.
            rgb_src = (pkg / "rgb.py").read_bytes()
            foreign_pyc = pycache / "rgb_privileged.cpython-311.pyc"
            hash_tmp = Path(td) / "hash_ref.pyc"
            py_compile.compile(
                str(pkg / "rgb_privileged.py"),
                cfile=str(hash_tmp),
                doraise=True,
                invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
            )
            foreign_raw = b"\xa7\x0c\r\n" + hash_tmp.read_bytes()[4:]
            foreign_pyc.write_bytes(foreign_raw)
            priv_src = (pkg / "rgb_privileged.py").read_bytes()
            self.assertFalse(
                _pyc_matches_nitro_ownership(
                    "rgb_privileged",
                    foreign_raw,
                    0o644,
                    known_sources={"rgb_privileged": (priv_src,)},
                )
            )

            # 2) Same-length unrelated source compiled in TIMESTAMP mode must be preserved!
            native_path = pkg / "native_rgb.py"
            native_src = native_path.read_bytes()
            unrelated_same_len = ("# " + "x" * max(0, len(native_src) - 3) + "\n").encode("utf-8")
            if len(unrelated_same_len) > len(native_src):
                unrelated_same_len = unrelated_same_len[: len(native_src) - 1] + b"\n"
            self.assertEqual(len(unrelated_same_len), len(native_src))
            evil_py = Path(td) / "same_len.py"
            evil_py.write_bytes(unrelated_same_len)
            same_size_pyc = pycache / f"native_rgb.{tag}.pyc"
            py_compile.compile(
                str(evil_py),
                cfile=str(same_size_pyc),
                dfile=str(native_path),
                doraise=True,
                invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP,
            )
            patched = bytearray(same_size_pyc.read_bytes())
            patched[8:12] = int(native_path.stat().st_mtime).to_bytes(4, "little")
            same_size_pyc.write_bytes(bytes(patched))
            self.assertFalse(
                _pyc_matches_nitro_ownership(
                    "native_rgb",
                    bytes(patched),
                    0o644,
                    known_sources={"native_rgb": (native_src,)},
                    candidate_filenames=(str(native_path),),
                )
            )

            # 3) Genuine PEP 552 hash-based & exact-bytecode Nitro .pyc files ARE verified and removed cleanly.
            genuine_hash_pyc = pycache / f"rgb.{tag}.pyc"
            py_compile.compile(
                str(pkg / "rgb.py"),
                cfile=str(genuine_hash_pyc),
                doraise=True,
                invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
            )
            genuine_ts_pyc = pycache / f"rgb_hardware.{tag}.pyc"
            py_compile.compile(
                str(pkg / "rgb_hardware.py"),
                cfile=str(genuine_ts_pyc),
                dfile=str(pkg / "rgb_hardware.py"),
                doraise=True,
                invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP,
            )
            self.assertEqual(
                importlib.util.source_hash(rgb_src),
                genuine_hash_pyc.read_bytes()[8:16],
            )

            # 4) H-P4-R07: hash-based .pyc with matching source_hash header but unrelated bytecode payload must be preserved!
            ms_path = pkg / "module_staging.py"
            ms_src = ms_path.read_bytes()
            bogus_py = Path(td) / "bogus_payload.py"
            bogus_py.write_text("BOGUS_STALE_PAYLOAD = 42\n", encoding="utf-8")
            bogus_tmp = Path(td) / "bogus_payload.pyc"
            py_compile.compile(
                str(bogus_py),
                cfile=str(bogus_tmp),
                doraise=True,
                invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
            )
            fake_hash_raw = (
                importlib.util.MAGIC_NUMBER
                + (3).to_bytes(4, "little")
                + importlib.util.source_hash(ms_src)
                + bogus_tmp.read_bytes()[16:]
            )
            fake_hash_pyc = pycache / f"module_staging.{tag}.pyc"
            fake_hash_pyc.write_bytes(fake_hash_raw)
            self.assertFalse(
                _pyc_matches_nitro_ownership(
                    "module_staging",
                    fake_hash_raw,
                    0o644,
                    known_sources={"module_staging": (ms_src,)},
                    candidate_filenames=(str(ms_path),),
                )
            )

            install_rgb_helper(root, REPO)

            self.assertFalse(genuine_hash_pyc.exists())
            self.assertFalse(genuine_ts_pyc.exists())
            self.assertEqual(foreign_pyc.read_bytes(), foreign_raw)
            self.assertEqual(same_size_pyc.read_bytes(), bytes(patched))
            self.assertEqual(fake_hash_pyc.read_bytes(), fake_hash_raw)


class IsolatedModuleLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require = os.environ.get("NITRO_REQUIRE_ISOLATED_ROOT_TESTS") == "1"
        if shutil.which("bwrap") is None:
            msg = "bubblewrap (bwrap) unavailable for isolated root tests"
            if require:
                raise AssertionError(msg)
            raise unittest.SkipTest(msg)
        with tempfile.TemporaryDirectory() as probe_td:
            probe_root = Path(probe_td)
            probe = cls._run_bwrap_cmd(probe_root, ["/usr/bin/true"])
            if probe.returncode != 0:
                msg = f"bwrap user-namespace unavailable: {probe.stderr.strip()}"
                if require:
                    raise AssertionError(msg)
                raise unittest.SkipTest(msg)

    @staticmethod
    def _bwrap_invocation(
        root: Path, argv: list[str], *, env: dict[str, str] | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        cmd = [
            "bwrap",
            "--die-with-parent",
            "--unshare-user",
            "--uid", "0",
            "--gid", "0",
            "--bind", str(root), "/",
            "--dev", "/dev",
            "--ro-bind", str(REPO), "/repo",
            "--ro-bind", "/usr/bin", "/usr/bin",
            "--ro-bind", "/usr/sbin", "/usr/sbin",
        ]
        if Path("/usr/lib64").is_dir():
            cmd.extend(["--ro-bind", "/usr/lib64", "/usr/lib64", "--symlink", "usr/lib64", "/lib64"])
        for extra_lib in ("/usr/lib/x86_64-linux-gnu", *sorted(Path("/usr/lib").glob("python3*"))):
            p = Path(extra_lib)
            if p.is_dir():
                cmd.extend(["--ro-bind", str(p), str(p)])
        cmd.extend(["--symlink", "usr/bin", "/bin", "--symlink", "usr/sbin", "/sbin"])
        run_env = {
            "PATH": "/mockbin:/usr/bin:/usr/sbin",
            "HOME": "/root",
            "LC_ALL": "C",
        }
        if env:
            run_env.update(env)
        return cmd + argv, run_env

    @classmethod
    def _run_bwrap_cmd(cls, root: Path, argv: list[str], *, env: dict[str, str] | None = None,
                       timeout: int = 20) -> subprocess.CompletedProcess[str]:
        cmd, run_env = cls._bwrap_invocation(root, argv, env=env)
        return subprocess.run(cmd, env=run_env, capture_output=True, text=True,
                              timeout=timeout, check=False)

    def _setup_root(self) -> Path:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        for rel in (
            "usr/local/lib",
            "usr/local/libexec",
            f"usr/lib/modules/{KVER_CUR}/kernel/drivers/platform/x86",
            f"usr/lib/modules/{KVER_OLD}/kernel/drivers/platform/x86",
            "etc/systemd/system",
            "etc/modprobe.d",
            "etc/modules-load.d",
            "dev",
            "run",
            "root",
            "tmp/mock_state",
            "boot",
            "src",
            "sys/class/dmi/id",
            "sys/module/acer_wmi",
            f"sys/bus/wmi/devices/{RGB_WMI_GUID}-8",
            "sys/devices/platform/acer-wmi/four_zoned_kb",
            "sys/class/hwmon/hwmon1",
            "sys/firmware/acpi",
            "proc",
            "mockbin",
        ):
            (root / rel).mkdir(parents=True, exist_ok=True)

        (root / "etc/passwd").write_text("root:x:0:0:root:/root:/usr/bin/bash\n", encoding="utf-8")
        (root / "etc/group").write_text("root:x:0:\n", encoding="utf-8")
        _prepare_native_sysfs(root)

        for kver in (KVER_CUR, KVER_OLD):
            stock = root / f"usr/lib/modules/{kver}/kernel/drivers/platform/x86/acer-wmi.ko"
            _make_dummy_ko(stock, "acer_wmi", kver, alias=WMI_ALIAS)
            (root / f"usr/lib/modules/{kver}/modules.dep").write_text(
                "kernel/drivers/platform/x86/acer-wmi.ko:\n", encoding="utf-8"
            )
            (root / f"usr/lib/modules/{kver}/modules.alias").write_text(
                f"alias {WMI_ALIAS} acer_wmi\n", encoding="utf-8"
            )

        self._install_mockbin(root / "mockbin")
        return root

    def _install_mockbin(self, mockbin: Path) -> None:
        mocks = {
            "uname": f"#!/usr/bin/bash\nprintf '%s\\n' '{KVER_CUR}'\n",
            "modinfo": """#!/usr/bin/bash
set -euo pipefail
field=""
while [[ $# -gt 1 ]]; do
  case "$1" in
    -F) field="$2"; shift 2 ;;
    -p) field="PARAMS"; shift ;;
    *) shift ;;
  esac
done
file="$1"
[[ -f "$file" ]] || exit 1
case "$field" in
  name) grep -m1 '^NAME=' "$file" | cut -d= -f2- ;;
  vermagic) grep -m1 '^VERMAGIC=' "$file" | cut -d= -f2- ;;
  alias) grep -m1 '^ALIAS=' "$file" | cut -d= -f2- ;;
  PARAMS) grep -m1 '^PARAMS=' "$file" | cut -d= -f2- | tr ';' '\\n' ;;
esac
""",
            "depmod": """#!/usr/bin/bash
set -euo pipefail
printf '%s\\n' "$*" >> /tmp/mock_state/depmod.log
kver=""
for arg in "$@"; do
  if [[ "$arg" != -* ]]; then kver="$arg"; fi
done
[[ -n "$kver" ]] || kver="$(uname -r)"
if [[ -f /tmp/mock_state/depmod.fail ]] && grep -Fxq "$kver" /tmp/mock_state/depmod.fail; then
  exit 1
fi
kdir="/usr/lib/modules/$kver"
[[ -d "$kdir" ]] || exit 1
: > "$kdir/modules.dep"
: > "$kdir/modules.alias"
for ko in $(find "$kdir" -type f -name '*.ko' | sort); do
  rel="${ko#"$kdir"/}"
  printf '%s:\\n' "$rel" >> "$kdir/modules.dep"
  name="$(modinfo -F name "$ko" 2>/dev/null || true)"
  alias_val="$(modinfo -F alias "$ko" 2>/dev/null || true)"
  if [[ -n "$alias_val" && -n "$name" ]]; then
    printf 'alias %s %s\\n' "$alias_val" "$name" >> "$kdir/modules.alias"
  fi
done
""",
            "modprobe": """#!/usr/bin/bash
set -euo pipefail
printf '%s\\n' "$*" >> /tmp/mock_state/modprobe.log
if [[ -f /tmp/mock_state/modprobe.fail ]]; then
  exit 1
fi
if [[ "${1:-}" == "-R" ]]; then
  if [[ -f /tmp/mock_state/modprobe.alias_extra ]]; then
    cat /tmp/mock_state/modprobe.alias_extra
    exit 0
  fi
  alias_file="/usr/lib/modules/$(uname -r)/modules.alias"
  grep -F "alias $2 " "$alias_file" 2>/dev/null | awk '{print $3}' || true
  exit 0
fi
if [[ "${1:-}" == "acer_wmi" ]]; then
  mkdir -p /sys/module/acer_wmi
  grep -q '^acer_wmi ' /proc/modules || printf 'acer_wmi 45056 0 - Live 0x0\\n' >> /proc/modules
fi
""",
            "insmod": """#!/usr/bin/bash
set -euo pipefail
printf '%s\\n' "$*" >> /tmp/mock_state/insmod.log
mod="$(modinfo -F name "$1")"
mkdir -p "/sys/module/$mod"
grep -q "^$mod " /proc/modules || printf '%s 16384 0 - Live 0x0\\n' "$mod" >> /proc/modules
if [[ "$mod" == "linuwu_sense" ]]; then
  mkdir -p /sys/devices/platform/acer-wmi/four_zoned_kb
  printf '0000ff,0000ff,0000ff,0000ff,100\\n' > /sys/devices/platform/acer-wmi/four_zoned_kb/per_zone_mode
fi
""",
            "rmmod": """#!/usr/bin/bash
set -euo pipefail
printf '%s\\n' "$*" >> /tmp/mock_state/rmmod.log
mod="$1"
if [[ -f /tmp/mock_state/rmmod.fail ]]; then exit 1; fi
rmdir "/sys/module/$mod" 2>/dev/null || rm -rf "/sys/module/$mod"
grep -v "^$mod " /proc/modules > /proc/modules.tmp || true
mv /proc/modules.tmp /proc/modules
""",
            "systemctl": """#!/usr/bin/bash
set -euo pipefail
printf '%s\\n' "$*" >> /tmp/mock_state/systemctl.log
cmd="${1:-}"
shift || true
unit=""
for arg in "$@"; do
  [[ "$arg" == -* ]] || unit="$arg"
done
case "$cmd" in
  daemon-reload)
    if [[ -f /tmp/mock_state/systemctl.daemon-reload.fail ]]; then exit 1; fi
    exit 0 ;;
  is-active)
    [[ -n "$unit" && -f "/tmp/mock_state/$unit.active" ]] && exit 0 || exit 3 ;;
  is-enabled)
    if [[ -n "$unit" && -f "/tmp/mock_state/$unit.enabled" ]]; then
      printf 'enabled\\n'
      exit 0
    fi
    printf 'disabled\\n'
    exit 1 ;;
  enable)
    [[ -n "$unit" ]] && : > "/tmp/mock_state/$unit.enabled"; exit 0 ;;
  disable)
    if [[ -f /tmp/mock_state/systemctl.disable.fail ]]; then exit 1; fi
    [[ -n "$unit" ]] && rm -f -- "/tmp/mock_state/$unit.enabled"; exit 0 ;;
  start)
    /usr/local/libexec/nitro-control-rgb-driver start
    : > "/tmp/mock_state/$unit.active"
    exit 0 ;;
  stop)
    /usr/local/libexec/nitro-control-rgb-driver stop
    rm -f -- "/tmp/mock_state/$unit.active"
    exit 0 ;;
esac
""",
            "mokutil": "#!/usr/bin/bash\nprintf 'SecureBoot disabled\\n'\n",
            "restorecon": "#!/usr/bin/bash\nexit 0\n",
            "lsinitrd": """#!/usr/bin/bash
img="${1:-}"
[[ -f "$img" ]] && cat "$img" || exit 0
""",
        }
        for name, script in mocks.items():
            target = mockbin / name
            target.write_text(script, encoding="utf-8")
            target.chmod(0o755)

    def _run_script(self, root: Path, script_rel: str, *args: str, timeout: int = 20):
        return self._run_bwrap_cmd(root, ["/usr/bin/bash", f"/repo/{script_rel}", *args], timeout=timeout)

    def test_native_clean_install_private_staging_and_uninstall(self):
        root = self._setup_root()
        _make_dummy_ko(root / "src/nitro_rgb_enable_poc.ko", "nitro_rgb_enable_poc", KVER_CUR,
                       params="apply:int;effect_mode:int")
        res = self._run_script(root, "scripts/install-native-rgb-fedora.sh", "/src/nitro_rgb_enable_poc.ko")
        self.assertEqual(res.returncode, 0, res.stderr)

        staged = root / f"usr/local/lib/nitro-control/modules/{KVER_CUR}/nitro_rgb_enable_poc.ko"
        digest = Path(f"{staged}.sha256")
        man = staged.parent / manifest_name(staged.name)
        self.assertTrue(staged.is_file())
        self.assertTrue(digest.is_file())
        self.assertTrue(man.is_file())
        self.assertEqual(digest.read_text().strip(), hashlib.sha256(staged.read_bytes()).hexdigest())
        self.assertFalse((root / f"usr/lib/modules/{KVER_CUR}/extra/nitro-control").exists())
        self.assertTrue((root / "run/nitro-control/native-rgb.lock").is_file())

        host_fake_runner, _ = _fake_modinfo_runner(root)
        ok, reason = NativeRGB(root, KVER_CUR, runner=host_fake_runner).status()
        self.assertTrue(ok, reason)

        # Repeated install & repeated uninstall stay clean and idempotent.
        self.assertEqual(
            self._run_script(root, "scripts/install-native-rgb-fedora.sh", "/src/nitro_rgb_enable_poc.ko").returncode, 0
        )
        res_uninst = self._run_script(root, "scripts/uninstall-native-rgb-fedora.sh")
        self.assertEqual(res_uninst.returncode, 0, res_uninst.stderr)
        self.assertFalse(staged.exists())
        self.assertFalse(digest.exists())
        self.assertFalse(man.exists())
        res_uninst2 = self._run_script(root, "scripts/uninstall-native-rgb-fedora.sh")
        self.assertEqual(res_uninst2.returncode, 0, res_uninst2.stderr)
        self.assertFalse(NativeRGB(root, KVER_CUR).status()[0])

    def test_simultaneous_first_use_shell_initialization_never_replaces_locked_inode(self):
        root = self._setup_root()
        # Neither run/nitro-control nor native-rgb.lock exists yet!
        self.assertFalse((root / "run/nitro-control").exists())
        _make_dummy_ko(root / "src/nitro_rgb_enable_poc.ko", "nitro_rgb_enable_poc", KVER_CUR)

        # Gate the first depmod call while the installer owns the new lock.
        # The contender must finish before the parent releases that gate.
        leg_ko = _make_dummy_ko(
            root / f"usr/lib/modules/{KVER_OLD}/extra/nitro-control/nitro_rgb_enable_poc.ko",
            "nitro_rgb_enable_poc", KVER_OLD,
        )
        _write_sha256(leg_ko)
        _write_manifest(leg_ko, "native-rgb", "nitro_rgb_enable_poc", KVER_OLD, hashlib.sha256(leg_ko.read_bytes()).hexdigest())
        orig_depmod = (root / "mockbin/depmod").read_text()
        (root / "mockbin/depmod").write_text(
            """#!/usr/bin/bash
set -euo pipefail
if [[ ! -e /tmp/mock_state/lock_race.entered ]]; then
    : > /tmp/mock_state/lock_race.entered
    if ! IFS= read -r -t 45 release || [[ "$release" != continue ]]; then
        echo "depmod fixture did not receive the release signal" >&2
        exit 1
    fi
fi
""" + orig_depmod.split("\n", 1)[1],
            encoding="utf-8",
        )
        (root / "mockbin/depmod").chmod(0o755)

        installer = ["/usr/bin/bash", "/repo/scripts/install-native-rgb-fedora.sh", "/src/nitro_rgb_enable_poc.ko"]
        cmd, run_env = self._bwrap_invocation(root, installer)
        p1 = subprocess.Popen(cmd, env=run_env, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            ready = root / "tmp/mock_state/lock_race.entered"
            deadline = time.monotonic() + 15
            while not ready.exists():
                if p1.poll() is not None or time.monotonic() >= deadline:
                    if p1.poll() is None:
                        p1.kill()
                    out, err = p1.communicate(timeout=5)
                    self.fail(f"First installer never reached the depmod gate (exit {p1.returncode}).\n"
                              f"stdout:\n{out}\nstderr:\n{err}")
                time.sleep(0.02)

            lock_file = root / "run/nitro-control/native-rgb.lock"
            self.assertTrue(lock_file.is_file())
            ino_during_p1 = lock_file.stat().st_ino

            p2 = self._run_bwrap_cmd(root, installer)
            self.assertNotEqual(p2.returncode, 0, p2.stdout + p2.stderr)
            self.assertIn("Another native RGB operation is in progress", p2.stderr, p2.stdout + p2.stderr)
            self.assertIsNone(p1.poll(), "First installer exited before the release signal")
            self.assertEqual(lock_file.stat().st_ino, ino_during_p1)

            p1_out, p1_err = p1.communicate(input="continue\n", timeout=20)
            self.assertEqual(p1.returncode, 0, p1_err + p1_out)
            self.assertEqual(lock_file.stat().st_ino, ino_during_p1)
        finally:
            if p1.poll() is None:
                p1.kill()
            p1.communicate(timeout=5)

    def test_legacy_driver_migration_requires_adopt_legacy_then_clears_aliases_and_preserves_foreign(self):
        root = self._setup_root()
        for kver in (KVER_CUR, KVER_OLD):
            leg_dir = root / f"usr/lib/modules/{kver}/extra/nitro-control"
            leg_ko = _make_dummy_ko(leg_dir / "linuwu_sense.ko", "linuwu_sense", kver, alias=WMI_ALIAS)
            _write_sha256(leg_ko)
            self._run_bwrap_cmd(root, ["/mockbin/depmod", "-a", kver])
            self.assertIn("linuwu_sense", (root / f"usr/lib/modules/{kver}/modules.alias").read_text())

        foreign_note = root / f"usr/lib/modules/{KVER_OLD}/extra/nitro-control/admin-notes.txt"
        foreign_note.write_text("keep me\n", encoding="utf-8")
        foreign_ko = root / f"usr/lib/modules/{KVER_OLD}/extra/vendor/custom.ko"
        _make_dummy_ko(foreign_ko, "custom_vendor", KVER_OLD)
        (root / "tmp/mock_state/nitro-control-rgb-driver.service.enabled").write_text("", encoding="utf-8")

        _make_dummy_ko(root / "src/linuwu_sense.ko", "linuwu_sense", KVER_CUR, alias=WMI_ALIAS)
        # First attempt WITHOUT --adopt-legacy fails closed and preserves unmanifested legacy files!
        res_no_adopt = self._run_script(root, "scripts/install-rgb-driver-fedora.sh", "/src/linuwu_sense.ko")
        self.assertNotEqual(res_no_adopt.returncode, 0)
        self.assertIn("adopt-legacy", res_no_adopt.stderr)
        self.assertTrue((root / f"usr/lib/modules/{KVER_OLD}/extra/nitro-control/linuwu_sense.ko").is_file())

        # Second attempt WITH --adopt-legacy migrates both kernels cleanly and clears WMI alias!
        res = self._run_script(root, "scripts/install-rgb-driver-fedora.sh", "--adopt-legacy", "/src/linuwu_sense.ko")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("previously enabled", res.stderr)

        for kver in (KVER_CUR, KVER_OLD):
            priv = root / f"usr/local/lib/nitro-control/modules/{kver}/linuwu_sense.ko"
            self.assertTrue(priv.is_file())
            self.assertTrue((priv.parent / manifest_name(priv.name)).is_file())
            self.assertFalse((root / f"usr/lib/modules/{kver}/extra/nitro-control/linuwu_sense.ko").exists())
            self.assertNotIn("linuwu_sense", (root / f"usr/lib/modules/{kver}/modules.alias").read_text())
            self.assertIn("acer_wmi", (root / f"usr/lib/modules/{kver}/modules.alias").read_text())

        self.assertEqual(foreign_note.read_text(), "keep me\n")
        self.assertTrue(foreign_ko.is_file())
        self.assertFalse((root / "tmp/mock_state/nitro-control-rgb-driver.service.enabled").exists())
        chk = self._run_bwrap_cmd(root, ["/usr/local/libexec/nitro-control-rgb-driver", "check"])
        self.assertEqual(chk.returncode, 0, chk.stderr)

    def test_false_autoload_clearance_prevented_on_disable_initramfs_and_modprobe_failures(self):
        root = self._setup_root()
        _make_dummy_ko(root / "src/linuwu_sense.ko", "linuwu_sense", KVER_CUR, alias=WMI_ALIAS)

        # Case 1: systemctl disable failure must fail install rather than falsely reporting DISABLED.
        (root / "tmp/mock_state/systemctl.disable.fail").write_text("1", encoding="utf-8")
        res_dis = self._run_script(root, "scripts/install-rgb-driver-fedora.sh", "/src/linuwu_sense.ko")
        self.assertNotEqual(res_dis.returncode, 0)
        self.assertNotIn("Boot autoload verified DISABLED", res_dis.stdout)
        (root / "tmp/mock_state/systemctl.disable.fail").unlink()

        # Install cleanly first, then simulate residual initramfs copy on stale legacy uninstall.
        self.assertEqual(
            self._run_script(root, "scripts/install-rgb-driver-fedora.sh", "/src/linuwu_sense.ko").returncode, 0
        )
        (root / f"usr/lib/modules/{KVER_OLD}/modules.dep").write_text("extra/nitro-control/linuwu_sense.ko:\n")
        img = root / f"boot/initramfs-{KVER_OLD}.img"
        img.write_text("extra/nitro-control/linuwu_sense.ko\n", encoding="utf-8")
        res_initramfs = self._run_script(root, "scripts/uninstall-rgb-driver-fedora.sh")
        self.assertNotEqual(res_initramfs.returncode, 0)
        self.assertIn("RESIDUAL_INITRAMFS_EXPOSURE", res_initramfs.stderr)
        # Must NOT modify host/fixture initramfs automatically.
        self.assertEqual(img.read_text(), "extra/nitro-control/linuwu_sense.ko\n")
        img.unlink()

        # Re-install and test residual modprobe alias resolution failure.
        self.assertEqual(
            self._run_script(root, "scripts/install-rgb-driver-fedora.sh", "/src/linuwu_sense.ko").returncode, 0
        )
        (root / f"usr/lib/modules/{KVER_CUR}/modules.dep").write_text("extra/nitro-control/linuwu_sense.ko:\n")
        (root / "tmp/mock_state/modprobe.alias_extra").write_text("acer_wmi\nlinuwu_sense\n", encoding="utf-8")
        res_alias = self._run_script(root, "scripts/uninstall-rgb-driver-fedora.sh")
        self.assertNotEqual(res_alias.returncode, 0)
        self.assertIn("still resolves to linuwu_sense", res_alias.stderr)

    def test_controller_independent_provenance_stop_and_depmod_failure(self):
        root = self._setup_root()
        _make_dummy_ko(root / "src/linuwu_sense.ko", "linuwu_sense", KVER_CUR, alias=WMI_ALIAS)
        res = self._run_script(root, "scripts/install-rgb-driver-fedora.sh", "/src/linuwu_sense.ko")
        self.assertEqual(res.returncode, 0, res.stderr)

        # Start legacy service cleanly, then uninstall -> stops unit & unloads cleanly without deadlock.
        start_res = self._run_bwrap_cmd(root, ["/mockbin/systemctl", "start", "nitro-control-rgb-driver.service"])
        self.assertEqual(start_res.returncode, 0, start_res.stderr)
        self.assertIn("linuwu_sense ", (root / "proc/modules").read_text())

        # Remove Nitro marker from controller independently of service unit -> uninstall must refuse!
        ctrl = root / "usr/local/libexec/nitro-control-rgb-driver"
        orig_ctrl = ctrl.read_text()
        ctrl.write_text("#!/usr/bin/bash\n# Foreign custom script\necho custom\n", encoding="utf-8")
        ctrl.chmod(0o755)
        uninst_foreign_ctrl = self._run_script(root, "scripts/uninstall-rgb-driver-fedora.sh")
        self.assertNotEqual(uninst_foreign_ctrl.returncode, 0)
        self.assertIn("ownership marker", uninst_foreign_ctrl.stderr)
        self.assertTrue(ctrl.is_file())

        # Restore controller and uninstall -> succeeds cleanly.
        ctrl.write_text(orig_ctrl, encoding="utf-8")
        ctrl.chmod(0o755)
        uninst = self._run_script(root, "scripts/uninstall-rgb-driver-fedora.sh")
        self.assertEqual(uninst.returncode, 0, uninst.stderr)
        self.assertFalse((root / "usr/local/libexec/nitro-control-rgb-driver").exists())
        self.assertFalse((root / "etc/systemd/system/nitro-control-rgb-driver.service").exists())

        # Depmod failure fails non-zero rather than claiming clean uninstall.
        (root / f"usr/lib/modules/{KVER_OLD}/modules.dep").write_text("extra/nitro-control/linuwu_sense.ko:\n")
        (root / "tmp/mock_state/depmod.fail").write_text(f"{KVER_OLD}\n")
        res_dep_fail = self._run_script(root, "scripts/uninstall-rgb-driver-fedora.sh")
        self.assertNotEqual(res_dep_fail.returncode, 0)
        self.assertIn("depmod -a", res_dep_fail.stderr)

    def test_cross_backend_lock_and_coexisting_legacy_controller_preflight(self):
        root = self._setup_root()
        _make_dummy_ko(root / "src/linuwu_sense.ko", "linuwu_sense", KVER_CUR, alias=WMI_ALIAS)
        self.assertEqual(
            self._run_script(root, "scripts/install-rgb-driver-fedora.sh", "/src/linuwu_sense.ko").returncode, 0
        )

        # Coexisting legacy file causes controller preflight check to fail closed!
        leg = _make_dummy_ko(
            root / f"usr/lib/modules/{KVER_CUR}/extra/nitro-control/linuwu_sense.ko",
            "linuwu_sense", KVER_CUR,
        )
        _write_sha256(leg)
        chk_coexist = self._run_bwrap_cmd(root, ["/usr/local/libexec/nitro-control-rgb-driver", "check"])
        self.assertNotEqual(chk_coexist.returncode, 0)
        self.assertIn("Unresolved legacy", chk_coexist.stderr)
        leg.unlink()
        Path(f"{leg}.sha256").unlink()

        # Holding native-rgb.lock from Python blocks rgb-driver service start cleanly.
        lock_dir = root / "run/nitro-control"
        lock_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        fd = os.open(lock_dir / "native-rgb.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        res_drv_busy = self._run_bwrap_cmd(root, ["/usr/local/libexec/nitro-control-rgb-driver", "start"])
        self.assertNotEqual(res_drv_busy.returncode, 0)
        self.assertIn("active", res_drv_busy.stderr)

    def test_h_p2_r03_lock_lifetime_exec_and_subprocess_and_no_unlocked_actions(self):
        root = self._setup_root()
        _make_dummy_ko(root / "src/linuwu.ko", "linuwu_sense", KVER_CUR, alias=WMI_ALIAS, params="nitro_v4=1")
        _make_dummy_ko(
            root / f"usr/lib/modules/{KVER_OLD}/extra/linuwu_sense.ko",
            "linuwu_sense", KVER_OLD, alias=WMI_ALIAS, params="nitro_v4=1",
        )
        res = self._run_bwrap_cmd(root, [
            "/usr/bin/python3", "-I", "/repo/nitro_control/module_staging.py",
            "install", "--component", "rgb-driver",
            "--kernel", KVER_CUR, "--source", "/src/linuwu.ko",
            "--controller-src", "/repo/scripts/rgb-driver-service-fedora.sh",
            "--unit-src", "/repo/scripts/nitro-control-rgb-driver.service",
        ])
        self.assertEqual(res.returncode, 0, msg=res.stderr)

        # Direct internal unlocked subcommands MUST NOT be accepted on CLI.
        for bad_action in ("__locked_start", "__locked_stop", "locked_start", "restore"):
            res_bad = self._run_bwrap_cmd(root, ["/usr/local/libexec/nitro-control-rgb-driver", bad_action])
            self.assertEqual(res_bad.returncode, 2, msg=f"{bad_action}: {res_bad.stderr}")
            self.assertIn("Usage:", res_bad.stderr)

        # lock-and-exec holds lock directly via os.execv in target process until target exits.
        ready_flag = root / "tmp/exec_ready"
        cmd_exec = [
            "bwrap", "--unshare-user", "--uid", "0", "--gid", "0",
            "--bind", str(root), "/", "--dev", "/dev",
            "--ro-bind", str(REPO), "/repo",
            "--ro-bind", "/usr/bin", "/usr/bin",
            "--ro-bind", "/usr/sbin", "/usr/sbin",
        ]
        if Path("/usr/lib64").is_dir():
            cmd_exec.extend(["--ro-bind", "/usr/lib64", "/usr/lib64", "--symlink", "usr/lib64", "/lib64"])
        for extra_lib in ("/usr/lib/x86_64-linux-gnu", *sorted(Path("/usr/lib").glob("python3*"))):
            p = Path(extra_lib)
            if p.is_dir():
                cmd_exec.extend(["--ro-bind", str(p), str(p)])
        cmd_exec.extend(["--symlink", "usr/bin", "/bin", "--symlink", "usr/sbin", "/sbin"])
        cmd_exec.extend([
            "/usr/bin/python3", "-I", "/repo/nitro_control/module_staging.py",
            "lock-and-exec", "--lock", "native-rgb.lock",
            "--busy-message", "busy",
            "--", "/usr/bin/bash", "-c", "touch /tmp/exec_ready; sleep 1.0",
        ])
        proc_exec = subprocess.Popen(cmd_exec, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: proc_exec.kill() if proc_exec.poll() is None else None)
        deadline = time.monotonic() + 5.0
        while not ready_flag.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(ready_flag.exists())

        lock_fd = os.open(root / "run/nitro-control/native-rgb.lock", os.O_RDWR | os.O_NOFOLLOW)
        self.addCleanup(os.close, lock_fd)
        with self.assertRaises(BlockingIOError):
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _, err_exec = proc_exec.communicate(timeout=10)
        self.assertEqual(proc_exec.returncode, 0, msg=err_exec)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(lock_fd, fcntl.LOCK_UN)

        # run-locked forwards SIGTERM to child process group and keeps lock held until child exits.
        res_sig = self._run_bwrap_cmd(root, [
            "/usr/bin/bash", "-c",
            (
                "set -euo pipefail; "
                "python3 -I /repo/nitro_control/module_staging.py "
                "run-locked --lock rgb-driver.lock --busy-message busy -- "
                "/usr/bin/bash -c 'trap \"sleep 0.4; touch /tmp/child_done; exit 0\" TERM; touch /tmp/child_ready; while true; do sleep 0.05; done' & "
                "lp=$!; "
                "for _ in $(seq 1 100); do [ -f /tmp/child_ready ] && break; sleep 0.02; done; "
                "[ -f /tmp/child_ready ]; "
                "kill -TERM \"$lp\"; "
                "sleep 0.1; "
                "if python3 -I -c 'import fcntl, os; fd=os.open(\"/run/nitro-control/rgb-driver.lock\", os.O_RDWR); fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)'; then exit 99; fi; "
                "rc=0; wait \"$lp\" || rc=$?; "
                "[ -f /tmp/child_done ]; "
                "[ \"$rc\" -eq 143 ]; "
                "python3 -I -c 'import fcntl, os; fd=os.open(\"/run/nitro-control/rgb-driver.lock\", os.O_RDWR); fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB); fcntl.flock(fd, fcntl.LOCK_UN)'"
            ),
        ])
        self.assertEqual(res_sig.returncode, 0, msg=f"stdout={res_sig.stdout}\nstderr={res_sig.stderr}")

    def test_experimental_diagnostic_native_build_refused_by_installer_and_staging(self):
        root = self._setup_root()
        for bad_param in ("backlight_test:bool", "red_test:bool", "enable:bool", "effect_test:bool"):
            with self.subTest(bad_param=bad_param):
                _make_dummy_ko(
                    root / "src/nitro_rgb_enable_poc.ko", "nitro_rgb_enable_poc", KVER_CUR,
                    params=f"apply:bool;{bad_param}",
                )
                res_script = self._run_script(root, "scripts/install-native-rgb-fedora.sh", "/src/nitro_rgb_enable_poc.ko")
                self.assertNotEqual(res_script.returncode, 0)
                self.assertIn("experimental diagnostic build", res_script.stderr.lower())
                res_cli = self._run_bwrap_cmd(root, [
                    "/usr/bin/python3", "-I", "/repo/nitro_control/module_staging.py",
                    "install", "--component", "native-rgb",
                    "--kernel", KVER_CUR, "--source", "/src/nitro_rgb_enable_poc.ko",
                ])
                self.assertNotEqual(res_cli.returncode, 0)
                self.assertIn("experimental diagnostic parameters", res_cli.stderr.lower())

    def test_rgb_helper_shell_install_upgrade_adopt_legacy_and_uninstall_in_bwrap(self):
        root = self._setup_root()
        unrelated_note = root / "usr/local/libexec/nitro-control-rgb/unrelated.txt"
        unrelated_pol = root / "usr/share/polkit-1/actions/unrelated.policy"

        # 1. Fresh install using real shell script inside bwrap.
        res_inst = self._run_script(root, "scripts/install-rgb-helper-fedora.sh")
        self.assertEqual(res_inst.returncode, 0, res_inst.stderr)
        helper = root / "usr/local/libexec/nitro-control-rgb-helper"
        policy = root / "usr/share/polkit-1/actions/io.github.tobiasguta.NitroControl.rgb.policy"
        manifest = root / "usr/local/libexec/nitro-control-rgb" / RGB_HELPER_MANIFEST
        self.assertTrue(helper.is_file() and manifest.is_file() and policy.is_file())
        self.assertIn("#!/usr/bin/python3 -IB", helper.read_text(encoding="utf-8"))
        self.assertIn("<allow_active>auth_admin</allow_active>", policy.read_text(encoding="utf-8"))

        # Verify installed helper acquires rgb-helper.lock BEFORE importing and fails cleanly when locked or interrupted.
        with acquire_private_locks(root, ("rgb-helper.lock",), "busy"):
            lock_deny = self._run_bwrap_cmd(root, [
                "/usr/local/libexec/nitro-control-rgb-helper",
            ])
        self.assertEqual(lock_deny.returncode, 1)
        self.assertFalse(json.loads(lock_deny.stdout)["ok"])
        self.assertIn("active", json.loads(lock_deny.stdout)["error"].lower())

        # Interrupted marker blocks live installed helper execution before touching hardware!
        tx_marker = root / "usr/local/libexec/nitro-control-rgb/.tx.rgb-helper.json"
        tx_marker.write_text('{"managed_by":"nitro-control","component":"rgb-helper","schema_version":1,"state":"installing","known_files":{},"target_files":{}}', encoding="utf-8")
        res_int = self._run_bwrap_cmd(root, [
            "/usr/local/libexec/nitro-control-rgb-helper",
        ])
        self.assertEqual(res_int.returncode, 1)
        self.assertIn("interrupted", json.loads(res_int.stdout)["error"].lower())
        tx_marker.unlink()

        # Verify installed helper answers --capabilities read-only inside bwrap and matches RGBClient.
        cap_out = self._run_bwrap_cmd(root, [str(Path("/") / "usr/local/libexec/nitro-control-rgb-helper"), "--capabilities"])
        self.assertEqual(cap_out.returncode, 0, cap_out.stderr)
        cap_payload = json.loads(cap_out.stdout)
        self.assertEqual(cap_payload["capabilities"], ["static", "effect"])
        self.assertEqual(cap_payload["scope"], "protocol-only")
        self.assertFalse(cap_payload["installation_verified"])
        with patch("nitro_control.rgb_client.shutil.which", return_value="/usr/bin/pkexec"):
            self.assertTrue(RGBClient(helper).capabilities().effect_supported)

        # H-P4-R04 & H-P4-R05: retained same-size poisoned .pyc in __pycache__ is ignored by launcher
        # (sys.implementation.cache_tag = None), AND missing/corrupt helper dependencies fail closed
        # with structured JSON diagnostics before importing nitro_control.rgb_privileged.
        pkg_dir = root / "usr/local/libexec/nitro-control-rgb/nitro_control"
        priv_path = pkg_dir / "rgb_privileged.py"
        priv_bytes = priv_path.read_bytes()
        poison_head = (
            'import json\n'
            'def main(argv=None, *, _lock_held=False):\n'
            '    print(json.dumps({"ok": True, "poisoned": True, "capabilities": ["static"]}))\n'
            '    return 0\n'
        ).encode("utf-8")
        poison_pad = poison_head + b"# " + (b"p" * max(0, len(priv_bytes) - len(poison_head) - 3)) + b"\n"
        self.assertEqual(len(poison_pad), len(priv_bytes))
        pycache = pkg_dir / "__pycache__"
        pycache.mkdir(exist_ok=True)
        poison_src = root / "run/poison_priv.py"
        poison_src.write_bytes(poison_pad)
        poison_pyc = pycache / f"rgb_privileged.{sys.implementation.cache_tag}.pyc"
        py_compile.compile(
            str(poison_src),
            cfile=str(poison_pyc),
            dfile="/usr/local/libexec/nitro-control-rgb/nitro_control/rgb_privileged.py",
            doraise=True,
            invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP,
        )
        patched_pyc = bytearray(poison_pyc.read_bytes())
        patched_pyc[8:12] = int(priv_path.stat().st_mtime).to_bytes(4, "little")
        poison_pyc.write_bytes(bytes(patched_pyc))

        cap_clean = self._run_bwrap_cmd(root, [str(Path("/") / "usr/local/libexec/nitro-control-rgb-helper"), "--capabilities"])
        self.assertEqual(cap_clean.returncode, 0, cap_clean.stderr)
        cap_clean_obj = json.loads(cap_clean.stdout)
        self.assertNotIn("poisoned", cap_clean_obj)
        self.assertEqual(cap_clean_obj["capabilities"], ["static", "effect"])

        hw_path = pkg_dir / "rgb_hardware.py"
        hw_backup = hw_path.read_bytes()
        hw_path.unlink()
        res_missing = self._run_bwrap_cmd(root, [str(Path("/") / "usr/local/libexec/nitro-control-rgb-helper")])
        self.assertEqual(res_missing.returncode, 1)
        self.assertEqual(res_missing.stderr.strip(), "")
        missing_obj = json.loads(res_missing.stdout)
        self.assertFalse(missing_obj["ok"])
        self.assertIn("incomplete", missing_obj["error"].lower())

        cap_missing = self._run_bwrap_cmd(root, [str(Path("/") / "usr/local/libexec/nitro-control-rgb-helper"), "--capabilities"])
        self.assertEqual(cap_missing.returncode, 1)
        self.assertEqual(cap_missing.stderr.strip(), "")
        self.assertFalse(json.loads(cap_missing.stdout)["ok"])

        hw_path.write_text("def corrupted_syntax(:\n", encoding="utf-8")
        res_corrupt = self._run_bwrap_cmd(root, [str(Path("/") / "usr/local/libexec/nitro-control-rgb-helper")])
        self.assertEqual(res_corrupt.returncode, 1)
        self.assertEqual(res_corrupt.stderr.strip(), "")
        corrupt_obj = json.loads(res_corrupt.stdout)
        self.assertFalse(corrupt_obj["ok"])
        self.assertIn("integrity mismatch", corrupt_obj["error"].lower())

        # H-P4-R06: replacing a helper dependency with a writerless FIFO must fail non-blocking (O_NONBLOCK)
        # well inside a bounded deadline rather than hanging on open().
        hw_path.unlink()
        os.mkfifo(hw_path, 0o644)
        res_fifo = self._run_bwrap_cmd(
            root,
            [str(Path("/") / "usr/local/libexec/nitro-control-rgb-helper")],
            timeout=2,
        )
        self.assertEqual(res_fifo.returncode, 1)
        self.assertEqual(res_fifo.stderr.strip(), "")
        fifo_obj = json.loads(res_fifo.stdout)
        self.assertFalse(fifo_obj["ok"])
        self.assertIn("not a regular file", fifo_obj["error"].lower())
        hw_path.unlink()

        hw_path.write_bytes(hw_backup)
        poison_pyc.unlink()
        poison_src.unlink()

        # Unrelated admin files remain untouched across upgrade & uninstall.
        unrelated_note.write_text("keep\n", encoding="utf-8")
        unrelated_pol.write_text("<policyconfig/>\n", encoding="utf-8")
        res_up = self._run_script(root, "scripts/install-rgb-helper-fedora.sh", "--cache-authorization")
        self.assertEqual(res_up.returncode, 0, res_up.stderr)
        self.assertIn("<allow_active>auth_admin_keep</allow_active>", policy.read_text(encoding="utf-8"))

        res_un = self._run_script(root, "scripts/uninstall-rgb-helper-fedora.sh")
        self.assertEqual(res_un.returncode, 0, res_un.stderr)
        self.assertFalse(helper.exists())
        self.assertFalse(policy.exists())
        self.assertFalse(manifest.exists())
        self.assertEqual(unrelated_note.read_text(encoding="utf-8"), "keep\n")
        self.assertEqual(unrelated_pol.read_text(encoding="utf-8"), "<policyconfig/>\n")

        # 2. Pre-manifest preview.4 helper requires --adopt-legacy to uninstall/upgrade safely.
        helper.write_text(
            '#!/usr/bin/python3 -I\nimport sys\nsys.path.insert(0, "/usr/local/libexec/nitro-control-rgb")\n'
            "from nitro_control.rgb_privileged import main\nraise SystemExit(main())\n",
            encoding="utf-8",
        )
        res_refuse = self._run_script(root, "scripts/uninstall-rgb-helper-fedora.sh")
        self.assertNotEqual(res_refuse.returncode, 0)
        self.assertIn("adopt-legacy", res_refuse.stderr)
        res_adopt_un = self._run_script(root, "scripts/uninstall-rgb-helper-fedora.sh", "--adopt-legacy")
        self.assertEqual(res_adopt_un.returncode, 0, res_adopt_un.stderr)
        self.assertFalse(helper.exists())


if __name__ == "__main__":
    unittest.main()

"""Per-user deployment regressions using only temporary homes and toy packages."""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, current_thread
from unittest.mock import patch

from test_gtk_lifecycle import BUS_CONFIG

from scripts import gui_deploy

ROOT = Path(__file__).resolve().parents[1]
ACTIONS = (
    "install-fedora.sh", "uninstall.sh", "enable-tray-autostart-fedora.sh",
    "disable-tray-autostart-fedora.sh", "smoke-test-gui-install-rollback-fedora.sh",
)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.home = self.directory / 'Å home "quote" = %f \\ char: $HOME;'
        self.home.mkdir()
        self.config = self.home / "config"
        self.observations = self.home / "launch.jsonl"
        self.env = dict(os.environ, HOME=str(self.home), XDG_CONFIG_HOME=str(self.config),
                        XDG_DATA_HOME=str(self.home / ".local/share"),
                        XDG_CACHE_HOME=str(self.home / "cache"),
                        NITRO_TEST_OBSERVATIONS=str(self.observations), GIO_USE_VFS="local")
        self.installation = gui_deploy.Deployment(self.home, self.config)
        self.which = shutil.which
        # Simulate systems without optional cache utilities.
        helper = patch.object(gui_deploy.shutil, "which", return_value=None)
        helper.start()
        self.addCleanup(helper.stop)

    def source(self, version):
        tree = self.directory / version / "nitro_control"
        tree.mkdir(parents=True)
        (tree / "__init__.py").write_text(f"__version__ = {version!r}\n", encoding="utf-8")
        (tree / "__main__.py").write_text(
            "import json,os,sys\nfrom . import __version__\n"
            "if os.environ.get('NITRO_TEST_RECORD'):\n"
            "    with open(os.environ['NITRO_TEST_OBSERVATIONS'], 'a') as output:\n"
            "        output.write(json.dumps([__version__,sys.argv[1:]]) + '\\n')\n"
            "else:\n"
            "    print(__version__)\n", encoding="utf-8",
        )
        for module in gui_deploy.ESSENTIAL_MODULES - {"__init__", "__main__"}:
            (tree / f"{module}.py").touch()
        return tree.parent

    def command(self, script, *args):
        return subprocess.run(["bash", str(ROOT / "scripts" / script), *args],
                              env=self.env, capture_output=True, text=True, timeout=20, check=False)

    def version(self, *, cwd=None):
        return subprocess.check_output([self.installation.launcher, "--version"],
                                       cwd=cwd, env=self.env, text=True, timeout=10).strip()

    def test_help_version_and_unsupported_arguments_never_mutate_home(self):
        for script in ACTIONS:
            for args, status in ((("--help",), 0), (("--version",), 0), (("--bogus",), 2),
                                 (("--help", "--bogus"), 2), (("--version", "--bogus"), 2)):
                with self.subTest(script=script, args=args):
                    result = self.command(script, *args)
                    self.assertEqual(result.returncode, status, result.stderr)
                    self.assertEqual(list(self.home.iterdir()), [])

    def test_upgrade_interruption_retry_reinstall_and_rollback(self):
        old, new = self.source("fixture-old"), self.source("fixture-new")
        (old / "nitro_control/obsolete.py").write_text("OLD = True\n")
        cache = new / "nitro_control/__pycache__"
        cache.mkdir()
        (cache / "__main__.pyc").write_bytes(b"unusable cache")
        instal = self.installation
        instal.install(old)
        self.assertFalse(instal.tray.exists())
        first = instal.current.readlink()
        self.env["PYTHONPATH"] = str(old)
        self.assertEqual(self.version(cwd=old), "fixture-old")
        publish = gui_deploy._link

        def interrupted(path, target):
            if path == instal.current:
                raise OSError("simulated interruption at release activation")
            publish(path, target)

        with patch.object(gui_deploy, "_link", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "interruption"):
                instal.install(new)
        self.assertEqual(instal.current.readlink(), first)
        self.assertEqual(self.version(cwd=new), "fixture-old")

        instal.install(new)
        self.assertEqual(self.version(cwd=old), "fixture-new")
        self.assertFalse((instal.current / "nitro_control/obsolete.py").exists())
        self.assertFalse((instal.current / "nitro_control/__pycache__").exists())
        active = instal.current.readlink()
        instal.install(new)
        self.assertEqual(instal.current.readlink(), active)
        self.assertEqual(len(list(instal.store.glob("release-*"))), 2)
        instal.install(old)
        self.assertEqual(instal.current.readlink(), first)
        self.assertEqual(self.version(cwd=new), "fixture-old")

    def test_incomplete_first_publication_can_be_retried(self):
        source = self.source("fixture")
        instal = self.installation
        publish = gui_deploy._link

        def interrupted(path, target):
            if path == instal.launcher:
                raise OSError("simulated interruption before launcher publication")
            publish(path, target)

        with patch.object(gui_deploy, "_link", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "interruption"):
                instal.install(source)
        self.assertTrue((instal.current / "nitro_control/__main__.py").is_file())
        self.assertFalse(instal.launcher.exists())

        instal.install(source)
        self.assertEqual(self.version(), "fixture")
        self.assertEqual(len(list(instal.store.glob("release-*"))), 1)

    def test_install_rejects_collisions_and_managed_release_edits(self):
        instal = self.installation
        source = self.source("fixture")
        instal.launcher.parent.mkdir(parents=True)
        instal.launcher.write_text("mine\n")
        with self.assertRaisesRegex(ValueError, "unrecognized artifact"):
            instal.install(source)
        self.assertEqual(instal.launcher.read_text(), "mine\n")
        self.assertFalse(instal.current.exists())
        instal.launcher.unlink()
        instal.install(source)
        path = instal.current / "nitro_control/__init__.py"
        path.write_text("# locally modified\n")
        with self.assertRaisesRegex(ValueError, "Modified or missing"):
            instal.install(source)
        self.assertEqual(path.read_text(), "# locally modified\n")

    def test_uninstall_preserves_unrelated_edits_symlinks_and_preferences(self):
        instal = self.installation
        instal.install(self.source("fixture"))
        instal.enable_tray()
        self.assertTrue(instal.tray.is_file())
        release = instal.current.resolve()
        notes = release / "notes.txt"
        notes.write_text("user data")
        changed = release / "nitro_control/__init__.py"
        changed.write_text("# changed")
        outside = self.directory / "outside"
        outside.mkdir()
        desktop = self.home / ".local/share/applications" / gui_deploy.LIVE
        desktop.unlink()
        desktop.symlink_to(outside / "local.desktop")
        settings = self.config / "nitro-control/tray-preferences.json"
        settings.parent.mkdir(parents=True)
        settings.write_text('{"keep_in_tray":true}')
        for _ in range(2):
            instal.uninstall()
            self.assertFalse(instal.tray.exists())
            self.assertFalse(instal.launcher.exists())
            self.assertTrue(desktop.is_symlink())
            self.assertEqual(changed.read_text(), "# changed")
            self.assertEqual(notes.read_text(), "user data")
            self.assertEqual(json.loads(settings.read_text()), {"keep_in_tray": True})

    def test_legacy_upgrade_preserves_the_pre_manifest_package_and_custom_autostart(self):
        instal = self.installation
        instal.launcher.parent.mkdir(parents=True)
        instal.launcher.write_bytes(gui_deploy.LEGACY_LAUNCHER)
        instal.launcher.chmod(0o755)
        legacy = instal.app / "nitro_control"
        legacy.mkdir(parents=True)
        legacy_file = legacy / "local-custom.py"
        legacy_file.write_text("local\n")
        instal.install(self.source("fixture"))
        self.assertEqual(self.version(), "fixture")
        instal.tray.parent.mkdir(parents=True)
        own = gui_deploy._desktop("tray", f'"{instal.launcher}" --tray')
        edited = own + b"# local change\n"
        instal.tray.write_bytes(edited)
        with self.assertRaisesRegex(ValueError, "unrecognized autostart"):
            instal.enable_tray()
        self.assertFalse(instal.disable_tray())
        instal.uninstall()
        self.assertEqual(instal.tray.read_bytes(), edited)
        self.assertEqual(legacy_file.read_text(), "local\n")

    def test_disable_removes_a_legacy_entry_in_default_config_with_xdg_override(self):
        # The legacy script always used ~/.config, regardless of XDG_CONFIG_HOME.
        home = self.directory / "legacy-home"
        instal = gui_deploy.Deployment(home, home / "config")
        target = home / ".config/autostart" / gui_deploy.TRAY
        target.parent.mkdir(parents=True)
        target.write_bytes(gui_deploy._desktop("tray", f'"{instal.launcher}" --tray'))
        target.chmod(0o644)
        self.assertTrue(instal.disable_tray())
        self.assertFalse(target.exists())
        self.assertFalse(instal.tray.exists())

    def test_autostart_requires_a_recognized_coherent_installation(self):
        instal = self.installation
        instal.launcher.parent.mkdir(parents=True)
        instal.launcher.write_text("#!/bin/sh\nexit 99\n")
        instal.launcher.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "Unrecognized tray launcher"):
            instal.enable_tray()
        self.assertFalse(instal.tray.exists())
        self.assertFalse(instal.store.exists())

        instal.launcher.write_bytes(gui_deploy.LEGACY_LAUNCHER)
        with self.assertRaisesRegex(ValueError, "Source package"):
            instal.enable_tray()
        source = self.source("fixture")
        shutil.copytree(source / "nitro_control", instal.app / "nitro_control")
        instal.enable_tray()
        self.assertTrue(instal.tray.is_file())
        instal.disable_tray()
        instal.install(source)
        instal.enable_tray()
        self.assertTrue(instal.tray.is_file())
        instal.disable_tray()

        hardware = instal.current / "nitro_control/hardware.py"
        original = hardware.read_bytes()
        hardware.unlink()
        with self.assertRaisesRegex(ValueError, "Modified or missing"):
            instal.enable_tray()
        self.assertFalse(instal.tray.exists())
        hardware.write_bytes(original)

        launcher = instal.current / "nitro-control"
        launcher.write_bytes(launcher.read_bytes() + b"# local edit\n")
        with self.assertRaisesRegex(ValueError, "Modified or missing"):
            instal.enable_tray()
        self.assertFalse(instal.tray.exists())

    def test_autostart_and_deployment_operations_use_one_lock(self):
        for first, gate_method, second, entry_expected in (
            ("enable", "_enable_tray_locked", "uninstall", False),
            ("install", "_publication", "disable", False),
            ("disable", "_disable_tray_locked", "enable", True),
        ):
            with self.subTest(first=first, second=second):
                self._check_serialized_operations(first, gate_method, second, entry_expected)

    def _check_serialized_operations(self, first, gate_method, second, entry_expected):
        home = self.directory / first
        home.mkdir()
        instal = gui_deploy.Deployment(home, home / "config")
        source = self.source(f"fixture-{first}")
        instal.install(source)
        if first != "enable":
            instal.enable_tray()
        operations = {"enable": instal.enable_tray, "disable": instal.disable_tray,
                      "install": lambda: instal.install(source), "uninstall": instal.uninstall}
        paused, resume, blocked = Event(), Event(), Event()
        original = getattr(instal, gate_method)
        flock = fcntl.flock

        def gate(*args, **kwargs):
            paused.set()
            if not resume.wait(4):
                raise RuntimeError("test did not release first operation")
            return original(*args, **kwargs)

        def observe_lock(fd, flags):
            if current_thread().name.startswith("second"):
                try:
                    flock(fd, flags | fcntl.LOCK_NB)
                except BlockingIOError:
                    blocked.set()
            flock(fd, flags)

        with (patch.object(instal, gate_method, side_effect=gate),
              patch.object(gui_deploy.fcntl, "flock", side_effect=observe_lock),
              ThreadPoolExecutor(1, thread_name_prefix="first") as one,
              ThreadPoolExecutor(1, thread_name_prefix="second") as two):
            before = one.submit(operations[first])
            try:
                self.assertTrue(paused.wait(3))
                after = two.submit(operations[second])
                self.assertTrue(blocked.wait(3))
            finally:
                resume.set()
            before.result(timeout=4)
            after.result(timeout=4)

        self.assertEqual(instal.tray.exists(), entry_expected)
        self.assertEqual(instal.launcher.exists(), second != "uninstall")

    def test_invalid_actual_package_cannot_replace_current_release(self):
        instal = self.installation
        instal.install(ROOT)
        current = instal.current.readlink()
        version = self.version()
        copy = self.directory / "source-copy"
        pkg = copy / "nitro_control"
        shutil.copytree(ROOT / "nitro_control", pkg)
        for filename, replacement, error in (
            ("hardware.py", None, "Missing hardware"),
            ("desktop_session.py", None, "imports missing desktop_session"),
            ("ui.py", b"def broken(:\n", None),
        ):
            with self.subTest(filename=filename):
                path = pkg / filename
                original = path.read_bytes()
                if replacement is None:
                    path.unlink()
                else:
                    path.write_bytes(replacement)
                if error:
                    with self.assertRaisesRegex(ValueError, error):
                        instal.install(copy)
                else:
                    with self.assertRaises(SyntaxError):
                        instal.install(copy)
                self.assertEqual(instal.current.readlink(), current)
                self.assertEqual(self.version(), version)
                path.write_bytes(original)

    def test_desktop_entries_launch_exact_modes_in_a_special_character_home(self):
        validator = self.which("desktop-file-validate")
        bus = self.which("dbus-run-session")
        gio = subprocess.run(["/usr/bin/python3", "-c", "from gi.repository import Gio"],
                             capture_output=True, check=False)
        if not bus or gio.returncode:
            self.skipTest("Gio and a private D-Bus session are required")
        instal = self.installation
        instal.install(self.source("fixture"))
        instal.enable_tray()
        desktops = self.home / ".local/share/applications"
        entries = (desktops / gui_deploy.LIVE, desktops / gui_deploy.DEMO, instal.tray)
        if validator:
            for entry in entries:
                subprocess.run([validator, entry], capture_output=True, check=True, timeout=10)
        bus_config = self.directory / "bus.conf"
        bus_config.write_text(BUS_CONFIG)
        # Launch sequentially using Gio, waiting for each toy process to record
        # its own argv. No dashboard or tray process is created by this test.
        driver = """
import os,sys,time
from pathlib import Path
from gi.repository import Gio
output = Path(os.environ["NITRO_TEST_OBSERVATIONS"])
for index, path in enumerate(sys.argv[1:], 1):
    Gio.DesktopAppInfo.new_from_filename(path).launch([], None)
    deadline = time.monotonic() + 3
    while not output.exists() or output.read_text().count("\\n") < index:
        if time.monotonic() >= deadline:
            raise SystemExit(f"Desktop entry did not launch: {path}")
        time.sleep(.01)
"""
        result = subprocess.run([bus, f"--config-file={bus_config}", "--", "/usr/bin/python3", "-c", driver, *entries],
                                env=dict(self.env, NITRO_TEST_RECORD="1"), capture_output=True,
                                text=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([json.loads(line) for line in self.observations.read_text().splitlines()],
                         [["fixture", []], ["fixture", ["--demo"]], ["fixture", ["--tray"]]])

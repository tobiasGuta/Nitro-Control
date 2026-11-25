"""GTK3 tray checks in a private session with synthetic sensor data."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

try:
    from nitro_control import tray_indicator as tray
except (ImportError, ValueError) as exc:
    print(f"GTK3/Ayatana unavailable. {exc}", file=sys.stderr)
    raise SystemExit(77) from exc

from nitro_control import desktop_session
from nitro_control.demo import demo_snapshot

Gio, GLib, Gtk = tray.Gio, tray.GLib, tray.Gtk


class TraySessionTests(unittest.TestCase):
    def wait_until(self, predicate):
        expired = False

        def expire():
            nonlocal expired
            expired = True
            return False

        source = GLib.timeout_add_seconds(4, expire)
        try:
            while not expired and not predicate():
                GLib.MainContext.default().iteration(True)
            self.assertTrue(predicate(), "GLib callback did not complete")
        finally:
            if not expired:
                GLib.source_remove(source)

    def test_runtime_lock_rejects_duplicate_process_and_allows_restart(self):
        code = ("from nitro_control.tray_indicator import _acquire_single_instance_lock; "
                "print(_acquire_single_instance_lock() is None)")
        with tempfile.TemporaryDirectory() as runtime, patch.dict(os.environ, XDG_RUNTIME_DIR=runtime):
            first = tray._acquire_single_instance_lock()
            self.assertIsNotNone(first)
            try:
                duplicate = subprocess.check_output([sys.executable, "-c", code], text=True, timeout=5)
                self.assertEqual(duplicate.strip(), "True")
            finally:
                os.close(first)
            restarted = subprocess.check_output([sys.executable, "-c", code], text=True, timeout=5)
            self.assertEqual(restarted.strip(), "False")
            self.assertTrue(Path(runtime, "nitro-control-tray.lock").is_file())

    def test_missing_notifier_exits_without_reading_hardware(self):
        with (tempfile.TemporaryDirectory() as runtime, patch.dict(os.environ, XDG_RUNTIME_DIR=runtime),
              patch.object(tray, "WATCHER_GRACE_MS", 30), patch.object(tray, "HardwareReader") as hardware,
              redirect_stderr(StringIO()) as stderr):
            self.assertEqual(tray.run_tray(), 2)
        self.assertIn("StatusNotifierWatcher", stderr.getvalue())
        hardware.assert_not_called()

    def test_thread_start_failure_can_retry_and_close_drops_late_read(self):
        hardware = Mock()
        hardware.collect.return_value = demo_snapshot()
        with (patch.object(tray, "HardwareReader", return_value=hardware),
              patch.object(tray, "Thread") as thread):
            thread.return_value.start.side_effect = RuntimeError("thread limit")
            window = tray.TrayIndicator()
        self.addCleanup(window.close)
        self.assertFalse(window._busy)
        self.assertIn("unavailable", window._measurements["cpu_temp"].get_label())
        window._request_refresh()
        self.wait_until(lambda: not window._busy)
        self.assertIn("45", window._measurements["cpu_temp"].get_label())
        entered, release, returned = Event(), Event(), Event()

        def read():
            entered.set()
            release.wait(5)
            returned.set()
            return demo_snapshot()

        hardware.collect.side_effect = read
        window._request_refresh()
        try:
            self.assertTrue(entered.wait(3))
            window.close()
            self.assertIsNone(GLib.MainContext.default().find_source_by_id(window._poll_source))
        finally:
            release.set()
        self.assertTrue(returned.wait(3))
        self.assertEqual(window._refresh_idle, 0)

    def test_open_prefers_live_activation_and_bounds_fallback_children(self):
        hardware = Mock()
        hardware.collect.return_value = demo_snapshot()
        with patch.object(tray, "HardwareReader", return_value=hardware):
            window = tray.TrayIndicator()
        self.addCleanup(window.close)
        with (patch.object(tray, "activate_live") as activate,
              patch.object(tray, "launch_session_app") as launch):
            window._on_open()
            window._on_open()
            activate.assert_called_once()
            activate.call_args.args[1](True, None)
            launch.assert_not_called()

            window._on_open()
            activate.call_args.args[1](False, None)
            launch.assert_called_once_with()
            window._on_open()
            activate.call_args.args[1](False, None)
            launch.assert_called_once_with()

            launch.return_value.get_identifier.return_value = None
            window._on_open()
            activate.call_args.args[1](False, None)
            self.assertEqual(launch.call_count, 2)
            window.close()
            window._on_open()
            self.assertEqual(activate.call_count, 4)

    def test_live_activation_reports_no_owner_without_autostart(self):
        replies = []
        desktop_session.activate_live(Gio.Cancellable(), lambda *result: replies.append(result))
        self.wait_until(lambda: bool(replies))
        self.assertEqual(replies, [(False, None)])

    def test_session_child_is_detached_and_reaped(self):
        with tempfile.TemporaryDirectory(prefix="nitro : Å ") as root:
            source = Path(root) / "nitro_control"
            source.mkdir()
            (source / "__init__.py").touch()
            result = Path(root) / "result"
            gate = Path(root) / "gate"
            os.mkfifo(gate)
            (source / "__main__.py").write_text(
                "import os,sys\nfrom pathlib import Path\n"
                "Path(sys.argv[1]).write_text(str(os.getsid(0)))\n"
                "with open(sys.argv[2], 'rb') as gate: gate.read(1)\n"
            )
            with os.fdopen(os.open(gate, os.O_RDWR | os.O_NONBLOCK), "wb", buffering=0) as release:
                with patch.object(desktop_session, "__file__", str(source / "desktop_session.py")):
                    child = desktop_session.launch_session_app(str(result), str(gate))
                pid = int(child.get_identifier())
                finished = []

                def on_exit(process, outcome):
                    finished.append(process.wait_finish(outcome))

                child.wait_async(None, on_exit)
                release.write(b"x")
                self.wait_until(lambda: bool(finished))
            self.assertTrue(child.get_successful())
            self.assertEqual(int(result.read_text()), pid)
            with self.assertRaises(ChildProcessError):
                os.waitpid(pid, os.WNOHANG)


    def test_installed_tray_opens_current_release_after_upgrade(self):
        from scripts.gui_deploy import ESSENTIAL_MODULES, Deployment

        with tempfile.TemporaryDirectory(prefix="nitro upgrade ") as directory:
            root = Path(directory)
            home = root / "home"
            home.mkdir()
            deployment = Deployment(home, home / ".config")
            output = root / "version"
            for version in ("old", "new"):
                source = root / version
                package = source / "nitro_control"
                package.mkdir(parents=True)
                for module in ESSENTIAL_MODULES:
                    (package / f"{module}.py").touch()
                (package / "__init__.py").write_text(f"__version__ = {version!r}\n")
                (package / "__main__.py").write_text(
                    "import os\nfrom pathlib import Path\nfrom . import __version__\n"
                    "Path(os.environ['NITRO_TEST_OUTPUT']).write_text(__version__)\n"
                )
                deployment.install(source)
                if version == "old":
                    running_tray = deployment.current.resolve() / "nitro_control/desktop_session.py"

            with (patch.dict(os.environ, HOME=str(home), NITRO_TEST_OUTPUT=str(output)),
                  patch.object(desktop_session, "__file__", str(running_tray))):
                child = desktop_session.launch_session_app()
                complete = []
                child.wait_async(None, lambda process, result: complete.append(process.wait_finish(result)))
                self.wait_until(lambda: bool(complete))
                self.assertTrue(child.get_successful())
                self.assertEqual(output.read_text(), "new")
                deployment.uninstall()
                with self.assertRaisesRegex(OSError, "launcher is unavailable"):
                    desktop_session.launch_session_app()


if __name__ == "__main__":
    if os.geteuid() == 0:
        print("Tray fixtures require an unprivileged user", file=sys.stderr)
        raise SystemExit(77)
    ready = Gtk.init_check()
    if not (ready[0] if isinstance(ready, tuple) else ready):
        print("GTK3 display unavailable for tray fixtures", file=sys.stderr)
        raise SystemExit(77)
    unittest.main(verbosity=2)

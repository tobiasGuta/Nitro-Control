"""Optional GNOME top-bar tray process (GTK3 Ayatana AppIndicator).

The main Nitro Control dashboard remains GTK4/Libadwaita. GTK3 and GTK4
MUST NOT be loaded in one process: the separate `--tray` process contains
only this GTK3 menu and read-only sensor polling. No WMI, RGB, power-profile,
fan PWM, privileged helper or automatic startup operation lives here.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
from threading import Thread

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as AppIndicator3, GLib, Gtk

from .hardware import HardwareReader
from .tray_model import TrayReadout, format_tray_snapshot

POLL_SECONDS = 12


def _acquire_single_instance_lock() -> int | None:
    """Hold a private user-runtime lock until process exit.

    Never fall back to /tmp or create/modify a system file. A second tray
    invocation simply reports that the indicator is already running.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise RuntimeError("XDG_RUNTIME_DIR is missing; launch from a GNOME login session")
    directory = Path(runtime)
    info = directory.stat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077):
        raise RuntimeError("XDG_RUNTIME_DIR must be private and owned by the current user")
    lock_path = directory / "nitro-control-tray.lock"
    fd = os.open(str(lock_path),
                 os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        file_info = os.fstat(fd)
        if not stat.S_ISREG(file_info.st_mode) or file_info.st_uid != os.getuid():
            raise RuntimeError("Unsafe tray lock file; refusing to start")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return None
        return fd
    except Exception:
        os.close(fd)
        raise


def _open_dashboard() -> None:
    """Activate the GTK4 app in its own Python interpreter and process."""
    source_root = Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    # Import from the exact installed/source checkout that launched the tray,
    # not an unrelated stale package in the current shell's working directory.
    env["PYTHONPATH"] = str(source_root)
    subprocess.Popen(
        [sys.executable, "-P", "-m", "nitro_control"],
        cwd=str(source_root), env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


class TrayIndicator:
    def __init__(self):
        self._reader = HardwareReader()
        self._busy = False
        self._closed = False
        self._indicator = AppIndicator3.Indicator.new(
            "nitro-control", "utilities-system-monitor",
            AppIndicator3.IndicatorCategory.HARDWARE,
        )
        self._indicator.set_title("Nitro Control · read-only hardware monitor")

        menu = Gtk.Menu()
        heading = Gtk.MenuItem(label="Nitro Control · live readings")
        heading.set_sensitive(False)
        menu.append(heading)
        menu.append(Gtk.SeparatorMenuItem())

        self._measurements = {}
        for name in ("cpu_temp", "gpu_temp", "cpu_fan", "gpu_fan", "updated"):
            item = Gtk.MenuItem(label="Waiting for sensor reading…")
            item.set_sensitive(False)
            menu.append(item)
            self._measurements[name] = item

        menu.append(Gtk.SeparatorMenuItem())
        open_item = Gtk.MenuItem(label="Open Nitro Control")
        open_item.connect("activate", self._on_open)
        menu.append(open_item)

        refresh = Gtk.MenuItem(label="Refresh readings")
        refresh.connect("activate", lambda *_: self._request_refresh())
        menu.append(refresh)

        quit_item = Gtk.MenuItem(label="Quit tray")
        quit_item.connect("activate", self._on_quit)
        menu.append(quit_item)

        menu.show_all()
        self._indicator.set_menu(menu)
        self._indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self._request_refresh()
        GLib.timeout_add_seconds(POLL_SECONDS, self._on_timer)

    def _request_refresh(self) -> None:
        if self._closed or self._busy:
            return
        self._busy = True
        Thread(target=self._collect_background, daemon=True,
               name="nitro-tray-readonly-poll").start()

    def _collect_background(self) -> None:
        try:
            labels = format_tray_snapshot(self._reader.collect())
        except Exception:
            labels = None
        GLib.idle_add(self._finish_refresh, labels)

    def _finish_refresh(self, readout: TrayReadout | None) -> bool:
        self._busy = False
        if self._closed:
            return False
        if readout is None:
            # Do not keep showing stale numbers as if they were current.
            for item in self._measurements.values():
                item.set_label("Sensor reading unavailable")
        else:
            for name, item in self._measurements.items():
                item.set_label(getattr(readout, name))
        return False

    def _on_timer(self) -> bool:
        if self._closed:
            return False
        self._request_refresh()
        return True

    def _on_open(self, *_args) -> None:
        try:
            _open_dashboard()
        except OSError as exc:
            print(f"Could not open Nitro Control: {exc}", file=sys.stderr)

    def _on_quit(self, *_args) -> None:
        self._closed = True
        self._indicator.set_status(AppIndicator3.IndicatorStatus.PASSIVE)
        Gtk.main_quit()


def run_tray() -> int:
    if os.geteuid() == 0:
        print("The optional Nitro Control tray must not run as root.", file=sys.stderr)
        return 2
    gtk_ready = Gtk.init_check()
    # PyGObject releases differ in whether Gtk.init_check returns a bool
    # or a (bool, remaining_argv) tuple.
    if not (gtk_ready[0] if isinstance(gtk_ready, tuple) else gtk_ready):
        print("No graphical GTK session available for Nitro Control tray.", file=sys.stderr)
        return 2
    try:
        lock = _acquire_single_instance_lock()
    except (OSError, RuntimeError) as exc:
        print(f"Cannot start Nitro Control tray: {exc}", file=sys.stderr)
        return 2
    if lock is None:
        print("Nitro Control tray is already running.")
        return 0
    try:
        TrayIndicator()
        # Ctrl+C in a development terminal must stop the tray as usual.
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        Gtk.main()
        return 0
    finally:
        os.close(lock)

"""Optional GNOME top-bar tray process (GTK3 Ayatana AppIndicator).

The main Nitro Control dashboard remains GTK4/Libadwaita. GTK3 and GTK4
MUST NOT be loaded in one process: the separate `--tray` process contains
only this GTK3 menu and read-only sensor polling. No WMI, RGB, power-profile,
fan PWM, privileged helper or automatic startup operation lives here.
"""
from __future__ import annotations

import fcntl
import os
import signal
import stat
import sys
from pathlib import Path
from threading import Lock, Thread

from gi import require_version

require_version("Gtk", "3.0")
require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as AppIndicator3
from gi.repository import Gio, GLib, Gtk

from . import APP_ID
from .desktop_session import activate_live, launch_session_app
from .hardware import HardwareReader
from .tray_model import TrayReadout, format_tray_snapshot

POLL_SECONDS = 12
WATCHER = "org.kde.StatusNotifierWatcher"
WATCHER_GRACE_MS = 3000


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


class TrayIndicator:
    def __init__(self):
        self._reader = HardwareReader(gpu_poll_interval=POLL_SECONDS - 1)
        self._busy = False
        self._closed = False
        self._callback_lock = Lock()
        self._refresh_idle = 0
        self._opening = False
        self._dashboard_process = None
        self._cancellable = Gio.Cancellable()
        self._indicator = AppIndicator3.Indicator.new(
            "nitro-control", f"{APP_ID}-symbolic",
            AppIndicator3.IndicatorCategory.HARDWARE,
        )
        icon_path = Path(__file__).resolve().parent / "icons/hicolor/symbolic/apps"
        self._indicator.set_icon_theme_path(str(icon_path))
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
        self._poll_source = GLib.timeout_add_seconds(POLL_SECONDS, self._on_timer)

    def _request_refresh(self) -> None:
        if self._closed or self._busy:
            return
        self._busy = True
        try:
            Thread(target=self._collect_background, daemon=True,
                   name="nitro-tray-readonly-poll").start()
        except RuntimeError as exc:
            print(f"Could not start tray refresh. {exc}", file=sys.stderr)
            self._finish_refresh(None)

    def _collect_background(self) -> None:
        try:
            labels = format_tray_snapshot(self._reader.collect())
        except Exception:
            labels = None
        with self._callback_lock:
            if not self._closed:
                self._refresh_idle = GLib.idle_add(self._finish_refresh, labels)

    def _finish_refresh(self, readout: TrayReadout | None) -> bool:
        with self._callback_lock:
            self._refresh_idle = 0
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
        if self._opening or self._closed:
            return
        self._opening = True
        activate_live(self._cancellable, self._dashboard_activated)

    def _dashboard_activated(self, activated: bool, error: GLib.Error | None) -> None:
        self._opening = False
        if self._closed or activated:
            return
        if error:
            print(f"Could not activate Nitro Control. {error}", file=sys.stderr)
            return
        # Another click can arrive before a newly spawned dashboard owns its
        # D-Bus name. GSubprocess reaps that process independently of the tray UI.
        if self._dashboard_process is not None and self._dashboard_process.get_identifier() is not None:
            return
        try:
            self._dashboard_process = launch_session_app()
        except OSError as exc:
            print(f"Could not open Nitro Control. {exc}", file=sys.stderr)

    def close(self) -> None:
        with self._callback_lock:
            if self._closed:
                return
            self._closed = True
            if self._refresh_idle:
                GLib.source_remove(self._refresh_idle)
        GLib.source_remove(self._poll_source)
        self._cancellable.cancel()
        self._indicator.set_status(AppIndicator3.IndicatorStatus.PASSIVE)

    def _on_quit(self, *_args) -> None:
        self.close()
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
    indicator = None
    grace = 0
    result = 0

    def watcher_timeout() -> bool:
        nonlocal grace, result
        grace = 0
        result = 2
        print("No StatusNotifierWatcher available. Enable an AppIndicator extension or use a supported desktop.",
              file=sys.stderr)
        Gtk.main_quit()
        return False

    def appeared(*_args) -> None:
        nonlocal grace, indicator, result
        if grace:
            GLib.source_remove(grace)
            grace = 0
        if indicator is None:
            try:
                indicator = TrayIndicator()
            except (GLib.Error, RuntimeError) as exc:
                result = 2
                print(f"Could not initialize Nitro Control tray. {exc}", file=sys.stderr)
                Gtk.main_quit()

    def vanished(*_args) -> None:
        nonlocal grace
        if not grace:
            grace = GLib.timeout_add(WATCHER_GRACE_MS, watcher_timeout)

    watch = 0
    signals = []
    try:
        grace = GLib.timeout_add(WATCHER_GRACE_MS, watcher_timeout)
        watch = Gio.bus_watch_name(Gio.BusType.SESSION, WATCHER, Gio.BusNameWatcherFlags.NONE, appeared, vanished)
        for number in (signal.SIGINT, signal.SIGTERM):
            signals.append(GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, number, Gtk.main_quit))
        Gtk.main()
        return result
    except (GLib.Error, RuntimeError) as exc:
        print(f"Could not run Nitro Control tray. {exc}", file=sys.stderr)
        return 2
    finally:
        if watch:
            Gio.bus_unwatch_name(watch)
        context = GLib.MainContext.default()
        for source in (*signals, grace):
            if source and context.find_source_by_id(source) is not None:
                GLib.source_remove(source)
        if indicator is not None:
            indicator.close()
        os.close(lock)

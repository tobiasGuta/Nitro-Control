"""Desktop IPC and detached, automatically reaped child processes."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from gi.repository import Gio, GLib

from . import APP_ID

_SOURCE_COMMAND = (
    "import os,runpy,sys; os.setsid(); sys.path.insert(0,sys.argv.pop(1)); "
    "runpy.run_module('nitro_control',run_name='__main__',alter_sys=True)"
)
_INSTALLED_COMMAND = "import os,sys; os.setsid(); os.execv(sys.argv[1],sys.argv[1:])"


def launch_session_app(*args: str) -> Gio.Subprocess:
    source = Path(__file__).resolve().parent.parent
    home = Path.home().resolve()
    store = home / ".local/share/nitro-control/deployment"
    if source.parent == store:
        # An already running tray may belong to an earlier release. Launch
        # through the stable entry point so it opens the current dashboard.
        wrapper = home / ".local/bin/nitro-control"
        expected = os.path.relpath(store / "current/nitro-control", wrapper.parent)
        if not wrapper.is_symlink() or os.readlink(wrapper) != expected or not wrapper.is_file():
            raise OSError("Nitro Control user launcher is unavailable")
        argv = [sys.executable, "-IB", "-c", _INSTALLED_COMMAND, str(wrapper), *args]
    else:
        argv = [sys.executable, "-IB", "-c", _SOURCE_COMMAND, str(source), *args]
    try:
        return Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_SILENCE)
    except GLib.Error as exc:
        raise OSError(str(exc)) from exc


def activate_live(cancellable: Gio.Cancellable, finished) -> None:
    """Ask the Live single instance to activate, with no D-Bus autostart."""
    def activated(bus, result) -> None:
        try:
            bus.call_finish(result)
        except GLib.Error as exc:
            if (exc.matches(Gio.dbus_error_quark(), Gio.DBusError.SERVICE_UNKNOWN)
                    or exc.matches(Gio.dbus_error_quark(), Gio.DBusError.NAME_HAS_NO_OWNER)):
                finished(False, None)
            else:
                finished(False, exc)
        else:
            finished(True, None)

    def got_bus(_source, result) -> None:
        try:
            bus = Gio.bus_get_finish(result)
        except GLib.Error as exc:
            finished(False, exc)
            return
        bus.call(
            APP_ID, "/" + APP_ID.replace(".", "/"), "org.freedesktop.Application", "Activate",
            GLib.Variant("(a{sv})", ({},)), None, Gio.DBusCallFlags.NO_AUTO_START,
            3000, cancellable, activated,
        )

    Gio.bus_get(Gio.BusType.SESSION, cancellable, got_bus)

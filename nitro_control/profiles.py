"""Optional desktop power-mode control through the standard system D-Bus API.

This module never writes sysfs or invokes a root helper. The service owns
permissions, authorization, and hardware-specific TuneD/firmware mapping.
GTK/GI are deliberately imported only by the real transport, keeping the
validation layer testable without GNOME installed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

SERVICE = "org.freedesktop.UPower.PowerProfiles"
PATH = "/org/freedesktop/UPower/PowerProfiles"
INTERFACE = SERVICE
PROPERTIES = "org.freedesktop.DBus.Properties"
ALLOWED = ("power-saver", "balanced", "performance")
DISPLAY = {"power-saver": "Power Saver", "balanced": "Balanced", "performance": "Performance"}


class ProfileError(Exception):
    """Service unavailable, invalid request, authorization error, or unverified result."""


@dataclass(frozen=True, slots=True)
class PowerState:
    active: str
    choices: tuple[str, ...]


class ProfileTransport(Protocol):
    def get(self, property_name: str): ...
    def set_active(self, profile: str) -> None: ...


def _plain(value):
    """Convert nested GLib variants and containers to normal Python objects."""
    if hasattr(value, "unpack"):
        return _plain(value.unpack())
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


class GioProfileTransport:
    """System bus transport; the OS service handles any necessary authorization."""

    def __init__(self):
        from gi.repository import Gio, GLib
        self.Gio = Gio
        self.GLib = GLib
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

    def get(self, property_name: str):
        response = self.bus.call_sync(
            SERVICE, PATH, PROPERTIES, "Get",
            self.GLib.Variant("(ss)", (INTERFACE, property_name)),
            self.GLib.VariantType.new("(v)"), self.Gio.DBusCallFlags.NONE, 3000, None,
        )
        return _plain(response.get_child_value(0).get_variant())

    def set_active(self, profile: str) -> None:
        self.bus.call_sync(
            SERVICE, PATH, PROPERTIES, "Set",
            self.GLib.Variant("(ssv)", (INTERFACE, "ActiveProfile", self.GLib.Variant("s", profile))),
            self.GLib.VariantType.new("()"), self.Gio.DBusCallFlags.NONE, 60000, None,
        )


class PowerProfileController:
    def __init__(self, transport: ProfileTransport | None = None):
        self.transport = transport

    def _transport(self) -> ProfileTransport:
        if self.transport is None:
            self.transport = GioProfileTransport()
        return self.transport

    def read(self) -> PowerState:
        try:
            transport = self._transport()
            raw_profiles = _plain(transport.get("Profiles"))
            active = _plain(transport.get("ActiveProfile"))
        except Exception as exc:
            raise ProfileError(f"Power profile service unavailable: {exc}") from exc
        if not isinstance(raw_profiles, (tuple, list)) or not isinstance(active, str):
            raise ProfileError("Power profile service returned unexpected data")
        choices = []
        for item in raw_profiles:
            if not isinstance(item, dict):
                raise ProfileError("Power profile service returned invalid profile metadata")
            name = _plain(item.get("Profile"))
            if isinstance(name, str) and name in ALLOWED and name not in choices:
                choices.append(name)
        if not choices or active not in choices:
            raise ProfileError("Power profile service has no usable active profile")
        return PowerState(active=active, choices=tuple(choices))

    def apply(self, target: str) -> PowerState:
        if target not in ALLOWED:
            raise ProfileError("Unsupported power mode")
        # Recheck live advertised capabilities, not a stale selection from the GUI.
        current = self.read()
        if target not in current.choices:
            raise ProfileError("This power mode is no longer offered by the system")
        if target == current.active:
            return current
        try:
            self._transport().set_active(target)
        except Exception as exc:
            raise ProfileError(f"The system refused the power mode change: {exc}") from exc
        verified = self.read()
        if verified.active != target:
            raise ProfileError(f"The system did not confirm {DISPLAY[target]} as active")
        return verified

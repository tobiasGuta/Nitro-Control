"""Simulated D-Bus behavior; tests never change the machine's real power mode."""

import unittest

from nitro_control.profiles import ALLOWED, PowerProfileController, ProfileError, _plain


class Variant:
    def __init__(self, value):
        self.value = value

    def unpack(self):
        return self.value


class FakeTransport:
    def __init__(self, active="balanced", choices=ALLOWED):
        self.active = active
        self.choices = choices
        self.calls = []
        self.fail = None
        self.ignore = False

    def get(self, property_name):
        if self.fail == "get":
            raise OSError("offline")
        if property_name == "Profiles":
            return [Variant({"Profile": Variant(name)}) for name in self.choices]
        if property_name == "ActiveProfile":
            return Variant(self.active)
        raise AssertionError(property_name)

    def set_active(self, name):
        self.calls.append(name)
        if self.fail == "set":
            raise PermissionError("authorization refused")
        if not self.ignore:
            self.active = name


class ProfileTests(unittest.TestCase):
    def test_unpack_nested_variants(self):
        self.assertEqual(_plain(Variant([{"Profile": Variant("balanced")}])),
                         [{"Profile": "balanced"}])

    def test_read_current_and_advertised_modes(self):
        state = PowerProfileController(FakeTransport()).read()
        self.assertEqual(state.active, "balanced")
        self.assertEqual(state.choices, ALLOWED)

    def test_apply_approved_advertised_mode_and_verify(self):
        transport = FakeTransport()
        result = PowerProfileController(transport).apply("performance")
        self.assertEqual(transport.calls, ["performance"])
        self.assertEqual(result.active, "performance")

    def test_no_write_for_already_active_mode(self):
        transport = FakeTransport()
        self.assertEqual(PowerProfileController(transport).apply("balanced").active, "balanced")
        self.assertEqual(transport.calls, [])

    def test_reject_malicious_or_firmware_only_profile(self):
        transport = FakeTransport()
        for name in ("balanced-performance", "performance; reboot", "", "quiet"):
            with self.subTest(name=name), self.assertRaises(ProfileError):
                PowerProfileController(transport).apply(name)
        self.assertEqual(transport.calls, [])

    def test_reject_unadvertised_profile(self):
        transport = FakeTransport(choices=("power-saver", "balanced"))
        with self.assertRaisesRegex(ProfileError, "no longer offered"):
            PowerProfileController(transport).apply("performance")
        self.assertEqual(transport.calls, [])

    def test_set_denied(self):
        transport = FakeTransport()
        transport.fail = "set"
        with self.assertRaisesRegex(ProfileError, "refused"):
            PowerProfileController(transport).apply("performance")

    def test_unverified_change_not_reported_as_success(self):
        transport = FakeTransport()
        transport.ignore = True
        with self.assertRaisesRegex(ProfileError, "did not confirm"):
            PowerProfileController(transport).apply("performance")

    def test_service_unavailable(self):
        transport = FakeTransport()
        transport.fail = "get"
        with self.assertRaisesRegex(ProfileError, "unavailable"):
            PowerProfileController(transport).read()

    def test_invalid_or_no_profile_data(self):
        transport = FakeTransport(choices=("bad",))
        with self.assertRaises(ProfileError):
            PowerProfileController(transport).read()
        transport = FakeTransport(active="something-unknown")
        with self.assertRaises(ProfileError):
            PowerProfileController(transport).read()


if __name__ == "__main__":
    unittest.main()

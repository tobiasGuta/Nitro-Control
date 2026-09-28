"""Privileged helper rejects unsafe requests before reaching any hardware writer."""
import io
import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from nitro_control import rgb_privileged


class HelperTests(unittest.TestCase):
    def invoke(self, request, euid=0):
        stdout = io.StringIO()
        stdin = SimpleNamespace(buffer=io.BytesIO(request))
        with patch("nitro_control.rgb_privileged.os.geteuid", return_value=euid), \
             patch("nitro_control.rgb_privileged.sys.stdin", stdin), \
             patch("nitro_control.rgb_privileged.sys.stdout", stdout), \
             patch("nitro_control.rgb_privileged.RGBHardware") as backend:
            code = rgb_privileged.main()
        return code, json.loads(stdout.getvalue()), backend

    def test_requires_root(self):
        code, reply, backend = self.invoke(b'{}', euid=1000)
        self.assertEqual(code, 1)
        self.assertFalse(reply["ok"])
        backend.assert_not_called()

    def test_rejects_extra_or_unknown_fields(self):
        for payload in (b'{}', b'{"zones": [], "brightness": 20, "path": "/etc/passwd"}', b'[]'):
            code, reply, backend = self.invoke(payload)
            self.assertEqual(code, 1)
            self.assertFalse(reply["ok"])
            backend.assert_not_called()

    def test_rejects_oversized_request(self):
        code, reply, backend = self.invoke(b' ' * (rgb_privileged.MAX_REQUEST_BYTES + 1))
        self.assertEqual(code, 1)
        self.assertIn("maximum", reply["error"])
        backend.assert_not_called()

    def test_valid_request_uses_validated_plan(self):
        payload = json.dumps({"zones": ["#ff0000"] * 4, "brightness": 30}).encode()
        stdout = io.StringIO()
        stdin = SimpleNamespace(buffer=io.BytesIO(payload))
        with patch("nitro_control.rgb_privileged.os.geteuid", return_value=0), \
             patch("nitro_control.rgb_privileged.sys.stdin", stdin), \
             patch("nitro_control.rgb_privileged.sys.stdout", stdout), \
             patch("nitro_control.rgb_privileged.RGBHardware") as constructor:
            constructor.return_value.apply.return_value = SimpleNamespace(
                backend="native-led", verified_readback=True, detail="sysfs accepted")
            code = rgb_privileged.main()
            plan = constructor.return_value.apply.call_args.args[0]
        self.assertEqual(code, 0)
        self.assertEqual(plan.zones, ("#FF0000",) * 4)
        self.assertEqual(plan.brightness, 30)
        self.assertTrue(json.loads(stdout.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()

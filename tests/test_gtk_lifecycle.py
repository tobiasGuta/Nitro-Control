"""Run the real GTK lifecycle fixtures on a private bus when GTK is available."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Permit application registration and activation without loading host bus services.
BUS_CONFIG = """<busconfig>
  <type>session</type>
  <listen>unix:tmpdir=/tmp</listen>
  <policy context="default">
    <allow send_destination="*"/>
    <allow receive_sender="*"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""


class IsolatedGtkTests(unittest.TestCase):
    def test_application_lifecycle_on_private_session_bus(self):
        self._run_isolated("gtk_lifecycle_cases.py")

    def test_tray_lifecycle_on_private_session_bus(self):
        self._run_isolated("tray_session_cases.py")

    def _run_isolated(self, script):
        launcher = shutil.which("dbus-run-session")
        if launcher is None:
            self.skipTest("dbus-run-session is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            bus_config = directory / "bus.conf"
            bus_config.write_text(BUS_CONFIG, encoding="utf-8")
            env = dict(
                os.environ,
                GIO_USE_VFS="local",
                GTK_A11Y="none",
                GSETTINGS_BACKEND="memory",
                XDG_CACHE_HOME=str(directory / "cache"),
                XDG_CONFIG_HOME=str(directory / "config"),
                XDG_DATA_HOME=str(directory / "data"),
                PYTHONPATH=str(ROOT),
            )
            result = subprocess.run(
                [launcher, f"--config-file={bus_config}", "--", sys.executable,
                 str(ROOT / "tests" / script)],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=60, check=False,
            )
        if result.returncode == 77:
            self.skipTest(result.stderr.strip())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

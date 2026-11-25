"""Offline launcher regression checks; no GTK, drivers or firmware writes."""
from __future__ import annotations

import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from nitro_control.__main__ import main

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-fedora.sh"


class LauncherTests(unittest.TestCase):
    def test_demo_refuses_live_rgb_probe(self):
        with (patch("nitro_control.rgb_hardware.RGBHardware.probe") as probe,
              redirect_stderr(StringIO()) as stderr, self.assertRaises(SystemExit) as raised):
            main(["--demo", "--rgb-probe"])
        self.assertEqual(raised.exception.code, 2)
        probe.assert_not_called()
        self.assertIn("live RGB probe", stderr.getvalue())

    def test_incomplete_pygobject_reports_missing_gui_dependencies(self):
        # A namespace directory left by an older interpreter can import as
        # "gi" without exporting any PyGObject API.
        command = (
            "import sys, types; sys.modules['gi'] = types.ModuleType('gi'); "
            "from nitro_control.__main__ import main; raise SystemExit(main(sys.argv[1:]))"
        )
        for mode, diagnostic in (("--demo", "GTK4"), ("--tray", "Tray unavailable")):
            with self.subTest(mode=mode):
                result = subprocess.run([sys.executable, "-c", command, mode], cwd=ROOT,
                                        capture_output=True, text=True, timeout=10, check=False)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(diagnostic, result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_installer_shell_syntax(self):
        subprocess.run(["bash", "-n", str(INSTALLER)], check=True)


if __name__ == "__main__":
    unittest.main()

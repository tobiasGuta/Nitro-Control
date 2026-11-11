"""Offline release metadata contracts; no GUI, fans or firmware access."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest

from nitro_control import __version__


ROOT = Path(__file__).resolve().parents[1]


class ReleaseMetadataTests(unittest.TestCase):
    def test_entry_point_reports_package_version_without_gtk(self):
        result = subprocess.run(
            [sys.executable, "-m", "nitro_control", "--version"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), f"Nitro Control {__version__}")

    def test_release_guide_and_readme_match_package_version(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"Version {__version__}", readme)
        release_name = f"RELEASE-v{__version__}.md"
        self.assertIn(release_name, readme)
        notes = (ROOT / "docs" / release_name).read_text(encoding="utf-8")
        self.assertIn(f"v{__version__}", notes)
        self.assertIn("NOT YET PUBLISHED", notes)

    def test_gui_dependency_hint_includes_pycairo(self):
        launcher = (ROOT / "nitro_control" / "__main__.py").read_text()
        installer = (ROOT / "scripts" / "install-fedora.sh").read_text()
        self.assertIn("python3-cairo", launcher)
        self.assertIn("python3-cairo", installer)
        self.assertIn("import cairo", installer)

    def test_release_notes_do_not_claim_unavailable_fan_controls(self):
        release_name = f"RELEASE-v{__version__}.md"
        notes = (ROOT / "docs" / release_name).read_text(encoding="utf-8")
        self.assertIn("no manual fan PWM", notes.lower())
        self.assertIn("exactly 100%", notes)
        self.assertIn("issue #6", notes)


if __name__ == "__main__":
    unittest.main()

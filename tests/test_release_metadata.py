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
        self.assertIn("GitHub Releases page", notes)
        self.assertIn("source archives", notes)

    def test_gui_dependency_hint_includes_pycairo(self):
        launcher = (ROOT / "nitro_control" / "__main__.py").read_text()
        installer = (ROOT / "scripts" / "install-fedora.sh").read_text()
        self.assertIn("python3-cairo", launcher)
        self.assertIn("python3-cairo", installer)
        self.assertIn("import cairo", installer)

    def test_isolated_install_rollback_script_is_syntactically_safe(self):
        script = ROOT / "scripts" / "smoke-test-gui-install-rollback-fedora.sh"
        content = script.read_text(encoding="utf-8")
        subprocess.run(["bash", "-n", str(script)], check=True, timeout=15)
        # All four installer invocations are explicitly redirected to an
        # isolated HOME, so no live per-user GUI installation is replaced.
        self.assertIn('temp_home_parent="$(mktemp -d', content)
        self.assertIn('HOME="$temp_home" bash "$repo_dir/scripts/install-fedora.sh"', content)
        self.assertIn('HOME="$temp_home" bash "$old_source/scripts/install-fedora.sh"', content)
        self.assertIn('git -C "$repo_dir" archive "$old_commit"', content)
        self.assertIn('check_version "$old_version"', content)
        self.assertIn('check_version "$new_version"', content)
        self.assertNotIn("pkexec ", content)
        self.assertNotIn("modprobe ", content)

    def test_original_preview_notes_preserve_release_scope(self):
        release_name = "RELEASE-v0.4.0-preview.2.md"
        notes = (ROOT / "docs" / release_name).read_text(encoding="utf-8")
        self.assertIn("no manual fan pwm", notes.lower())
        self.assertIn("exactly 100%", notes)
        self.assertIn("issue #6", notes)


if __name__ == "__main__":
    unittest.main()

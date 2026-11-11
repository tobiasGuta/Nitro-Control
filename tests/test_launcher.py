"""Offline launcher regression checks; no GTK, drivers or firmware writes."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-fedora.sh"


def installed_wrapper() -> str:
    """Test the *actual generated wrapper* rather than a reimplementation."""
    source = INSTALLER.read_text(encoding="utf-8")
    match = re.search(
        r"""cat > "\$bin_dir/nitro-control" <<'WRAPPER'\n(.*?)\nWRAPPER""",
        source, re.DOTALL,
    )
    if not match:
        raise AssertionError("Unable to extract installer-generated launcher")
    return match.group(1) + "\n"


class LauncherTests(unittest.TestCase):
    def test_installer_shell_syntax(self):
        subprocess.run(["bash", "-n", str(INSTALLER)], check=True)

    def test_installed_package_wins_over_older_checkout_and_pythonpath(self):
        # The original bug manifested when launching `python -m nitro_control`
        # from inside a checkout that contained an older nitro_control package.
        with tempfile.TemporaryDirectory() as temp:
            tmp = Path(temp)
            home = tmp / "home"
            installed = home / ".local/share/nitro-control/nitro_control"
            stale_root = tmp / "old-experimental-checkout"
            stale = stale_root / "nitro_control"
            for target, marker in ((installed, "installed-preview"),
                                   (stale, "stale-experimental")):
                target.mkdir(parents=True)
                (target / "__init__.py").write_text(
                    f"MARKER = {marker!r}\n", encoding="utf-8")
                (target / "__main__.py").write_text(
                    "from . import MARKER\n"
                    "import sys\n"
                    "print(MARKER + ':' + ','.join(sys.argv[1:]))\n",
                    encoding="utf-8")

            wrapper = tmp / "nitro-control"
            wrapper.write_text(installed_wrapper(), encoding="utf-8")
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["PYTHONPATH"] = str(stale_root)

            for cwd in (stale_root, home):
                with self.subTest(cwd=cwd):
                    process = subprocess.run(
                        ["bash", str(wrapper), "--rgb-probe"],
                        cwd=cwd, env=env, capture_output=True, text=True,
                        check=False, timeout=15,
                    )
                    self.assertEqual(process.returncode, 0, process.stderr)
                    self.assertEqual(
                        process.stdout.strip(), "installed-preview:--rgb-probe")


if __name__ == "__main__":
    unittest.main()

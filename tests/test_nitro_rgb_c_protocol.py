"""Offline compilation & execution of native RGB C protocol harness and isolated Kbuild."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
C_TEST_SRC = REPO / "tests/c/test_nitro_rgb_protocol.c"
POC_DIR = REPO / "experiments/acer-wmi-rgb-enable-poc"


class NativeRGBCProtocolTests(unittest.TestCase):
    def _kernel_build_dir(self) -> Path:
        selected = os.environ.get("NITRO_KBUILD_DIR")
        if selected is not None:
            path = Path(selected)
            self.assertTrue((path / "Makefile").is_file(), f"Invalid kernel build directory: {path}")
            return path
        for path in sorted(Path("/lib/modules").glob("*/build")):
            if (path / "Makefile").is_file():
                return path
        self.skipTest("No kernel headers available in /lib/modules/*/build")

    def test_c_protocol_harness_passes_with_strict_warnings(self):
        gcc = shutil.which("gcc")
        if gcc is None:
            self.skipTest("gcc unavailable for C protocol harness")
        with tempfile.TemporaryDirectory() as td:
            bin_path = Path(td) / "test_nitro_rgb_protocol"
            compile_res = subprocess.run(
                [
                    gcc,
                    "-std=c11",
                    "-O2",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-Wpedantic",
                    "-Wshadow",
                    "-Wconversion",
                    str(C_TEST_SRC),
                    "-o",
                    str(bin_path),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            self.assertEqual(compile_res.returncode, 0, msg=compile_res.stderr + compile_res.stdout)
            run_res = subprocess.run([str(bin_path)], capture_output=True, text=True, check=False, timeout=20)
            self.assertEqual(run_res.returncode, 0, msg=run_res.stderr + run_res.stdout)
            self.assertIn("ALL_C_PROTOCOL_TESTS_OK", run_res.stdout)

    def test_isolated_kernel_module_compiles_without_experimental_params(self):
        kver_dir = self._kernel_build_dir()
        with tempfile.TemporaryDirectory() as td:
            bdir = Path(td)
            for name in ("nitro_rgb_enable_poc.c", "nitro_rgb_protocol.h", "Makefile"):
                shutil.copy2(POC_DIR / name, bdir / name)
            res = subprocess.run(
                ["make", "-C", str(kver_dir), f"M={bdir}", "W=1", "modules"],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            self.assertEqual(res.returncode, 0, msg=res.stderr + res.stdout)
            ko = bdir / "nitro_rgb_enable_poc.ko"
            self.assertTrue(ko.is_file())
            modinfo = shutil.which("modinfo") or "/usr/sbin/modinfo"
            parms = subprocess.run([modinfo, "-p", str(ko)], capture_output=True, text=True, check=True, timeout=20).stdout
            for forbidden in ("backlight_test:", "red_test:", "enable:", "effect_test:"):
                self.assertNotIn(forbidden, parms)
            for required in ("probe:", "apply:", "colors:", "brightness:", "effect_mode:", "full_test:"):
                self.assertIn(required, parms)
            alias = subprocess.run([modinfo, "-F", "alias", str(ko)], capture_output=True, text=True, check=True, timeout=20).stdout
            self.assertEqual(alias.strip(), "")

    def test_isolated_kernel_module_compiles_with_experimental_params(self):
        kver_dir = self._kernel_build_dir()
        with tempfile.TemporaryDirectory() as td:
            bdir = Path(td)
            for name in ("nitro_rgb_enable_poc.c", "nitro_rgb_protocol.h", "Makefile"):
                shutil.copy2(POC_DIR / name, bdir / name)
            res = subprocess.run(
                ["make", "-C", str(kver_dir), f"M={bdir}", "W=1", "EXPERIMENTAL=1", "modules"],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            self.assertEqual(res.returncode, 0, msg=res.stderr + res.stdout)
            ko = bdir / "nitro_rgb_enable_poc.ko"
            self.assertTrue(ko.is_file())
            modinfo = shutil.which("modinfo") or "/usr/sbin/modinfo"
            parms = subprocess.run([modinfo, "-p", str(ko)], capture_output=True, text=True, check=True, timeout=20).stdout
            for exp_param in ("backlight_test:", "red_test:", "enable:", "effect_test:"):
                self.assertIn(exp_param, parms)


if __name__ == "__main__":
    unittest.main()

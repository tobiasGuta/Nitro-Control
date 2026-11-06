"""Static contract tests: native WMI setup is explicit, reversible and opt-in.

These tests do not install software, load a module, or make firmware calls.
"""
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
INSTALL = SCRIPTS / "install-native-rgb-fedora.sh"
UNINSTALL = SCRIPTS / "uninstall-native-rgb-fedora.sh"
HELPER_INSTALL = SCRIPTS / "install-rgb-helper-fedora.sh"
HELPER_UNINSTALL = SCRIPTS / "uninstall-rgb-helper-fedora.sh"
POC = ROOT / "experiments" / "acer-wmi-rgb-enable-poc" / "nitro_rgb_enable_poc.c"


class NativeRGBInstallContracts(unittest.TestCase):
    def test_install_and_removal_shell_syntax(self):
        for path in (INSTALL, UNINSTALL, HELPER_INSTALL, HELPER_UNINSTALL):
            with self.subTest(path=path.name):
                subprocess.run(["bash", "-n", str(path)], check=True)

    def test_installer_uses_exact_kernel_and_only_stages_copy(self):
        source = INSTALL.read_text()
        for requirement in (
            'KERNEL="$(uname -r)"',
            "extra/nitro-control/nitro_rgb_enable_poc.ko",
            "modinfo -F name",
            "modinfo -F vermagic",
            "sha256sum",
            "Nitro AN515-58",
            "mokutil --sb-state",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, source)
        for forbidden in ("systemctl enable", "systemctl start", "insmod ", "modprobe -r",
                          "blacklist acer_wmi", "make install", "grubby", "dracut"):
            self.assertNotIn(forbidden, source)

    def test_installed_privileged_python_is_removed_on_rollback(self):
        installer = HELPER_INSTALL.read_text()
        remover = HELPER_UNINSTALL.read_text()
        self.assertIn("native_rgb.py", installer)
        self.assertIn("native_rgb.py", remover)
        self.assertIn("auth_admin'", installer)
        self.assertIn('<allow_inactive>no</allow_inactive>', installer)
        self.assertNotIn("<allow_active>yes</allow_active>", installer)

    def test_uninstall_refuses_to_remove_active_module(self):
        source = UNINSTALL.read_text()
        self.assertIn("grep -q '^nitro_rgb_enable_poc ' /proc/modules", source)
        self.assertIn('MODULE="/usr/lib/modules/$(uname -r)/', source)
        self.assertNotIn("rmmod ", source)
        self.assertNotIn("systemctl enable", source)

    def test_kernel_module_has_no_autoload_or_fan_commands(self):
        source = POC.read_text()
        self.assertNotIn("MODULE_DEVICE_TABLE", source)
        self.assertIn("if (strlen(colors) != 27 || brightness != 100)", source)
        self.assertIn("nitro_exact_model()", source)
        self.assertIn("wmi_has_guid(ACER_GAMING_WMI_GUID)", source)
        self.assertNotIn("ec_write", source)
        self.assertNotIn("pwm", source)


if __name__ == "__main__":
    unittest.main()

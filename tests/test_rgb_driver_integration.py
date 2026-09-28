"""Offline contract checks; never load a kernel module or edit systemd."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'scripts'
INSTALL = ROOT / 'install-rgb-driver-fedora.sh'
CONTROLLER = ROOT / 'rgb-driver-service-fedora.sh'
UNINSTALL = ROOT / 'uninstall-rgb-driver-fedora.sh'
UNIT = ROOT / 'nitro-control-rgb-driver.service'


class DriverIntegrationContracts(unittest.TestCase):
    def test_shell_syntax(self):
        for script in (INSTALL, CONTROLLER, UNINSTALL):
            with self.subTest(script=script.name):
                subprocess.run(['bash', '-n', str(script)], check=True)

    def test_never_auto_enable_or_blacklist(self):
        source = INSTALL.read_text()
        self.assertIn('systemctl disable nitro-control-rgb-driver.service', source)
        self.assertNotIn('systemctl enable nitro-control-rgb-driver.service', source)
        self.assertNotIn('blacklist acer_wmi"', source)
        self.assertNotIn('make install', source)

    def test_module_pinned_to_running_kernel_and_digest(self):
        source = INSTALL.read_text() + CONTROLLER.read_text()
        self.assertIn('KERNEL="$(uname -r)"', source)
        self.assertIn('extra/nitro-control/linuwu_sense.ko', source)
        self.assertIn('sha256sum', source)
        self.assertIn('modinfo -F vermagic', source)

    def test_service_guards_and_lifecycle(self):
        source = UNIT.read_text()
        self.assertIn('ExecCondition=', source)
        self.assertIn(' check', source)
        self.assertIn('ExecStart=', source)
        self.assertIn('ExecStop=', source)
        self.assertIn('RemainAfterExit=yes', source)
        self.assertNotIn('ExecStartPre=-', source)

    def test_rollback_and_rgb_snapshot(self):
        source = CONTROLLER.read_text()
        for requirement in ('original-rgb', 'rmmod linuwu_sense', 'modprobe acer_wmi',
                            'trap recover_failure EXIT', 'valid_rgb "$saved"'):
            self.assertIn(requirement, source)
        self.assertNotIn('pwm', source)
        self.assertNotIn('platform_profile=', source)

    def test_uninstall_refuses_when_replacement_loaded(self):
        source = UNINSTALL.read_text()
        self.assertIn("grep -q '^linuwu_sense ' /proc/modules", source)
        self.assertIn('systemctl stop', source)
        self.assertIn('modprobe acer_wmi', source)


if __name__ == '__main__':
    unittest.main()

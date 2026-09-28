"""Contract checks for the optional RGB PolicyKit authorization mode."""
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/install-rgb-helper-fedora.sh"


class PolicyInstallTests(unittest.TestCase):
    def test_default_is_not_cached(self):
        source = SCRIPT.read_text()
        self.assertIn("'') authorization='auth_admin'", source)
        self.assertIn("--cache-authorization) authorization='auth_admin_keep'", source)
        self.assertIn('<allow_active>${authorization}</allow_active>', source)

    def test_action_is_only_for_fixed_helper_and_active_session(self):
        source = SCRIPT.read_text()
        self.assertIn('<allow_any>no</allow_any>', source)
        self.assertIn('<allow_inactive>no</allow_inactive>', source)
        self.assertIn('/usr/local/libexec/nitro-control-rgb-helper</annotate>', source)
        self.assertNotIn('<allow_active>yes</allow_active>', source)


if __name__ == '__main__':
    unittest.main()

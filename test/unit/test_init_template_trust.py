"""Init templates: the trust decision (design §6)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm.init_template import InitTemplate
from ivpm.init_template.trust import is_trusted, trust_hint


class TestTrust(unittest.TestCase):

    # (origin, trusted as resolved by the registry) -> expected
    ROWS = [
        ("built-in", True, True),
        ("acme-templates", True, True),          # entry point
        ("site-config", True, True),             # SiteConfig hook
        ("site-config", False, False),           # hook opted out explicitly
        ("user-config", True, True),             # trust: true
        ("user-config", False, False),
        ("site-config-file", False, False),
        ("env", False, False),
        ("cli", False, False),
    ]

    def test_table(self):
        for origin, trusted, expect in self.ROWS:
            t = InitTemplate(name="t", src="/t", origin=origin, trusted=trusted)
            self.assertEqual(expect, is_trusted(t), (origin, trusted))
            self.assertTrue(is_trusted(t, cli_trust=True), origin)

    def test_unresolved_is_untrusted(self):
        self.assertFalse(is_trusted(InitTemplate(name="t", src="/t")))

    def test_hint(self):
        self.assertIn("trust: true", trust_hint(InitTemplate(name="t", src="/t",
                                                             origin="user-config")))
        h = trust_hint(InitTemplate(name="t", src="/t", origin="env"))
        self.assertIn("--trust", h)
        self.assertNotIn("trust: true", h)


if __name__ == "__main__":
    unittest.main()

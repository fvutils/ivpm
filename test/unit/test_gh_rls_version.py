import os, sys, unittest
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

from ivpm.pkg_types.package_gh_rls import PackageGhRls

class TestGhRlsVersion(unittest.TestCase):
    def mk_pkg(self, spec, prerelease=False):
        p = PackageGhRls("dummy")
        p.version = spec
        p.prerelease = prerelease
        return p

    def test_exact_match_with_v_prefix(self):
        releases = [
            {"tag_name": "v1.2.4", "prerelease": False},
            {"tag_name": "v1.2.3", "prerelease": False},
        ]
        pkg = self.mk_pkg("v1.2.3")
        sel = pkg._select_release_by_version(releases)
        self.assertIsNotNone(sel)
        self.assertEqual(sel["tag_name"], "v1.2.3")

    def test_exact_match_without_v_prefix(self):
        releases = [
            {"tag_name": "v1.2.4", "prerelease": False},
            {"tag_name": "v1.2.3", "prerelease": False},
        ]
        pkg = self.mk_pkg("1.2.3")
        sel = pkg._select_release_by_version(releases)
        self.assertIsNotNone(sel)
        self.assertEqual(sel["tag_name"], "v1.2.3")

    def test_gt_operator(self):
        releases = [
            {"tag_name": "v2.0.0", "prerelease": False},
            {"tag_name": "v1.5.0", "prerelease": False},
            {"tag_name": "v1.2.0", "prerelease": False},
        ]
        pkg = self.mk_pkg(">1.2.0")
        sel = pkg._select_release_by_version(releases)
        self.assertEqual(sel["tag_name"], "v2.0.0")

    def test_gte_operator(self):
        releases = [
            {"tag_name": "v1.5.1", "prerelease": False},
            {"tag_name": "v1.5.0", "prerelease": False},
            {"tag_name": "v1.4.9", "prerelease": False},
        ]
        pkg = self.mk_pkg(">=1.5.0")
        sel = pkg._select_release_by_version(releases)
        self.assertEqual(sel["tag_name"], "v1.5.1")

    def test_lt_operator(self):
        releases = [
            {"tag_name": "v2.0.0", "prerelease": False},
            {"tag_name": "v1.5.1", "prerelease": False},
            {"tag_name": "v1.5.0", "prerelease": False},
            {"tag_name": "v1.4.9", "prerelease": False},
        ]
        pkg = self.mk_pkg("<2.0.0")
        sel = pkg._select_release_by_version(releases)
        self.assertEqual(sel["tag_name"], "v1.5.1")

    def test_lte_operator(self):
        releases = [
            {"tag_name": "v1.5.1", "prerelease": False},
            {"tag_name": "v1.5.0", "prerelease": False},
            {"tag_name": "v1.4.9", "prerelease": False},
        ]
        pkg = self.mk_pkg("<=1.5.0")
        sel = pkg._select_release_by_version(releases)
        self.assertEqual(sel["tag_name"], "v1.5.0")

    def test_prerelease_excluded(self):
        releases = [
            {"tag_name": "v2.0.0", "prerelease": True},
            {"tag_name": "v1.9.0", "prerelease": False},
            {"tag_name": "v1.8.0", "prerelease": False},
        ]
        pkg = self.mk_pkg(">=1.0.0", prerelease=False)
        sel = pkg._select_release_by_version(releases)
        self.assertEqual(sel["tag_name"], "v1.9.0")

    def test_prerelease_included(self):
        releases = [
            {"tag_name": "v2.0.0", "prerelease": True},
            {"tag_name": "v1.9.0", "prerelease": False},
            {"tag_name": "v1.8.0", "prerelease": False},
        ]
        pkg = self.mk_pkg(">=1.0.0", prerelease=True)
        sel = pkg._select_release_by_version(releases)
        self.assertEqual(sel["tag_name"], "v2.0.0")

    def test_no_match_returns_none(self):
        releases = [
            {"tag_name": "v1.5.0", "prerelease": False},
        ]
        pkg = self.mk_pkg(">2.0.0")
        sel = pkg._select_release_by_version(releases)
        self.assertIsNone(sel)

    # --- Semver prefix (partial version) matching ---

    def test_partial_major_minor_picks_newest_patch(self):
        """v5.3 should match v5.3.4 (newest) over v5.3.3."""
        releases = [
            {"tag_name": "v5.4.0", "prerelease": False},
            {"tag_name": "v5.3.4", "prerelease": False},
            {"tag_name": "v5.3.3", "prerelease": False},
            {"tag_name": "v5.3.0", "prerelease": False},
        ]
        pkg = self.mk_pkg("v5.3")
        sel = pkg._select_release_by_version(releases)
        self.assertIsNotNone(sel)
        self.assertEqual(sel["tag_name"], "v5.3.4")

    def test_partial_major_minor_without_v_prefix(self):
        """5.3 (no leading v) should also match v5.3.4."""
        releases = [
            {"tag_name": "v5.3.4", "prerelease": False},
            {"tag_name": "v5.3.3", "prerelease": False},
        ]
        pkg = self.mk_pkg("5.3")
        sel = pkg._select_release_by_version(releases)
        self.assertIsNotNone(sel)
        self.assertEqual(sel["tag_name"], "v5.3.4")

    def test_partial_major_only_picks_newest(self):
        """5 should match the newest v5.x.y release."""
        releases = [
            {"tag_name": "v6.0.0", "prerelease": False},
            {"tag_name": "v5.3.4", "prerelease": False},
            {"tag_name": "v5.2.1", "prerelease": False},
        ]
        pkg = self.mk_pkg("5")
        sel = pkg._select_release_by_version(releases)
        self.assertIsNotNone(sel)
        self.assertEqual(sel["tag_name"], "v5.3.4")

    def test_partial_prerelease_excluded(self):
        """Partial spec should skip prerelease entries when prerelease=False."""
        releases = [
            {"tag_name": "v5.3.5-rc1", "prerelease": True},
            {"tag_name": "v5.3.4", "prerelease": False},
            {"tag_name": "v5.3.3", "prerelease": False},
        ]
        pkg = self.mk_pkg("v5.3", prerelease=False)
        sel = pkg._select_release_by_version(releases)
        self.assertIsNotNone(sel)
        self.assertEqual(sel["tag_name"], "v5.3.4")

    def test_partial_prerelease_included(self):
        """Partial spec should include prerelease entries when prerelease=True."""
        releases = [
            {"tag_name": "v5.3.5-rc1", "prerelease": True},
            {"tag_name": "v5.3.4", "prerelease": False},
        ]
        pkg = self.mk_pkg("v5.3", prerelease=True)
        sel = pkg._select_release_by_version(releases)
        self.assertIsNotNone(sel)
        self.assertEqual(sel["tag_name"], "v5.3.5-rc1")

    def test_leading_zeros_rejected(self):
        """Semver does not allow leading zeros: 05.3 should not match v5.3.4."""
        releases = [
            {"tag_name": "v5.3.4", "prerelease": False},
        ]
        pkg = self.mk_pkg("05.3")
        sel = pkg._select_release_by_version(releases)
        self.assertIsNone(sel)

    def test_full_three_part_spec_still_exact(self):
        """A full 3-part spec (5.3.4) should match exactly v5.3.4, not v5.3.5."""
        releases = [
            {"tag_name": "v5.3.5", "prerelease": False},
            {"tag_name": "v5.3.4", "prerelease": False},
            {"tag_name": "v5.3.3", "prerelease": False},
        ]
        pkg = self.mk_pkg("5.3.4")
        sel = pkg._select_release_by_version(releases)
        self.assertIsNotNone(sel)
        self.assertEqual(sel["tag_name"], "v5.3.4")

if __name__ == "__main__":
    unittest.main()

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

if __name__ == "__main__":
    unittest.main()

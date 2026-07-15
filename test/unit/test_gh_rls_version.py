import os, sys, unittest
from unittest import mock
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

    def test_leading_zeros_tolerated(self):
        """Leading zeros are tolerated (not strict SemVer): 05.3 matches v5.3.4.

        Reversed from earlier strict behavior so upstream zero-padded schemes
        (e.g. Verilator's '5.049') can be pinned.  '05.3' parses to (5, 3).
        """
        releases = [
            {"tag_name": "v5.3.4", "prerelease": False},
        ]
        pkg = self.mk_pkg("05.3")
        sel = pkg._select_release_by_version(releases)
        self.assertIsNotNone(sel)
        self.assertEqual(sel["tag_name"], "v5.3.4")

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

class TestGhRlsBuildStampScheme(unittest.TestCase):
    """Non-SemVer tag scheme: '{version}.{ci-build-id}' with a zero-padded minor.

    Real-world regression: edapack/verilator-bin tags releases as
    'v5.049.27094495137' -- a zero-padded Verilator minor plus a monotonic CI
    run id in the third slot.  Strict SemVer rejects the leading zero, so every
    tag failed to parse and only 'latest'/exact-tag selection worked.
    """

    # Newest-first, as GitHub returns them.
    RELEASES = [
        {"tag_name": "v5.051.28742674377", "prerelease": True},
        {"tag_name": "v5.049.28324256665", "prerelease": True},
        {"tag_name": "v5.049.27094495137", "prerelease": False},
        {"tag_name": "v5.049.26714312947", "prerelease": False},
        {"tag_name": "v5.047.23403476108", "prerelease": False},
    ]

    def mk_pkg(self, spec, prerelease=False):
        p = PackageGhRls("verilator")
        p.version = spec
        p.prerelease = prerelease
        return p

    def test_parses_zero_padded_minor_and_build_id(self):
        pkg = PackageGhRls("verilator")
        self.assertEqual(
            pkg._parse_version_tuple("v5.049.27094495137"), (5, 49, 27094495137))
        # A user may pin either spelling of the minor.
        self.assertEqual(pkg._parse_version_tuple("5.49"), (5, 49))
        self.assertEqual(pkg._parse_version_tuple("5.049"), (5, 49))

    def test_partial_minor_picks_newest_build_of_that_minor(self):
        """'5.049' should select the newest 5.049 build (highest CI id)."""
        pkg = self.mk_pkg("5.049")
        sel = pkg._select_release_by_version(self.RELEASES)
        self.assertIsNotNone(sel)
        # 5.049.28324256665 is newer but prerelease -> skipped; next is .27094495137.
        self.assertEqual(sel["tag_name"], "v5.049.27094495137")

    def test_partial_minor_includes_prerelease_when_requested(self):
        pkg = self.mk_pkg("5.049", prerelease=True)
        sel = pkg._select_release_by_version(self.RELEASES)
        self.assertEqual(sel["tag_name"], "v5.049.28324256665")

    def test_range_spec_across_zero_padded_minors(self):
        """'>=5.049' resolves numerically (build ids compare as ints, not strings)."""
        pkg = self.mk_pkg(">=5.049")
        sel = pkg._select_release_by_version(self.RELEASES)
        # Newest non-prerelease satisfying >=5.049.
        self.assertEqual(sel["tag_name"], "v5.049.27094495137")

    def test_exact_full_tag_still_matches(self):
        pkg = self.mk_pkg("5.049.26714312947")
        sel = pkg._select_release_by_version(self.RELEASES)
        self.assertEqual(sel["tag_name"], "v5.049.26714312947")


class _Resp:
    """Minimal stand-in for an httpx.Response."""
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text
        self.content = text.encode()


# Canned github.com web-endpoint payloads (o/r = owner/repo), newest-first,
# modeled on edapack/verilator-bin: newest is a prerelease, then a stable.
_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><id>tag:github.com,2008:Repository/1/v2.0.0</id><title>Release v2.0.0</title></entry>
  <entry><id>tag:github.com,2008:Repository/1/v1.5.0</id><title>Release v1.5.0</title></entry>
  <entry><id>tag:github.com,2008:Repository/1/v1.4.0</id><title>Release v1.4.0</title></entry>
</feed>"""

_RELEASES_HTML = """
<a href="/o/r/releases/tag/v2.0.0">v2.0.0</a> <span class="Label--orange">Pre-release</span>
<a href="/o/r/releases/tag/v1.5.0">v1.5.0</a> <span class="Label--success">Latest</span>
<a href="/o/r/releases/tag/v1.4.0">v1.4.0</a>
"""

_EXPANDED_ASSETS_v150 = """
<a href="/o/r/releases/download/v1.5.0/tool-manylinux_2_28_x86_64.tar.gz">a</a>
<a href="/o/r/releases/download/v1.5.0/tool-macos-arm64.tar.gz">b</a>
<a href="/o/r/releases/download/v1.5.0/tool-manylinux_2_28_x86_64.tar.gz">dup</a>
"""


def _router(**by_substr):
    """Return a fake httpx.get that dispatches on URL substring."""
    def _get(url, **kw):
        for sub, resp in by_substr.items():
            if sub in url:
                return resp
        return _Resp(404, "")
    return _get


class TestGhRlsWebResolution(unittest.TestCase):
    """Non-REST resolution via the atom feed + release pages."""

    def mk(self, spec="latest", prerelease=False, source=False):
        p = PackageGhRls("dummy")
        p.url = "https://github.com/o/r"
        p.version = spec
        p.prerelease = prerelease
        p.source = source
        return p

    def test_fetch_tags_atom_orders_newest_first(self):
        p = self.mk()
        with mock.patch("ivpm.pkg_types.package_gh_rls.httpx.get",
                        _router(**{"releases.atom": _Resp(200, _ATOM)})):
            self.assertEqual(p._fetch_tags_atom(), ["v2.0.0", "v1.5.0", "v1.4.0"])

    def test_fetch_tags_atom_empty_on_error(self):
        p = self.mk()
        with mock.patch("ivpm.pkg_types.package_gh_rls.httpx.get",
                        _router(**{"releases.atom": _Resp(404, "")})):
            self.assertEqual(p._fetch_tags_atom(), [])

    def test_prerelease_flags_parsed_from_html(self):
        p = self.mk()
        with mock.patch("ivpm.pkg_types.package_gh_rls.httpx.get",
                        _router(**{"/releases": _Resp(200, _RELEASES_HTML)})):
            flags = p._fetch_prerelease_flags_web()
        self.assertEqual(flags, {"v2.0.0": True, "v1.5.0": False, "v1.4.0": False})

    def test_prerelease_flags_none_when_unparseable(self):
        p = self.mk()
        with mock.patch("ivpm.pkg_types.package_gh_rls.httpx.get",
                        _router(**{"/releases": _Resp(200, "<html>no cards</html>")})):
            self.assertIsNone(p._fetch_prerelease_flags_web())

    def test_fetch_assets_web_dedupes_and_builds_urls(self):
        p = self.mk()
        with mock.patch("ivpm.pkg_types.package_gh_rls.httpx.get",
                        _router(**{"expanded_assets/v1.5.0": _Resp(200, _EXPANDED_ASSETS_v150)})):
            assets = p._fetch_assets_web("v1.5.0")
        self.assertEqual([a["name"] for a in assets],
                         ["tool-manylinux_2_28_x86_64.tar.gz", "tool-macos-arm64.tar.gz"])
        self.assertEqual(assets[0]["browser_download_url"],
                         "https://github.com/o/r/releases/download/v1.5.0/tool-manylinux_2_28_x86_64.tar.gz")

    def _full_router(self):
        return _router(**{
            "releases.atom": _Resp(200, _ATOM),
            "expanded_assets/v2.0.0": _Resp(200, _EXPANDED_ASSETS_v150.replace("v1.5.0", "v2.0.0")),
            "expanded_assets/v1.5.0": _Resp(200, _EXPANDED_ASSETS_v150),
            "/releases": _Resp(200, _RELEASES_HTML),  # substring fallback for the page
        })

    def test_resolve_latest_skips_prerelease(self):
        # source=True keeps asset selection platform-independent.
        p = self.mk("latest", prerelease=False, source=True)
        with mock.patch("ivpm.pkg_types.package_gh_rls.httpx.get", self._full_router()):
            rls_info, rls, file_url, forced_ext = p._resolve_release_web()
        self.assertEqual(rls["tag_name"], "v1.5.0")  # v2.0.0 is prerelease -> skipped
        self.assertTrue(file_url.endswith("/archive/refs/tags/v1.5.0.tar.gz"))

    def test_resolve_latest_includes_prerelease(self):
        p = self.mk("latest", prerelease=True, source=True)
        with mock.patch("ivpm.pkg_types.package_gh_rls.httpx.get", self._full_router()):
            _, rls, _, _ = p._resolve_release_web()
        self.assertEqual(rls["tag_name"], "v2.0.0")

    def test_resolve_explicit_version(self):
        p = self.mk("1.5", prerelease=False, source=True)
        with mock.patch("ivpm.pkg_types.package_gh_rls.httpx.get", self._full_router()):
            _, rls, _, _ = p._resolve_release_web()
        self.assertEqual(rls["tag_name"], "v1.5.0")

    def test_resolve_returns_none_on_empty_atom(self):
        """Empty feed (tag-only repo) -> None so caller falls back to REST."""
        p = self.mk("latest", source=True)
        empty = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
        with mock.patch("ivpm.pkg_types.package_gh_rls.httpx.get",
                        _router(**{"releases.atom": _Resp(200, empty)})):
            self.assertIsNone(p._resolve_release_web())

    def test_resolve_returns_none_when_prerelease_undeterminable(self):
        """Atom OK but releases page unparseable -> None (unsafe to filter)."""
        p = self.mk("latest", prerelease=False, source=True)
        with mock.patch("ivpm.pkg_types.package_gh_rls.httpx.get",
                        _router(**{"releases.atom": _Resp(200, _ATOM),
                                   "/releases": _Resp(404, "")})):
            self.assertIsNone(p._resolve_release_web())

    def test_dispatcher_falls_back_to_rest(self):
        """When the web path returns None, _resolve_release calls the REST path."""
        p = self.mk("latest", source=True)
        with mock.patch.object(p, "_resolve_release_web", return_value=None) as web, \
             mock.patch.object(p, "_resolve_release_rest", return_value=("REST",)) as rest:
            self.assertEqual(p._resolve_release(), ("REST",))
        web.assert_called_once()
        rest.assert_called_once()

    def test_dispatcher_falls_back_on_web_exception(self):
        p = self.mk("latest", source=True)
        with mock.patch.object(p, "_resolve_release_web", side_effect=RuntimeError("boom")), \
             mock.patch.object(p, "_resolve_release_rest", return_value=("REST",)) as rest:
            self.assertEqual(p._resolve_release(), ("REST",))
        rest.assert_called_once()


class TestGhRlsChecksumFiltering(unittest.TestCase):
    """Checksum/signature companion assets must never be selected for download.

    Regression test: nextpnr-bin releases ship a `.tar.gz.sha256` next to each
    `.tar.gz`.  The platform tag embedded in the checksum file's name made it a
    selection candidate, and a version tie let it win over the real archive,
    producing 'unsupported archive type .sha256'.
    """

    def _assets(self, *names):
        return [{"name": n, "browser_download_url": "http://example/" + n} for n in names]

    def test_is_checksum_or_sig(self):
        pkg = PackageGhRls("dummy")
        for n in ("foo.tar.gz.sha256", "FOO.TAR.GZ.SHA512", "foo.tar.gz.asc",
                  "foo.zip.md5", "foo.sig", "foo.minisig"):
            self.assertTrue(pkg._is_checksum_or_sig(n), n)
        for n in ("foo.tar.gz", "foo.zip", "foo.tar.xz", "manifest.json"):
            self.assertFalse(pkg._is_checksum_or_sig(n), n)

    def test_select_linux_asset_skips_checksum(self):
        pkg = PackageGhRls("dummy")
        ver = "0.10.20260604"
        names = [
            "manifest.json",
            "nextpnr-bin-manylinux_2_28_x86_64-%s.tar.gz" % ver,
            "nextpnr-bin-manylinux_2_28_x86_64-%s.tar.gz.sha256" % ver,
            "nextpnr-bin-manylinux_2_34_x86_64-%s.tar.gz" % ver,
            "nextpnr-bin-manylinux_2_34_x86_64-%s.tar.gz.sha256" % ver,
        ]
        assets = [a for a in self._assets(*names) if not pkg._is_checksum_or_sig(a["name"])]
        # glibc 2.39 -> newest eligible manylinux (2.34), and never a .sha256
        sel = pkg._select_linux_asset(assets, "x86_64", (2, 39))
        self.assertIsNotNone(sel)
        self.assertEqual(sel["name"], "nextpnr-bin-manylinux_2_34_x86_64-%s.tar.gz" % ver)
        self.assertFalse(pkg._is_checksum_or_sig(sel["name"]))


if __name__ == "__main__":
    unittest.main()

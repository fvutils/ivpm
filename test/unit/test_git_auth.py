"""Unit tests for git transport/auth selection.

Covers the URL helpers, the auth-order resolution (CLI/env/config-file/site),
the layered user+site config files, and the per-package preference logic in
PackageGit (including the `anonymous: false` deferral fix).
"""
import os
import sys
import tempfile
import textwrap
import types
import unittest
from unittest.mock import patch

# Ensure src is on the path (mirrors CI setup)
_ROOTDIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOTDIR, "src"))

import ivpm.utils as utils
from ivpm.utils import https_to_ssh_url, url_host, resolve_clone_url
import ivpm.site_config as sc
from ivpm.site_config import (
    SiteConfig, DefaultSiteConfig, parse_git_auth_order, resolve_git_auth_order,
    loaded_config_paths, reset_site_config,
)
import ivpm.pkg_types.package_git as pg


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

class TestUrlHelpers(unittest.TestCase):

    def test_https_to_ssh(self):
        self.assertEqual(https_to_ssh_url("https://github.com/o/r"),
                         "git@github.com:o/r")
        self.assertEqual(https_to_ssh_url("http://gitlab.com/a/b"),
                         "git@gitlab.com:a/b")

    def test_https_to_ssh_leaves_others_unchanged(self):
        for url in ("git@github.com:o/r", "file:///tmp/x", "/local/path",
                    "../sibling", "ssh://git@host.example/o/r.git",
                    "ssh://git@host.example:222/o/r.git",
                    "git://host.example/o/r.git"):
            self.assertEqual(https_to_ssh_url(url), url)

    def test_url_host(self):
        self.assertEqual(url_host("https://github.com/o/r"), "github.com")
        self.assertEqual(url_host("https://user@host.example/x"), "host.example")
        self.assertEqual(url_host("https://host.example:8443/x"), "host.example")

    def test_url_host_none_for_non_urls(self):
        for url in ("git@github.com:o/r", "/local/path", "file:///tmp/x"):
            self.assertIsNone(url_host(url))


# ---------------------------------------------------------------------------
# parse_git_auth_order
# ---------------------------------------------------------------------------

class TestParseOrder(unittest.TestCase):

    def test_parse_string(self):
        self.assertEqual(parse_git_auth_order("gh, ssh ,HTTPS"),
                         ["gh", "ssh", "https"])

    def test_parse_list(self):
        self.assertEqual(parse_git_auth_order(["GH", " ssh "]), ["gh", "ssh"])

    def test_parse_drops_empties(self):
        self.assertEqual(parse_git_auth_order("gh,,  ,ssh"), ["gh", "ssh"])


# ---------------------------------------------------------------------------
# resolve_clone_url (pure logic; auth_order passed explicitly)
# ---------------------------------------------------------------------------

class TestResolveCloneUrl(unittest.TestCase):

    URL = "https://github.com/o/r"
    SSH = "git@github.com:o/r"

    def _resolve(self, ssh_pref, order, gh_ok):
        with patch.object(utils, "gh_auth_available", lambda h: gh_ok):
            return resolve_clone_url(self.URL, ssh_pref, order)

    def test_force_ssh(self):
        self.assertEqual(self._resolve(True, None, True), self.SSH)

    def test_force_https(self):
        self.assertEqual(self._resolve(False, None, True), self.URL)

    def test_auto_gh_authed(self):
        self.assertEqual(self._resolve(None, ["gh", "ssh"], True), self.URL)

    def test_auto_gh_not_authed_falls_to_ssh(self):
        self.assertEqual(self._resolve(None, ["gh", "ssh"], False), self.SSH)

    def test_order_ssh_terminal(self):
        self.assertEqual(self._resolve(None, ["ssh", "gh"], True), self.SSH)

    def test_order_https_terminal(self):
        self.assertEqual(self._resolve(None, ["gh", "https"], False), self.URL)

    def test_empty_or_unknown_order_falls_back_to_ssh(self):
        self.assertEqual(self._resolve(None, ["bogus"], True), self.SSH)
        self.assertEqual(self._resolve(None, [], True), self.SSH)


# ---------------------------------------------------------------------------
# gh_auth_available
# ---------------------------------------------------------------------------

class TestGhDetection(unittest.TestCase):

    def tearDown(self):
        utils.gh_auth_available.cache_clear()

    def test_gh_missing_returns_false(self):
        utils.gh_auth_available.cache_clear()
        with patch("ivpm.utils.subprocess.run", side_effect=FileNotFoundError()):
            self.assertFalse(utils.gh_auth_available("github.com"))

    def test_empty_host_returns_false(self):
        self.assertFalse(utils.gh_auth_available(None))
        self.assertFalse(utils.gh_auth_available(""))

    def test_rc_zero_is_true(self):
        utils.gh_auth_available.cache_clear()
        fake = types.SimpleNamespace(returncode=0)
        with patch("ivpm.utils.subprocess.run", return_value=fake):
            self.assertTrue(utils.gh_auth_available("github.com"))

    def test_rc_nonzero_is_false(self):
        utils.gh_auth_available.cache_clear()
        fake = types.SimpleNamespace(returncode=1)
        with patch("ivpm.utils.subprocess.run", return_value=fake):
            self.assertFalse(utils.gh_auth_available("ghe.example"))

    def test_result_cached_per_host(self):
        """gh is probed at most once per host (cached), but per distinct host."""
        utils.gh_auth_available.cache_clear()
        fake = types.SimpleNamespace(returncode=0)
        with patch("ivpm.utils.subprocess.run", return_value=fake) as m:
            utils.gh_auth_available("github.com")
            utils.gh_auth_available("github.com")   # served from cache
            utils.gh_auth_available("other.example")  # distinct host
        self.assertEqual(m.call_count, 2)


# ---------------------------------------------------------------------------
# Layered config files (user + site)
# ---------------------------------------------------------------------------

class TestConfigFiles(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        # Pin the Python site config to the default so an installed
        # ivpm_site_config can't perturb these file/env-precedence tests.
        self._p = patch("ivpm.site_config.get_site_config",
                        return_value=DefaultSiteConfig())
        self._p.start()
        self.addCleanup(self._p.stop)
        reset_site_config()

    def tearDown(self):
        reset_site_config()

    def _write(self, name, text):
        path = os.path.join(self._dir, name)
        with open(path, "w") as fp:
            fp.write(textwrap.dedent(text))
        return path

    def _env(self, user=None, site=None, order_env=None):
        env = {
            "IVPM_USER_CONFIG": user or os.path.join(self._dir, "no-user.yaml"),
            "IVPM_SITE_CONFIG": site or os.path.join(self._dir, "no-site.yaml"),
        }
        if order_env is not None:
            env["IVPM_GIT_AUTH_ORDER"] = order_env
        else:
            env["IVPM_GIT_AUTH_ORDER"] = ""
        return env

    def test_user_rule_overrides_site_rule(self):
        user = self._write("u.yaml", """
            git-auth:
              - host: "github.com"
                order: [https]
            """)
        site = self._write("s.yaml", """
            git-auth:
              - host: "github.com"
                order: [gh, ssh]
            """)
        with patch.dict(os.environ, self._env(user, site)):
            reset_site_config()
            self.assertEqual(resolve_git_auth_order("github.com"), ["https"])

    def test_site_rule_when_no_user_match(self):
        site = self._write("s.yaml", """
            git-auth:
              - host: "*.internal.corp"
                order: [ssh]
            """)
        with patch.dict(os.environ, self._env(site=site)):
            reset_site_config()
            self.assertEqual(resolve_git_auth_order("git.internal.corp"), ["ssh"])

    def test_default_order_from_user_file(self):
        user = self._write("u.yaml", "git-auth-order: [gh, ssh, https]\n")
        with patch.dict(os.environ, self._env(user=user)):
            reset_site_config()
            self.assertEqual(resolve_git_auth_order("bitbucket.org"),
                             ["gh", "ssh", "https"])

    def test_matching_rule_beats_env(self):
        site = self._write("s.yaml", """
            git-auth:
              - host: "*.internal.corp"
                order: [ssh]
            """)
        with patch.dict(os.environ, self._env(site=site, order_env="https")):
            reset_site_config()
            # env applies to unmatched hosts only
            self.assertEqual(resolve_git_auth_order("git.internal.corp"), ["ssh"])
            self.assertEqual(resolve_git_auth_order("elsewhere.org"), ["https"])

    def test_default_site_default_when_nothing(self):
        with patch.dict(os.environ, self._env()):
            reset_site_config()
            self.assertEqual(resolve_git_auth_order("github.com"), ["gh", "ssh"])

    def test_malformed_file_ignored(self):
        bad = self._write("bad.yaml", "git-auth: [broken: : :\n  - nope")
        with patch.dict(os.environ, self._env(user=bad)):
            reset_site_config()
            # falls back to default; the bad file is not in loaded paths
            self.assertEqual(resolve_git_auth_order("github.com"), ["gh", "ssh"])
            self.assertEqual(loaded_config_paths(), [])

    def test_non_mapping_file_ignored(self):
        lst = self._write("list.yaml", "- a\n- b\n")
        with patch.dict(os.environ, self._env(user=lst)):
            reset_site_config()
            self.assertEqual(loaded_config_paths(), [])

    def test_loaded_paths_reports_found_files(self):
        user = self._write("u.yaml", "git-auth-order: [ssh]\n")
        with patch.dict(os.environ, self._env(user=user)):
            reset_site_config()
            self.assertEqual(loaded_config_paths(), [user])


# ---------------------------------------------------------------------------
# PackageGit transport preference
# ---------------------------------------------------------------------------

class TestPackageGitPref(unittest.TestCase):

    URL = "https://github.com/o/r"
    SSH = "git@github.com:o/r"

    def _eff(self, ssh=None, anon=None, args_ssh=False, args_anon=False,
             order=None, gh_ok=True):
        p = pg.PackageGit("p")
        p.url = self.URL
        p.ssh = ssh
        p.anonymous = anon
        args = types.SimpleNamespace(ssh=args_ssh, anonymous=args_anon,
                                     git_auth_order=order)
        ui = types.SimpleNamespace(args=args)
        with patch.object(utils, "gh_auth_available", lambda h: gh_ok):
            return p._get_effective_url(ui)

    def test_pkg_ssh_true_forces_ssh(self):
        self.assertEqual(self._eff(ssh=True, gh_ok=True), self.SSH)

    def test_pkg_ssh_false_forces_https(self):
        self.assertEqual(self._eff(ssh=False, gh_ok=False), self.URL)

    def test_anonymous_true_forces_https(self):
        self.assertEqual(self._eff(anon=True, gh_ok=False), self.URL)

    def test_anonymous_false_defers_to_order(self):
        # The Q2 fix: anonymous:false no longer forces SSH; gh can win.
        self.assertEqual(self._eff(anon=False, order=["gh", "ssh"], gh_ok=True),
                         self.URL)
        self.assertEqual(self._eff(anon=False, order=["gh", "ssh"], gh_ok=False),
                         self.SSH)

    def test_cli_ssh_forces_ssh(self):
        self.assertEqual(self._eff(args_ssh=True, gh_ok=True), self.SSH)

    def test_cli_anonymous_forces_https(self):
        self.assertEqual(self._eff(args_anon=True, gh_ok=False), self.URL)

    def test_cli_git_auth_order_used(self):
        self.assertEqual(self._eff(order=["https"], gh_ok=False), self.URL)

    def test_auto_default(self):
        self.assertEqual(self._eff(order=["gh", "ssh"], gh_ok=True), self.URL)
        self.assertEqual(self._eff(order=["gh", "ssh"], gh_ok=False), self.SSH)


if __name__ == "__main__":
    unittest.main()

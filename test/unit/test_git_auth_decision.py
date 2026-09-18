"""Unit tests for the git auth *decision trace* (plan Phase 0).

The trace answers "why did IVPM choose this transport and these credentials
for this package?".  These tests pin the provenance vocabulary, because a
trace that says "from a config file" instead of naming the file is no more
useful than the silence it replaced.
"""
import os
import tempfile
import textwrap
import types
import unittest
from unittest.mock import patch

import ivpm.utils as utils
import ivpm.git_auth as git_auth
from ivpm.site_config import (
    DefaultSiteConfig, SiteConfig, reset_site_config, resolve_git_auth_order_ex,
    apply_git_url_map_ex,
)
import ivpm.pkg_types.package_git as pg


URL = "https://github.com/o/r.git"
SSH = "git@github.com:o/r.git"


class _RulesSiteConfig(SiteConfig):
    """Site config with a host rule, to pin the site-config rule provenance."""

    def get_default_cache_dir(self):
        return ""

    def get_ivpm_install_args(self):
        return ["ivpm"]

    def get_git_auth_rules(self):
        return [("github.com", ["https"])]


class _DecisionTestBase(unittest.TestCase):

    def setUp(self):
        utils.gh_auth_available.cache_clear()
        self._dir = tempfile.mkdtemp()
        reset_site_config()
        self.addCleanup(reset_site_config)
        self.addCleanup(utils.gh_auth_available.cache_clear)

    def _write(self, name, text):
        path = os.path.join(self._dir, name)
        with open(path, "w") as fp:
            fp.write(textwrap.dedent(text))
        return path

    def _env(self, user=None, site=None, order_env=""):
        return {
            "IVPM_USER_CONFIG": user or os.path.join(self._dir, "no-user.yaml"),
            "IVPM_SITE_CONFIG": site or os.path.join(self._dir, "no-site.yaml"),
            "IVPM_GIT_AUTH_ORDER": order_env,
            "IVPM_GIT_URL_MAP": "",
        }

    def _decide(self, ssh_pref=None, order=None, gh_ok=True, url=URL,
                ssh_pref_source=None):
        with patch.object(utils, "gh_auth_available", lambda h: gh_ok):
            return git_auth.clone_url_candidates_ex(
                url, ssh_pref, order, ssh_pref_source=ssh_pref_source)


# ---------------------------------------------------------------------------
# auth_order_source -- one assertion per layer
# ---------------------------------------------------------------------------

class TestAuthOrderSource(_DecisionTestBase):

    def test_cli_flag(self):
        _, dec = self._decide(order=["https"])
        self.assertEqual(dec.auth_order_source, "cli:--git-auth-order")

    def test_env_var(self):
        with patch.dict(os.environ, self._env(order_env="ssh")):
            reset_site_config()
            order, source = resolve_git_auth_order_ex("github.com")
        self.assertEqual(order, ["ssh"])
        self.assertEqual(source, "env:IVPM_GIT_AUTH_ORDER")

    def test_user_config_host_rule(self):
        user = self._write("u.yaml", """
            git-auth:
              - host: "gith*.com"
                order: [https]
            """)
        with patch.dict(os.environ, self._env(user=user)):
            reset_site_config()
            order, source = resolve_git_auth_order_ex("github.com")
        self.assertEqual(order, ["https"])
        self.assertEqual(source, 'host-rule:gith*.com (user-config:%s)' % user)

    def test_site_config_file_default(self):
        site = self._write("s.yaml", "git-auth-order: [ssh, https]\n")
        with patch.dict(os.environ, self._env(site=site)):
            reset_site_config()
            order, source = resolve_git_auth_order_ex("example.org")
        self.assertEqual(order, ["ssh", "https"])
        self.assertEqual(source, "site-config-file:%s" % site)

    def test_user_config_file_default(self):
        user = self._write("u.yaml", "git-auth-order: [https]\n")
        with patch.dict(os.environ, self._env(user=user)):
            reset_site_config()
            _, source = resolve_git_auth_order_ex("example.org")
        self.assertEqual(source, "user-config:%s" % user)

    def test_site_config_host_rule(self):
        with patch.dict(os.environ, self._env()), \
             patch("ivpm.site_config.get_site_config",
                   return_value=_RulesSiteConfig()):
            reset_site_config()
            order, source = resolve_git_auth_order_ex("github.com")
        self.assertEqual(order, ["https"])
        self.assertEqual(source,
                         "host-rule:github.com (site-config:_RulesSiteConfig)")

    def test_site_config_default(self):
        with patch.dict(os.environ, self._env()), \
             patch("ivpm.site_config.get_site_config",
                   return_value=DefaultSiteConfig()):
            reset_site_config()
            order, source = resolve_git_auth_order_ex("github.com")
        self.assertEqual(order, ["gh", "ssh"])
        self.assertEqual(source, "site-config-default:DefaultSiteConfig")


# ---------------------------------------------------------------------------
# url_map_rule / ssh_pref_source
# ---------------------------------------------------------------------------

class TestUrlMapProvenance(_DecisionTestBase):

    def test_names_the_matched_rule(self):
        with patch.dict(os.environ, {
                "IVPM_GIT_URL_MAP": "https://github.com/o/=file:///mirror/o/"}):
            result, rule = apply_git_url_map_ex(URL)
        self.assertEqual(result, "file:///mirror/o/r.git")
        self.assertIn("https://github.com/o/", rule)
        self.assertIn("file:///mirror/o/", rule)

    def test_none_when_no_rule_matched(self):
        with patch.dict(os.environ, self._env()):
            reset_site_config()
            result, rule = apply_git_url_map_ex(URL)
        self.assertEqual(result, URL)
        self.assertIsNone(rule)

    def test_decision_carries_the_rule(self):
        with patch.dict(os.environ, {
                "IVPM_GIT_URL_MAP": "https://github.com/o/=file:///mirror/o/",
                "IVPM_USER_CONFIG": os.path.join(self._dir, "nope.yaml"),
                "IVPM_SITE_CONFIG": os.path.join(self._dir, "nope.yaml")}):
            reset_site_config()
            _, dec = self._decide(order=["https"])
        self.assertIsNotNone(dec.url_map_rule)
        self.assertEqual(dec.mapped_url, "file:///mirror/o/r.git")
        self.assertEqual(dec.declared_url, URL)


class TestSshPrefSource(_DecisionTestBase):

    def _pref(self, ssh=None, anon=None, args_ssh=False, args_anon=False):
        p = pg.PackageGit("p")
        p.url = URL
        p.ssh = ssh
        p.anonymous = anon
        args = types.SimpleNamespace(ssh=args_ssh, anonymous=args_anon,
                                     git_auth_order=None)
        return p._ssh_pref_ex(types.SimpleNamespace(args=args))

    def test_package_ssh(self):
        self.assertEqual(self._pref(ssh=True), (True, "package:ssh"))
        self.assertEqual(self._pref(ssh=False), (False, "package:ssh"))

    def test_package_anonymous(self):
        self.assertEqual(self._pref(anon=True), (False, "package:anonymous"))

    def test_cli_flags(self):
        self.assertEqual(self._pref(args_ssh=True), (True, "cli:--ssh"))
        self.assertEqual(self._pref(args_anon=True), (False, "cli:--anonymous"))

    def test_no_override(self):
        self.assertEqual(self._pref(), (None, None))

    def test_source_reaches_the_decision(self):
        _, dec = self._decide(ssh_pref=True, ssh_pref_source="cli:--ssh")
        self.assertEqual(dec.ssh_pref_source, "cli:--ssh")
        self.assertIn("cli:--ssh", dec.format())


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

class TestDecisionSteps(_DecisionTestBase):

    def _steps(self, **kw):
        _, dec = self._decide(**kw)
        return dec

    def test_gh_accepted_records_rc_zero(self):
        dec = self._steps(order=["gh", "ssh"], gh_ok=True)
        self.assertEqual(dec.chosen_method, "gh")
        self.assertEqual(dec.steps[0][0], "gh")
        self.assertEqual(dec.steps[0][1], "ACCEPTED")
        self.assertIn("rc=0", dec.steps[0][2])

    def test_gh_rejected_records_the_reason(self):
        dec = self._steps(order=["gh", "ssh"], gh_ok=False)
        self.assertEqual(dec.chosen_method, "ssh")
        self.assertEqual(dec.steps[0][1], "rejected")
        self.assertIn("not authenticated", dec.steps[0][2])

    def test_later_methods_are_not_evaluated(self):
        dec = self._steps(order=["gh", "ssh"], gh_ok=True)
        self.assertEqual(dec.steps[1][0], "ssh")
        self.assertEqual(dec.steps[1][1], "not evaluated (gh won)")

    def test_unrecognized_token_is_reported(self):
        # Regression: a typo'd order used to degrade to the SSH fallback with
        # no message at all.
        dec = self._steps(order=["gh", "shh"], gh_ok=False)
        outcomes = [o for _, o, _ in dec.steps]
        self.assertIn("ignored: unrecognized method 'shh'", outcomes)
        # Nothing applied, so the historical SSH fallback still wins.
        self.assertEqual(dec.effective_url, SSH)
        self.assertEqual(dec.chosen_method, "ssh")

    def test_credential_injection_reported(self):
        with patch.object(git_auth, "gh_path", lambda: "/usr/bin/gh"):
            dec = self._steps(order=["gh", "ssh"], gh_ok=True)
        self.assertEqual(dec.credential_injection, "gh-helper:/usr/bin/gh")

    def test_credential_injection_none_for_plain_https(self):
        dec = self._steps(order=["https"], gh_ok=True)
        self.assertEqual(dec.credential_injection,
                         "none (relying on user git config)")

    def test_credential_injection_na_for_ssh(self):
        dec = self._steps(order=["ssh"], gh_ok=True)
        self.assertEqual(dec.credential_injection, "n/a (ssh)")


class TestProbedVsCached(_DecisionTestBase):

    def test_second_package_on_a_host_reports_cached(self):
        fake = types.SimpleNamespace(returncode=0)
        with patch("ivpm.utils.subprocess.run", return_value=fake):
            _, first = git_auth.clone_url_candidates_ex(URL, None, ["gh", "ssh"])
            _, second = git_auth.clone_url_candidates_ex(URL, None, ["gh", "ssh"])
        self.assertIn("(probed)", first.steps[0][2])
        self.assertIn("(cached)", second.steps[0][2])

    def test_formatting_does_not_re_derive(self):
        """Building/formatting a decision must not re-probe gh.

        Re-deriving could observe different cache state and report something
        that is not what actually happened.
        """
        calls = []

        def _counting(host):
            calls.append(host)
            return True

        with patch.object(utils, "gh_auth_available", _counting):
            _, dec = git_auth.clone_url_candidates_ex(URL, None, ["gh", "ssh"])
            before = len(calls)
            text = dec.format()
            dec.as_dict()
        self.assertEqual(len(calls), before)
        self.assertEqual(before, 1)
        self.assertIn("chosen", text)


class TestDecisionFormat(_DecisionTestBase):

    #: Anything that looks like a credential must never appear in a trace.
    _TOKEN_SHAPES = ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_")

    def test_no_token_shaped_strings(self):
        with patch.dict(os.environ, self._env()):
            reset_site_config()
            _, dec = self._decide(order=["gh", "ssh"], gh_ok=True)
        text = dec.format()
        for shape in self._TOKEN_SHAPES:
            self.assertNotIn(shape, text)

    def test_format_names_every_field(self):
        _, dec = self._decide(order=["gh", "ssh"], gh_ok=True)
        text = dec.format()
        for label in ("declared url", "url-map", "host", "auth order",
                      "chosen", "effective url", "credentials"):
            self.assertIn(label, text)


if __name__ == "__main__":
    unittest.main()

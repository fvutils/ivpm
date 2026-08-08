"""Unit tests for git-url-map (on-the-fly git URL rewriting).

Covers boundary-aligned path matching, most-specific (path-depth) selection,
wildcards + captures, env/file layering, the IVPM_GIT_URL_MAP fast-path, and
integration through resolve_clone_url (remap-before-auth).
"""
import os
import sys
import unittest
from unittest.mock import patch

# Ensure src is on the path (mirrors CI setup)
_ROOTDIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOTDIR, "src"))

import ivpm.utils as utils
from ivpm.utils import resolve_clone_url
import ivpm.site_config as sc
from ivpm.site_config import (
    apply_git_url_map, _env_git_url_map, _file_git_url_map, reset_site_config,
)


def _apply(rules, url, env=""):
    """apply_git_url_map with *rules* as the file rules and *env* as IVPM_GIT_URL_MAP."""
    with patch.object(sc, "_file_git_url_map", lambda: list(rules)):
        with patch.dict(os.environ, {"IVPM_GIT_URL_MAP": env}):
            return apply_git_url_map(url)


# ---------------------------------------------------------------------------
# Boundary-aligned prefix matching
# ---------------------------------------------------------------------------

class TestBoundaryMatch(unittest.TestCase):

    RULE = [("https://github.com/ORG", "file:///repos/ORG")]

    def test_tail_appended(self):
        self.assertEqual(_apply(self.RULE, "https://github.com/ORG/lib.git"),
                         "file:///repos/ORG/lib.git")

    def test_exact_match(self):
        self.assertEqual(_apply(self.RULE, "https://github.com/ORG"),
                         "file:///repos/ORG")

    def test_no_partial_segment_match(self):
        # "ORG" must not match the "ORGANIZATION" segment
        self.assertEqual(_apply(self.RULE, "https://github.com/ORGANIZATION/x"),
                         "https://github.com/ORGANIZATION/x")

    def test_non_matching_passthrough(self):
        self.assertEqual(_apply(self.RULE, "https://github.com/OTHER/x"),
                         "https://github.com/OTHER/x")

    def test_dot_git_is_a_boundary(self):
        # A repo-scoped rule must match the ".git" spelling manifests use.
        # Regression: this silently fell through to upstream, because ".git"
        # satisfied neither "/" nor end-of-string.
        rule = [("https://github.com/owner/repo", "ssh://git@host:222/org/repo")]
        self.assertEqual(_apply(rule, "https://github.com/owner/repo.git"),
                         "ssh://git@host:222/org/repo.git")

    def test_dot_git_boundary_with_trailing_path(self):
        rule = [("https://github.com/owner/repo", "ssh://git@host:222/org/repo")]
        self.assertEqual(_apply(rule, "https://github.com/owner/repo.git/info/refs"),
                         "ssh://git@host:222/org/repo.git/info/refs")

    def test_dot_git_does_not_weaken_segment_boundary(self):
        # ".git" must not let a prefix match a longer segment.
        rule = [("https://github.com/owner/repo", "file:///m/repo")]
        for url in ("https://github.com/owner/repo-extras.git",
                    "https://github.com/owner/repository.git",
                    "https://github.com/owner/repogit"):
            self.assertEqual(_apply(rule, url), url)

    def test_no_rules_passthrough(self):
        self.assertEqual(_apply([], "https://github.com/o/r"),
                         "https://github.com/o/r")

    def test_trailing_slash_prefix(self):
        self.assertEqual(
            _apply([("https://github.com/", "file:///repos/")],
                   "https://github.com/o/lib.git"),
            "file:///repos/o/lib.git")


# ---------------------------------------------------------------------------
# Most-specific selection (by path-element depth)
# ---------------------------------------------------------------------------

class TestMostSpecific(unittest.TestCase):

    def test_deeper_literal_beats_wildcard(self):
        # a-b/c (2 segments) beats a-* (1 segment) even though a-* also matches,
        # and even though the wildcard rule is declared first (not first-match).
        rules = [("https://github.com/a-*", "file:///Q"),
                 ("https://github.com/a-b/c", "file:///P")]
        self.assertEqual(_apply(rules, "https://github.com/a-b/c"), "file:///P")

    def test_deeper_prefix_beats_shallow(self):
        rules = [("https://github.com/", "file:///A/"),
                 ("https://github.com/fvutils/", "file:///B/")]
        self.assertEqual(_apply(rules, "https://github.com/fvutils/a"),
                         "file:///B/a")
        self.assertEqual(_apply(rules, "https://github.com/fvutils/b"),
                         "file:///B/b")

    def test_shallow_used_when_deep_does_not_match(self):
        rules = [("https://github.com/", "file:///A/"),
                 ("https://github.com/fvutils/", "file:///B/")]
        self.assertEqual(_apply(rules, "https://github.com/other/bar"),
                         "file:///A/other/bar")

    def test_literal_breaks_tie_within_same_depth(self):
        # same depth (1 segment): the literal a-b beats the wildcard a-*
        rules = [("https://github.com/a-*", "file:///Q/"),
                 ("https://github.com/a-b", "file:///P")]
        self.assertEqual(_apply(rules, "https://github.com/a-b/x"),
                         "file:///P/x")


# ---------------------------------------------------------------------------
# Wildcards and captures
# ---------------------------------------------------------------------------

class TestWildcards(unittest.TestCase):

    def test_single_segment_capture(self):
        rules = [("https://github.com/*/", "file:///mirror/\\1/")]
        self.assertEqual(_apply(rules, "https://github.com/fvutils/lib.git"),
                         "file:///mirror/fvutils/lib.git")

    def test_star_does_not_cross_segment(self):
        # '*' matches within one segment; org "a/b" is not a single segment
        rules = [("https://github.com/*", "file:///m/\\1")]
        self.assertEqual(_apply(rules, "https://github.com/org/repo.git"),
                         "file:///m/org/repo.git")   # \1=org, tail=/repo.git

    def test_doublestar_crosses_segments(self):
        rules = [("https://github.com/**", "file:///all/\\1")]
        self.assertEqual(_apply(rules, "https://github.com/a/b/c.git"),
                         "file:///all/a/b/c.git")


# ---------------------------------------------------------------------------
# Layering / tie-break ordering
# ---------------------------------------------------------------------------

class TestOrdering(unittest.TestCase):

    def test_env_wins_tie_over_file(self):
        rules = [("https://github.com/x", "file:///FILE")]
        self.assertEqual(
            _apply(rules, "https://github.com/x",
                   env="https://github.com/x=file:///ENV"),
            "file:///ENV")

    def test_first_file_rule_wins_tie(self):
        rules = [("https://github.com/x", "file:///1"),
                 ("https://github.com/x", "file:///2")]
        self.assertEqual(_apply(rules, "https://github.com/x"), "file:///1")


# ---------------------------------------------------------------------------
# IVPM_GIT_URL_MAP parsing
# ---------------------------------------------------------------------------

class TestEnvParsing(unittest.TestCase):

    def _parse(self, spec):
        with patch.dict(os.environ, {"IVPM_GIT_URL_MAP": spec}):
            return _env_git_url_map()

    def test_multiple_pairs(self):
        self.assertEqual(self._parse("a=b; c=d "), [("a", "b"), ("c", "d")])

    def test_malformed_entries_skipped(self):
        # no '=', empty from, empty to -> all skipped; the good pair survives
        self.assertEqual(self._parse("bad; =e; f=; g=h"), [("g", "h")])

    def test_unset_is_empty(self):
        with patch.dict(os.environ, {"IVPM_GIT_URL_MAP": ""}):
            self.assertEqual(_env_git_url_map(), [])


# ---------------------------------------------------------------------------
# File-rule parsing robustness
# ---------------------------------------------------------------------------

class TestFileParsing(unittest.TestCase):

    def tearDown(self):
        reset_site_config()

    def test_malformed_rules_skipped(self):
        data = {"git-url-map": [
            {"from": "a", "to": "b"},
            {"from": "a"},          # missing to
            {"to": "x"},            # missing from
            "not-a-dict",
        ]}
        with patch.object(sc, "_load_config_files", lambda: [("cfg.yaml", data)]):
            self.assertEqual(_file_git_url_map(), [("a", "b")])

    def test_absent_key_is_empty(self):
        with patch.object(sc, "_load_config_files", lambda: [("cfg.yaml", {})]):
            self.assertEqual(_file_git_url_map(), [])


# ---------------------------------------------------------------------------
# Integration through resolve_clone_url (remap before auth)
# ---------------------------------------------------------------------------

class TestResolveCloneUrlIntegration(unittest.TestCase):

    def _resolve(self, rules, url, ssh_pref, order, gh_ok=False):
        with patch.object(sc, "_file_git_url_map", lambda: list(rules)):
            with patch.dict(os.environ, {"IVPM_GIT_URL_MAP": ""}):
                with patch.object(utils, "gh_auth_available", lambda h: gh_ok):
                    return resolve_clone_url(url, ssh_pref, order)

    def test_remap_to_file_bypasses_auth(self):
        # https -> file:// : auth handling must leave the file URL untouched
        rules = [("https://github.com/o/r", "file:///repos/o/r")]
        self.assertEqual(
            self._resolve(rules, "https://github.com/o/r", None, ["gh", "ssh"]),
            "file:///repos/o/r")

    def test_remap_to_other_host_applies_auth(self):
        # https -> https(other host): ssh rewrite applies to the *new* host
        rules = [("https://github.com/o/r", "https://mirror.internal/o/r")]
        self.assertEqual(
            self._resolve(rules, "https://github.com/o/r", None, ["ssh"]),
            "git@mirror.internal:o/r")

    def test_remap_to_ssh_url_preserved(self):
        # https -> ssh://git@host:port/path : the ssh rewrite step must leave
        # the (already-ssh, port-carrying) URL exactly as written
        rules = [("https://github.com/o/", "ssh://git@mirror.internal:222/o/")]
        target = "ssh://git@mirror.internal:222/o/r.git"
        for order in (["gh", "ssh"], ["ssh"], ["https"], []):
            self.assertEqual(
                self._resolve(rules, "https://github.com/o/r.git", None, order),
                target)
        # ...and under explicit ssh/anonymous overrides too
        self.assertEqual(
            self._resolve(rules, "https://github.com/o/r.git", True, None), target)
        self.assertEqual(
            self._resolve(rules, "https://github.com/o/r.git", False, None), target)

    def test_explicit_ssh_pref_noop_on_file_target(self):
        # ssh_pref=True can't ssh-rewrite a file:// URL -> left as-is, no error
        rules = [("https://github.com/o/r", "file:///repos/o/r")]
        self.assertEqual(
            self._resolve(rules, "https://github.com/o/r", True, None),
            "file:///repos/o/r")


# ---------------------------------------------------------------------------
# Consumers that key off *where the repo lives*, not how it was spelled
# ---------------------------------------------------------------------------

class TestGithubDetectionUsesMappedUrl(unittest.TestCase):
    """A github.com URL redirected to a mirror is no longer a GitHub repo:
    resolving its commit through api.github.com would answer from an upstream
    the rule deliberately steered away from."""

    RULES = [("https://github.com/o/", "ssh://git@mirror.internal:222/o/")]

    def _pkg(self, url):
        from ivpm.pkg_types.package_git import PackageGit
        return PackageGit(name="p", url=url)

    def test_mapped_url_applied(self):
        with patch.object(sc, "_file_git_url_map", lambda: list(self.RULES)):
            with patch.dict(os.environ, {"IVPM_GIT_URL_MAP": ""}):
                pkg = self._pkg("https://github.com/o/r.git")
                self.assertEqual(pkg._mapped_url(),
                                 "ssh://git@mirror.internal:222/o/r.git")

    def test_remapped_github_url_is_not_github(self):
        from ivpm.cache import is_github_url
        with patch.object(sc, "_file_git_url_map", lambda: list(self.RULES)):
            with patch.dict(os.environ, {"IVPM_GIT_URL_MAP": ""}):
                pkg = self._pkg("https://github.com/o/r.git")
                self.assertFalse(is_github_url(pkg._mapped_url()))

    def test_unmapped_github_url_is_still_github(self):
        from ivpm.cache import is_github_url
        with patch.object(sc, "_file_git_url_map", lambda: list(self.RULES)):
            with patch.dict(os.environ, {"IVPM_GIT_URL_MAP": ""}):
                pkg = self._pkg("https://github.com/other/r.git")
                self.assertTrue(is_github_url(pkg._mapped_url()))

    def test_ls_remote_fallback_stays_on_the_mirror(self):
        # The https fallback (used when the ssh attempt fails) must be the
        # remapped spelling, not the declared upstream URL.
        tried = []

        with patch.object(sc, "_file_git_url_map",
                          lambda: [("https://github.com/o/", "https://mirror.internal/o/")]):
            with patch.dict(os.environ, {"IVPM_GIT_URL_MAP": "",
                                         "IVPM_GIT_AUTH_ORDER": "ssh"}):
                pkg = self._pkg("https://github.com/o/r.git")
                with patch.object(type(pkg), "_ls_remote",
                                  lambda self, url, ref: tried.append(url)):
                    pkg._get_commit_hash_ls_remote("main")

        self.assertEqual(tried, ["git@mirror.internal:o/r.git",
                                 "https://mirror.internal/o/r.git"])


class TestCloneProviderResolutionUsesMappedUrl(unittest.TestCase):
    """`ivpm clone` selects its provider from the remapped locator, so a rule
    that redirects to another transport/scheme picks the provider that can
    actually fetch it."""

    def test_provider_resolved_from_mapped_src(self):
        from ivpm.cmds.cmd_clone import CmdClone   # noqa: F401 (import check)
        import ivpm.cmds.cmd_clone as cc
        from ivpm.clone.clone_provider_rgy import CloneProviderRgy

        seen = []
        rules = [("https://git.example.org/o/", "ssh://git@mirror.internal:222/o/")]

        class _Rgy:
            def resolve(self, src, forced=None):
                seen.append(src)
                raise SystemExit(0)   # stop before any cloning happens

        with patch.object(sc, "_file_git_url_map", lambda: list(rules)):
            with patch.dict(os.environ, {"IVPM_GIT_URL_MAP": ""}):
                with patch.object(CloneProviderRgy, "inst", staticmethod(lambda: _Rgy())):
                    args = type("A", (), {"src": "https://git.example.org/o/r.git",
                                          "provider": None})()
                    with self.assertRaises(SystemExit):
                        cc.CmdClone()(args)

        self.assertEqual(seen, ["ssh://git@mirror.internal:222/o/r.git"])


if __name__ == "__main__":
    unittest.main()

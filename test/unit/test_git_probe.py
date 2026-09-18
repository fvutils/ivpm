"""Unit tests for the active git-auth probe (plan Phase 4).

Every subprocess is mocked: the probe must never reach the network here, and
CI has no credentials to reach it with.  The rows of the decision table
(``gh api`` OK / 403-SSO / 404 / gh-not-authenticated / gh-absent) each get a
test, because that table is the whole point of the probe -- it separates an
authorization problem from a credential-plumbing problem.
"""
import os
import subprocess
import tempfile
import textwrap
import unittest
from unittest.mock import patch

import ivpm.git_probe as gp
from ivpm.git_probe import (
    ProbeResult, format_verdict, probe_enabled, probe_git_auth,
    probe_worthwhile,
)


_URL = "https://github.com/example-org/lib-example.git"
_SSH_URL = "git@github.com:example-org/lib-example.git"

_AUTH_ERR = ("remote: Invalid username or token. Password authentication is "
             "not supported for Git operations.\n"
             "fatal: Authentication failed for '%s'" % _URL)

_SSO_API_ERR = ("gh: Resource protected by organization SAML enforcement. "
                "You must grant your OAuth token access to this organization. "
                "Visit https://github.com/orgs/example-org/sso?"
                "authorization_request=ABC123 (HTTP 403)")

_GH_STATUS_OK = textwrap.dedent("""\
    github.com
      - Active account: true
      - Logged in to github.com account example-user (keyring)
      - Token: gho_************************************
      - Token scopes: 'gist', 'read:org', 'repo'
    """)


class _FakeProc(object):
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Runner(object):
    """Dispatches mocked subprocess.run by the command's shape.

    Any command not explicitly handled fails loudly rather than silently
    returning success, so a new check cannot be accidentally un-asserted.
    """

    def __init__(self, gh_status=None, gh_api_repo=None, gh_api_user=None,
                 helper=None, ssh_t=None, ssh_add=None):
        self.gh_status = gh_status if gh_status is not None else _FakeProc(0, _GH_STATUS_OK)
        self.gh_api_repo = gh_api_repo if gh_api_repo is not None else _FakeProc(
            0, "example-org/lib-example")
        self.gh_api_user = gh_api_user if gh_api_user is not None else _FakeProc(
            0, "example-user")
        self.helper = helper if helper is not None else _FakeProc(1, "")
        self.ssh_t = ssh_t if ssh_t is not None else _FakeProc(1, "", "")
        self.ssh_add = ssh_add if ssh_add is not None else _FakeProc(1, "", "")
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        exe = os.path.basename(cmd[0])
        if exe == "gh":
            if cmd[1:3] == ["auth", "status"]:
                return self.gh_status
            if cmd[1:2] == ["api"] and cmd[2] == "user":
                return self.gh_api_user
            if cmd[1:2] == ["api"]:
                return self.gh_api_repo
            return _FakeProc(0, "gh version 2.69.0")
        if exe == "git":
            if "--get-urlmatch" in cmd:
                return self.helper
            return _FakeProc(1, "", "unexpected git command")
        if exe == "ssh":
            return self.ssh_t
        if exe == "ssh-add":
            return self.ssh_add
        raise AssertionError("unmocked command: %s" % cmd)


class _ProbeTestBase(unittest.TestCase):

    def setUp(self):
        self._home = tempfile.mkdtemp()
        # A clean environment: no netrc, no gh overrides, no askpass.
        env = {k: v for k, v in os.environ.items()
               if k not in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN",
                            "GITHUB_ENTERPRISE_TOKEN", "GH_CONFIG_DIR",
                            "GIT_ASKPASS", "SSH_ASKPASS",
                            "GIT_TERMINAL_PROMPT", "IVPM_GIT_NO_PROBE")}
        env["HOME"] = self._home
        self._p = patch.dict(os.environ, env, clear=True)
        self._p.start()
        self.addCleanup(self._p.stop)

    def _probe(self, url=_URL, stderr=_AUTH_ERR, gh="/usr/bin/gh", runner=None,
               **kw):
        runner = runner or _Runner(**kw)
        with patch.object(gp.shutil, "which", lambda n: gh), \
             patch.object(gp.subprocess, "run", runner):
            res = probe_git_auth(url, stderr)
        self.runner = runner
        return res

    def _checks(self, res):
        return dict(res.checks)


# ---------------------------------------------------------------------------
# The decision table (plan §7.3)
# ---------------------------------------------------------------------------

class TestVerdictTable(_ProbeTestBase):

    def test_api_ok_means_git_could_not_present_the_token(self):
        res = self._probe()
        self.assertIn("valid and authorized", res.verdict)
        self.assertIn("git was not able to present it", res.verdict)
        self.assertTrue(res.remedies)

    def test_api_sso_403(self):
        res = self._probe(gh_api_repo=_FakeProc(1, "", _SSO_API_ERR))
        self.assertIn("not SSO-authorized", res.verdict)
        self.assertIn("https://github.com/orgs/example-org/sso?"
                      "authorization_request=ABC123", res.remedies[0])

    def test_api_404(self):
        res = self._probe(gh_api_repo=_FakeProc(
            1, "", "gh: Not Found (HTTP 404)"))
        self.assertIn("not visible to that account", res.verdict)
        self.assertTrue(any("owner" in r for r in res.remedies))

    def test_gh_not_authenticated(self):
        res = self._probe(gh_status=_FakeProc(1, "", "not logged in"))
        self.assertIn("not authenticated", res.verdict)
        self.assertIn("gh auth login --hostname github.com", res.remedies[0])

    def test_gh_absent(self):
        res = self._probe(gh=None)
        self.assertIn("not installed", res.verdict)
        self.assertTrue(any("IVPM_GIT_AUTH_ORDER=ssh" in r
                            for r in res.remedies))
        # No gh means no gh subprocesses were attempted at all.
        self.assertFalse([c for c in self.runner.calls
                          if os.path.basename(c[0]) == "gh"])

    def test_gh_token_env_override_is_reported(self):
        with patch.dict(os.environ, {"GH_TOKEN": "irrelevant-value"}):
            res = self._probe(gh_status=_FakeProc(1, "", "not logged in"))
        self.assertIn("GH_TOKEN is set", self._checks(res)["gh token env"])
        self.assertTrue(any("GH_TOKEN" in r for r in res.remedies))


# ---------------------------------------------------------------------------
# The §2.6 regression: --get-urlmatch, not --get-all
# ---------------------------------------------------------------------------

class TestEffectiveHelper(unittest.TestCase):
    """A host-scoped helper must be detected.

    ``gh auth setup-git`` writes ``credential.https://github.com.helper``,
    which a bare ``git config --get-all credential.helper`` does not see -- so
    the old diagnostic told a correctly-configured user to run the command
    they had already run.  This uses a real git binary against a temp config,
    which is the only way to pin the query semantics rather than the mock's.
    """

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._cfg = os.path.join(self._dir, "gitconfig")
        with open(self._cfg, "w") as fp:
            fp.write('[credential "https://github.com"]\n'
                     '\thelper = !gh auth git-credential\n')
        self._env = patch.dict(os.environ, {
            "GIT_CONFIG_GLOBAL": self._cfg,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "HOME": self._dir,
        })
        self._env.start()
        self.addCleanup(self._env.stop)

    def _git_ok(self):
        try:
            subprocess.run(["git", "--version"], capture_output=True, timeout=10)
            return True
        except Exception:
            return False

    def test_host_scoped_helper_is_found(self):
        if not self._git_ok():
            self.skipTest("git is not available")
        finding = gp._effective_helper(_URL, timeout=10)
        self.assertIn("gh", finding)

        # The old query is blind to it -- this is what made the diagnostic
        # point away from the real cause.
        r = subprocess.run(["git", "config", "--get-all", "credential.helper"],
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(r.stdout.strip(), "")

    def test_not_configured_for_an_unrelated_host(self):
        if not self._git_ok():
            self.skipTest("git is not available")
        finding = gp._effective_helper("https://gitlab.com/o/r.git", timeout=10)
        self.assertIn("not configured", finding)


# ---------------------------------------------------------------------------
# Secrecy
# ---------------------------------------------------------------------------

class TestNoSecretsLeak(_ProbeTestBase):
    """Allowlist what is printed; never blocklist what is redacted.

    The fixtures deliberately place tokens on commented, indented, and
    trailing-whitespace lines -- the shapes a redaction pattern misses.
    """

    _TOKEN_PATTERNS = ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_")

    _NETRC = ("machine github.com\n"
              "  login example-user\n"
              "    password ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA   \n"
              "#   password github_pat_BBBBBBBBBBBBBBBBBBBBBBBB\n")

    def _write_netrc(self):
        with open(os.path.join(self._home, ".netrc"), "w") as fp:
            fp.write(self._NETRC)

    def _all_text(self, res):
        return "\n".join([res.format_checks(), res.verdict] + res.remedies)

    def test_netrc_reports_presence_only(self):
        self._write_netrc()
        res = self._probe()
        text = self._all_text(res)
        self.assertIn("~/.netrc exists and names this host",
                      self._checks(res)["netrc"])
        for pat in self._TOKEN_PATTERNS:
            self.assertNotIn(pat, text)
        self.assertNotIn("password", text.lower())
        self.assertNotIn("login example-user", text)

    def test_netrc_shadowing_leads_the_remedies(self):
        self._write_netrc()
        res = self._probe()
        self.assertIn("~/.netrc", res.remedies[0])

    def test_no_netrc_is_reported_plainly(self):
        res = self._probe()
        self.assertEqual(self._checks(res)["netrc"], "no ~/.netrc")

    def test_masked_token_line_is_never_echoed(self):
        res = self._probe()
        text = self._all_text(res)
        for pat in self._TOKEN_PATTERNS:
            self.assertNotIn(pat, text)
        self.assertNotIn("*****", text)

    def test_scopes_are_echoed_but_tokens_cannot_be(self):
        # Scope names carry no digits; a token always does (and gh masks it).
        self.assertEqual(gp._gh_scopes(_GH_STATUS_OK),
                         ["gist", "read:org", "repo"])
        self.assertEqual(
            gp._gh_scopes("  - Token scopes: 'ghp_A1B2C3D4E5F6G7H8I9J0'"), [])

    def test_helper_value_with_a_secret_is_not_echoed(self):
        res = self._probe(helper=_FakeProc(
            0, "!echo password=ghp_AAAAAAAAAAAAAAAAAAAA"))
        text = self._all_text(res)
        for pat in self._TOKEN_PATTERNS:
            self.assertNotIn(pat, text)

    def test_never_reads_gh_hosts_yml(self):
        """The probe must not open gh's config store at all.

        Reading it was the mistake that leaked a real token during the
        investigation behind this feature: the token sat on a commented line a
        redaction pattern did not match.
        """
        opened = []
        real_open = open

        def _tracking_open(path, *a, **kw):
            opened.append(str(path))
            return real_open(path, *a, **kw)

        with patch("builtins.open", _tracking_open):
            self._probe()
        for path in opened:
            self.assertNotIn("hosts.yml", path)
            self.assertNotIn(".git-credentials", path)

    def test_never_passes_show_token(self):
        self._probe()
        for cmd in self.runner.calls:
            self.assertNotIn("--show-token", cmd)


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------

class TestRobustness(_ProbeTestBase):

    def test_timeout_becomes_a_finding_not_an_exception(self):
        def _timeout(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 15)

        with patch.object(gp.shutil, "which", lambda n: "/usr/bin/gh"), \
             patch.object(gp.subprocess, "run", _timeout):
            res = probe_git_auth(_URL, _AUTH_ERR)
        self.assertIsInstance(res, ProbeResult)
        self.assertIn("not authenticated", res.verdict)

    def test_arbitrary_exception_becomes_a_finding(self):
        def _boom(cmd, **kw):
            raise RuntimeError("nope")

        with patch.object(gp.shutil, "which", lambda n: "/usr/bin/gh"), \
             patch.object(gp.subprocess, "run", _boom):
            res = probe_git_auth(_URL, _AUTH_ERR)
        self.assertIsInstance(res, ProbeResult)

    def test_missing_executable_becomes_a_finding(self):
        rc, out, err = gp._run(["definitely-not-a-real-binary-xyz"], timeout=5)
        self.assertEqual(rc, 127)
        self.assertIn("not found", err)

    def test_every_subprocess_is_bounded(self):
        seen = []

        class _Recorder(_Runner):
            def __call__(self, cmd, **kw):
                seen.append(kw.get("timeout"))
                return _Runner.__call__(self, cmd, **kw)

        self._probe(runner=_Recorder())
        self.assertTrue(seen)
        for t in seen:
            self.assertIsNotNone(t)
            self.assertLessEqual(t, 15)


# ---------------------------------------------------------------------------
# SSH transport
# ---------------------------------------------------------------------------

class TestSshProbe(_ProbeTestBase):

    def test_successful_ssh_points_at_repository_access(self):
        res = self._probe(
            url=_SSH_URL,
            stderr="ERROR: Repository not found.\n"
                   "fatal: Could not read from remote repository.",
            ssh_t=_FakeProc(1, "", "Hi example-user! You've successfully "
                                   "authenticated, but GitHub does not provide "
                                   "shell access."),
            ssh_add=_FakeProc(0, "256 SHA256:abc example-user@host (ED25519)"))
        self.assertIn("succeeds", res.verdict)
        self.assertEqual(self._checks(res)["ssh-agent keys"], "1 loaded")

    def test_no_keys_anywhere(self):
        res = self._probe(url=_SSH_URL,
                          stderr="git@github.com: Permission denied (publickey).")
        self.assertIn("no usable key", res.verdict)
        self.assertTrue(any("ssh-add" in r for r in res.remedies))

    def test_permission_denied_from_the_host(self):
        res = self._probe(
            url=_SSH_URL,
            stderr="git@github.com: Permission denied (publickey).",
            ssh_t=_FakeProc(255, "", "git@github.com: Permission denied (publickey)."),
            ssh_add=_FakeProc(0, "256 SHA256:abc key (ED25519)"))
        self.assertIn("refused the keys", res.verdict)


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------

class TestGating(_ProbeTestBase):

    def test_env_var_disables(self):
        with patch.dict(os.environ, {"IVPM_GIT_NO_PROBE": "1"}):
            self.assertFalse(probe_enabled())
            self.assertFalse(probe_worthwhile(_URL, _AUTH_ERR))

    def test_env_var_zero_does_not_disable(self):
        with patch.dict(os.environ, {"IVPM_GIT_NO_PROBE": "0"}):
            self.assertTrue(probe_enabled())

    def test_no_probe_flag_disables(self):
        class _Args:
            no_probe = True
        self.assertFalse(probe_enabled(_Args()))

    def test_not_worthwhile_for_non_auth_failures(self):
        for stderr in ("remote: Repository not found.",
                       "fatal: Could not resolve host: nope.example"):
            self.assertFalse(probe_worthwhile(_URL, stderr), stderr)

    def test_not_worthwhile_for_local_locators(self):
        self.assertFalse(probe_worthwhile("file:///tmp/mirror", _AUTH_ERR))
        self.assertFalse(probe_worthwhile("/tmp/mirror", _AUTH_ERR))

    def test_worthwhile_for_auth_failures(self):
        self.assertTrue(probe_worthwhile(_URL, _AUTH_ERR))
        self.assertTrue(probe_worthwhile(_SSH_URL,
                                         "Permission denied (publickey)."))


class TestFormatting(_ProbeTestBase):

    def test_verdict_rendering_is_tight(self):
        res = ProbeResult(verdict="v", remedies=["a", "b", "c", "d"])
        lines = format_verdict(res)
        self.assertEqual(lines[0], "probe: v")
        self.assertEqual(len(lines), 4)   # verdict + at most 3 remedies

    def test_empty_verdict_renders_nothing(self):
        self.assertEqual(format_verdict(ProbeResult()), [])
        self.assertEqual(format_verdict(None), [])

    def test_checks_table_is_aligned(self):
        res = self._probe()
        text = res.format_checks()
        self.assertIn("git auth probe:", text)
        self.assertIn("effective helper", text)


if __name__ == "__main__":
    unittest.main()

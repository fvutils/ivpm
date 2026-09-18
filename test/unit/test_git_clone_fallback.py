"""Unit tests for transport fallback on an auth failure (plan Phase 2).

The auth order used to be selection-only: ``ssh`` sitting second in the
default ``gh, ssh`` looked like a fallback and never behaved as one.  These
tests pin both halves of the fix -- that an auth-shaped failure falls through
to the next transport, and that anything else still fails fast.
"""
import os
import tempfile
import types
import unittest
from unittest.mock import patch

import ivpm.utils as utils
import ivpm.pkg_types.package_git as pg


_URL = "https://github.com/example-org/lib-example.git"
_SSH = "git@github.com:example-org/lib-example.git"

_AUTH_ERR = ("remote: Invalid username or token. Password authentication is "
             "not supported for Git operations.\n"
             "fatal: Authentication failed for '%s'" % _URL)
_NOT_FOUND_ERR = ("remote: Repository not found.\n"
                  "fatal: repository not found")


class _Fatal(Exception):
    """Stand-in for ivpm.msg.fatal, which exits the process."""


class TestCloneFallback(unittest.TestCase):

    def setUp(self):
        utils.gh_auth_available.cache_clear()
        self.addCleanup(utils.gh_auth_available.cache_clear)
        self._dir = tempfile.mkdtemp()
        self.target = os.path.join(self._dir, "packages", "lib-example")
        self.notes = []
        self.attempts = []

    def _pkg(self):
        p = pg.PackageGit("lib-example")
        p.url = _URL
        return p

    def _update_info(self):
        args = types.SimpleNamespace(ssh=False, anonymous=False,
                                     git_auth_order=["gh", "ssh"],
                                     no_probe=True)
        return types.SimpleNamespace(args=args, suppress_output=False,
                                     perf=None, event_dispatcher=None)

    def _clone(self, outcomes, depth=None):
        """Run _clone_to_dir with a scripted (rc, stderr) per attempt.

        *outcomes* maps the cloned URL to its ``(rc, stderr)``; the clone is
        driven through PackageGit's own _run_git seam so the candidate walk,
        the cleanup, and the message assembly are all real.
        """
        pkg = self._pkg()

        def _fake_run_git(pkg_self, cmd, update_info, cwd=None, progress=False):
            # Identify the attempt by the URL argument git was handed.
            url = next((a for a in cmd if a in outcomes), None)
            self.attempts.append(list(cmd))
            if url is None:
                return 0, ""
            rc, err = outcomes[url]
            if rc == 0:
                os.makedirs(self.target, exist_ok=True)
                with open(os.path.join(self.target, "README"), "w") as fp:
                    fp.write("cloned\n")
            else:
                # git leaves a partial destination behind on failure.
                os.makedirs(self.target, exist_ok=True)
                with open(os.path.join(self.target, "partial"), "w") as fp:
                    fp.write("junk\n")
            return rc, err

        def _fatal(msg):
            raise _Fatal(msg)

        with patch.object(utils, "gh_auth_available", lambda h: True), \
             patch.object(pg, "fatal", _fatal), \
             patch.object(pg, "note", lambda m: self.notes.append(m)), \
             patch.object(pg.PackageGit, "_run_git", _fake_run_git):
            pkg._clone_to_dir(self._update_info(), self.target, depth=depth)
        return pkg

    # ------------------------------------------------------------------ #

    def test_auth_failure_falls_through_to_the_next_transport(self):
        self._clone({_URL: (128, _AUTH_ERR), _SSH: (0, "")})
        urls = [u for cmd in self.attempts for u in cmd if u in (_URL, _SSH)]
        self.assertEqual(urls, [_URL, _SSH])
        self.assertTrue(os.path.isfile(os.path.join(self.target, "README")))

    def test_the_retry_names_both_transports(self):
        self._clone({_URL: (128, _AUTH_ERR), _SSH: (0, "")})
        self.assertEqual(len(self.notes), 1)
        self.assertIn("gh", self.notes[0])
        self.assertIn("ssh", self.notes[0])

    def test_partial_target_is_removed_between_attempts(self):
        seen = {}

        outcomes = {_URL: (128, _AUTH_ERR), _SSH: (0, "")}
        real_clone = self._clone

        def _record(url):
            seen[url] = sorted(os.listdir(self.target)) \
                if os.path.isdir(self.target) else None

        # Capture the destination's contents at the start of each attempt.
        orig_attempt = pg.PackageGit._clone_attempt

        def _wrapped(self_, update_info, target_dir, depth, url):
            _record(url)
            return orig_attempt(self_, update_info, target_dir, depth, url)

        with patch.object(pg.PackageGit, "_clone_attempt", _wrapped):
            real_clone(outcomes)

        self.assertEqual(seen[_URL], None)
        # The first attempt's partial tree must not still be there.
        self.assertEqual(seen[_SSH], None)

    def test_not_found_fails_fast(self):
        with self.assertRaises(_Fatal) as cm:
            self._clone({_URL: (128, _NOT_FOUND_ERR), _SSH: (0, "")})
        urls = [u for cmd in self.attempts for u in cmd if u in (_URL, _SSH)]
        self.assertEqual(urls, [_URL])
        self.assertEqual(self.notes, [])
        self.assertIn("does not exist", str(cm.exception))

    def test_all_candidates_failing_reports_every_attempt(self):
        with self.assertRaises(_Fatal) as cm:
            self._clone({_URL: (128, _AUTH_ERR), _SSH: (128, _AUTH_ERR)})
        msg = str(cm.exception)
        self.assertIn("all 2 configured transports failed", msg)
        self.assertIn("attempt 1 (gh)", msg)
        self.assertIn("attempt 2 (ssh)", msg)
        # Each attempt's own URL is named, so a fallback never hides the first
        # failure.
        self.assertIn(_URL, msg)
        self.assertIn(_SSH, msg)

    def test_single_candidate_keeps_the_original_message_shape(self):
        pkg = self._pkg()
        pkg.ssh = True     # an explicit override yields exactly one candidate
        self.assertEqual(
            len(pkg._clone_candidates(self._update_info())), 1)

        with self.assertRaises(_Fatal) as cm:
            with patch.object(utils, "gh_auth_available", lambda h: True), \
                 patch.object(pg, "fatal", lambda m: (_ for _ in ()).throw(_Fatal(m))), \
                 patch.object(pg.PackageGit, "_run_git",
                              lambda *a, **k: (128, _AUTH_ERR)):
                pkg._clone_to_dir(self._update_info(), self.target)
        msg = str(cm.exception)
        self.assertTrue(msg.startswith("Failed to clone %s (git exit 128)" % _SSH))
        self.assertIn("git reported:", msg)
        self.assertNotIn("configured transports failed", msg)

    def test_injection_reaches_the_clone_command(self):
        with patch.object(pg.git_auth.shutil, "which", lambda n: "/usr/bin/gh"):
            self._clone({_URL: (0, "")})
        first = self.attempts[0]
        self.assertEqual(first[0], "git")
        self.assertEqual(first[1:3], ["-c", "credential.helper="])
        self.assertIn("credential.helper=!/usr/bin/gh auth git-credential",
                      first)
        self.assertEqual(first[5], "clone")

    def test_no_injection_when_the_ssh_candidate_runs(self):
        with patch.object(pg.git_auth.shutil, "which", lambda n: "/usr/bin/gh"):
            self._clone({_URL: (128, _AUTH_ERR), _SSH: (0, "")})
        ssh_cmd = next(c for c in self.attempts if _SSH in c)
        self.assertNotIn("-c", ssh_cmd)


class TestSubmoduleInjection(unittest.TestCase):
    """``-c`` config propagates into the submodule operations git spawns, so
    the credentials have to travel with that one command."""

    def setUp(self):
        utils.gh_auth_available.cache_clear()
        self.addCleanup(utils.gh_auth_available.cache_clear)
        self._dir = tempfile.mkdtemp()
        with open(os.path.join(self._dir, ".gitmodules"), "w") as fp:
            fp.write('[submodule "x"]\n\tpath = x\n\turl = %s\n' % _URL)

    def test_submodule_update_carries_the_helper(self):
        seen = []
        pkg = pg.PackageGit("p")
        pkg.url = _URL
        ui = types.SimpleNamespace(
            args=types.SimpleNamespace(ssh=False, anonymous=False,
                                       git_auth_order=["gh"], no_probe=True),
            suppress_output=False, perf=None, event_dispatcher=None)

        with patch.object(utils, "gh_auth_available", lambda h: True), \
             patch.object(pg.git_auth.shutil, "which", lambda n: "/usr/bin/gh"), \
             patch.object(pg.PackageGit, "_run_git",
                          lambda s, cmd, u, cwd=None, progress=False:
                              (seen.append(list(cmd)), (0, ""))[1]):
            pkg._update_submodules(ui, self._dir, _URL)

        self.assertEqual(len(seen), 1)
        self.assertIn("credential.helper=!/usr/bin/gh auth git-credential",
                      seen[0])
        self.assertEqual(seen[0][-4:],
                         ["submodule", "update", "--init", "--recursive"])


class TestLsRemoteErrorRetention(unittest.TestCase):
    """``_ls_remote`` used to swallow everything and keep only the *first*
    attempt's stderr, so an auth failure surfaced as a bare "Failed to resolve
    commit"."""

    def setUp(self):
        utils.gh_auth_available.cache_clear()
        self.addCleanup(utils.gh_auth_available.cache_clear)

    def _pkg(self):
        p = pg.PackageGit("p")
        p.url = _URL
        return p

    def test_last_failure_is_retained(self):
        pkg = self._pkg()
        errs = []

        def _run(cmd, **kw):
            # Name the refspec in the error, so "which attempt was kept?" is
            # unambiguous regardless of how many git calls happen.
            errs.append("failed for %s" % cmd[-1])
            return types.SimpleNamespace(
                returncode=128, stdout="", stderr=errs[-1])

        with patch.object(utils, "gh_auth_available", lambda h: False), \
             patch.object(pg.subprocess, "run", _run):
            self.assertIsNone(pkg._ls_remote(_URL, "main"))
        # Three refspecs are tried; the LAST one's error is the one kept.
        self.assertEqual(len(errs), 3)
        self.assertEqual(pkg._last_git_err, "failed for refs/tags/main")

    def test_exception_is_recorded_not_swallowed(self):
        pkg = self._pkg()

        def _boom(cmd, **kw):
            raise OSError(2, "No such file or directory", "git")

        with patch.object(utils, "gh_auth_available", lambda h: False), \
             patch.object(pg.subprocess, "run", _boom):
            self.assertIsNone(pkg._ls_remote(_URL, "main"))
        self.assertIn("ls-remote", pkg._last_git_err)
        self.assertIn("No such file", pkg._last_git_err)

    def test_injection_reaches_ls_remote(self):
        pkg = self._pkg()
        seen = []

        def _run(cmd, **kw):
            seen.append(list(cmd))
            return types.SimpleNamespace(returncode=0, stdout="abc123\tmain",
                                         stderr="")

        with patch.object(utils, "gh_auth_available", lambda h: True), \
             patch.object(pg.git_auth.shutil, "which", lambda n: "/usr/bin/gh"), \
             patch.object(pg.subprocess, "run", _run):
            pkg._clone_candidates(None)     # records the auth decision
            self.assertEqual(pkg._ls_remote(_URL, "main"), "abc123")
        self.assertIn("credential.helper=!/usr/bin/gh auth git-credential",
                      seen[0])


if __name__ == "__main__":
    unittest.main()

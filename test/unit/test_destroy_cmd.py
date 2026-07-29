"""
Front-end tests for `ivpm destroy`: CmdDestroy presentation, confirm prompt,
and exit codes. Drives CmdDestroy directly with a fake args object.
"""
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "src"))

from .test_base import TestBase

from ivpm.cmds.cmd_destroy import CmdDestroy
from ivpm import destroy_tui
from ivpm.pkg_remove import (
    DestroyReport, RemovalSafety, SafetyLevel, SafetyReason,
    PkgRemoveResult, RemoveOutcome,
)


def _git(repo, *args):
    return subprocess.run(["git"] + list(args), cwd=repo,
                          capture_output=True, text=True, check=True)


def _mk_git_dep(path):
    os.makedirs(path, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t.com")
    _git(path, "config", "user.name", "T")
    _git(path, "config", "commit.gpgsign", "false")
    with open(os.path.join(path, "f.txt"), "w") as fp:
        fp.write("1")
    _git(path, "add", "f.txt")
    _git(path, "commit", "-q", "-m", "init")
    head = _git(path, "rev-parse", "HEAD").stdout.strip()
    _git(path, "checkout", "-q", head)        # detached -> clean


class _FakeStdin:
    """Stand-in for sys.stdin so tty-ness is explicit and never depends on
    how the tests were launched (pytest capture, `pytest -s`, or plain
    unittest from a terminal)."""

    def __init__(self, tty=False):
        self._tty = tty

    def isatty(self):
        return self._tty

    def readline(self, *args, **kw):
        raise AssertionError("destroy read from stdin")


def _no_input(prompt=""):
    raise AssertionError("unexpected interactive prompt: %s" % prompt)


class _Args:
    def __init__(self, wsdir=None, **kw):
        self.wsdir = wsdir
        self.project_dir = kw.get("project_dir", None)
        self.deps_only = kw.get("deps_only", False)
        self.force = kw.get("force", False)
        self.dry_run = kw.get("dry_run", False)
        self.yes = kw.get("yes", False)
        self.keep_venv = kw.get("keep_venv", False)
        self.verbose = kw.get("verbose", 0)
        # Force the transcript TUI so the captured output these tests assert
        # on doesn't depend on whether stdout happens to be a tty.
        self.no_rich = kw.get("no_rich", True)


class TestDestroyCmd(TestBase):

    def setUp(self):
        super().setUp()
        # No destroy test may block on an interactive prompt. Default stdin
        # to a non-tty and trap input(); the tests that exercise the confirm
        # prompt opt in explicitly via _set_stdin()/mock.patch.
        self._stdin_patcher = mock.patch("sys.stdin", _FakeStdin(tty=False))
        self._stdin_patcher.start()
        self.addCleanup(lambda: self._stdin_patcher.stop())
        input_patcher = mock.patch("builtins.input", _no_input)
        input_patcher.start()
        self.addCleanup(input_patcher.stop)

    def _set_stdin(self, tty):
        """Replace the fake stdin installed by setUp()."""
        self._stdin_patcher.stop()
        self._stdin_patcher = mock.patch("sys.stdin", _FakeStdin(tty=tty))
        self._stdin_patcher.start()

    def _mk_workspace(self, names=("liba", "libb")):
        ws = os.path.join(self.testdir, "ws")
        deps = os.path.join(ws, "packages")
        os.makedirs(deps)
        with open(os.path.join(ws, "ivpm.yaml"), "w") as fp:
            fp.write("package:\n  name: root\n")
        entries = {}
        for n in names:
            _mk_git_dep(os.path.join(deps, n))
            entries[n] = {"src": "git", "url": "http://example/%s.git" % n}
        with open(os.path.join(deps, "package-lock.json"), "w") as fp:
            json.dump({"ivpm_lock_version": 1, "packages": entries}, fp)
        return ws, deps

    def test_requires_wsdir_when_not_deps_only(self):
        with self.assertRaises(Exception):
            CmdDestroy()(_Args(wsdir=None))

    def test_dry_run_changes_nothing(self):
        ws, deps = self._mk_workspace()
        out = io.StringIO()
        with redirect_stdout(out):
            CmdDestroy()(_Args(wsdir=ws, dry_run=True))
        self.assertIn("--dry-run", out.getvalue())
        self.assertTrue(os.path.isdir(os.path.join(deps, "liba")))

    def test_blocked_exits_nonzero_and_reports(self):
        ws, deps = self._mk_workspace()
        with open(os.path.join(deps, "liba", "f.txt"), "w") as fp:
            fp.write("changed")
        err = io.StringIO()
        with redirect_stderr(err):
            with self.assertRaises(SystemExit) as cm:
                CmdDestroy()(_Args(wsdir=ws))
        self.assertEqual(cm.exception.code, 1)
        text = err.getvalue()
        self.assertIn("refusing to remove", text)
        self.assertIn("liba", text)
        self.assertIn("modified", text)

    def test_verbose_lists_files(self):
        ws, deps = self._mk_workspace()
        with open(os.path.join(deps, "liba", "f.txt"), "w") as fp:
            fp.write("changed")
        err = io.StringIO()
        with redirect_stderr(err):
            with self.assertRaises(SystemExit):
                CmdDestroy()(_Args(wsdir=ws, verbose=1))
        self.assertIn("f.txt", err.getvalue())

    def test_yes_removes_without_prompt(self):
        ws, deps = self._mk_workspace()
        out = io.StringIO()
        with redirect_stdout(out):
            CmdDestroy()(_Args(wsdir=ws, yes=True))
        self.assertFalse(os.path.exists(ws))
        self.assertIn("removed", out.getvalue())

    def test_non_tty_without_yes_refuses(self):
        ws, deps = self._mk_workspace()
        # stdin is explicitly a non-tty (see setUp); without --yes this must
        # refuse rather than prompt. input() is trapped, so a prompt fails.
        self._set_stdin(tty=False)
        with self.assertRaises(Exception):
            CmdDestroy()(_Args(wsdir=ws))
        self.assertTrue(os.path.exists(ws))   # nothing removed

    def test_deps_only_keeps_root(self):
        ws, deps = self._mk_workspace()
        out = io.StringIO()
        with redirect_stdout(out):
            CmdDestroy()(_Args(deps_only=True, project_dir=ws, yes=True))
        self.assertTrue(os.path.isfile(os.path.join(ws, "ivpm.yaml")))
        self.assertFalse(os.path.exists(os.path.join(deps, "liba")))

    def test_confirm_prompt_accepts_yes(self):
        ws, deps = self._mk_workspace()
        out = io.StringIO()
        self._set_stdin(tty=True)
        with mock.patch("builtins.input", return_value="y"):
            with redirect_stdout(out):
                CmdDestroy()(_Args(wsdir=ws))
        self.assertFalse(os.path.exists(ws))

    def test_confirm_prompt_abort(self):
        ws, deps = self._mk_workspace()
        out = io.StringIO()
        self._set_stdin(tty=True)
        with mock.patch("builtins.input", return_value="n"):
            with redirect_stdout(out):
                CmdDestroy()(_Args(wsdir=ws))
        self.assertIn("Aborted", out.getvalue())
        self.assertTrue(os.path.exists(ws))


class TestDestroyTuiRendering(TestBase):
    """Direct unit tests of the presentation layer (no I/O)."""

    def test_unknown_kind_falls_back_to_label(self):
        report = DestroyReport(mode="full", target="/ws", deps_dir="/ws/packages")
        report.gate = {
            "ext": RemovalSafety(SafetyLevel.BLOCKED, [
                SafetyReason("svn-locked", items=["x"], label="svn lock held")]),
        }
        report.blocked = True
        text = destroy_tui.render_blocking(report, verbose=0)
        self.assertIn("svn lock held", text)

    def test_unpushed_label_and_upstream(self):
        r = SafetyReason("unpushed", items=["msg"], count=2,
                         data={"upstream": "origin/feat"})
        self.assertIn("2 commits ahead of origin/feat",
                      destroy_tui._reason_summary(r))

    def test_summary_counts(self):
        report = DestroyReport(mode="deps-only", target="/ws",
                               deps_dir="/ws/packages")
        report.results = [
            PkgRemoveResult("a", "git", "/p/a", "rmtree", RemoveOutcome.REMOVED),
            PkgRemoveResult("b", "git", "/p/b", "error", RemoveOutcome.MANUAL,
                            error="boom"),
        ]
        text = destroy_tui.render_summary(report)
        self.assertIn("1 removed", text)
        self.assertIn("boom", text)

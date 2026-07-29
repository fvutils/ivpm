"""
Parallelization + progress-listener tests for `ivpm destroy`.

Verifies that the gate and teardown emit per-package progress events through a
RemoveProgressListener, and that a recording listener sees every package.
"""
import io
import json
import os
import subprocess
import sys
import threading
from unittest import mock

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "src"))

from .test_base import TestBase

from ivpm.project_ops import ProjectOps
from ivpm.pkg_remove import RemoveProgressListener, SafetyLevel, RemoveOutcome
from ivpm.destroy_tui import (
    create_destroy_tui, TranscriptDestroyTUI, RichDestroyTUI,
)


class _StdoutTty(io.StringIO):
    """A stdout stand-in with a fixed tty-ness, so TUI selection doesn't
    depend on how the tests were launched."""

    def __init__(self, tty):
        super().__init__()
        self._tty = tty

    def isatty(self):
        return self._tty


def _stdout_tty(tty):
    return mock.patch("sys.stdout", _StdoutTty(tty))


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
    _git(path, "checkout", "-q", head)


class _Recorder(RemoveProgressListener):
    def __init__(self):
        self.lock = threading.Lock()
        self.gate_started = []
        self.gate_done = []
        self.remove_started = []
        self.remove_done = []

    def on_gate_start(self, name):
        with self.lock:
            self.gate_started.append(name)

    def on_gate_result(self, name, safety):
        with self.lock:
            self.gate_done.append((name, safety.level))

    def on_remove_start(self, name):
        with self.lock:
            self.remove_started.append(name)

    def on_remove_result(self, result):
        with self.lock:
            self.remove_done.append((result.name, result.outcome))


class _Args:
    def __init__(self, **kw):
        self.deps_only = kw.get("deps_only", True)
        self.force = kw.get("force", False)
        self.dry_run = kw.get("dry_run", False)
        self.keep_venv = False
        self.jobs = kw.get("jobs", 0)
        self._destroy_progress = kw.get("progress", None)


class TestDestroyParallel(TestBase):

    def _mk_workspace(self, names):
        ws = os.path.join(self.testdir, "ws")
        deps = os.path.join(ws, "packages")
        os.makedirs(deps)
        with open(os.path.join(ws, "ivpm.yaml"), "w") as fp:
            fp.write("package:\n  name: root\n")
        entries = {}
        for n in names:
            _mk_git_dep(os.path.join(deps, n))
            entries[n] = {"src": "git", "url": "u"}
        with open(os.path.join(deps, "package-lock.json"), "w") as fp:
            json.dump({"ivpm_lock_version": 1, "packages": entries}, fp)
        return ws, deps

    def test_gate_emits_progress_for_every_package(self):
        names = ["a", "b", "c", "d"]
        ws, deps = self._mk_workspace(names)
        rec = _Recorder()
        ProjectOps(ws).destroy_plan(args=_Args(progress=rec, dry_run=True))
        self.assertEqual(set(rec.gate_started), set(names))
        self.assertEqual({n for n, _ in rec.gate_done}, set(names))
        self.assertTrue(all(lvl == SafetyLevel.SAFE for _, lvl in rec.gate_done))

    def test_teardown_emits_progress_for_every_package(self):
        names = ["a", "b", "c"]
        ws, deps = self._mk_workspace(names)
        rec = _Recorder()
        ProjectOps(ws).destroy_apply(args=_Args(progress=rec))
        self.assertEqual(set(rec.remove_started), set(names))
        self.assertEqual({n for n, _ in rec.remove_done}, set(names))
        self.assertTrue(all(o == RemoveOutcome.REMOVED
                            for _, o in rec.remove_done))

    def test_single_job_still_emits_all(self):
        names = ["a", "b", "c"]
        ws, deps = self._mk_workspace(names)
        rec = _Recorder()
        ProjectOps(ws).destroy_apply(args=_Args(progress=rec, jobs=1))
        self.assertEqual(set(rec.remove_done and
                             [n for n, _ in rec.remove_done]), set(names))

    def test_no_progress_listener_is_fine(self):
        # progress=None must not raise (the common headless path).
        names = ["a", "b"]
        ws, deps = self._mk_workspace(names)
        report = ProjectOps(ws).destroy_apply(args=_Args(progress=None))
        self.assertEqual(len(report.results), 2)


class TestCreateDestroyTui(TestBase):

    class _A:
        def __init__(self, no_rich=False):
            self.no_rich = no_rich
            self.verbose = 0

    def test_non_tty_returns_transcript(self):
        # A non-tty stdout -> transcript regardless of --no-rich. Force the
        # tty-ness rather than relying on how the tests were launched.
        with _stdout_tty(False):
            tui = create_destroy_tui(self._A(no_rich=False))
        self.assertIsInstance(tui, TranscriptDestroyTUI)

    def test_no_rich_returns_transcript(self):
        with _stdout_tty(True):
            tui = create_destroy_tui(self._A(no_rich=True))
        self.assertIsInstance(tui, TranscriptDestroyTUI)

    def test_tty_returns_rich(self):
        with _stdout_tty(True):
            tui = create_destroy_tui(self._A(no_rich=False))
        self.assertIsInstance(tui, RichDestroyTUI)

    def test_transcript_lifecycle_is_safe(self):
        # The lifecycle + listener methods must be callable without a live.
        tui = TranscriptDestroyTUI(verbose=1)
        tui.gate_begin()
        tui.on_gate_start("x")
        from ivpm.pkg_remove import RemovalSafety
        tui.on_gate_result("x", RemovalSafety(SafetyLevel.SAFE))
        tui.gate_end()
        tui.teardown_begin()
        tui.on_remove_start("x")
        from ivpm.pkg_remove import PkgRemoveResult
        tui.on_remove_result(PkgRemoveResult(
            "x", "git", "/p/x", "rmtree", RemoveOutcome.REMOVED))
        tui.teardown_end()

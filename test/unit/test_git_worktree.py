"""Unit tests for git worktree detection (src/ivpm/git_worktree.py).

The non-git and git-absent cases must pass regardless of the environment;
the worktree cases are skipped when git is unavailable.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOTDIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRCDIR = os.path.join(ROOTDIR, 'src')
sys.path.insert(0, SRCDIR)

from ivpm import git_worktree
from ivpm.git_worktree import detect_main_worktree

_HAVE_GIT = shutil.which("git") is not None


def _git(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _init_repo(path):
    os.makedirs(path, exist_ok=True)
    _git("init", "-q", cwd=path)
    _git("config", "user.email", "t@e.st", cwd=path)
    _git("config", "user.name", "Test", cwd=path)
    _git("config", "commit.gpgsign", "false", cwd=path)
    with open(os.path.join(path, "f.txt"), "w") as f:
        f.write("hi\n")
    _git("add", "f.txt", cwd=path)
    _git("commit", "-q", "-m", "init", cwd=path)


class TestGitWorktreeNoGit(unittest.TestCase):
    """These must run even without git installed."""

    def test_non_repo_dir_returns_none(self):
        d = tempfile.mkdtemp(prefix="ivpm-nogit-")
        try:
            self.assertIsNone(detect_main_worktree(d))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_git_absent_returns_none(self):
        # Simulate git not being installed: _git's subprocess.run raises.
        orig = subprocess.run

        def boom(*a, **kw):
            raise FileNotFoundError("git")

        subprocess.run = boom
        try:
            self.assertIsNone(detect_main_worktree("."))
        finally:
            subprocess.run = orig

    def test_git_helper_swallows_nonzero(self):
        # _git returns None on a bogus subcommand rather than raising.
        d = tempfile.mkdtemp(prefix="ivpm-nogit-")
        try:
            self.assertIsNone(git_worktree._git(d, "rev-parse", "--git-dir"))
        finally:
            shutil.rmtree(d, ignore_errors=True)


@unittest.skipUnless(_HAVE_GIT, "git not installed")
class TestGitWorktreeWithGit(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ivpm-wt-")
        self.main = os.path.join(self.tmp, "main")
        _init_repo(self.main)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_main_worktree_returns_none(self):
        self.assertIsNone(detect_main_worktree(self.main))

    def test_linked_worktree_returns_main(self):
        wt = os.path.join(self.tmp, "feature")
        _git("worktree", "add", "--detach", wt, cwd=self.main)
        got = detect_main_worktree(wt)
        self.assertIsNotNone(got)
        self.assertEqual(os.path.realpath(got), os.path.realpath(self.main))

    def test_nested_worktree_returns_true_main(self):
        # A worktree's `git worktree list` still reports the real main first.
        wt = os.path.join(self.tmp, "feature")
        _git("worktree", "add", "--detach", wt, cwd=self.main)
        got = detect_main_worktree(wt)
        self.assertEqual(os.path.realpath(got), os.path.realpath(self.main))


if __name__ == "__main__":
    unittest.main()

"""End-to-end tests for automatic worktree deps-source detection.

Exercises ProjectOps._configure_deps_source (auto-detection of the parent git
worktree's deps-dir) and ProjectOps._mark_worktree_provenance, without any
network fetch.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

ROOTDIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRCDIR = os.path.join(ROOTDIR, 'src')
sys.path.insert(0, SRCDIR)

from ivpm.project_ops import ProjectOps
from ivpm.project_ops_info import ProjectUpdateInfo
from ivpm.pkg_status import PkgVcsStatus

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
    with open(os.path.join(path, "ivpm.yaml"), "w") as f:
        f.write("package:\n  name: demo\n")
    _git("add", "ivpm.yaml", cwd=path)
    _git("commit", "-q", "-m", "init", cwd=path)


def _materialize_deps(deps_dir, names, with_lock=True):
    os.makedirs(deps_dir, exist_ok=True)
    if with_lock:
        lock = {"ivpm_lock_version": 1, "packages":
                {n: {"src": "git", "commit_resolved": "abc"} for n in names}}
        with open(os.path.join(deps_dir, "package-lock.json"), "w") as f:
            json.dump(lock, f)
    for n in names:
        d = os.path.join(deps_dir, n)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "marker.txt"), "w") as f:
            f.write("from-main\n")


def _args(**over):
    base = dict(deps_source=None, no_worktree_deps_source=False,
                trust_deps_source=False, deps_source_mode="link")
    base.update(over)
    return argparse.Namespace(**base)


def _proj_info(deps_dir="packages"):
    return types.SimpleNamespace(deps_dir=deps_dir)


@unittest.skipUnless(_HAVE_GIT, "git not installed")
class TestWorktreeDepsSourceConfig(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ivpm-wt-ds-")
        self.main = os.path.join(self.tmp, "main")
        _init_repo(self.main)
        self.main_deps = os.path.join(self.main, "packages")
        _materialize_deps(self.main_deps, ["foo", "bar"])
        self.wt = os.path.join(self.tmp, "feature")
        _git("worktree", "add", "--detach", self.wt, cwd=self.main)
        self.wt_deps = os.path.join(self.wt, "packages")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _configure(self, args):
        ops = ProjectOps(root_dir=self.wt)
        info = ProjectUpdateInfo(args=args, deps_dir=self.wt_deps)
        ops._configure_deps_source(info, args, _proj_info())
        return info

    def test_auto_detects_main_worktree(self):
        info = self._configure(_args())
        self.assertIsNotNone(info.deps_source)
        self.assertTrue(info.deps_source_auto)
        self.assertEqual(len(info.deps_source.entries), 1)
        self.assertEqual(info.deps_source.entries[0].parent_dir,
                         os.path.realpath(self.main_deps))
        # auto path must NOT trust by name
        self.assertFalse(info.deps_source.entries[0].trust)

    def test_no_worktree_flag_disables(self):
        info = self._configure(_args(no_worktree_deps_source=True))
        self.assertIsNone(info.deps_source)
        self.assertFalse(info.deps_source_auto)

    def test_explicit_deps_source_takes_precedence(self):
        other = os.path.join(self.tmp, "other")
        _materialize_deps(other, ["foo"])
        info = self._configure(_args(deps_source=[other]))
        self.assertIsNotNone(info.deps_source)
        self.assertFalse(info.deps_source_auto)
        self.assertEqual(info.deps_source.entries[0].parent_dir,
                         os.path.realpath(other))

    def test_env_var_takes_precedence(self):
        other = os.path.join(self.tmp, "other")
        _materialize_deps(other, ["foo"])
        os.environ["IVPM_DEPS_SOURCE"] = other
        try:
            info = self._configure(_args())
        finally:
            del os.environ["IVPM_DEPS_SOURCE"]
        self.assertFalse(info.deps_source_auto)
        self.assertEqual(info.deps_source.entries[0].parent_dir,
                         os.path.realpath(other))

    def test_main_without_lock_still_engages(self):
        # Remove the main lock file; auto still configures (matching will miss).
        os.remove(os.path.join(self.main_deps, "package-lock.json"))
        info = self._configure(_args())
        self.assertIsNotNone(info.deps_source)
        self.assertTrue(info.deps_source_auto)
        self.assertIsNone(info.deps_source.entries[0].lock)

    def test_main_without_deps_dir_is_noop(self):
        shutil.rmtree(self.main_deps)
        info = self._configure(_args())
        self.assertIsNone(info.deps_source)
        self.assertFalse(info.deps_source_auto)


@unittest.skipUnless(_HAVE_GIT, "git not installed")
class TestWorktreeProvenanceMarking(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ivpm-wt-prov-")
        self.main = os.path.join(self.tmp, "main")
        _init_repo(self.main)
        _materialize_deps(os.path.join(self.main, "packages"), ["foo"])
        self.wt = os.path.join(self.tmp, "feature")
        _git("worktree", "add", "--detach", self.wt, cwd=self.main)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_marks_worktree_sourced_package(self):
        ops = ProjectOps(root_dir=self.wt)
        in_main = PkgVcsStatus(
            name="foo", src_type="git", path="x", vcs="none",
            from_deps_source=os.path.join(self.main, "packages", "foo"))
        elsewhere = PkgVcsStatus(
            name="bar", src_type="git", path="y", vcs="none",
            from_deps_source="/some/other/golden/packages/bar")
        plain = PkgVcsStatus(name="baz", src_type="git", path="z", vcs="git")
        ops._mark_worktree_provenance([in_main, elsewhere, plain], "packages")
        self.assertTrue(in_main.deps_source_auto)
        self.assertFalse(elsewhere.deps_source_auto)
        self.assertFalse(plain.deps_source_auto)


class TestNonGitProject(unittest.TestCase):
    """A non-git project directory must never auto-configure a deps-source."""

    def test_non_git_dir_no_auto(self):
        tmp = tempfile.mkdtemp(prefix="ivpm-nongit-")
        try:
            deps = os.path.join(tmp, "packages")
            os.makedirs(deps, exist_ok=True)
            ops = ProjectOps(root_dir=tmp)
            args = _args()
            info = ProjectUpdateInfo(args=args, deps_dir=deps)
            ops._configure_deps_source(info, args, _proj_info())
            self.assertIsNone(info.deps_source)
            self.assertFalse(info.deps_source_auto)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

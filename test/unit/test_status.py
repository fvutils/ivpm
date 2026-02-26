import json
import os
import subprocess
import unittest
from .test_base import TestBase


def _git(args, cwd):
    subprocess.check_call(["git"] + args, cwd=cwd,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _make_git_repo(path, name="repo", branch="main"):
    """Create a minimal git repo with one commit."""
    os.makedirs(path, exist_ok=True)
    _git(["init", "-b", branch], path)
    _git(["config", "user.email", "test@ivpm"], path)
    _git(["config", "user.name", "IVPM Test"], path)
    readme = os.path.join(path, "README.md")
    with open(readme, "w") as f:
        f.write("# %s\n" % name)
    _git(["add", "README.md"], path)
    _git(["commit", "-m", "initial"], path)


def _write_lock(packages_dir, packages_dict):
    """Write a minimal package-lock.json that passes read_lock()."""
    import hashlib
    lock = {
        "ivpm_lock_version": 1,
        "generated": "2026-01-01T00:00:00+00:00",
        "packages": packages_dict,
    }
    body = json.dumps(lock, indent=2, sort_keys=True)
    lock["sha256"] = hashlib.sha256(body.encode()).hexdigest()
    os.makedirs(packages_dir, exist_ok=True)
    with open(os.path.join(packages_dir, "package-lock.json"), "w") as f:
        json.dump(lock, f, indent=2, sort_keys=True)


class TestStatus(TestBase):

    def _make_project(self, packages):
        """Create a minimal IVPM project with the given packages dict in the lock."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_status_project
            dep-sets:
                - name: default-dev
                  deps: []
        """)
        packages_dir = os.path.join(self.testdir, "packages")
        _write_lock(packages_dir, packages)
        return packages_dir

    def test_clean_git_pkg(self):
        """A clean git package reports is_dirty=False."""
        packages_dir = self._make_project({})
        pkg_path = os.path.join(packages_dir, "mypkg")
        _make_git_repo(pkg_path, name="mypkg")
        _write_lock(packages_dir, {
            "mypkg": {"src": "git", "resolved_by": "root", "dep_set": None,
                      "url": "file://%s" % pkg_path, "branch": "main",
                      "reproducible": True}
        })

        from ivpm.project_ops import ProjectOps
        results = ProjectOps(self.testdir).status()

        git_results = [r for r in results if r.name == "mypkg"]
        self.assertEqual(len(git_results), 1)
        r = git_results[0]
        self.assertEqual(r.vcs, "git")
        self.assertFalse(r.is_dirty)
        self.assertEqual(r.branch, "main")
        self.assertNotEqual(r.commit, "")

    def test_dirty_git_pkg(self):
        """A git package with a modified file reports is_dirty=True."""
        packages_dir = self._make_project({})
        pkg_path = os.path.join(packages_dir, "dirtypkg")
        _make_git_repo(pkg_path, name="dirtypkg")
        # Modify a tracked file
        with open(os.path.join(pkg_path, "README.md"), "a") as f:
            f.write("dirty change\n")
        _write_lock(packages_dir, {
            "dirtypkg": {"src": "git", "resolved_by": "root", "dep_set": None,
                         "url": "file://%s" % pkg_path, "branch": "main",
                         "reproducible": True}
        })

        from ivpm.project_ops import ProjectOps
        results = ProjectOps(self.testdir).status()

        r = next(x for x in results if x.name == "dirtypkg")
        self.assertEqual(r.vcs, "git")
        self.assertTrue(r.is_dirty)
        self.assertTrue(len(r.modified) > 0)
        self.assertTrue(any("README.md" in line for line in r.modified))

    def test_branch_detected(self):
        """The active branch name is reported correctly."""
        packages_dir = self._make_project({})
        pkg_path = os.path.join(packages_dir, "branchpkg")
        _make_git_repo(pkg_path, name="branchpkg", branch="feature-x")
        _write_lock(packages_dir, {
            "branchpkg": {"src": "git", "resolved_by": "root", "dep_set": None,
                          "url": "file://%s" % pkg_path, "branch": "feature-x",
                          "reproducible": True}
        })

        from ivpm.project_ops import ProjectOps
        results = ProjectOps(self.testdir).status()

        r = next(x for x in results if x.name == "branchpkg")
        self.assertEqual(r.branch, "feature-x")

    def test_non_git_pkg(self):
        """A dir-type package produces vcs='none' and does not crash."""
        packages_dir = self._make_project({})
        pkg_path = os.path.join(packages_dir, "dirpkg")
        os.makedirs(pkg_path)
        with open(os.path.join(pkg_path, "file.txt"), "w") as f:
            f.write("hello\n")
        _write_lock(packages_dir, {
            "dirpkg": {"src": "dir", "resolved_by": "root", "dep_set": None,
                       "path": pkg_path, "reproducible": False}
        })

        from ivpm.project_ops import ProjectOps
        results = ProjectOps(self.testdir).status()

        r = next(x for x in results if x.name == "dirpkg")
        self.assertEqual(r.vcs, "none")
        self.assertFalse(r.is_dirty)

    def test_missing_pkg_dir(self):
        """A git package whose directory is absent returns an error sentinel."""
        packages_dir = self._make_project({
            "ghostpkg": {"src": "git", "resolved_by": "root", "dep_set": None,
                         "url": "file:///nonexistent", "branch": "main",
                         "reproducible": True}
        })

        from ivpm.project_ops import ProjectOps
        results = ProjectOps(self.testdir).status()

        r = next(x for x in results if x.name == "ghostpkg")
        self.assertEqual(r.vcs, "git")
        self.assertIsNotNone(r.error)

    def test_missing_lockfile_fatal(self):
        """Calling status without a package-lock.json calls fatal()."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_no_lock
            dep-sets:
                - name: default-dev
                  deps: []
        """)
        # No packages dir / lockfile created

        from ivpm.project_ops import ProjectOps
        with self.assertRaises(Exception):
            ProjectOps(self.testdir).status()

    def test_transcript_render(self):
        """TranscriptStatusTUI.render() produces output without error."""
        from ivpm.pkg_status import PkgVcsStatus
        from ivpm.status_tui import TranscriptStatusTUI
        import io

        results = [
            PkgVcsStatus(name="clean", src_type="git", path="/tmp/clean",
                         vcs="git", branch="main", commit="abc1234",
                         is_dirty=False, modified=[], ahead=0, behind=0),
            PkgVcsStatus(name="dirty", src_type="git", path="/tmp/dirty",
                         vcs="git", branch="dev", commit="def5678",
                         is_dirty=True, modified=[" M foo.py"], ahead=1, behind=0),
            PkgVcsStatus(name="lib", src_type="dir", path="/tmp/lib",
                         vcs="none"),
        ]

        import sys
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            TranscriptStatusTUI().render(results, verbose=0)
            output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        self.assertIn("clean", output)
        self.assertIn("dirty", output)
        self.assertIn("lib", output)
        self.assertNotIn("foo.py", output)  # verbose=0

    def test_transcript_render_verbose(self):
        """TranscriptStatusTUI with verbose=True shows modified files."""
        from ivpm.pkg_status import PkgVcsStatus
        from ivpm.status_tui import TranscriptStatusTUI
        import io, sys

        results = [
            PkgVcsStatus(name="dirty", src_type="git", path="/tmp/dirty",
                         vcs="git", branch="dev", commit="def5678",
                         is_dirty=True, modified=[" M foo.py", "?? bar.py"],
                         ahead=None, behind=None),
        ]

        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            TranscriptStatusTUI().render(results, verbose=1)
            output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        self.assertIn("foo.py", output)
        self.assertIn("bar.py", output)

"""
Gate tests for `ivpm destroy`: Package.removal_safety().

Phase 2 covers the base Package classification (missing / symlink / writable
dir via working_tree_dirty). Phase 3 adds the real git override cases.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "src"))

from .test_base import TestBase

from ivpm.package import Package
from ivpm.pkg_types.package_git import PackageGit
from ivpm.pkg_remove import SafetyLevel


class _Tree(Package):
    """A base Package whose working-tree cleanliness we can pin per-test."""
    _dirty = None

    def working_tree_dirty(self, pkg_dir):
        return self._dirty


class TestDestroyGateBase(TestBase):

    def _mkpkg(self, relpath, dirty=None):
        pkg = _Tree(name="p")
        pkg.path = os.path.join(self.testdir, relpath)
        pkg._dirty = dirty
        return pkg

    def test_missing_path_is_safe(self):
        pkg = self._mkpkg("does-not-exist")
        v = pkg.removal_safety(None)
        self.assertEqual(v.level, SafetyLevel.SAFE)

    def test_symlink_is_safe_and_records_target(self):
        target = os.path.join(self.testdir, "real")
        os.makedirs(target)
        link = os.path.join(self.testdir, "link")
        os.symlink(target, link)
        pkg = self._mkpkg("link")
        v = pkg.removal_safety(None)
        self.assertEqual(v.level, SafetyLevel.SAFE)
        self.assertEqual(len(v.reasons), 1)
        self.assertEqual(v.reasons[0].kind, "symlink")
        self.assertEqual(
            os.path.realpath(v.reasons[0].data["target"]),
            os.path.realpath(target))

    def test_clean_writable_dir_is_safe(self):
        os.makedirs(os.path.join(self.testdir, "clean"))
        pkg = self._mkpkg("clean", dirty=False)
        v = pkg.removal_safety(None)
        self.assertEqual(v.level, SafetyLevel.SAFE)

    def test_dirty_writable_dir_is_blocked(self):
        os.makedirs(os.path.join(self.testdir, "dirty"))
        pkg = self._mkpkg("dirty", dirty=True)
        v = pkg.removal_safety(None)
        self.assertEqual(v.level, SafetyLevel.BLOCKED)
        self.assertEqual(v.reasons[0].kind, "modified")

    def test_unverifiable_writable_dir(self):
        os.makedirs(os.path.join(self.testdir, "archive"))
        pkg = self._mkpkg("archive", dirty=None)
        v = pkg.removal_safety(None)
        self.assertEqual(v.level, SafetyLevel.UNVERIFIABLE)
        self.assertEqual(v.reasons[0].kind, "unverifiable")
        self.assertIsNotNone(v.reasons[0].label)


def _git(repo, *args):
    return subprocess.run(["git"] + list(args), cwd=repo,
                          capture_output=True, text=True, check=True)


def _init_repo(repo):
    os.makedirs(repo, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "commit.gpgsign", "false")


def _commit(repo, fname, content, msg):
    with open(os.path.join(repo, fname), "w") as fp:
        fp.write(content)
    _git(repo, "add", fname)
    _git(repo, "commit", "-q", "-m", msg)


class TestDestroyGateGit(TestBase):
    """Real git repos in tmp; PackageGit.removal_safety() over each state."""

    def _pkg(self, repo):
        pkg = PackageGit(name="p")
        pkg.path = repo
        return pkg

    def _clone_with_upstream(self):
        """Return a working clone whose 'main' tracks origin/main."""
        origin = os.path.join(self.testdir, "origin.git")
        seed = os.path.join(self.testdir, "seed")
        _init_repo(seed)
        _commit(seed, "a.txt", "1", "init")
        subprocess.run(["git", "init", "-q", "--bare", origin], check=True)
        subprocess.run(["git", "-C", origin, "symbolic-ref", "HEAD",
                        "refs/heads/main"], check=True)
        _git(seed, "remote", "add", "origin", origin)
        _git(seed, "push", "-q", "-u", "origin", "main")
        work = os.path.join(self.testdir, "work")
        subprocess.run(["git", "clone", "-q", origin, work], check=True)
        _git(work, "config", "user.email", "t@t.com")
        _git(work, "config", "user.name", "T")
        _git(work, "config", "commit.gpgsign", "false")
        return work

    def test_missing_is_safe(self):
        pkg = self._pkg(os.path.join(self.testdir, "nope"))
        self.assertEqual(pkg.removal_safety(None).level, SafetyLevel.SAFE)

    def test_clean_clone_is_safe(self):
        work = self._clone_with_upstream()
        v = self._pkg(work).removal_safety(None)
        self.assertEqual(v.level, SafetyLevel.SAFE)
        self.assertEqual(v.reasons, [])

    def test_modified_tracked_file_blocks(self):
        work = self._clone_with_upstream()
        with open(os.path.join(work, "a.txt"), "w") as fp:
            fp.write("changed")
        v = self._pkg(work).removal_safety(None)
        self.assertEqual(v.level, SafetyLevel.BLOCKED)
        kinds = {r.kind for r in v.reasons}
        self.assertIn("modified", kinds)
        mod = next(r for r in v.reasons if r.kind == "modified")
        self.assertIn("a.txt", mod.items)

    def test_untracked_file_blocks(self):
        work = self._clone_with_upstream()
        with open(os.path.join(work, "new.txt"), "w") as fp:
            fp.write("x")
        v = self._pkg(work).removal_safety(None)
        self.assertEqual(v.level, SafetyLevel.BLOCKED)
        unt = next(r for r in v.reasons if r.kind == "untracked")
        self.assertIn("new.txt", unt.items)

    def test_gitignored_file_does_not_block(self):
        work = self._clone_with_upstream()
        with open(os.path.join(work, ".gitignore"), "w") as fp:
            fp.write("build/\n")
        _git(work, "add", ".gitignore")
        _git(work, "commit", "-q", "-m", "ignore")
        _git(work, "push", "-q")
        os.makedirs(os.path.join(work, "build"))
        with open(os.path.join(work, "build", "junk.o"), "w") as fp:
            fp.write("junk")
        v = self._pkg(work).removal_safety(None)
        self.assertEqual(v.level, SafetyLevel.SAFE)

    def test_unpushed_commit_blocks(self):
        work = self._clone_with_upstream()
        _commit(work, "b.txt", "2", "local work")
        v = self._pkg(work).removal_safety(None)
        self.assertEqual(v.level, SafetyLevel.BLOCKED)
        up = next(r for r in v.reasons if r.kind == "unpushed")
        self.assertEqual(up.count, 1)
        self.assertIn("local work", up.items)
        self.assertIn("origin/main", up.data["upstream"])

    def test_local_only_branch_blocks(self):
        repo = os.path.join(self.testdir, "localonly")
        _init_repo(repo)
        _commit(repo, "a.txt", "1", "init")
        v = self._pkg(repo).removal_safety(None)
        self.assertEqual(v.level, SafetyLevel.BLOCKED)
        self.assertIn("local-branch", {r.kind for r in v.reasons})

    def test_stash_blocks(self):
        work = self._clone_with_upstream()
        with open(os.path.join(work, "a.txt"), "w") as fp:
            fp.write("wip")
        _git(work, "stash")
        v = self._pkg(work).removal_safety(None)
        self.assertEqual(v.level, SafetyLevel.BLOCKED)
        self.assertIn("stash", {r.kind for r in v.reasons})

    def test_detached_head_clean_is_safe(self):
        work = self._clone_with_upstream()
        _commit(work, "b.txt", "2", "second")
        _git(work, "push", "-q")
        head = _git(work, "rev-parse", "HEAD").stdout.strip()
        _git(work, "checkout", "-q", head)   # detached HEAD at a pushed commit
        v = self._pkg(work).removal_safety(None)
        self.assertEqual(v.level, SafetyLevel.SAFE)

    def test_cache_symlink_dep_is_safe_without_git(self):
        # A cache-backed dep is a symlink; the gate must not run git on it.
        real = os.path.join(self.testdir, "cache-entry")
        _init_repo(real)
        _commit(real, "a.txt", "1", "init")
        # make it look locally-dirty so we'd notice if git ran through the link
        with open(os.path.join(real, "a.txt"), "w") as fp:
            fp.write("dirty")
        link = os.path.join(self.testdir, "deps", "p")
        os.makedirs(os.path.dirname(link))
        os.symlink(real, link)
        v = self._pkg(link).removal_safety(None)
        self.assertEqual(v.level, SafetyLevel.SAFE)

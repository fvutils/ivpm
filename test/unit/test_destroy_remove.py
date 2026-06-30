"""
Teardown tests for `ivpm destroy`: Package.remove().

Covers unlink-vs-rmtree, read-only trees, the critical symlink-target-intact
safety property, dry-run, and idempotency.
"""
import os
import stat
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "src"))

from .test_base import TestBase

from ivpm.package import Package
from ivpm.pkg_remove import RemoveOutcome
from ivpm.project_ops_info import ProjectRemoveInfo


def _info(dry=False):
    return ProjectRemoveInfo(args=None, deps_dir="/unused", dry_run=dry)


class TestDestroyRemove(TestBase):

    def _pkg(self, relpath):
        pkg = Package(name="p")
        pkg.path = os.path.join(self.testdir, relpath)
        return pkg

    def test_writable_dir_rmtree(self):
        d = os.path.join(self.testdir, "tree")
        os.makedirs(os.path.join(d, "sub"))
        with open(os.path.join(d, "sub", "f.txt"), "w") as fp:
            fp.write("x")
        r = self._pkg("tree").remove(_info())
        self.assertEqual(r.removal, "rmtree")
        self.assertEqual(r.outcome, RemoveOutcome.REMOVED)
        self.assertFalse(os.path.exists(d))

    def test_readonly_tree_removed(self):
        d = os.path.join(self.testdir, "ro")
        os.makedirs(d)
        f = os.path.join(d, "f.txt")
        with open(f, "w") as fp:
            fp.write("x")
        os.chmod(f, stat.S_IRUSR)            # strip write bit
        os.chmod(d, stat.S_IRUSR | stat.S_IXUSR)
        r = self._pkg("ro").remove(_info())
        self.assertEqual(r.outcome, RemoveOutcome.REMOVED)
        self.assertFalse(os.path.exists(d))

    def test_symlink_unlinked_target_intact(self):
        # The critical safety property: a cache/deps-source symlink must be
        # unlinked, NEVER recursed into.
        target = os.path.join(self.testdir, "cache-entry")
        os.makedirs(target)
        with open(os.path.join(target, "keep.txt"), "w") as fp:
            fp.write("precious")
        link = os.path.join(self.testdir, "deps", "p")
        os.makedirs(os.path.dirname(link))
        os.symlink(target, link)
        r = self._pkg("deps/p").remove(_info())
        self.assertEqual(r.removal, "unlink")
        self.assertEqual(r.outcome, RemoveOutcome.REMOVED)
        self.assertFalse(os.path.lexists(link))           # link gone
        self.assertTrue(os.path.isdir(target))            # target intact
        self.assertTrue(os.path.exists(os.path.join(target, "keep.txt")))

    def test_plain_file_unlinked(self):
        f = os.path.join(self.testdir, "afile")
        with open(f, "w") as fp:
            fp.write("x")
        r = self._pkg("afile").remove(_info())
        self.assertEqual(r.removal, "unlink")
        self.assertFalse(os.path.exists(f))

    def test_missing_is_noop(self):
        pkg = self._pkg("ghost")
        r = pkg.remove(_info())
        self.assertEqual(r.removal, "noop")
        self.assertEqual(r.outcome, RemoveOutcome.SKIPPED)
        # idempotent — second call also fine
        r2 = pkg.remove(_info())
        self.assertEqual(r2.removal, "noop")

    def test_dry_run_changes_nothing(self):
        d = os.path.join(self.testdir, "tree")
        os.makedirs(d)
        with open(os.path.join(d, "f.txt"), "w") as fp:
            fp.write("x")
        r = self._pkg("tree").remove(_info(dry=True))
        self.assertEqual(r.removal, "rmtree")
        self.assertEqual(r.outcome, RemoveOutcome.SKIPPED)
        self.assertEqual(r.removed_paths, [d])
        self.assertTrue(os.path.isdir(d))                 # still there

    def test_dry_run_symlink_reports_unlink(self):
        target = os.path.join(self.testdir, "t")
        os.makedirs(target)
        link = os.path.join(self.testdir, "lnk")
        os.symlink(target, link)
        r = self._pkg("lnk").remove(_info(dry=True))
        self.assertEqual(r.removal, "unlink")
        self.assertTrue(os.path.lexists(link))

    def test_pypi_defers_to_provider(self):
        from ivpm.pkg_types.package_pypi import PackagePyPi
        pkg = PackagePyPi("mylib")
        pkg.path = os.path.join(self.testdir, "python", "mylib")
        r = pkg.remove(_info())
        self.assertEqual(r.removal, "provider")
        self.assertEqual(r.outcome, RemoveOutcome.SKIPPED)

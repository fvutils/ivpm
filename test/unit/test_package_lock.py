#****************************************************************************
#* test_package_lock.py
#*
#* Tests for the package_lock module (write/read round-trip, change detection,
#* reproducible flag, and Python pip version contribution).
#****************************************************************************
import os
import json
import tempfile
import unittest

from ivpm.package_lock import write_lock, read_lock, check_lock_changes, LOCK_VERSION
from ivpm.packages_info import PackagesInfo


def _make_git_pkg(name, url, branch=None, commit=None, resolved_commit=None, cache=None):
    from ivpm.pkg_types.package_git import PackageGit
    p = PackageGit(name)
    p.url = url
    p.branch = branch
    p.commit = commit
    p.resolved_commit = resolved_commit
    p.cache = cache
    p.src_type = "git"
    p.resolved_by = "root"
    return p


def _make_gh_rls_pkg(name, url, version="latest", resolved_version=None):
    from ivpm.pkg_types.package_gh_rls import PackageGhRls
    p = PackageGhRls(name)
    p.url = url
    p.version = version
    p.resolved_version = resolved_version
    p.src_type = "gh-rls"
    p.resolved_by = "root"
    return p


def _make_pypi_pkg(name, version=None, resolved_version=None):
    from ivpm.pkg_types.package_pypi import PackagePyPi
    p = PackagePyPi(name)
    p.version = version
    p.resolved_version = resolved_version
    p.src_type = "pypi"
    p.resolved_by = "root"
    return p


def _make_dir_pkg(name, path):
    from ivpm.pkg_types.package_dir import PackageDir
    p = PackageDir(name)
    p.url = "file://" + path
    p.src_type = "dir"
    p.resolved_by = "root"
    return p


class TestPackageLock(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_pkgs(self, *pkgs):
        pi = PackagesInfo("root")
        for p in pkgs:
            pi[p.name] = p
        return pi

    # ------------------------------------------------------------------
    # Basic write/read round-trip
    # ------------------------------------------------------------------

    def test_write_read_roundtrip_git(self):
        pkg = _make_git_pkg(
            "myrepo",
            "https://github.com/org/myrepo.git",
            branch="main",
            resolved_commit="abc123def456",
        )
        pkgs = self._make_pkgs(pkg)
        write_lock(self.tmpdir, pkgs)

        lock_path = os.path.join(self.tmpdir, "package-lock.json")
        self.assertTrue(os.path.isfile(lock_path))

        data = read_lock(lock_path)
        self.assertEqual(data["ivpm_lock_version"], LOCK_VERSION)
        self.assertIn("myrepo", data["packages"])
        entry = data["packages"]["myrepo"]
        self.assertEqual(entry["src"], "git")
        self.assertEqual(entry["url"], "https://github.com/org/myrepo.git")
        self.assertEqual(entry["branch"], "main")
        self.assertEqual(entry["commit_resolved"], "abc123def456")
        self.assertTrue(entry["reproducible"])

    def test_write_read_roundtrip_gh_rls(self):
        pkg = _make_gh_rls_pkg(
            "mytool", "https://github.com/org/mytool",
            version="latest", resolved_version="v2.3.1"
        )
        pkgs = self._make_pkgs(pkg)
        write_lock(self.tmpdir, pkgs)

        data = read_lock(os.path.join(self.tmpdir, "package-lock.json"))
        entry = data["packages"]["mytool"]
        self.assertEqual(entry["src"], "gh-rls")
        self.assertEqual(entry["version_requested"], "latest")
        self.assertEqual(entry["version_resolved"], "v2.3.1")
        self.assertNotIn("platform", entry)  # platform NOT stored in lock

    def test_write_read_roundtrip_pypi(self):
        pkg = _make_pypi_pkg("requests", version=">=2.0", resolved_version="2.31.0")
        pkgs = self._make_pkgs(pkg)
        write_lock(self.tmpdir, pkgs)

        data = read_lock(os.path.join(self.tmpdir, "package-lock.json"))
        entry = data["packages"]["requests"]
        self.assertEqual(entry["src"], "pypi")
        self.assertEqual(entry["version_requested"], ">=2.0")
        self.assertEqual(entry["version_resolved"], "2.31.0")
        self.assertTrue(entry["reproducible"])

    def test_dir_package_not_reproducible(self):
        pkg = _make_dir_pkg("locallib", "/home/user/locallib")
        pkgs = self._make_pkgs(pkg)
        write_lock(self.tmpdir, pkgs)

        data = read_lock(os.path.join(self.tmpdir, "package-lock.json"))
        entry = data["packages"]["locallib"]
        self.assertFalse(entry["reproducible"])
        self.assertEqual(entry["src"], "dir")

    # ------------------------------------------------------------------
    # Atomic write (tmp file replaced)
    # ------------------------------------------------------------------

    def test_atomic_write_no_tmp_left(self):
        pkgs = self._make_pkgs(_make_git_pkg("repo", "https://github.com/x/y.git"))
        write_lock(self.tmpdir, pkgs)
        tmp = os.path.join(self.tmpdir, "package-lock.json.tmp")
        self.assertFalse(os.path.exists(tmp))

    # ------------------------------------------------------------------
    # Integrity checksum
    # ------------------------------------------------------------------

    def test_checksum_present(self):
        pkgs = self._make_pkgs(_make_git_pkg("repo", "https://github.com/x/y.git"))
        write_lock(self.tmpdir, pkgs)
        with open(os.path.join(self.tmpdir, "package-lock.json")) as f:
            raw = json.load(f)
        self.assertIn("sha256", raw)
        self.assertEqual(len(raw["sha256"]), 64)  # hex SHA-256

    def test_checksum_tamper_warns(self):
        pkgs = self._make_pkgs(_make_git_pkg("repo", "https://github.com/x/y.git"))
        write_lock(self.tmpdir, pkgs)
        lock_path = os.path.join(self.tmpdir, "package-lock.json")
        with open(lock_path) as f:
            raw = json.load(f)
        raw["sha256"] = "0" * 64  # corrupt checksum
        with open(lock_path, "w") as f:
            json.dump(raw, f)
        # Should not raise, but logs a warning
        import logging
        with self.assertLogs("ivpm.package_lock", level="WARNING"):
            read_lock(lock_path)

    # ------------------------------------------------------------------
    # Handler contributions (python_packages)
    # ------------------------------------------------------------------

    def test_handler_contributions_written(self):
        pkgs = self._make_pkgs(_make_pypi_pkg("requests", ">=2.0", "2.31.0"))
        contributions = {"python_packages": {"requests": "2.31.0", "certifi": "2024.1.1"}}
        write_lock(self.tmpdir, pkgs, handler_contributions=contributions)

        data = read_lock(os.path.join(self.tmpdir, "package-lock.json"))
        self.assertIn("python_packages", data)
        self.assertEqual(data["python_packages"]["requests"], "2.31.0")
        self.assertEqual(data["python_packages"]["certifi"], "2024.1.1")

    # ------------------------------------------------------------------
    # Change detection
    # ------------------------------------------------------------------

    def test_change_detection_no_diff(self):
        pkg = _make_git_pkg(
            "repo", "https://github.com/x/y.git", branch="main",
            resolved_commit="abc"
        )
        pkgs = self._make_pkgs(pkg)
        write_lock(self.tmpdir, pkgs)

        # Same spec — no diffs expected
        diffs = check_lock_changes(self.tmpdir, pkgs)
        self.assertEqual(diffs, {})

    def test_change_detection_branch_changed(self):
        pkg_orig = _make_git_pkg(
            "repo", "https://github.com/x/y.git", branch="main",
            resolved_commit="abc"
        )
        pkgs_orig = self._make_pkgs(pkg_orig)
        write_lock(self.tmpdir, pkgs_orig)

        # User changes branch to 'dev'
        pkg_new = _make_git_pkg(
            "repo", "https://github.com/x/y.git", branch="dev",
        )
        pkgs_new = self._make_pkgs(pkg_new)
        diffs = check_lock_changes(self.tmpdir, pkgs_new)
        self.assertIn("repo", diffs)

    def test_change_detection_no_lock_file(self):
        """When no lock file exists, check_lock_changes returns empty dict."""
        pkg = _make_git_pkg("repo", "https://github.com/x/y.git")
        diffs = check_lock_changes(self.tmpdir, self._make_pkgs(pkg))
        self.assertEqual(diffs, {})

    # ------------------------------------------------------------------
    # Version rejection
    # ------------------------------------------------------------------

    def test_reject_unknown_lock_version(self):
        pkgs = self._make_pkgs(_make_git_pkg("repo", "https://github.com/x/y.git"))
        write_lock(self.tmpdir, pkgs)
        lock_path = os.path.join(self.tmpdir, "package-lock.json")
        with open(lock_path) as f:
            raw = json.load(f)
        raw["ivpm_lock_version"] = 999
        with open(lock_path, "w") as f:
            json.dump(raw, f)
        with self.assertRaises(ValueError):
            read_lock(lock_path)

    # ------------------------------------------------------------------
    # src_type set at create time (regression test for sub-package URLs)
    # ------------------------------------------------------------------

    def test_dir_pkg_src_type_set_by_create(self):
        """PackageDir.create must set src_type='dir' so the lock file records the path."""
        from ivpm.pkg_types.pkg_type_rgy import PkgTypeRgy
        class _Si:
            pass
        pkg = PkgTypeRgy.inst().mkPackage("dir", "mypkg", {"url": "file:///some/path"}, _Si())
        self.assertEqual(pkg.src_type, "dir")
        pkgs = self._make_pkgs(pkg)
        write_lock(self.tmpdir, pkgs)
        data = read_lock(os.path.join(self.tmpdir, "package-lock.json"))
        entry = data["packages"]["mypkg"]
        self.assertEqual(entry["src"], "dir")
        self.assertIn("path", entry)
        self.assertEqual(entry["path"], "/some/path")

    def test_git_pkg_src_type_set_by_create(self):
        """PackageGit.create must set src_type='git' so the lock file records the URL."""
        from ivpm.pkg_types.pkg_type_rgy import PkgTypeRgy
        class _Si:
            pass
        pkg = PkgTypeRgy.inst().mkPackage(
            "git", "myrepo",
            {"url": "https://github.com/org/myrepo.git", "branch": "main"},
            _Si()
        )
        self.assertEqual(pkg.src_type, "git")
        pkgs = self._make_pkgs(pkg)
        write_lock(self.tmpdir, pkgs)
        data = read_lock(os.path.join(self.tmpdir, "package-lock.json"))
        entry = data["packages"]["myrepo"]
        self.assertEqual(entry["src"], "git")
        self.assertEqual(entry["url"], "https://github.com/org/myrepo.git")
        self.assertEqual(entry["branch"], "main")


if __name__ == "__main__":
    unittest.main()

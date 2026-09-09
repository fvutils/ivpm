#****************************************************************************
#* test_package_lock.py
#*
#* Tests for the package_lock module (write/read round-trip, change detection,
#* reproducible flag, Python pip version contribution, and lock-file hook
#* delegation).
#****************************************************************************
import dataclasses as dc
import os
import json
import tempfile
import unittest

from ivpm.package_lock import (
    write_lock, read_lock, check_lock_changes, stamp_root_record,
    current_platform, _spec_matches_lock, LOCK_VERSION)
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

    # ------------------------------------------------------------------
    # Lock-file hook delegation
    # ------------------------------------------------------------------

    def test_get_lock_entry_hook_merged(self):
        """get_lock_entry() return value is merged into the base lock entry."""
        from ivpm.package import Package

        @dc.dataclass
        class CustomPkg(Package):
            def get_lock_entry(self):
                return {"custom_field": "custom_value"}

        pkg = CustomPkg("custompkg")
        pkg.src_type = "custom"
        pkg.resolved_by = "root"
        pkgs = self._make_pkgs(pkg)
        write_lock(self.tmpdir, pkgs)

        data = read_lock(os.path.join(self.tmpdir, "package-lock.json"))
        entry = data["packages"]["custompkg"]
        # Base fields present
        self.assertEqual(entry["src"], "custom")
        self.assertEqual(entry["resolved_by"], "root")
        # Custom field merged
        self.assertEqual(entry["custom_field"], "custom_value")

    def test_get_lock_entry_hook_none_falls_through(self):
        """Plain Package (no override) with unknown src_type gets only base fields."""
        from ivpm.package import Package

        pkg = Package("plainpkg")
        pkg.src_type = "unknown_type"
        pkg.resolved_by = "root"
        pkgs = self._make_pkgs(pkg)
        write_lock(self.tmpdir, pkgs)

        data = read_lock(os.path.join(self.tmpdir, "package-lock.json"))
        entry = data["packages"]["plainpkg"]
        self.assertEqual(entry["src"], "unknown_type")
        self.assertEqual(entry["resolved_by"], "root")
        self.assertIn("dep_set", entry)
        self.assertIn("reproducible", entry)

    def test_spec_matches_lock_hook_true(self):
        """spec_matches_lock() returning True suppresses diff reporting."""
        from ivpm.package import Package

        @dc.dataclass
        class AlwaysMatchPkg(Package):
            def spec_matches_lock(self, lock_entry):
                return True

        pkg = AlwaysMatchPkg("matchpkg")
        pkg.src_type = "custom"
        pkg.resolved_by = "root"
        pkgs = self._make_pkgs(pkg)
        write_lock(self.tmpdir, pkgs)

        diffs = check_lock_changes(self.tmpdir, pkgs)
        self.assertEqual(diffs, {})

    def test_spec_matches_lock_hook_false(self):
        """spec_matches_lock() returning False forces a diff to be reported."""
        from ivpm.package import Package

        @dc.dataclass
        class NeverMatchPkg(Package):
            def spec_matches_lock(self, lock_entry):
                return False

        pkg = NeverMatchPkg("nomatchpkg")
        pkg.src_type = "custom"
        pkg.resolved_by = "root"
        pkgs = self._make_pkgs(pkg)
        write_lock(self.tmpdir, pkgs)

        diffs = check_lock_changes(self.tmpdir, pkgs)
        self.assertIn("nomatchpkg", diffs)

    def test_spec_matches_lock_hook_none_falls_through(self):
        """spec_matches_lock() returning None falls through to built-in git logic."""
        from ivpm.package import Package

        @dc.dataclass
        class GitLikePkg(Package):
            url: str = None
            branch: str = None
            tag: str = None
            commit: str = None
            resolved_commit: str = None
            cache: str = None

            def spec_matches_lock(self, lock_entry):
                return None

        pkg = GitLikePkg("gitlike")
        pkg.src_type = "git"
        pkg.resolved_by = "root"
        pkg.url = "https://github.com/org/repo.git"
        pkg.branch = "main"
        pkg.resolved_commit = "abc123"
        pkgs = self._make_pkgs(pkg)
        write_lock(self.tmpdir, pkgs)

        # Same spec -- built-in git comparison should find no diff
        diffs = check_lock_changes(self.tmpdir, pkgs)
        self.assertEqual(diffs, {})

    def test_existing_git_roundtrip_unaffected(self):
        """Plain PackageGit (no custom hooks) round-trips with no diffs."""
        pkg = _make_git_pkg(
            "plainrepo",
            "https://github.com/org/plainrepo.git",
            branch="main",
            commit=None,
            resolved_commit="deadbeef",
        )
        pkgs = self._make_pkgs(pkg)
        write_lock(self.tmpdir, pkgs)

        diffs = check_lock_changes(self.tmpdir, pkgs)
        self.assertEqual(diffs, {})

    # ------------------------------------------------------------------
    # root record + root.config (self-describing bare workspace)
    # ------------------------------------------------------------------

    def test_stamp_root_record_with_config(self):
        write_lock(self.tmpdir, self._make_pkgs())
        rc = {
            "default_package": {"name": "myrepo", "deps-dir": "import",
                                "dep-sets": [{"name": "default", "deps": []}]},
            "handler_overlay": {"example-handler": {"items": ["a"]}},
        }
        stamp_root_record(self.tmpdir, provider="myvcs", src="myvcs://myrepo",
                          resolved_revision="123", root_config=rc)
        root = read_lock(os.path.join(self.tmpdir, "package-lock.json"))["root"]
        self.assertEqual(root["provider"], "myvcs")
        self.assertEqual(root["src"], "myvcs://myrepo")
        self.assertEqual(root["config"]["default_package"]["name"], "myrepo")
        self.assertEqual(root["config"]["handler_overlay"],
                         {"example-handler": {"items": ["a"]}})

    def test_stamp_root_record_without_config(self):
        write_lock(self.tmpdir, self._make_pkgs())
        stamp_root_record(self.tmpdir, provider="git")
        root = read_lock(os.path.join(self.tmpdir, "package-lock.json"))["root"]
        self.assertEqual(root["provider"], "git")
        self.assertNotIn("config", root)

    def test_root_config_carried_forward_across_rewrite(self):
        write_lock(self.tmpdir, self._make_pkgs())
        rc = {"default_package": {"name": "x", "deps-dir": "import",
                                  "dep-sets": [{"name": "default", "deps": []}]}}
        stamp_root_record(self.tmpdir, provider="myvcs", root_config=rc)
        # An ordinary update re-writes the lock; the root block must survive.
        write_lock(self.tmpdir, self._make_pkgs())
        root = read_lock(os.path.join(self.tmpdir, "package-lock.json"))["root"]
        self.assertEqual(root["config"]["default_package"]["name"], "x")


class TestResolvedOn(unittest.TestCase):
    """``resolved_on`` -- the cross-platform guard on http-family entries.

    An http-family entry's ``url`` *is* the artifact, so a lock committed from
    Linux would otherwise hand a macOS teammate a Linux tarball with a
    perfectly valid ETag and no error anywhere.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_pkgs(self, *pkgs):
        pi = PackagesInfo("root")
        for p in pkgs:
            pi[p.name] = p
        return pi

    def _http_pkg(self, name, url, derived=None):
        from ivpm.pkg_types.package_http import PackageHttp
        p = PackageHttp(name)
        p.url = url
        p.src_type = "http"
        p.resolved_etag = "etag-1"
        p.resolved_by = "root"
        p.used_derived_vars = set(derived or ())
        return p

    def _entry(self, pkg):
        write_lock(self.tmpdir, self._make_pkgs(pkg))
        data = read_lock(os.path.join(self.tmpdir, "package-lock.json"))
        return data["packages"][pkg.name]

    def test_written_for_derived_package(self):
        pkg = self._http_pkg("emsdk", "https://ex.com/linux/x.tar.xz",
                             derived={"p"})
        self.assertEqual(current_platform(), self._entry(pkg)["resolved_on"])

    def test_records_the_resolved_platform_not_the_running_machine(self):
        # Cross-resolution (-D ivpm_os=macos on a Linux box) must tag the entry
        # with the platform it resolved *for*. Tagging it with the machine that
        # ran the resolve is worse than not tagging it: on the next bare run
        # here the entry would look native, and the macOS URL would never be
        # re-resolved.
        pkg = self._http_pkg("emsdk", "https://ex.com/mac/x-arm64.tar.xz",
                             derived={"p"})
        pkg.resolved_platform = "macos-arm64"
        entry = self._entry(pkg)
        self.assertEqual("macos-arm64", entry["resolved_on"])
        self.assertNotEqual(current_platform(), entry["resolved_on"])
        # ...and it still matches, because the *package* is a macOS package.
        self.assertTrue(_spec_matches_lock(pkg, entry))

    def test_cross_resolved_entry_rejected_on_a_plain_run(self):
        # Same lock, read back by a run that did not pass -D: the package now
        # resolves for this machine, so the macOS entry must not match.
        pkg = self._http_pkg("emsdk", "https://ex.com/mac/x-arm64.tar.xz",
                             derived={"p"})
        pkg.resolved_platform = "macos-arm64"
        entry = self._entry(pkg)

        native = self._http_pkg("emsdk", "https://ex.com/mac/x-arm64.tar.xz",
                                derived={"p"})
        native.resolved_platform = current_platform()
        self.assertFalse(_spec_matches_lock(native, entry))

    def test_absent_for_non_derived_package(self):
        # Every manifest written before this feature existed lands here; the
        # entry shape must be exactly what it always was.
        pkg = self._http_pkg("plain", "https://ex.com/x.tar.gz")
        self.assertNotIn("resolved_on", self._entry(pkg))

    def test_gh_rls_entry_unchanged(self):
        # gh-rls records the repo URL and re-runs asset selection per
        # platform, so it needs no tag even when it is platform-dependent.
        pkg = _make_gh_rls_pkg("mytool", "https://github.com/org/mytool")
        pkg.used_derived_vars = {"p"}
        write_lock(self.tmpdir, self._make_pkgs(pkg))
        data = read_lock(os.path.join(self.tmpdir, "package-lock.json"))
        self.assertNotIn("resolved_on", data["packages"]["mytool"])

    def test_spec_match_rejects_foreign_platform(self):
        # The URL matches, but it was resolved elsewhere: report a mismatch so
        # the package goes back through the manifest.
        pkg = self._http_pkg("emsdk", "https://ex.com/linux/x.tar.xz",
                             derived={"p"})
        entry = dict(self._entry(pkg))
        entry["resolved_on"] = "someother-platform"
        self.assertFalse(_spec_matches_lock(pkg, entry))

    def test_spec_match_accepts_own_platform(self):
        pkg = self._http_pkg("emsdk", "https://ex.com/linux/x.tar.xz",
                             derived={"p"})
        self.assertTrue(_spec_matches_lock(pkg, self._entry(pkg)))

    def test_entry_without_resolved_on_honoured(self):
        # Back-compat: an old lock file has no tag at all.
        pkg = self._http_pkg("plain", "https://ex.com/x.tar.gz")
        entry = self._entry(pkg)
        self.assertNotIn("resolved_on", entry)
        self.assertTrue(_spec_matches_lock(pkg, entry))

    def test_reproduction_of_foreign_entry_is_refused(self):
        from ivpm.package_lock import IvpmLockReader
        from ivpm.yamlsrc import SrcLoaderError
        pkg = self._http_pkg("emsdk", "https://ex.com/linux/x.tar.xz",
                             derived={"p"})
        write_lock(self.tmpdir, self._make_pkgs(pkg))
        lock_path = os.path.join(self.tmpdir, "package-lock.json")
        data = read_lock(lock_path)
        data["packages"]["emsdk"]["resolved_on"] = "someother-platform"
        with open(lock_path, "w") as fp:
            json.dump(data, fp)

        # Reproduction has no manifest to re-resolve from, so installing the
        # foreign artifact anyway is exactly the silent failure to avoid.
        with self.assertRaises(SrcLoaderError) as ctx:
            IvpmLockReader(lock_path).build_packages_info()
        self.assertIn("someother-platform", str(ctx.exception))

    def test_reproduction_of_own_entry_works(self):
        from ivpm.package_lock import IvpmLockReader
        pkg = self._http_pkg("emsdk", "https://ex.com/linux/x.tar.xz",
                             derived={"p"})
        write_lock(self.tmpdir, self._make_pkgs(pkg))
        lock_path = os.path.join(self.tmpdir, "package-lock.json")
        pkgs = IvpmLockReader(lock_path).build_packages_info()
        self.assertEqual("https://ex.com/linux/x.tar.xz", pkgs["emsdk"].url)


if __name__ == "__main__":
    unittest.main()

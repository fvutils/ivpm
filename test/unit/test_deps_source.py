"""Unit tests for ivpm.deps_source."""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOTDIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRCDIR = os.path.join(ROOTDIR, 'src')
sys.path.insert(0, SRCDIR)

from ivpm.deps_source import DepsSource, DepsSourceEntry


class _FakePkg:
    """Minimal stand-in for a Package object — only the attributes the
    deps-source matcher consults."""
    def __init__(self, name, src_type, **kw):
        self.name = name
        self.src_type = src_type
        for k, v in kw.items():
            setattr(self, k, v)


def _make_parent(tmp, packages_lock=None, pkg_dirs=None):
    """Create a parent deps-dir with optional package-lock.json and pkg dirs.

    *packages_lock* is the dict that goes under ``packages:`` in the lock.
    *pkg_dirs* is an iterable of package directory names to materialize.
    """
    os.makedirs(tmp, exist_ok=True)
    if packages_lock is not None:
        lock = {
            "ivpm_lock_version": 1,
            "generated": "2026-01-01T00:00:00Z",
            "packages": packages_lock,
        }
        with open(os.path.join(tmp, "package-lock.json"), "w") as f:
            json.dump(lock, f)
    for name in (pkg_dirs or []):
        os.makedirs(os.path.join(tmp, name), exist_ok=True)
    return tmp


class TestDepsSourceLookup(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ivpm-deps-source-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_from_args_none_returns_none(self):
        self.assertIsNone(DepsSource.from_args(None))
        self.assertIsNone(DepsSource.from_args([]))

    def test_lookup_returns_none_with_no_entries(self):
        ds = DepsSource([])
        pkg = _FakePkg("foo", "git", resolved_commit="abc")
        self.assertIsNone(ds.lookup(pkg))

    def test_lookup_skips_missing_directory(self):
        parent = os.path.join(self.tmp, "parent")
        _make_parent(parent,
                     packages_lock={"foo": {"src": "git", "commit_resolved": "abc"}})
        # note: no foo/ dir created
        ds = DepsSource.from_args([parent])
        pkg = _FakePkg("foo", "git", resolved_commit="abc")
        self.assertIsNone(ds.lookup(pkg))

    def test_lookup_silent_when_no_lockfile(self):
        parent = os.path.join(self.tmp, "parent")
        _make_parent(parent, pkg_dirs=["foo"])  # no lock file
        ds = DepsSource.from_args([parent])
        pkg = _FakePkg("foo", "git", resolved_commit="abc")
        self.assertIsNone(ds.lookup(pkg))

    def test_lookup_matches_git_by_commit(self):
        parent = os.path.join(self.tmp, "parent")
        _make_parent(parent,
                     packages_lock={"foo": {"src": "git", "commit_resolved": "deadbeef"}},
                     pkg_dirs=["foo"])
        ds = DepsSource.from_args([parent])
        pkg = _FakePkg("foo", "git", resolved_commit="deadbeef")
        hit = ds.lookup(pkg)
        self.assertEqual(hit, os.path.realpath(os.path.join(parent, "foo")))

    def test_lookup_rejects_git_on_commit_mismatch(self):
        parent = os.path.join(self.tmp, "parent")
        _make_parent(parent,
                     packages_lock={"foo": {"src": "git", "commit_resolved": "AAA"}},
                     pkg_dirs=["foo"])
        ds = DepsSource.from_args([parent])
        pkg = _FakePkg("foo", "git", resolved_commit="BBB")
        self.assertIsNone(ds.lookup(pkg))

    def test_lookup_matches_pypi_by_version(self):
        parent = os.path.join(self.tmp, "parent")
        _make_parent(parent,
                     packages_lock={"requests": {"src": "pypi",
                                                 "version_resolved": "2.31.0"}},
                     pkg_dirs=["requests"])
        ds = DepsSource.from_args([parent])
        pkg = _FakePkg("requests", "pypi", resolved_version="2.31.0")
        self.assertIsNotNone(ds.lookup(pkg))

    def test_lookup_matches_http_by_etag(self):
        parent = os.path.join(self.tmp, "parent")
        _make_parent(parent,
                     packages_lock={"thing": {"src": "http", "etag": "abc123"}},
                     pkg_dirs=["thing"])
        ds = DepsSource.from_args([parent])
        pkg = _FakePkg("thing", "http", resolved_etag="abc123")
        self.assertIsNotNone(ds.lookup(pkg))

    def test_lookup_matches_http_by_last_modified_fallback(self):
        parent = os.path.join(self.tmp, "parent")
        _make_parent(parent,
                     packages_lock={"thing": {"src": "http",
                                              "last_modified": "Wed, 01 Jan 2026 00:00:00 GMT"}},
                     pkg_dirs=["thing"])
        ds = DepsSource.from_args([parent])
        pkg = _FakePkg("thing", "http",
                       resolved_last_modified="Wed, 01 Jan 2026 00:00:00 GMT")
        self.assertIsNotNone(ds.lookup(pkg))

    def test_lookup_skips_dir_pkg(self):
        parent = os.path.join(self.tmp, "parent")
        _make_parent(parent,
                     packages_lock={"local": {"src": "dir", "path": "../local"}},
                     pkg_dirs=["local"])
        ds = DepsSource.from_args([parent])
        pkg = _FakePkg("local", "dir")
        self.assertIsNone(ds.lookup(pkg))

    def test_lookup_trust_mode_bypasses_lock(self):
        parent = os.path.join(self.tmp, "parent")
        _make_parent(parent, pkg_dirs=["foo"])  # no lock file
        ds = DepsSource.from_args([parent], trust=True)
        pkg = _FakePkg("foo", "git", resolved_commit="whatever")
        self.assertIsNotNone(ds.lookup(pkg))

    def test_lookup_first_match_wins(self):
        p1 = os.path.join(self.tmp, "p1")
        p2 = os.path.join(self.tmp, "p2")
        _make_parent(p1,
                     packages_lock={"foo": {"src": "git", "commit_resolved": "AAA"}},
                     pkg_dirs=["foo"])
        _make_parent(p2,
                     packages_lock={"foo": {"src": "git", "commit_resolved": "BBB"}},
                     pkg_dirs=["foo"])
        ds = DepsSource.from_args([p1, p2])
        pkg = _FakePkg("foo", "git", resolved_commit="BBB")
        # p1 doesn't match (AAA vs BBB) → falls through to p2
        hit = ds.lookup(pkg)
        self.assertEqual(hit, os.path.realpath(os.path.join(p2, "foo")))

    def test_lookup_rejects_on_src_type_mismatch(self):
        parent = os.path.join(self.tmp, "parent")
        _make_parent(parent,
                     packages_lock={"foo": {"src": "git", "commit_resolved": "AAA"}},
                     pkg_dirs=["foo"])
        ds = DepsSource.from_args([parent])
        # pkg is a pypi package — name collision with the git entry, but
        # src_type differs so it must be a miss
        pkg = _FakePkg("foo", "pypi", resolved_version="1.0")
        self.assertIsNone(ds.lookup(pkg))

    def test_lookup_returns_realpath_through_symlink_parent(self):
        real = os.path.join(self.tmp, "real")
        link = os.path.join(self.tmp, "link")
        _make_parent(real,
                     packages_lock={"foo": {"src": "git", "commit_resolved": "X"}},
                     pkg_dirs=["foo"])
        os.symlink(real, link)
        ds = DepsSource.from_args([link])
        pkg = _FakePkg("foo", "git", resolved_commit="X")
        hit = ds.lookup(pkg)
        self.assertEqual(hit, os.path.realpath(os.path.join(real, "foo")))

    def test_lookup_with_bad_lockfile_treats_as_no_lock(self):
        parent = os.path.join(self.tmp, "parent")
        os.makedirs(parent)
        with open(os.path.join(parent, "package-lock.json"), "w") as f:
            f.write("{not valid json")
        os.makedirs(os.path.join(parent, "foo"))
        ds = DepsSource.from_args([parent])
        pkg = _FakePkg("foo", "git", resolved_commit="X")
        # bad lock → strategy A can't match; without trust we get a miss
        self.assertIsNone(ds.lookup(pkg))


if __name__ == "__main__":
    unittest.main()

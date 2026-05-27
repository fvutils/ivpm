"""End-to-end tests for the deps-source resolution pipeline.

These tests exercise the integration points (ProjectUpdateInfo.try_deps_source,
package_lock emission, sync skip, status provenance) without performing real
network fetches.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOTDIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRCDIR = os.path.join(ROOTDIR, 'src')
sys.path.insert(0, SRCDIR)

from ivpm.deps_source import DepsSource
from ivpm.project_ops_info import ProjectUpdateInfo
from ivpm.package_lock import _entry_from_pkg


class _FakePkg:
    """Minimal stand-in that quacks like a Package."""
    def __init__(self, name, src_type, **kw):
        self.name = name
        self.src_type = src_type
        self.dep_set = "default"
        self.resolved_by = None
        self.path = None
        self.from_deps_source = None
        for k, v in kw.items():
            setattr(self, k, v)

    def get_lock_entry(self):
        return None

    def spec_matches_lock(self, lock_entry):
        return None


def _golden(tmp, pkg_specs):
    """Build a parent deps-dir.  pkg_specs is dict[name -> lock_entry].
    The package directory is materialized for each entry."""
    os.makedirs(tmp, exist_ok=True)
    lock = {
        "ivpm_lock_version": 1,
        "generated": "2026-01-01T00:00:00Z",
        "packages": pkg_specs,
    }
    with open(os.path.join(tmp, "package-lock.json"), "w") as f:
        json.dump(lock, f)
    for name in pkg_specs:
        d = os.path.join(tmp, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "marker.txt"), "w") as f:
            f.write("from-parent\n")
    return tmp


class TestE2EDepsSource(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ivpm-ds-e2e-")
        self.deps_dir = os.path.join(self.tmp, "deps")
        os.makedirs(self.deps_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _info(self, parents, mode="link", trust=False):
        info = ProjectUpdateInfo(args=object(), deps_dir=self.deps_dir)
        info.deps_source = DepsSource.from_args(parents, trust=trust)
        info.deps_source_mode = mode
        return info

    # ── try_deps_source: hit & symlink ────────────────────────────────────

    def test_hit_creates_symlink(self):
        parent = os.path.join(self.tmp, "parent")
        _golden(parent, {"foo": {"src": "git", "commit_resolved": "abc"}})
        info = self._info([parent])
        pkg = _FakePkg("foo", "git", resolved_commit="abc")

        self.assertTrue(info.try_deps_source(pkg))
        target = os.path.join(self.deps_dir, "foo")
        self.assertTrue(os.path.islink(target))
        self.assertEqual(os.readlink(target),
                         os.path.realpath(os.path.join(parent, "foo")))
        self.assertEqual(pkg.from_deps_source,
                         os.path.realpath(os.path.join(parent, "foo")))
        self.assertEqual(info.deps_source_hits, 1)
        self.assertEqual(info.deps_source_misses, 0)

    def test_miss_makes_no_symlink_and_counts_miss(self):
        parent = os.path.join(self.tmp, "parent")
        _golden(parent, {"foo": {"src": "git", "commit_resolved": "AAA"}})
        info = self._info([parent])
        pkg = _FakePkg("foo", "git", resolved_commit="BBB")

        self.assertFalse(info.try_deps_source(pkg))
        self.assertFalse(os.path.exists(os.path.join(self.deps_dir, "foo")))
        self.assertEqual(info.deps_source_hits, 0)
        self.assertEqual(info.deps_source_misses, 1)
        self.assertIsNone(pkg.from_deps_source)

    def test_no_deps_source_configured_is_a_noop(self):
        info = ProjectUpdateInfo(args=object(), deps_dir=self.deps_dir)
        pkg = _FakePkg("foo", "git", resolved_commit="abc")
        self.assertFalse(info.try_deps_source(pkg))
        self.assertEqual(info.deps_source_hits, 0)
        self.assertEqual(info.deps_source_misses, 0)

    def test_copy_mode_creates_real_directory(self):
        parent = os.path.join(self.tmp, "parent")
        _golden(parent, {"foo": {"src": "git", "commit_resolved": "abc"}})
        info = self._info([parent], mode="copy")
        pkg = _FakePkg("foo", "git", resolved_commit="abc")

        self.assertTrue(info.try_deps_source(pkg))
        target = os.path.join(self.deps_dir, "foo")
        self.assertFalse(os.path.islink(target))
        self.assertTrue(os.path.isdir(target))
        # editing the copy doesn't touch the parent
        with open(os.path.join(target, "new.txt"), "w") as f:
            f.write("local")
        self.assertFalse(os.path.exists(os.path.join(parent, "foo", "new.txt")))

    def test_trust_mode_hits_without_lock(self):
        parent = os.path.join(self.tmp, "parent")
        os.makedirs(os.path.join(parent, "foo"))   # no lock at all
        info = self._info([parent], trust=True)
        pkg = _FakePkg("foo", "git", resolved_commit="whatever")
        self.assertTrue(info.try_deps_source(pkg))

    def test_collision_with_existing_deps_raises(self):
        parent = os.path.join(self.tmp, "parent")
        _golden(parent, {"foo": {"src": "git", "commit_resolved": "abc"}})
        # pre-existing deps/foo
        os.makedirs(os.path.join(self.deps_dir, "foo"))
        info = self._info([parent])
        pkg = _FakePkg("foo", "git", resolved_commit="abc")
        with self.assertRaises(RuntimeError):
            info.try_deps_source(pkg)

    def test_first_match_wins_across_sources(self):
        p1 = os.path.join(self.tmp, "p1")
        p2 = os.path.join(self.tmp, "p2")
        _golden(p1, {"foo": {"src": "git", "commit_resolved": "AAA"}})
        _golden(p2, {"foo": {"src": "git", "commit_resolved": "BBB"}})
        info = self._info([p1, p2])
        pkg = _FakePkg("foo", "git", resolved_commit="BBB")
        self.assertTrue(info.try_deps_source(pkg))
        # links into p2 (since p1's commit doesn't match)
        self.assertEqual(os.readlink(os.path.join(self.deps_dir, "foo")),
                         os.path.realpath(os.path.join(p2, "foo")))

    def test_dir_pkg_never_hits(self):
        parent = os.path.join(self.tmp, "parent")
        _golden(parent, {"foo": {"src": "dir", "path": "../foo"}})
        info = self._info([parent])
        pkg = _FakePkg("foo", "dir")
        self.assertFalse(info.try_deps_source(pkg))

    # ── lock-file emission ────────────────────────────────────────────────

    def test_lock_entry_records_from_deps_source(self):
        pkg = _FakePkg("foo", "git",
                       url="http://example/foo.git",
                       branch="main", tag=None, commit=None,
                       resolved_commit="abc", cache=None,
                       from_deps_source="/tmp/parent/deps/foo")
        entry = _entry_from_pkg(pkg)
        self.assertEqual(entry["from_deps_source"], "/tmp/parent/deps/foo")

    def test_lock_entry_omits_when_not_deps_sourced(self):
        pkg = _FakePkg("foo", "git",
                       url="http://example/foo.git",
                       branch="main", tag=None, commit=None,
                       resolved_commit="abc", cache=None)
        # from_deps_source defaults to None
        entry = _entry_from_pkg(pkg)
        self.assertNotIn("from_deps_source", entry)


class TestE2EStatusAndSyncProvenance(unittest.TestCase):
    """Lighter-weight checks that the provenance plumbing reaches the
    sync/status data objects."""

    def test_pkg_vcs_status_has_from_deps_source_field(self):
        from ivpm.pkg_status import PkgVcsStatus
        s = PkgVcsStatus(name="x", src_type="git", path="/p", vcs="git",
                         from_deps_source="/parent/deps")
        self.assertEqual(s.from_deps_source, "/parent/deps")

    def test_pkg_sync_result_skipped_carries_next_steps(self):
        from ivpm.pkg_sync import PkgSyncResult, SyncOutcome
        r = PkgSyncResult(name="x", src_type="git", path="/p",
                          outcome=SyncOutcome.SKIPPED,
                          skipped_reason="materialized from deps-source /parent",
                          next_steps=["Re-run ivpm update without --deps-source"])
        self.assertEqual(r.outcome, SyncOutcome.SKIPPED)
        self.assertEqual(len(r.next_steps), 1)


if __name__ == "__main__":
    unittest.main()

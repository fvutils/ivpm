"""
Tests for switching the installed dep-set, and for reporting the packages a
shrinking lock leaves behind.

A workspace records the dep-set it was installed with in
``<deps-dir>/ivpm.json`` and in ``package-lock.json`` (the lock is the durable
copy: ivpm.json is regenerated state and is not written when an update fails
partway).  Asking for a different one is refused by default --
it re-shapes the workspace -- but ``--force`` is the escape hatch, the same
role it plays for the refresh safety errors.  Whatever the previous dep-set
installed that the new one does not name stays on disk, so ``update`` says so
rather than letting ``status`` quietly under-report.
"""
import json
import os
import unittest

from .test_base import TestBase
from ivpm.project_ops import ProjectOps
from ivpm.yamlsrc import SrcLoaderError


_MANIFEST = """
package:
  name: t

  dep-sets:
  - name: use
    deps: []

  - name: dev
    deps: []
"""


class _StubUpdateInfo(object):
    def __init__(self, lock_data):
        self.lock_data = lock_data


class _StubUpdater(object):
    """Just enough of PackageUpdater for _report_orphans."""
    def __init__(self, lock_data, all_pkgs):
        self.update_info = _StubUpdateInfo(lock_data)
        self.all_pkgs = all_pkgs


class TestDepSetSwitch(TestBase):

    def _mkws(self, persisted_dep_set="use"):
        """A workspace whose ivpm.json records *persisted_dep_set*."""
        deps_dir = os.path.join(self.testdir, "packages")
        os.makedirs(deps_dir, exist_ok=True)
        with open(os.path.join(self.testdir, "ivpm.yaml"), "w") as fp:
            fp.write(_MANIFEST)
        with open(os.path.join(deps_dir, "ivpm.json"), "w") as fp:
            json.dump({"dep-set": persisted_dep_set}, fp)
        return deps_dir

    def test_persisted_dep_set_adopted(self):
        """With no explicit request, the recorded dep-set is reused."""
        self._mkws("dev")
        _, _, dep_sets, _ = ProjectOps(self.testdir)._init()
        self.assertEqual(["dev"], dep_sets)

    def test_switch_refused_without_force(self):
        """A different dep-set is refused, and the message names both."""
        self._mkws("use")
        with self.assertRaises(SrcLoaderError) as ctx:
            ProjectOps(self.testdir)._init("dev")
        msg = str(ctx.exception)
        self.assertIn("requested dev", msg)
        self.assertIn("use was installed", msg)
        self.assertIn("--force", msg)

    def test_switch_allowed_with_force(self):
        """--force lets the requested dep-set win over the recorded one."""
        self._mkws("use")
        _, _, dep_sets, _ = ProjectOps(self.testdir)._init("dev", force=True)
        self.assertEqual(["dev"], dep_sets)

    def test_same_dep_set_needs_no_force(self):
        """Re-requesting the recorded dep-set is not a switch."""
        self._mkws("dev")
        _, _, dep_sets, _ = ProjectOps(self.testdir)._init("dev")
        self.assertEqual(["dev"], dep_sets)

    def test_reuse_is_reported(self):
        """Adopting the recorded dep-set says so, and how to change it."""
        self._mkws("dev")
        notes = _capture_notes(lambda: ProjectOps(self.testdir)._init())
        self.assertIn("dev", notes)
        self.assertIn("--force", notes)


class TestDepSetFromLock(TestBase):
    """The lock is the fallback when ivpm.json is missing or has no dep-set.

    Without it a workspace whose ivpm.json was lost -- or never written,
    because the update that installed the packages failed after writing the
    lock -- silently reverts to the manifest's default dep-set on the next
    bare `ivpm update`, wiping the lock of everything the selected set had
    installed.
    """

    def _mkws(self, lock_body, ivpm_json=None):
        deps_dir = os.path.join(self.testdir, "packages")
        os.makedirs(deps_dir, exist_ok=True)
        with open(os.path.join(self.testdir, "ivpm.yaml"), "w") as fp:
            fp.write(_MANIFEST)
        with open(os.path.join(deps_dir, "package-lock.json"), "w") as fp:
            json.dump(lock_body, fp)
        if ivpm_json is not None:
            with open(os.path.join(deps_dir, "ivpm.json"), "w") as fp:
                json.dump(ivpm_json, fp)
        return deps_dir

    def test_dep_sets_recovered_from_lock(self):
        self._mkws({"ivpm_lock_version": 2, "packages": {},
                    "dep_sets": ["dev"]})
        _, _, dep_sets, _ = ProjectOps(self.testdir)._init()
        self.assertEqual(["dev"], dep_sets)

    def test_lock_dep_set_guards_switch(self):
        """A lock-recorded set is as binding as an ivpm.json-recorded one."""
        self._mkws({"ivpm_lock_version": 2, "packages": {},
                    "dep_sets": ["dev"]})
        with self.assertRaises(SrcLoaderError) as ctx:
            ProjectOps(self.testdir)._init("use")
        self.assertIn("dev was installed", str(ctx.exception))

    def test_source_manifest_dep_set_recovered(self):
        """--from workspaces record the selection under source_manifest."""
        self._mkws({"ivpm_lock_version": 2, "packages": {},
                    "source_manifest": {"from": "x", "dep_set": "dev"}})
        _, _, dep_sets, _ = ProjectOps(self.testdir)._init()
        self.assertEqual(["dev"], dep_sets)

    def test_ivpm_json_wins_over_lock(self):
        self._mkws({"ivpm_lock_version": 2, "packages": {},
                    "dep_sets": ["dev"]},
                   ivpm_json={"dep-set": "use"})
        _, _, dep_sets, _ = ProjectOps(self.testdir)._init()
        self.assertEqual(["use"], dep_sets)

    def test_no_record_uses_default(self):
        """A fresh workspace still falls through to the manifest default."""
        self._mkws({"ivpm_lock_version": 2, "packages": {}})
        _, _, dep_sets, _ = ProjectOps(self.testdir)._init()
        self.assertIsNone(dep_sets)


def _capture_notes(fn):
    notes = []
    import ivpm.project_ops as po
    orig = po.note
    po.note = lambda m: notes.append(m)
    try:
        fn()
    finally:
        po.note = orig
    return "\n".join(notes)


class TestOrphanReport(TestBase):

    def _report(self, prev_keys, cur_keys, on_disk):
        deps_dir = os.path.join(self.testdir, "packages")
        for name in on_disk:
            os.makedirs(os.path.join(deps_dir, name), exist_ok=True)
        lock_data = {"packages": {k: {"src": "git"} for k in prev_keys}}
        updater = _StubUpdater(lock_data, {k: object() for k in cur_keys})

        notes = []
        import ivpm.project_ops as po
        orig = po.note
        po.note = lambda m: notes.append(m)
        try:
            ProjectOps(self.testdir)._report_orphans(deps_dir, updater)
        finally:
            po.note = orig
        return "\n".join(notes)

    def test_dropped_package_still_on_disk_is_reported(self):
        out = self._report(["foo", "bar"], ["bar"], ["foo", "bar"])
        self.assertIn("1 package(s) remain", out)
        self.assertIn("foo", out)

    def test_dropped_package_not_on_disk_is_silent(self):
        """A lock entry that was never materialized is not an orphan."""
        out = self._report(["foo", "bar"], ["bar"], ["bar"])
        self.assertEqual("", out)

    def test_empty_dep_set_reports_every_package(self):
        """The case that silently zeroed the lock: dep-set resolves to none."""
        out = self._report(["foo", "bar"], [], ["foo", "bar"])
        self.assertIn("2 package(s) remain", out)
        self.assertIn("foo", out)
        self.assertIn("bar", out)

    def test_no_previous_lock_is_silent(self):
        out = self._report([], ["foo"], ["foo"])
        self.assertEqual("", out)


if __name__ == "__main__":
    unittest.main()

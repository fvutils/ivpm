"""
Orchestration tests for `ivpm destroy`: ProjectOps.destroy_plan/destroy_apply.

Builds a minimal workspace by hand (ivpm.yaml + a valid package-lock.json +
local git repos as deps) so no network is needed.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "src"))

from .test_base import TestBase

from ivpm.project_ops import ProjectOps
from ivpm.pkg_remove import RemoveOutcome, SafetyLevel
from ivpm.msg import SrcLoaderError


def _git(repo, *args):
    return subprocess.run(["git"] + list(args), cwd=repo,
                          capture_output=True, text=True, check=True)


def _mk_git_dep(path, detached=True):
    """A git repo with one commit. Detached HEAD -> clean -> SAFE gate."""
    os.makedirs(path, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t.com")
    _git(path, "config", "user.name", "T")
    _git(path, "config", "commit.gpgsign", "false")
    with open(os.path.join(path, "f.txt"), "w") as fp:
        fp.write("1")
    _git(path, "add", "f.txt")
    _git(path, "commit", "-q", "-m", "init")
    if detached:
        head = _git(path, "rev-parse", "HEAD").stdout.strip()
        _git(path, "checkout", "-q", head)


class _Args:
    def __init__(self, **kw):
        self.deps_only = kw.get("deps_only", False)
        self.force = kw.get("force", False)
        self.dry_run = kw.get("dry_run", False)
        self.keep_venv = kw.get("keep_venv", False)


class TestDestroyOps(TestBase):

    def _mk_workspace(self, names=("liba", "libb"), with_venv=True,
                      detached=True):
        ws = os.path.join(self.testdir, "ws")
        deps = os.path.join(ws, "packages")
        os.makedirs(deps)
        with open(os.path.join(ws, "ivpm.yaml"), "w") as fp:
            fp.write("package:\n  name: root\n")
        entries = {}
        for n in names:
            _mk_git_dep(os.path.join(deps, n), detached=detached)
            entries[n] = {"src": "git", "url": "http://example/%s.git" % n}
        lock = {"ivpm_lock_version": 1, "packages": entries}
        with open(os.path.join(deps, "package-lock.json"), "w") as fp:
            json.dump(lock, fp, indent=2)
        with open(os.path.join(deps, "ivpm.json"), "w") as fp:
            json.dump({"handlers": {}}, fp)
        if with_venv:
            os.makedirs(os.path.join(deps, "python", "bin"))
            with open(os.path.join(deps, "python", "bin", "python"), "w") as fp:
                fp.write("#!/bin/sh\n")
        return ws, deps

    # ---- happy paths ------------------------------------------------------

    def test_deps_only_keeps_root(self):
        ws, deps = self._mk_workspace()
        ProjectOps(ws).destroy_apply(args=_Args(deps_only=True))
        # deps gone
        self.assertFalse(os.path.exists(os.path.join(deps, "liba")))
        self.assertFalse(os.path.exists(os.path.join(deps, "libb")))
        # venv gone
        self.assertFalse(os.path.exists(os.path.join(deps, "python")))
        # lock/state gone
        self.assertFalse(os.path.exists(os.path.join(deps, "package-lock.json")))
        self.assertFalse(os.path.exists(os.path.join(deps, "ivpm.json")))
        # root + ivpm.yaml intact
        self.assertTrue(os.path.isfile(os.path.join(ws, "ivpm.yaml")))

    def test_full_removes_everything(self):
        ws, deps = self._mk_workspace()
        ProjectOps(ws).destroy_apply(args=_Args())
        self.assertFalse(os.path.exists(ws))

    def test_deps_only_prunes_empty_deps_dir(self):
        ws, deps = self._mk_workspace()
        ProjectOps(ws).destroy_apply(args=_Args(deps_only=True))
        self.assertFalse(os.path.exists(deps))   # emptied -> pruned

    # ---- gate -------------------------------------------------------------

    def test_gate_blocks_on_unpushed_dirty(self):
        ws, deps = self._mk_workspace()
        # make liba dirty (modified tracked file)
        with open(os.path.join(deps, "liba", "f.txt"), "w") as fp:
            fp.write("changed")
        report = ProjectOps(ws).destroy_plan(args=_Args())
        self.assertTrue(report.blocked)
        self.assertEqual(report.gate["liba"].level, SafetyLevel.BLOCKED)
        self.assertEqual(report.gate["libb"].level, SafetyLevel.SAFE)
        # plan never mutates
        self.assertTrue(os.path.isdir(os.path.join(deps, "liba")))

    def test_apply_refuses_when_blocked(self):
        ws, deps = self._mk_workspace()
        with open(os.path.join(deps, "liba", "new.txt"), "w") as fp:
            fp.write("untracked work")
        report = ProjectOps(ws).destroy_apply(args=_Args())
        self.assertTrue(report.blocked)
        # nothing removed
        self.assertTrue(os.path.isdir(os.path.join(deps, "liba")))
        self.assertTrue(os.path.isfile(os.path.join(deps, "package-lock.json")))

    def test_force_overrides_gate(self):
        ws, deps = self._mk_workspace()
        with open(os.path.join(deps, "liba", "f.txt"), "w") as fp:
            fp.write("changed")
        report = ProjectOps(ws).destroy_apply(args=_Args(force=True))
        self.assertFalse(report.blocked)
        self.assertTrue(report.forced)
        self.assertFalse(os.path.exists(ws))

    def test_full_mode_gates_root(self):
        # A root on a local-only branch (no upstream) blocks a full destroy.
        ws, deps = self._mk_workspace()
        _git(ws, "init", "-q", "-b", "main")
        _git(ws, "config", "user.email", "t@t.com")
        _git(ws, "config", "user.name", "T")
        _git(ws, "config", "commit.gpgsign", "false")
        _git(ws, "add", "ivpm.yaml")
        _git(ws, "commit", "-q", "-m", "root")
        report = ProjectOps(ws).destroy_plan(args=_Args())
        self.assertTrue(report.blocked)
        self.assertEqual(report.gate["<root>"].level, SafetyLevel.BLOCKED)

    # ---- dry-run ----------------------------------------------------------

    def test_dry_run_changes_nothing(self):
        ws, deps = self._mk_workspace()
        report = ProjectOps(ws).destroy_plan(args=_Args(dry_run=True))
        self.assertTrue(report.dry_run)
        self.assertEqual(len(report.results), 2)
        for r in report.results:
            self.assertEqual(r.outcome, RemoveOutcome.SKIPPED)
        self.assertTrue(os.path.isdir(os.path.join(deps, "liba")))
        self.assertTrue(os.path.exists(ws))

    # ---- refusals ---------------------------------------------------------

    def test_refuse_non_workspace(self):
        d = os.path.join(self.testdir, "random")
        os.makedirs(d)
        with self.assertRaises(SrcLoaderError):
            ProjectOps(d).destroy_plan(args=_Args())

    def test_refuse_full_destroy_of_cwd(self):
        ws, deps = self._mk_workspace()
        cwd = os.getcwd()
        try:
            os.chdir(ws)
            with self.assertRaises(SrcLoaderError):
                ProjectOps(ws).destroy_plan(args=_Args())
        finally:
            os.chdir(cwd)

    def test_deps_only_allowed_in_cwd(self):
        ws, deps = self._mk_workspace()
        cwd = os.getcwd()
        try:
            os.chdir(ws)
            report = ProjectOps(ws).destroy_plan(args=_Args(deps_only=True))
            self.assertFalse(report.blocked)
        finally:
            os.chdir(cwd)

    # ---- best-effort continue + cache survival ----------------------------

    def test_best_effort_continue_on_provider_error(self):
        ws, deps = self._mk_workspace()
        # Force liba's remove() to raise; libb must still be removed and the
        # error must be recorded (not aborted).
        from unittest import mock
        from ivpm.pkg_types.package_git import PackageGit
        real_remove = PackageGit.remove

        def flaky_remove(self, remove_info):
            if self.name == "liba":
                raise RuntimeError("boom")
            return real_remove(self, remove_info)

        with mock.patch.object(PackageGit, "remove", flaky_remove):
            report = ProjectOps(ws).destroy_apply(args=_Args(deps_only=True))

        self.assertFalse(report.blocked)
        by_name = {r.name: r for r in report.results}
        self.assertEqual(by_name["liba"].outcome, RemoveOutcome.MANUAL)
        self.assertIn("boom", by_name["liba"].error)
        self.assertEqual(by_name["libb"].outcome, RemoveOutcome.REMOVED)
        self.assertFalse(os.path.exists(os.path.join(deps, "libb")))

    def test_deps_only_idempotent(self):
        # A second deps-only destroy of an already-emptied workspace is a clean
        # no-op (lock gone -> empty package list -> nothing to remove).
        ws, deps = self._mk_workspace()
        ProjectOps(ws).destroy_apply(args=_Args(deps_only=True))
        report = ProjectOps(ws).destroy_apply(args=_Args(deps_only=True))
        self.assertFalse(report.blocked)
        self.assertEqual(report.results, [])
        self.assertTrue(os.path.isfile(os.path.join(ws, "ivpm.yaml")))

    def test_cache_symlink_dep_survives(self):
        ws, deps = self._mk_workspace(names=("liba",))
        # Add a cache-backed symlink dep pointing into a shared cache tree.
        cache = os.path.join(self.testdir, "cache", "entry")
        os.makedirs(cache)
        with open(os.path.join(cache, "keep.txt"), "w") as fp:
            fp.write("shared")
        os.symlink(cache, os.path.join(deps, "cachedep"))
        # register it in the lock
        lock_path = os.path.join(deps, "package-lock.json")
        with open(lock_path) as fp:
            lock = json.load(fp)
        lock["packages"]["cachedep"] = {"src": "git", "cache": True,
                                        "url": "http://example/c.git"}
        with open(lock_path, "w") as fp:
            json.dump(lock, fp)
        ProjectOps(ws).destroy_apply(args=_Args(deps_only=True))
        # symlink gone, shared cache intact
        self.assertFalse(os.path.lexists(os.path.join(deps, "cachedep")))
        self.assertTrue(os.path.isfile(os.path.join(cache, "keep.txt")))

"""LoadPlanner: deciding whether a package needs to be loaded.

Residency comes from the filesystem, identity from the lock file. These tests
pin down the classification order (which is the specification, not an
implementation detail) and the two directions that must never be confused:
disk can assert absence, the lock cannot assert presence.

See pkg-prepare-design.md §3 / pkg-prepare-impl-plan.md P1.
"""
import os
import shutil
import tempfile
import threading
import unittest

from ivpm.load_plan import LoadAction, LoadPlanner, LoadState
from ivpm.package import Package
from ivpm.patch import PatchSpec


class _LoadPlanTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ivpm-loadplan-")
        self.deps_dir = os.path.join(self.tmp, "packages")
        os.makedirs(self.deps_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixture helpers ------------------------------------------------- #

    def mkPkg(self, name="p1", scope_key=None, src_type="git", **kw):
        pkg = Package(name)
        pkg.src_type = src_type
        pkg.path = os.path.join(self.deps_dir, name)
        pkg.scope_key = scope_key if scope_key is not None else name
        for k, v in kw.items():
            setattr(pkg, k, v)
        return pkg

    def populate(self, pkg, content="x"):
        os.makedirs(pkg.path, exist_ok=True)
        with open(os.path.join(pkg.path, "README"), "w") as fp:
            fp.write(content)

    def mkEmptyDir(self, pkg):
        os.makedirs(pkg.path, exist_ok=True)

    def mkLink(self, pkg):
        target = os.path.join(self.tmp, "shared", pkg.name)
        os.makedirs(target, exist_ok=True)
        with open(os.path.join(target, "README"), "w") as fp:
            fp.write("shared")
        os.symlink(target, pkg.path)

    def mkLock(self, entries):
        return {"ivpm_lock_version": 2, "packages": entries}

    def gitEntry(self, name, url="https://example.com/p1.git", **kw):
        entry = {"name": name, "src": "git", "url": url,
                 "branch": None, "tag": None,
                 "commit_requested": None, "cache": None}
        entry.update(kw)
        return entry


class TestResidencyStates(_LoadPlanTestCase):
    """One test per state, and the action each implies."""

    def test_absent_fetches(self):
        pkg = self.mkPkg()
        d = LoadPlanner(self.deps_dir).decide(pkg)
        self.assertIs(d.state, LoadState.ABSENT)
        self.assertIs(d.action, LoadAction.FETCH)

    def test_empty_directory_fetches(self):
        """The case the old existence checks got wrong."""
        pkg = self.mkPkg()
        self.mkEmptyDir(pkg)
        d = LoadPlanner(self.deps_dir).decide(pkg)
        self.assertIs(d.state, LoadState.PREPARED_EMPTY)
        self.assertIs(d.action, LoadAction.FETCH)

    def test_symlink_reuses(self):
        pkg = self.mkPkg()
        self.mkLink(pkg)
        d = LoadPlanner(self.deps_dir).decide(pkg)
        self.assertIs(d.state, LoadState.RESIDENT_LINK)
        self.assertIs(d.action, LoadAction.REUSE)

    def test_populated_untracked_reuses(self):
        """No lock entry must never mean 'safe to overwrite'."""
        pkg = self.mkPkg()
        self.populate(pkg)
        d = LoadPlanner(self.deps_dir).decide(pkg)
        self.assertIs(d.state, LoadState.RESIDENT_UNTRACKED)
        self.assertIs(d.action, LoadAction.REUSE)

    def test_populated_matching_reuses(self):
        pkg = self.mkPkg(url="https://example.com/p1.git")
        self.populate(pkg)
        lock = self.mkLock({"p1": self.gitEntry("p1")})
        d = LoadPlanner(self.deps_dir, lock).decide(pkg)
        self.assertIs(d.state, LoadState.RESIDENT_MATCHING)
        self.assertIs(d.action, LoadAction.REUSE)

    def test_populated_drifted_reports_but_reuses(self):
        """Drift is detected and reported; re-fetching stays opt-in."""
        pkg = self.mkPkg(url="https://example.com/CHANGED.git")
        self.populate(pkg)
        lock = self.mkLock({"p1": self.gitEntry("p1")})
        d = LoadPlanner(self.deps_dir, lock).decide(pkg)
        self.assertIs(d.state, LoadState.RESIDENT_DRIFTED)
        self.assertIs(d.action, LoadAction.REUSE)
        self.assertIsNotNone(d.drift)
        self.assertEqual(d.drift["locked"]["url"], "https://example.com/p1.git")

    def test_plain_file_is_resident(self):
        """A `src: file` dependency is a file, not a directory."""
        pkg = self.mkPkg(src_type="file")
        with open(pkg.path, "w") as fp:
            fp.write("data")
        d = LoadPlanner(self.deps_dir).decide(pkg)
        self.assertIs(d.action, LoadAction.REUSE)


class TestClassificationOrder(_LoadPlanTestCase):
    """The order of the tests in _classify is the specification."""

    def test_patched_wins_over_every_residency_state(self):
        """Patched trees reconcile whether absent, empty, linked or populated.

        Guards the regression that would silently break patch reconciliation:
        git deliberately re-examines a patched tree rather than skipping it.
        """
        spec = PatchSpec(name="p.diff", source="p.diff",
                         resolved_path="/nonexistent/p.diff", md5="abc123")

        for setup in ("absent", "empty", "link", "populated"):
            with self.subTest(residency=setup):
                pkg = self.mkPkg(name="pp_%s" % setup, patches=[spec])
                if setup == "empty":
                    self.mkEmptyDir(pkg)
                elif setup == "link":
                    self.mkLink(pkg)
                elif setup == "populated":
                    self.populate(pkg)
                d = LoadPlanner(self.deps_dir).decide(pkg)
                self.assertIs(d.state, LoadState.PATCHED)
                self.assertIs(d.action, LoadAction.RECONCILE)

    def test_patch_manifest_on_disk_reconciles(self):
        """A previously-patched tree reconciles even with no declared patches."""
        pkg = self.mkPkg()
        self.populate(pkg)
        os.makedirs(os.path.join(pkg.path, ".ivpm"), exist_ok=True)
        with open(os.path.join(pkg.path, ".ivpm", "patch-manifest.json"), "w") as fp:
            fp.write("{}")
        d = LoadPlanner(self.deps_dir).decide(pkg)
        self.assertIs(d.state, LoadState.PATCHED)
        self.assertIs(d.action, LoadAction.RECONCILE)

    def test_symlink_target_is_never_traversed(self):
        """islink must be tested before any emptiness probe.

        Cache hits point into shared, read-only, potentially large trees; the
        decision must not depend on reading them.
        """
        pkg = self.mkPkg()
        self.mkLink(pkg)

        real_listdir = os.listdir
        calls = []

        def _tattling_listdir(path, *a, **kw):
            calls.append(path)
            return real_listdir(path, *a, **kw)

        os.listdir = _tattling_listdir
        try:
            d = LoadPlanner(self.deps_dir).decide(pkg)
        finally:
            os.listdir = real_listdir

        self.assertIs(d.state, LoadState.RESIDENT_LINK)
        self.assertEqual(calls, [], "listdir was called: %r" % calls)


class TestDiskWinsOverLock(_LoadPlanTestCase):
    """The lock adds identity to a disk observation; it never asserts presence."""

    def test_locked_but_deleted_directory_is_absent(self):
        pkg = self.mkPkg(url="https://example.com/p1.git")
        lock = self.mkLock({"p1": self.gitEntry("p1")})
        d = LoadPlanner(self.deps_dir, lock).decide(pkg)
        self.assertIs(d.state, LoadState.ABSENT)
        self.assertIs(d.action, LoadAction.FETCH)

    def test_locked_but_emptied_directory_fetches(self):
        pkg = self.mkPkg(url="https://example.com/p1.git")
        self.mkEmptyDir(pkg)
        lock = self.mkLock({"p1": self.gitEntry("p1")})
        d = LoadPlanner(self.deps_dir, lock).decide(pkg)
        self.assertIs(d.state, LoadState.PREPARED_EMPTY)
        self.assertIs(d.action, LoadAction.FETCH)

    def test_no_lock_file_at_all(self):
        pkg = self.mkPkg()
        self.populate(pkg)
        d = LoadPlanner(self.deps_dir, None).decide(pkg)
        self.assertIs(d.state, LoadState.RESIDENT_UNTRACKED)


class TestMemoization(_LoadPlanTestCase):
    """A decision is stable for the life of the planner."""

    def test_decision_is_immune_to_its_own_consequences(self):
        """Creating the directory after deciding must not change the answer.

        This is what lets a prepare step run between the dispatch call and the
        provider's later call without the two observing different decisions.
        """
        pkg = self.mkPkg()
        planner = LoadPlanner(self.deps_dir)

        first = planner.decide(pkg)
        self.assertIs(first.state, LoadState.ABSENT)

        self.mkEmptyDir(pkg)          # what a prepare step does
        second = planner.decide(pkg)

        self.assertIs(second, first)
        self.assertIs(second.state, LoadState.ABSENT)
        self.assertIs(second.action, LoadAction.FETCH)

    def test_concurrent_decide_yields_one_decision(self):
        pkg = self.mkPkg()
        self.populate(pkg)
        planner = LoadPlanner(self.deps_dir)

        results = []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            results.append(planner.decide(pkg))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 8)
        self.assertTrue(all(r is results[0] for r in results))


class TestScopeKeying(_LoadPlanTestCase):
    """Lock keys are scope paths, and so is the memo key."""

    def test_same_name_in_two_scopes_decides_independently(self):
        os.makedirs(os.path.join(self.deps_dir, "toolB", "packages"),
                    exist_ok=True)

        root = self.mkPkg(name="libA", scope_key="libA",
                          url="https://example.com/libA.git")
        self.populate(root)

        nested = Package("libA")
        nested.src_type = "git"
        nested.url = "https://example.com/libA.git"
        nested.scope_key = "toolB/packages/libA"
        nested.path = os.path.join(self.deps_dir, "toolB", "packages", "libA")
        # deliberately not created on disk

        lock = self.mkLock({
            "libA": self.gitEntry("libA", url="https://example.com/libA.git"),
            "toolB/packages/libA": self.gitEntry(
                "libA", url="https://example.com/libA.git"),
        })
        planner = LoadPlanner(self.deps_dir, lock)

        self.assertIs(planner.decide(root).state, LoadState.RESIDENT_MATCHING)
        self.assertIs(planner.decide(nested).state, LoadState.ABSENT)

    def test_drift_report_covers_every_scope(self):
        """Drift in a nested dependency is reported -- impossible before."""
        nested_dir = os.path.join(self.deps_dir, "toolB", "packages")
        os.makedirs(nested_dir, exist_ok=True)

        nested = Package("libA")
        nested.src_type = "git"
        nested.url = "https://example.com/CHANGED.git"
        nested.scope_key = "toolB/packages/libA"
        nested.path = os.path.join(nested_dir, "libA")
        os.makedirs(nested.path)
        with open(os.path.join(nested.path, "README"), "w") as fp:
            fp.write("x")

        lock = self.mkLock({"toolB/packages/libA": self.gitEntry(
            "libA", url="https://example.com/libA.git")})
        planner = LoadPlanner(self.deps_dir, lock)
        planner.decide(nested)

        drifted = planner.drifted()
        self.assertIn("toolB/packages/libA", drifted)


class TestRobustness(_LoadPlanTestCase):

    def test_comparison_failure_does_not_refetch(self):
        """A blown-up comparison must fail toward the non-destructive answer."""

        class _Exploding(Package):
            def spec_matches_lock(self, lock_entry):
                raise RuntimeError("boom")

        pkg = _Exploding("p1")
        pkg.src_type = "git"
        pkg.path = os.path.join(self.deps_dir, "p1")
        pkg.scope_key = "p1"
        self.populate(pkg)

        lock = self.mkLock({"p1": self.gitEntry("p1")})
        d = LoadPlanner(self.deps_dir, lock).decide(pkg)
        self.assertIs(d.action, LoadAction.REUSE)

    def test_package_with_no_path_is_absent(self):
        pkg = self.mkPkg()
        pkg.path = None
        d = LoadPlanner(self.deps_dir).decide(pkg)
        self.assertIs(d.state, LoadState.ABSENT)

    def test_falls_back_to_name_without_scope_key(self):
        pkg = self.mkPkg(url="https://example.com/p1.git")
        pkg.scope_key = None
        self.populate(pkg)
        lock = self.mkLock({"p1": self.gitEntry("p1")})
        d = LoadPlanner(self.deps_dir, lock).decide(pkg)
        self.assertIs(d.state, LoadState.RESIDENT_MATCHING)


if __name__ == "__main__":
    unittest.main()

"""
test_sync_ops.py — unit tests for the refactored sync command.

All tests use local git repositories (no network access required).
Each test scenario constructs a minimal upstream repo, clones it into a
packages/ directory, writes a lock file, then calls ProjectOps.sync()
directly and asserts on the PkgSyncResult outcome.
"""
import hashlib
import json
import os
import stat
import subprocess
import unittest

from .test_base import TestBase, _force_rmtree
from ivpm.project_ops import ProjectOps
from ivpm.pkg_sync import SyncOutcome


# ---------------------------------------------------------------------------
# Helpers shared across test cases
# ---------------------------------------------------------------------------

def _git(*args, cwd, check=True, capture=True):
    """Run a git command and return the CompletedProcess."""
    kwargs = dict(cwd=cwd, check=check)
    if capture:
        kwargs["capture_output"] = True
        kwargs["text"] = True
    return subprocess.run(["git"] + list(args), **kwargs)


def _git_config(repo_dir):
    """Set a minimal git identity for commits inside *repo_dir*."""
    _git("config", "user.email", "test@ivpm.test", cwd=repo_dir)
    _git("config", "user.name",  "IVPM Test",       cwd=repo_dir)


def _make_lock(packages_dir, pkg_entries):
    """Write a minimal package-lock.json to *packages_dir*."""
    os.makedirs(packages_dir, exist_ok=True)
    lock = {
        "ivpm_lock_version": 1,
        "generated": "2024-01-01T00:00:00+00:00",
        "packages": pkg_entries,
    }
    body = json.dumps(lock, indent=2, sort_keys=True)
    lock["sha256"] = hashlib.sha256(body.encode()).hexdigest()
    lock_path = os.path.join(packages_dir, "package-lock.json")
    with open(lock_path, "w") as f:
        json.dump(lock, indent=2, sort_keys=True, fp=f)
        f.write("\n")
    return lock_path


class _SyncArgs:
    """Minimal args object accepted by ProjectOps.sync()."""
    project_dir = None
    dry_run = False
    no_rich = True
    packages_filter = None
    jobs = 0
    _sync_progress = None


# ---------------------------------------------------------------------------
# Base class with shared setup helpers
# ---------------------------------------------------------------------------

class SyncTestBase(TestBase):
    """Extends TestBase with helpers for building local git repos."""

    def tearDown(self):
        if hasattr(self, "testdir") and os.path.isdir(self.testdir):
            _force_rmtree(self.testdir)
        return super(SyncTestBase, self).tearDown()

    # ------------------------------------------------------------------
    # Repo construction
    # ------------------------------------------------------------------

    def _make_upstream(self, name):
        """Create a local upstream git repo with an initial commit.

        Returns the path to the upstream directory.
        """
        upstream_dir = os.path.join(self.testdir, "upstreams", name)
        os.makedirs(upstream_dir)
        _git("init", upstream_dir, cwd=self.testdir)
        _git("checkout", "-b", "main", cwd=upstream_dir, check=False)  # git≥2.28
        _git_config(upstream_dir)
        _write_file(upstream_dir, "README.md", "initial\n")
        _git("add", ".", cwd=upstream_dir)
        _git("commit", "-m", "initial", cwd=upstream_dir)
        # Normalise: make sure we're on a branch called 'main' or the default.
        return upstream_dir

    def _clone_pkg(self, upstream_dir, name):
        """Clone *upstream_dir* into packages/<name>. Returns package path."""
        packages_dir = os.path.join(self.testdir, "packages")
        os.makedirs(packages_dir, exist_ok=True)
        pkg_dir = os.path.join(packages_dir, name)
        _git("clone", upstream_dir, pkg_dir, cwd=self.testdir)
        _git_config(pkg_dir)
        return pkg_dir

    def _add_upstream_commit(self, upstream_dir, content="update\n"):
        """Append *content* to README.md and commit to upstream."""
        readme_path = os.path.join(upstream_dir, "README.md")
        with open(readme_path) as f:
            existing = f.read()
        _write_file(upstream_dir, "README.md", existing + content)
        _git("add", ".",          cwd=upstream_dir)
        _git("commit", "-m", "upstream update", cwd=upstream_dir)

    def _branch_name(self, repo_dir):
        """Return the current branch name of a repo."""
        r = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo_dir)
        return r.stdout.strip()

    # ------------------------------------------------------------------
    # Project setup
    # ------------------------------------------------------------------

    def _setup_project(self, pkg_entries):
        """Write ivpm.yaml and package-lock.json for a test project."""
        self.mkFile("ivpm.yaml", "package:\n  name: test_proj\n  dep-sets:\n    - name: default-dev\n      deps: []\n")
        packages_dir = os.path.join(self.testdir, "packages")
        _make_lock(packages_dir, pkg_entries)

    def _sync(self, dry_run=False, args=None):
        """Run ProjectOps.sync() and return the list of PkgSyncResult."""
        if args is None:
            args = _SyncArgs()
            args.dry_run = dry_run
        return ProjectOps(self.testdir).sync(args=args)

    def _result(self, results, name):
        """Return the single result for package *name*."""
        matches = [r for r in results if r.name == name]
        self.assertEqual(len(matches), 1,
                         "Expected exactly one result for %r, got %d" % (name, len(matches)))
        return matches[0]


def _write_file(directory, filename, content):
    with open(os.path.join(directory, filename), "w") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSyncOps(SyncTestBase):
    """Tests for all sync outcomes using local git repos."""

    # ── UP_TO_DATE ────────────────────────────────────────────────────────

    def test_up_to_date(self):
        """Package already at latest commit → UP_TO_DATE."""
        upstream = self._make_upstream("pkg_up2date")
        self._clone_pkg(upstream, "pkg_up2date")
        branch = self._branch_name(upstream)
        self._setup_project({"pkg_up2date": {"src": "git", "branch": branch}})

        results = self._sync()
        r = self._result(results, "pkg_up2date")
        self.assertEqual(r.outcome, SyncOutcome.UP_TO_DATE)
        self.assertEqual(r.branch, branch)
        self.assertIsNotNone(r.old_commit)

    # ── SYNCED ────────────────────────────────────────────────────────────

    def test_synced_fast_forward(self):
        """Upstream has a new commit → SYNCED (fast-forward)."""
        upstream = self._make_upstream("pkg_sync")
        pkg_dir = self._clone_pkg(upstream, "pkg_sync")
        before = _git("rev-parse", "--short", "HEAD", cwd=pkg_dir).stdout.strip()

        self._add_upstream_commit(upstream)
        branch = self._branch_name(upstream)
        self._setup_project({"pkg_sync": {"src": "git", "branch": branch}})

        results = self._sync()
        r = self._result(results, "pkg_sync")
        self.assertEqual(r.outcome, SyncOutcome.SYNCED)
        self.assertEqual(r.old_commit, before)
        self.assertIsNotNone(r.new_commit)
        self.assertNotEqual(r.old_commit, r.new_commit)
        self.assertGreater(r.commits_behind or 0, 0)

    def test_synced_updates_lock_file(self):
        """After SYNCED, lock file commit_resolved should be updated."""
        upstream = self._make_upstream("pkg_lockupdate")
        pkg_dir = self._clone_pkg(upstream, "pkg_lockupdate")
        self._add_upstream_commit(upstream)
        branch = self._branch_name(upstream)
        self._setup_project({"pkg_lockupdate": {"src": "git", "branch": branch}})

        self._sync()

        lock_path = os.path.join(self.testdir, "packages", "package-lock.json")
        with open(lock_path) as f:
            lock = json.load(f)
        new_head = _git("rev-parse", "HEAD", cwd=pkg_dir).stdout.strip()
        self.assertEqual(
            lock["packages"]["pkg_lockupdate"]["commit_resolved"], new_head
        )

    # ── DIRTY ─────────────────────────────────────────────────────────────

    def test_dirty_blocks_sync(self):
        """Uncommitted staged changes + upstream commits → DIRTY."""
        upstream = self._make_upstream("pkg_dirty")
        pkg_dir = self._clone_pkg(upstream, "pkg_dirty")
        self._add_upstream_commit(upstream)

        # Stage a new file in the working tree
        _write_file(pkg_dir, "dirty.txt", "uncommitted\n")
        _git("add", "dirty.txt", cwd=pkg_dir)

        branch = self._branch_name(upstream)
        self._setup_project({"pkg_dirty": {"src": "git", "branch": branch}})

        results = self._sync()
        r = self._result(results, "pkg_dirty")
        self.assertEqual(r.outcome, SyncOutcome.DIRTY)
        self.assertTrue(len(r.dirty_files) > 0)
        # Working tree must not have been touched
        self.assertFalse(os.path.exists(os.path.join(pkg_dir, ".git", "MERGE_HEAD")))

    def test_dirty_up_to_date_is_still_up_to_date(self):
        """Dirty tree when already up-to-date → UP_TO_DATE (nothing to merge)."""
        upstream = self._make_upstream("pkg_dirty_utd")
        pkg_dir = self._clone_pkg(upstream, "pkg_dirty_utd")

        # Dirty the tree but no upstream commits
        _write_file(pkg_dir, "dirty.txt", "local\n")

        branch = self._branch_name(upstream)
        self._setup_project({"pkg_dirty_utd": {"src": "git", "branch": branch}})

        results = self._sync()
        r = self._result(results, "pkg_dirty_utd")
        self.assertEqual(r.outcome, SyncOutcome.UP_TO_DATE)

    # ── CONFLICT ──────────────────────────────────────────────────────────

    def test_conflict(self):
        """Diverged history with same-file edits → CONFLICT, repo left clean."""
        upstream = self._make_upstream("pkg_conflict")
        pkg_dir = self._clone_pkg(upstream, "pkg_conflict")

        # Local commit on pkg_dir
        _write_file(pkg_dir, "README.md", "local version\n")
        _git("add", ".",             cwd=pkg_dir)
        _git("commit", "-m", "local", cwd=pkg_dir)

        # Conflicting upstream commit on same file
        _write_file(upstream, "README.md", "upstream version\n")
        _git("add", ".",                cwd=upstream)
        _git("commit", "-m", "upstream", cwd=upstream)

        branch = self._branch_name(upstream)
        self._setup_project({"pkg_conflict": {"src": "git", "branch": branch}})

        results = self._sync()
        r = self._result(results, "pkg_conflict")
        self.assertEqual(r.outcome, SyncOutcome.CONFLICT)
        self.assertTrue(len(r.conflict_files) > 0)
        self.assertTrue(len(r.next_steps) > 0)
        # merge --abort must have cleaned up
        self.assertFalse(
            os.path.exists(os.path.join(pkg_dir, ".git", "MERGE_HEAD")),
            "MERGE_HEAD should not exist after merge --abort",
        )

    # ── AHEAD ─────────────────────────────────────────────────────────────

    def test_ahead(self):
        """Local commits not pushed to origin → AHEAD."""
        upstream = self._make_upstream("pkg_ahead")
        pkg_dir = self._clone_pkg(upstream, "pkg_ahead")

        # Commit locally — nothing pushed to upstream
        _write_file(pkg_dir, "local_only.txt", "local\n")
        _git("add", ".",                  cwd=pkg_dir)
        _git("commit", "-m", "local only", cwd=pkg_dir)

        branch = self._branch_name(upstream)
        self._setup_project({"pkg_ahead": {"src": "git", "branch": branch}})

        results = self._sync()
        r = self._result(results, "pkg_ahead")
        self.assertEqual(r.outcome, SyncOutcome.AHEAD)
        self.assertGreater(r.commits_ahead or 0, 0)

    # ── ERROR ─────────────────────────────────────────────────────────────

    def test_error_not_git_repo(self):
        """Package dir exists but has no .git → ERROR."""
        pkg_dir = os.path.join(self.testdir, "packages", "pkg_nogit")
        os.makedirs(pkg_dir)
        _write_file(pkg_dir, "README.md", "not a git repo\n")
        self._setup_project({"pkg_nogit": {"src": "git"}})

        results = self._sync()
        r = self._result(results, "pkg_nogit")
        self.assertEqual(r.outcome, SyncOutcome.ERROR)
        self.assertIsNotNone(r.error)

    # ── SKIPPED — read-only (cached) ──────────────────────────────────────

    def test_skipped_readonly(self):
        """Read-only package directory → SKIPPED (read-only/cached)."""
        upstream = self._make_upstream("pkg_ro")
        pkg_dir = self._clone_pkg(upstream, "pkg_ro")

        # Make the top-level directory read-only (simulates cache:false)
        for root, dirs, files in os.walk(pkg_dir, topdown=False):
            for d in dirs:
                os.chmod(os.path.join(root, d), stat.S_IRUSR | stat.S_IXUSR)
            for fname in files:
                os.chmod(os.path.join(root, fname), stat.S_IRUSR)
        os.chmod(pkg_dir, stat.S_IRUSR | stat.S_IXUSR)

        self._setup_project({"pkg_ro": {"src": "git"}})

        results = self._sync()
        r = self._result(results, "pkg_ro")
        self.assertEqual(r.outcome, SyncOutcome.SKIPPED)
        self.assertIn("read-only", r.skipped_reason)

    # ── SKIPPED — tag-pinned ──────────────────────────────────────────────

    def test_skipped_tag_pinned(self):
        """Package pinned to a tag → SKIPPED (no sync makes sense)."""
        upstream = self._make_upstream("pkg_tag")
        self._clone_pkg(upstream, "pkg_tag")
        self._setup_project({"pkg_tag": {"src": "git", "tag": "v1.0.0"}})

        results = self._sync()
        r = self._result(results, "pkg_tag")
        self.assertEqual(r.outcome, SyncOutcome.SKIPPED)
        self.assertIn("tag", r.skipped_reason)
        self.assertIn("v1.0.0", r.skipped_reason)

    # ── SKIPPED — non-git package ─────────────────────────────────────────

    def test_skipped_pypi(self):
        """PyPI package → SKIPPED (base Package.sync() default)."""
        self._setup_project({"mypkg": {"src": "pypi", "version_requested": ">=1.0"}})

        results = self._sync()
        r = self._result(results, "mypkg")
        self.assertEqual(r.outcome, SyncOutcome.SKIPPED)

    def test_skipped_dir(self):
        """Dir-type package → SKIPPED."""
        self._setup_project({"localpkg": {"src": "dir", "path": "/some/path"}})

        results = self._sync()
        r = self._result(results, "localpkg")
        self.assertEqual(r.outcome, SyncOutcome.SKIPPED)

    # ── Dry-run: DRY_WOULD_SYNC ───────────────────────────────────────────

    def test_dry_run_would_sync(self):
        """Dry-run, upstream ahead by one fast-forward commit → DRY_WOULD_SYNC."""
        upstream = self._make_upstream("pkg_dry_sync")
        pkg_dir = self._clone_pkg(upstream, "pkg_dry_sync")
        commit_before = _git("rev-parse", "HEAD", cwd=pkg_dir).stdout.strip()

        self._add_upstream_commit(upstream)
        branch = self._branch_name(upstream)
        self._setup_project({"pkg_dry_sync": {"src": "git", "branch": branch}})

        results = self._sync(dry_run=True)
        r = self._result(results, "pkg_dry_sync")
        self.assertEqual(r.outcome, SyncOutcome.DRY_WOULD_SYNC)
        self.assertGreater(r.commits_behind or 0, 0)

        # CRITICAL: no actual merge should have taken place
        commit_after = _git("rev-parse", "HEAD", cwd=pkg_dir).stdout.strip()
        self.assertEqual(commit_before, commit_after,
                         "dry-run must not alter the working tree")

    # ── Dry-run: DRY_DIRTY ────────────────────────────────────────────────

    def test_dry_run_dirty(self):
        """Dry-run, upstream ahead but working tree dirty → DRY_DIRTY."""
        upstream = self._make_upstream("pkg_dry_dirty")
        pkg_dir = self._clone_pkg(upstream, "pkg_dry_dirty")
        self._add_upstream_commit(upstream)

        # Stage a new file
        _write_file(pkg_dir, "staged.txt", "staged\n")
        _git("add", "staged.txt", cwd=pkg_dir)

        branch = self._branch_name(upstream)
        self._setup_project({"pkg_dry_dirty": {"src": "git", "branch": branch}})

        results = self._sync(dry_run=True)
        r = self._result(results, "pkg_dry_dirty")
        self.assertEqual(r.outcome, SyncOutcome.DRY_DIRTY)
        self.assertTrue(len(r.dirty_files) > 0)
        # No merge should have occurred
        self.assertFalse(os.path.exists(
            os.path.join(pkg_dir, ".git", "MERGE_HEAD")))

    # ── Dry-run: DRY_WOULD_CONFLICT ───────────────────────────────────────

    def test_dry_run_would_conflict(self):
        """Dry-run on diverged history → DRY_WOULD_CONFLICT, tree untouched."""
        upstream = self._make_upstream("pkg_dry_conflict")
        pkg_dir = self._clone_pkg(upstream, "pkg_dry_conflict")

        # Local commit
        _write_file(pkg_dir, "README.md", "local\n")
        _git("add", ".",              cwd=pkg_dir)
        _git("commit", "-m", "local", cwd=pkg_dir)

        # Upstream commit on same file
        _write_file(upstream, "README.md", "upstream\n")
        _git("add", ".",                 cwd=upstream)
        _git("commit", "-m", "upstream", cwd=upstream)

        branch = self._branch_name(upstream)
        self._setup_project({"pkg_dry_conflict": {"src": "git", "branch": branch}})

        results = self._sync(dry_run=True)
        r = self._result(results, "pkg_dry_conflict")
        self.assertEqual(r.outcome, SyncOutcome.DRY_WOULD_CONFLICT)
        # Working tree must be untouched
        self.assertFalse(os.path.exists(
            os.path.join(pkg_dir, ".git", "MERGE_HEAD")))
        with open(os.path.join(pkg_dir, "README.md")) as f:
            content = f.read()
        self.assertEqual(content, "local\n",
                         "dry-run must not modify working tree files")

    # ── Mixed results ─────────────────────────────────────────────────────

    def test_mixed_results(self):
        """Multiple packages with different outcomes are all collected."""
        # pkg_a: up-to-date
        upstream_a = self._make_upstream("pkg_a")
        self._clone_pkg(upstream_a, "pkg_a")
        branch_a = self._branch_name(upstream_a)

        # pkg_b: will be synced
        upstream_b = self._make_upstream("pkg_b")
        self._clone_pkg(upstream_b, "pkg_b")
        self._add_upstream_commit(upstream_b)
        branch_b = self._branch_name(upstream_b)

        # pkg_c: pypi (skipped)
        self._setup_project({
            "pkg_a": {"src": "git", "branch": branch_a},
            "pkg_b": {"src": "git", "branch": branch_b},
            "pkg_c": {"src": "pypi"},
        })

        results = self._sync()
        outcomes = {r.name: r.outcome for r in results}
        self.assertEqual(outcomes["pkg_a"], SyncOutcome.UP_TO_DATE)
        self.assertEqual(outcomes["pkg_b"], SyncOutcome.SYNCED)
        self.assertEqual(outcomes["pkg_c"], SyncOutcome.SKIPPED)

    # ── Exit-code: only ERROR triggers sys.exit ───────────────────────────

    def test_conflict_does_not_raise(self):
        """CONFLICT outcome must not raise or call sys.exit — it is informational."""
        upstream = self._make_upstream("pkg_conflict_noexit")
        pkg_dir = self._clone_pkg(upstream, "pkg_conflict_noexit")

        _write_file(pkg_dir, "README.md", "local\n")
        _git("add", ".", cwd=pkg_dir)
        _git("commit", "-m", "local", cwd=pkg_dir)

        _write_file(upstream, "README.md", "upstream\n")
        _git("add", ".", cwd=upstream)
        _git("commit", "-m", "upstream", cwd=upstream)

        branch = self._branch_name(upstream)
        self._setup_project({"pkg_conflict_noexit": {"src": "git", "branch": branch}})

        # Should not raise SystemExit
        results = self._sync()
        r = self._result(results, "pkg_conflict_noexit")
        self.assertEqual(r.outcome, SyncOutcome.CONFLICT)

    def test_dirty_does_not_raise(self):
        """DIRTY outcome must not raise or call sys.exit — it is informational."""
        upstream = self._make_upstream("pkg_dirty_noexit")
        pkg_dir = self._clone_pkg(upstream, "pkg_dirty_noexit")
        self._add_upstream_commit(upstream)

        _write_file(pkg_dir, "dirty.txt", "local\n")
        _git("add", "dirty.txt", cwd=pkg_dir)

        branch = self._branch_name(upstream)
        self._setup_project({"pkg_dirty_noexit": {"src": "git", "branch": branch}})

        results = self._sync()
        r = self._result(results, "pkg_dirty_noexit")
        self.assertEqual(r.outcome, SyncOutcome.DIRTY)

    # ── Parallel sync ─────────────────────────────────────────────────────

    def test_parallel_multiple_packages(self):
        """Multiple packages run in parallel all get the correct outcomes."""
        # Create 4 upstream repos, 2 with new commits and 2 up-to-date
        entries = {}
        for i in range(4):
            name = "pkg_par%d" % i
            upstream = self._make_upstream(name)
            self._clone_pkg(upstream, name)
            if i < 2:
                self._add_upstream_commit(upstream)
            branch = self._branch_name(upstream)
            entries[name] = {"src": "git", "branch": branch}

        self._setup_project(entries)

        # Run with explicit parallelism of 2 workers
        args = _SyncArgs()
        args.jobs = 2
        results = self._sync(args=args)

        outcomes = {r.name: r.outcome for r in results}
        self.assertEqual(outcomes["pkg_par0"], SyncOutcome.SYNCED)
        self.assertEqual(outcomes["pkg_par1"], SyncOutcome.SYNCED)
        self.assertEqual(outcomes["pkg_par2"], SyncOutcome.UP_TO_DATE)
        self.assertEqual(outcomes["pkg_par3"], SyncOutcome.UP_TO_DATE)

    def test_progress_callbacks_fire(self):
        """on_pkg_start and on_pkg_result are called once per package."""
        from ivpm.pkg_sync import SyncProgressListener

        class _Collector(SyncProgressListener):
            def __init__(self):
                self.started = []
                self.finished = []
            def on_pkg_start(self, name):
                self.started.append(name)
            def on_pkg_result(self, result):
                self.finished.append(result.name)

        upstream_a = self._make_upstream("pkg_cb_a")
        self._clone_pkg(upstream_a, "pkg_cb_a")
        upstream_b = self._make_upstream("pkg_cb_b")
        self._clone_pkg(upstream_b, "pkg_cb_b")
        self._add_upstream_commit(upstream_b)

        branch_a = self._branch_name(upstream_a)
        branch_b = self._branch_name(upstream_b)
        self._setup_project({
            "pkg_cb_a": {"src": "git", "branch": branch_a},
            "pkg_cb_b": {"src": "git", "branch": branch_b},
        })

        collector = _Collector()
        args = _SyncArgs()
        args._sync_progress = collector
        results = self._sync(args=args)

        self.assertCountEqual(collector.started, ["pkg_cb_a", "pkg_cb_b"])
        self.assertCountEqual(collector.finished, ["pkg_cb_a", "pkg_cb_b"])

    def test_dry_run_parallel(self):
        """Dry-run works correctly in parallel mode."""
        entries = {}
        upstreams = {}
        pkg_dirs = {}
        for i in range(3):
            name = "pkg_dry_par%d" % i
            upstream = self._make_upstream(name)
            pkg_dir = self._clone_pkg(upstream, name)
            self._add_upstream_commit(upstream)
            branch = self._branch_name(upstream)
            entries[name] = {"src": "git", "branch": branch}
            upstreams[name] = upstream
            pkg_dirs[name] = pkg_dir

        self._setup_project(entries)

        # Record HEAD commits before dry-run
        heads_before = {n: _git("rev-parse", "HEAD", cwd=d).stdout.strip()
                        for n, d in pkg_dirs.items()}

        args = _SyncArgs()
        args.dry_run = True
        args.jobs = 2
        results = self._sync(args=args)

        outcomes = {r.name: r.outcome for r in results}
        for name in pkg_dirs:
            self.assertEqual(outcomes[name], SyncOutcome.DRY_WOULD_SYNC,
                             "expected DRY_WOULD_SYNC for %s" % name)
            # Working trees must not have been modified
            head_after = _git("rev-parse", "HEAD", cwd=pkg_dirs[name]).stdout.strip()
            self.assertEqual(heads_before[name], head_after,
                             "dry-run must not modify %s" % name)


if __name__ == "__main__":
    unittest.main()

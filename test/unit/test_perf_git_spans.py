#****************************************************************************
#* test_perf_git_spans.py
#*
#* Phase-3 integration tests: per-package git sub-phase spans and the
#* queue-wait span. Uses local file:// git repos so the test is hermetic
#* (no network) -- see memory flaky-network-integration-tests.md.
#****************************************************************************
import os
import subprocess
import tempfile
import unittest

from .test_base import TestBase
from ivpm.project_ops import ProjectOps


class _Args:
    def __init__(self, jobs=None):
        self.anonymous_git = None
        self.jobs = jobs


class TestPerfGitSpans(TestBase):

    def setUp(self):
        super().setUp()
        self.cache_dir = tempfile.mkdtemp(prefix="ivpm-perf-cache-")
        os.environ["IVPM_CACHE"] = self.cache_dir

    def tearDown(self):
        os.environ.pop("IVPM_CACHE", None)
        super().tearDown()

    def _init_git_repo(self, path, files):
        os.makedirs(path, exist_ok=True)
        subprocess.check_call(["git", "init", "-b", "main"], cwd=path)
        subprocess.check_call(["git", "config", "user.email", "t@e.com"], cwd=path)
        subprocess.check_call(["git", "config", "user.name", "T"], cwd=path)
        for rel, content in files.items():
            with open(os.path.join(path, rel), "w") as f:
                f.write(content)
        subprocess.check_call(["git", "add", "-A"], cwd=path)
        subprocess.check_call(["git", "commit", "-m", "init"], cwd=path)

    def _write_root(self, deps_yaml):
        self.mkFile("ivpm.yaml",
            "package:\n"
            "    name: perf_git_root\n"
            "    dep-sets:\n"
            "        - name: default-dev\n"
            "          deps:\n"
            "%s\n" % deps_yaml)

    def _update(self, jobs=None):
        args = _Args(jobs=jobs)
        ops = ProjectOps(self.testdir, args)
        ops.update(dep_set="default-dev", skip_venv=True, args=args)
        return ops

    def _cats(self, perf, package=None):
        return [s.category for s in perf.spans
                if package is None or s.package == package]

    def test_cache_miss_then_hit(self):
        """MISS build emits clone+store+materialize; a subsequent HIT (cache
        pre-populated, deps symlink removed) emits materialize but NOT clone."""
        src = os.path.join(self.testdir, "src_repo")
        self._init_git_repo(src, {"test.txt": "hello"})
        self._write_root(
            "                    - name: gpkg\n"
            "                      url: file://%s\n"
            "                      src: git\n"
            "                      cache: true" % src)

        ops1 = self._update()
        cats1 = self._cats(ops1._perf, "gpkg")
        self.assertIn("git.resolve_hash", cats1)
        self.assertIn("cache.lookup", cats1)
        self.assertIn("git.clone", cats1)
        self.assertIn("cache.store", cats1)
        self.assertIn("cache.materialize", cats1)
        # cache.lookup recorded a miss; the package span recorded cache_hit=False.
        lookup = next(s for s in ops1._perf.spans if s.category == "cache.lookup")
        self.assertEqual(lookup.meta.get("state"), "miss")
        pkg_span = next(s for s in ops1._perf.spans if s.category == "fetch.pkg")
        self.assertEqual(pkg_span.meta.get("cache_hit"), False)

        # Drop the deps symlink so the next update re-resolves against the cache
        # (which now holds the entry) -> HIT.
        pkg_link = os.path.join(self.testdir, "packages", "gpkg")
        if os.path.islink(pkg_link) or os.path.exists(pkg_link):
            os.unlink(pkg_link)

        ops2 = self._update()
        cats2 = self._cats(ops2._perf, "gpkg")
        self.assertIn("cache.lookup", cats2)
        self.assertIn("cache.materialize", cats2)
        self.assertNotIn("git.clone", cats2)
        lookup2 = next(s for s in ops2._perf.spans if s.category == "cache.lookup")
        self.assertEqual(lookup2.meta.get("state"), "hit")
        pkg_span2 = next(s for s in ops2._perf.spans if s.category == "fetch.pkg")
        self.assertEqual(pkg_span2.meta.get("cache_hit"), True)

    def test_full_clone_no_cache_spans(self):
        """cache unspecified -> full clone: git.clone present, cache.* absent."""
        src = os.path.join(self.testdir, "src_repo")
        self._init_git_repo(src, {"a.txt": "a"})
        self._write_root(
            "                    - name: gpkg\n"
            "                      url: file://%s\n"
            "                      src: git" % src)

        ops = self._update()
        cats = self._cats(ops._perf, "gpkg")
        self.assertIn("git.clone", cats)
        self.assertNotIn("cache.lookup", cats)
        self.assertNotIn("cache.store", cats)

    def test_git_sub_spans_parent_onto_pkg(self):
        """Every git sub-phase span nests under its package's fetch.pkg span
        (verifies the worker-thread stack parenting) -- no orphans."""
        src = os.path.join(self.testdir, "src_repo")
        self._init_git_repo(src, {"a.txt": "a"})
        self._write_root(
            "                    - name: gpkg\n"
            "                      url: file://%s\n"
            "                      src: git" % src)

        ops = self._update()
        spans = ops._perf.spans
        pkg_span = next(s for s in spans if s.category == "fetch.pkg"
                        and s.package == "gpkg")
        clone = next(s for s in spans if s.category == "git.clone"
                     and s.package == "gpkg")
        self.assertEqual(clone.parent_id, pkg_span.span_id)

        # Whole run reconstructs to a single root with no orphans.
        roots = ops._perf.to_tree()
        self.assertEqual(len(roots), 1)

    def test_queue_wait_measured_serial(self):
        """With jobs=1 and two packages, the second to acquire the worker slot
        records a non-trivial queue_wait (it waits out the first's fetch)."""
        src_a = os.path.join(self.testdir, "src_a")
        src_b = os.path.join(self.testdir, "src_b")
        self._init_git_repo(src_a, {"a.txt": "a"})
        self._init_git_repo(src_b, {"b.txt": "b"})
        self._write_root(
            "                    - name: apkg\n"
            "                      url: file://%s\n"
            "                      src: git\n"
            "                    - name: bpkg\n"
            "                      url: file://%s\n"
            "                      src: git" % (src_a, src_b))

        ops = self._update(jobs=1)
        qwaits = [s for s in ops._perf.spans if s.category == "pkg.queue_wait"]
        self.assertEqual(len(qwaits), 2)
        for s in qwaits:
            self.assertIsNotNone(s.duration)
            self.assertGreaterEqual(s.duration, 0.0)
        # Serialized: the later package waits out the earlier package's fetch,
        # so the max queue-wait is clearly non-zero.
        self.assertGreater(max(s.duration for s in qwaits), 0.001)
        # queue_wait parents onto the fetch phase (sibling of fetch.pkg).
        fetch = next(s for s in ops._perf.spans if s.category == "fetch")
        for s in qwaits:
            self.assertEqual(s.parent_id, fetch.span_id)


if __name__ == "__main__":
    unittest.main()

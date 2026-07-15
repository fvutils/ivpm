"""Concurrency & mutual-exclusion tests for the cache store.

Companion to cache-concurrency-design.md / cache-concurrency-impl-plan.md.
Covers the correctness fixes H1-H5 across the four threat scenarios T1-T4:

* T1 — multi-process, shared cache (the store race)
* T2 — concurrent runs sharing a deps_dir (patch resolver staging)
* T3 — genuine store failures are surfaced, not mistaken for a race
* T4 — crash-leftover recovery (empty dirs, orphaned staging)
"""
import errno
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

ROOTDIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRCDIR = os.path.join(ROOTDIR, 'src')

import sys
sys.path.insert(0, SRCDIR)

from ivpm.cache import DirectoryCacheStore, CacheStoreError


# --- module-level workers for the multi-process races (fork-based) ----------

def _make_source(root, tag, content):
    """Build a distinct source tree with identical published content."""
    src = os.path.join(root, "src_%s" % tag)
    os.makedirs(src)
    with open(os.path.join(src, "content.txt"), "w") as f:
        f.write(content)
    return src


def _store_worker(cache_dir, scratch, tag, content, barrier):
    """T1 worker: all processes store the SAME (pkg, version) at once."""
    store = DirectoryCacheStore(cache_dir)
    src = _make_source(scratch, tag, content)
    barrier.wait()                       # maximize contention on the publish
    store.store_version("pkg", "v1", src)


def _variant_worker(cache_dir, deps_dir, base_version, eff, tag, content, barrier):
    """T2 worker: mirror PatchAwareResolver's pre-store staging + publish,
    concurrently, sharing one deps_dir and one cache."""
    from ivpm.patch import _copy_tree, _rmtree_if_exists
    store = DirectoryCacheStore(cache_dir)
    base_path = store.get_version_cache_dir("pkg", base_version)
    barrier.wait()
    # Build on the cache FS via new_staging (mirrors provider.new_staging):
    # unique per-build (H5) and same-filesystem so store publishes by rename.
    staging = store.new_staging("pkg")
    _rmtree_if_exists(staging)
    _copy_tree(base_path, staging)
    with open(os.path.join(staging, "patched.txt"), "w") as f:
        f.write(content)                 # same for every worker -> identical variant
    store.store_version("pkg", eff, staging)


def _run_procs(target, args_list):
    import multiprocessing
    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=target, args=a) for a in args_list]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    return procs


class _StoreBase(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.cache_dir = os.path.join(self.test_dir, "cache")
        self.deps_dir = os.path.join(self.test_dir, "deps")
        os.makedirs(self.deps_dir)
        self.store = DirectoryCacheStore(self.cache_dir)

    def tearDown(self):
        for root, dirs, files in os.walk(self.test_dir):
            for d in dirs:
                try:
                    os.chmod(os.path.join(root, d), 0o755)
                except OSError:
                    pass
            for f in files:
                try:
                    os.chmod(os.path.join(root, f), 0o644)
                except OSError:
                    pass
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _pkg_dir(self, pkg="pkg"):
        return os.path.join(self.cache_dir, pkg)

    def _staging_residue(self, pkg="pkg"):
        d = self._pkg_dir(pkg)
        if not os.path.isdir(d):
            return []
        return [n for n in os.listdir(d) if ".staging." in n]


# --- 3.1 Publish race & adopt (single process) ------------------------------

class TestPublishRaceAndAdopt(_StoreBase):
    def test_lost_race_adopts_populated_winner(self):
        # A populated winner is already present; our store discards its source
        # and returns the winner untouched.
        winner = self.store.get_version_cache_dir("pkg", "v1")
        os.makedirs(winner)
        with open(os.path.join(winner, "winner.txt"), "w") as f:
            f.write("winner")

        src = _make_source(self.test_dir, "loser", "loser")
        got = self.store.store_version("pkg", "v1", src)

        self.assertEqual(got, winner)
        self.assertFalse(os.path.exists(src))                     # source discarded
        self.assertTrue(os.path.isfile(os.path.join(winner, "winner.txt")))
        self.assertFalse(os.path.exists(os.path.join(winner, "content.txt")))

    def test_empty_leftover_is_rebuilt_not_adopted(self):
        # H4: an empty version_dir must not short-circuit as a hit; the atomic
        # rename replaces it with our real tree.
        empty = self.store.get_version_cache_dir("pkg", "v1")
        os.makedirs(empty)

        src = _make_source(self.test_dir, "real", "real-content")
        got = self.store.store_version("pkg", "v1", src)

        self.assertTrue(self.store.has_version("pkg", "v1"))
        with open(os.path.join(got, "content.txt")) as f:
            self.assertEqual(f.read(), "real-content")

    def test_genuine_error_raises_cachestoreerror(self):
        # H3: a non-race errno (ENOSPC) is surfaced, not swallowed as a miss.
        src = _make_source(self.test_dir, "x", "x")

        def boom(a, b):
            raise OSError(errno.ENOSPC, "no space")

        with patch("ivpm.cache.os.rename", side_effect=boom):
            with self.assertRaises(CacheStoreError):
                self.store.store_version("pkg", "v1", src)

        # staging and source both cleaned up; no half-entry left behind
        self.assertFalse(self.store.has_version("pkg", "v1"))
        self.assertEqual(self._staging_residue(), [])

    def test_enotempty_adopts_interim_entry(self):
        # H3 adopt branch: a competitor publishes between our top-check and our
        # rename -> ENOTEMPTY -> we adopt the (now complete) winner.
        src = _make_source(self.test_dir, "loser", "loser")
        version_dir = self.store.get_version_cache_dir("pkg", "v1")
        real_rename = os.rename

        def competitor_then_fail(staging, dst):
            # Only intercept the final publish (dst == version_dir); let
            # shutil.move's internal rename (dst == staging) proceed for real.
            if dst != version_dir:
                return real_rename(staging, dst)
            os.makedirs(dst)
            with open(os.path.join(dst, "winner.txt"), "w") as f:
                f.write("winner")
            raise OSError(errno.ENOTEMPTY, "directory not empty")

        with patch("ivpm.cache.os.rename", side_effect=competitor_then_fail):
            got = self.store.store_version("pkg", "v1", src)

        self.assertEqual(got, version_dir)
        self.assertTrue(os.path.isfile(os.path.join(version_dir, "winner.txt")))
        self.assertFalse(os.path.exists(src))
        self.assertEqual(self._staging_residue(), [])

    def test_stale_staging_leftover_not_nested(self):
        # H2: a leftover staging dir (crash / PID reuse) must not be nested into
        # by shutil.move; the uuid4 staging name sidesteps it entirely.
        pkg_dir = self.store.ensure_cache_dir("pkg")
        stale = os.path.join(pkg_dir, "v1.staging.deadbeef")
        os.makedirs(stale)
        with open(os.path.join(stale, "junk.txt"), "w") as f:
            f.write("junk")

        src = _make_source(self.test_dir, "real", "real-content")
        got = self.store.store_version("pkg", "v1", src)

        # The real entry is our content only — no nested staging junk.
        self.assertEqual(sorted(os.listdir(got)), ["content.txt"])


# --- 3.2 Presence semantics -------------------------------------------------

class TestPresenceSemantics(_StoreBase):
    def test_link_to_deps_rejects_vanished_entry(self):
        # Entry emptied (e.g. concurrent GC) between lookup and materialize.
        empty = self.store.get_version_cache_dir("pkg", "v1")
        os.makedirs(empty)
        with self.assertRaises(CacheStoreError):
            self.store.link_to_deps("pkg", "v1", self.deps_dir)

    def test_link_to_deps_ok_when_populated(self):
        version_dir = self.store.get_version_cache_dir("pkg", "v1")
        os.makedirs(version_dir)
        with open(os.path.join(version_dir, "f.txt"), "w") as f:
            f.write("c")
        link = self.store.link_to_deps("pkg", "v1", self.deps_dir)
        self.assertTrue(os.path.islink(link))


# --- 3.3 Real multi-process races -------------------------------------------

@unittest.skipUnless(hasattr(os, "fork"), "requires fork for the process race")
class TestMultiProcessRaces(_StoreBase):
    def test_t1_store_race_single_populated_entry(self):
        import multiprocessing
        n = 6
        barrier = multiprocessing.get_context("fork").Barrier(n)
        scratch = [os.path.join(self.test_dir, "w%d" % i) for i in range(n)]
        for s in scratch:
            os.makedirs(s)
        args = [(self.cache_dir, scratch[i], str(i), "IDENTICAL", barrier)
                for i in range(n)]
        _run_procs(_store_worker, args)

        self.assertTrue(self.store.has_version("pkg", "v1"))
        got = self.store.get_version_cache_dir("pkg", "v1")
        with open(os.path.join(got, "content.txt")) as f:
            self.assertEqual(f.read(), "IDENTICAL")
        self.assertEqual(self._staging_residue(), [])

    def test_t2_patch_variant_race_no_corruption(self):
        import multiprocessing
        # Seed a shared, populated base entry.
        base = _make_source(self.test_dir, "base", "base-content")
        self.store.store_version("pkg", "base1", base)

        n = 6
        eff = "base1+patch.abcdef0123456789"
        barrier = multiprocessing.get_context("fork").Barrier(n)
        args = [(self.cache_dir, self.deps_dir, "base1", eff, str(i),
                 "PATCHED", barrier) for i in range(n)]
        _run_procs(_variant_worker, args)

        # Exactly one populated variant entry; base intact; no staging residue
        # in the cache and no .patch_stage residue in deps_dir.
        self.assertTrue(self.store.has_version("pkg", eff))
        got = self.store.get_version_cache_dir("pkg", eff)
        self.assertEqual(sorted(os.listdir(got)), ["content.txt", "patched.txt"])
        self.assertTrue(self.store.has_version("pkg", "base1"))
        self.assertEqual(self._staging_residue(), [])
        self.assertEqual(
            [n for n in os.listdir(self.deps_dir) if n.startswith(".patch_")], [])


# --- cache-side staging (same-FS publish) -----------------------------------

class TestCacheSideStaging(_StoreBase):
    def test_new_staging_is_on_cache_fs(self):
        staging = self.store.new_staging("pkg")
        # Sibling of the package's version dirs, not yet created, marked.
        self.assertEqual(os.path.dirname(staging), self._pkg_dir("pkg"))
        self.assertFalse(os.path.exists(staging))
        self.assertIn(".staging.", os.path.basename(staging))

    def test_store_from_new_staging_publishes(self):
        staging = self.store.new_staging("pkg")
        os.makedirs(staging)
        with open(os.path.join(staging, "f.txt"), "w") as f:
            f.write("built")
        got = self.store.store_version("pkg", "v1", staging)
        self.assertTrue(self.store.has_version("pkg", "v1"))
        self.assertFalse(os.path.exists(staging))          # consumed by the move
        with open(os.path.join(got, "f.txt")) as f:
            self.assertEqual(f.read(), "built")
        self.assertEqual(self._staging_residue(), [])

    def test_get_cache_info_skips_staging(self):
        # A populated real entry plus an in-flight staging sibling.
        real = self.store.get_version_cache_dir("pkg", "v1")
        os.makedirs(real)
        with open(os.path.join(real, "f.txt"), "w") as f:
            f.write("c")
        staging = self.store.new_staging("pkg")
        os.makedirs(staging)
        with open(os.path.join(staging, "junk.txt"), "w") as f:
            f.write("junk")

        info = self.store.get_cache_info()
        versions = [v["version"] for p in info["packages"] for v in p["versions"]]
        self.assertIn("v1", versions)
        self.assertTrue(all(".staging." not in v for v in versions))


# --- 3.4 Crash recovery & sweep ---------------------------------------------

class TestCrashRecoverySweep(_StoreBase):
    def test_old_staging_swept_on_ensure(self):
        pkg_dir = self.store.ensure_cache_dir("pkg")
        stale = os.path.join(pkg_dir, "v1.staging.oldhex")
        os.makedirs(stale)
        # ctime cannot be backdated from a test, so push the staleness cutoff
        # into the future instead (negative age => cutoff = now + age).
        self.store._STALE_STAGING_AGE_S = -2
        self.store.ensure_cache_dir("pkg")          # triggers opportunistic sweep
        self.assertFalse(os.path.exists(stale))

    def test_recent_staging_not_swept(self):
        pkg_dir = self.store.ensure_cache_dir("pkg")
        fresh = os.path.join(pkg_dir, "v1.staging.freshhex")
        os.makedirs(fresh)                            # mtime = now
        self.store.ensure_cache_dir("pkg")
        self.assertTrue(os.path.exists(fresh))       # a live build must survive

    def test_clean_removes_empty_version_dir(self):
        empty = self.store.get_version_cache_dir("pkg", "v1")
        os.makedirs(empty)
        self.store.clean_older_than(7)
        self.assertFalse(os.path.exists(empty))
        self.assertFalse(self.store.has_version("pkg", "v1"))


if __name__ == "__main__":
    unittest.main()

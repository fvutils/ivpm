"""Cache integrity tests.

Companion to cache-integrity-review.md / cache-integrity-impl-plan.md.  Where
test_cache_concurrency.py covers the *publish* race (H1-H5), this file covers
the paths around it: fetch staging (C1/C2), garbage collection (C3/C9), and
cache-key soundness (K1).
"""
import errno
import os
import shutil
import stat
import tempfile
import unittest
from unittest.mock import patch

ROOTDIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRCDIR = os.path.join(ROOTDIR, 'src')

import sys
sys.path.insert(0, SRCDIR)

from ivpm.cache import DirectoryCacheStore, CacheStoreError
from ivpm.cache_provider import (
    CacheContext, DirectoryCacheProvider, NullCacheProvider,
    acquire_staging, staging_scratch,
)


def _content(entry_dir):
    """An entry's payload, excluding the cache's own seal file."""
    return sorted(n for n in os.listdir(entry_dir)
                  if n != DirectoryCacheStore._ENTRY_MANIFEST)


def _tree(root, files):
    for rel, content in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(content)
    return root


def _snapshot(root):
    """(path, is_dir, size, mode) for everything under root, order-independent."""
    out = set()
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            st = os.lstat(p)
            out.add((os.path.relpath(p, root), stat.S_ISDIR(st.st_mode),
                     st.st_size if not stat.S_ISDIR(st.st_mode) else 0,
                     stat.S_IMODE(st.st_mode)))
    return out


class _Base(unittest.TestCase):
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

    def _store_entry(self, pkg, version, files=None):
        src = os.path.join(self.test_dir, "src_%s_%s" % (pkg, version))
        os.makedirs(src)
        _tree(src, files or {"a.txt": "a"})
        return self.store.store_version(pkg, version, src)

    def _provider(self):
        ctx = CacheContext(root_name=None, root_version=None, root_dir=None,
                           deps_dir=self.deps_dir)
        return DirectoryCacheProvider(ctx, self.store)


class _Pkg:
    def __init__(self, name):
        self.name = name
        self.cache = True


# --- P0.1/P0.2: fetch staging -----------------------------------------------

class TestAcquireStaging(_Base):
    def test_staging_is_on_cache_fs(self):
        """A provider-backed staging path is inside the package's cache
        directory, so the subsequent store() is a same-filesystem rename, not
        a tree copy.

        One level down from the entry, not beside it: the tree is built inside
        a private parent so an in-flight fetch is not readable by other cache
        users.  What has to stay true is same-filesystem and not-yet-existing.
        """
        pkg = _Pkg("libX")
        staging = acquire_staging(self._provider(), pkg, self.deps_dir)
        private = os.path.dirname(staging)
        self.assertEqual(os.path.dirname(private),
                         self.store.get_package_cache_dir("libX"))
        self.assertFalse(os.path.exists(staging))

    def test_staging_parent_is_private(self):
        """An in-flight fetch is readable by nobody but its owner.

        Content lands with whatever modes the fetch created (umask), and is
        only brought under the entry's protection by the seal -- so until then
        it must not be reachable at all.
        """
        staging = acquire_staging(self._provider(), _Pkg("libX"), self.deps_dir)
        mode = stat.S_IMODE(os.stat(os.path.dirname(staging)).st_mode)
        self.assertEqual(mode & 0o077, 0,
                         "staging parent is 0o%o; group/other can reach it"
                         % mode)

    def test_staging_is_unique_per_call(self):
        """The C1 property: no two concurrent fetches of one package can ever
        be handed the same build path."""
        pkg = _Pkg("libX")
        prov = self._provider()
        paths = {acquire_staging(prov, pkg, self.deps_dir) for _ in range(50)}
        self.assertEqual(len(paths), 50)

    def test_staging_falls_back_when_cache_unwritable(self):
        os.makedirs(self.cache_dir)
        os.chmod(self.cache_dir, 0o555)
        try:
            pkg = _Pkg("libX")
            staging = acquire_staging(self._provider(), pkg, self.deps_dir)
        finally:
            os.chmod(self.cache_dir, 0o755)
        private = os.path.dirname(staging)
        self.assertEqual(os.path.dirname(private), self.deps_dir)
        self.assertIn(".ivpm-fetch.libX.", os.path.basename(private))
        self.assertFalse(os.path.exists(staging))
        self.assertEqual(stat.S_IMODE(os.stat(private).st_mode) & 0o077, 0)

    def test_staging_falls_back_for_provider_without_staging(self):
        staging = acquire_staging(NullCacheProvider(None), _Pkg("libX"),
                                  self.deps_dir)
        self.assertEqual(os.path.dirname(os.path.dirname(staging)),
                         self.deps_dir)

    def test_scratch_is_outside_staging_and_marked_transient(self):
        """The download scratch must not be inside the tree that gets published,
        and must be invisible to cache scans / reclaimable by the sweep."""
        staging = acquire_staging(self._provider(), _Pkg("libX"), self.deps_dir)
        dl = staging_scratch(staging)
        self.assertFalse(dl.startswith(staging + os.sep))
        self.assertTrue(self.store._is_transient(os.path.basename(dl)))


# --- P0.3: rename-then-delete GC --------------------------------------------

class TestEviction(_Base):
    def test_evict_removes_from_view(self):
        self._store_entry("libX", "v1")
        self.assertTrue(self.store.has_version("libX", "v1"))
        self.assertTrue(self.store._evict("libX", "v1"))
        self.assertFalse(self.store.has_version("libX", "v1"))
        self.assertFalse(os.path.exists(
            self.store._meta_path("libX", "v1")))

    def test_evict_of_absent_entry_is_false(self):
        self.store.ensure_cache_dir("libX")
        self.assertFalse(self.store._evict("libX", "nope"))

    def test_interrupted_gc_leaves_no_phantom_hit(self):
        """C3, the headline regression.

        A partial delete used to leave a non-empty fragment at the entry path,
        which _is_populated reports as a HIT *forever* -- a permanently
        truncated package.  With rename-then-delete the entry leaves view
        before any byte is removed, so a failed delete can only leave inert
        tombstone residue.
        """
        self._store_entry("libX", "v1", {"a.txt": "a", "sub/b.txt": "b"})

        def partial_rmtree(path, *a, **kw):
            # Delete one file and give up -- exactly what rmtree(
            # ignore_errors=True) does when it hits a file it cannot unlink
            # partway through a tree.  This is the worst case for the old
            # in-place delete.
            for dirpath, _dirnames, filenames in os.walk(path):
                for f in filenames:
                    os.chmod(os.path.join(dirpath, f), 0o644)
                    os.unlink(os.path.join(dirpath, f))
                    return

        with patch("ivpm.cache.shutil.rmtree", side_effect=partial_rmtree):
            self.assertTrue(self.store._evict("libX", "v1"))

        # The entry is gone from view even though its bytes are not.
        self.assertFalse(self.store.has_version("libX", "v1"))
        pkg_dir = self.store.get_package_cache_dir("libX")
        residue = [n for n in os.listdir(pkg_dir) if os.path.isdir(
            os.path.join(pkg_dir, n))]
        self.assertTrue(residue, "expected tombstone residue")
        for name in residue:
            self.assertTrue(self.store._is_transient(name),
                            "residue %r poses as a version directory" % name)

    def test_clean_evicts_expired_entries(self):
        self._store_entry("libX", "old")
        self._store_entry("libX", "new")
        old_dir = self.store.get_version_cache_dir("libX", "old")
        ancient = 1000.0
        os.utime(old_dir, (ancient, ancient))
        self.store._write_meta("libX", "old", {
            "schema": 1, "stored": ancient, "last_linked": ancient})

        removed = self.store.clean_older_than(days=1)
        self.assertEqual(removed, 1)
        self.assertFalse(self.store.has_version("libX", "old"))
        self.assertTrue(self.store.has_version("libX", "new"))

    def test_clean_does_not_remove_package_dir(self):
        """C9: dropping <cache>/<pkg>/ races a concurrent builder that has just
        called ensure_cache_dir() into a spurious CacheStoreError."""
        self._store_entry("libX", "old")
        os.utime(self.store.get_version_cache_dir("libX", "old"), (1000.0, 1000.0))
        self.store._write_meta("libX", "old", {
            "schema": 1, "stored": 1000.0, "last_linked": 1000.0})
        self.store.clean_older_than(days=1)
        self.assertTrue(os.path.isdir(self.store.get_package_cache_dir("libX")))

    def test_clean_uses_rmdir_for_empty_version_dir(self):
        """A builder that publishes into an 'empty' version dir between the
        check and the delete must win: rmdir fails ENOTEMPTY, rmtree did not."""
        pkg_dir = self.store.ensure_cache_dir("libX")
        empty = os.path.join(pkg_dir, "v1")
        os.makedirs(empty)

        real_rmdir = os.rmdir

        def racing_rmdir(path):
            # Simulate a concurrent publish landing just before the delete.
            if os.path.basename(path) == "v1":
                with open(os.path.join(path, "content.txt"), "w") as f:
                    f.write("published")
            return real_rmdir(path)

        with patch("ivpm.cache.os.rmdir", side_effect=racing_rmdir):
            self.store.clean_older_than(days=30)

        self.assertTrue(self.store.has_version("libX", "v1"),
                        "GC destroyed a freshly published entry")

    def test_tombstones_are_invisible(self):
        self._store_entry("libX", "v1")
        pkg_dir = self.store.get_package_cache_dir("libX")
        tomb = os.path.join(pkg_dir, self.store._TOMB_MARKER + "deadbeef")
        os.makedirs(tomb)
        with open(os.path.join(tomb, "leftover.txt"), "w") as f:
            f.write("x")

        info = self.store.get_cache_info()
        versions = [v["version"] for p in info["packages"] for v in p["versions"]]
        self.assertEqual(versions, ["v1"])

        # Not treated as an expired entry by GC either.
        os.utime(tomb, (1000.0, 1000.0))
        self.assertEqual(self.store.clean_older_than(days=1), 0)

    def test_stale_tombstone_is_swept(self):
        pkg_dir = self.store.ensure_cache_dir("libX")
        tomb = os.path.join(pkg_dir, self.store._TOMB_MARKER + "deadbeef")
        os.makedirs(tomb)
        with open(os.path.join(tomb, "leftover.txt"), "w") as f:
            f.write("x")
        # ctime cannot be back-dated, and staleness keys on max(mtime, ctime),
        # so advance the clock instead of aging the directory.
        import time as _time
        with patch("ivpm.cache.time.time",
                   return_value=_time.time() + 2 * 60 * 60 + 60):
            self.store._sweep_stale_tombs(pkg_dir)
        self.assertFalse(os.path.exists(tomb))

    def test_recent_tombstone_not_swept(self):
        pkg_dir = self.store.ensure_cache_dir("libX")
        tomb = os.path.join(pkg_dir, self.store._TOMB_MARKER + "deadbeef")
        os.makedirs(tomb)
        self.store._sweep_stale_tombs(pkg_dir)
        self.assertTrue(os.path.isdir(tomb))


# --- P0.5 / §0.2: publish robustness ----------------------------------------

class TestPublishGuards(_Base):
    def test_store_retries_vanished_pkg_dir(self):
        """C9's other half: a GC that removed <cache>/<pkg>/ between
        ensure_cache_dir() and the move is recoverable, not a store failure."""
        src = os.path.join(self.test_dir, "src")
        os.makedirs(src)
        _tree(src, {"a.txt": "a"})

        real_transfer = DirectoryCacheStore._transfer
        state = {"n": 0}

        def vanishing_transfer(store, s, d, policy):
            state["n"] += 1
            if state["n"] == 1:
                shutil.rmtree(self.store.get_package_cache_dir("libX"),
                              ignore_errors=True)
                raise OSError(errno.ENOENT, "no such file or directory")
            return real_transfer(store, s, d, policy)

        with patch.object(DirectoryCacheStore, "_transfer",
                          autospec=True, side_effect=vanishing_transfer):
            self.store.store_version("libX", "v1", src)

        self.assertEqual(state["n"], 2)
        self.assertTrue(self.store.has_version("libX", "v1"))

    def test_store_does_not_retry_forever(self):
        src = os.path.join(self.test_dir, "src")
        os.makedirs(src)
        _tree(src, {"a.txt": "a"})
        with patch.object(DirectoryCacheStore, "_transfer",
                          side_effect=OSError(errno.ENOENT, "gone")) as mv:
            with self.assertRaises(CacheStoreError):
                self.store.store_version("libX", "v1", src)
        self.assertEqual(mv.call_count, 2)

    def test_non_sibling_staging_is_refused(self):
        """The atomic-publish invariant is enforced unconditionally, not by an
        assert that vanishes under python -O."""
        with self.assertRaises(CacheStoreError):
            DirectoryCacheStore._check_sibling(
                "libX", "v1", "/tmp/elsewhere/build", "/cache/libX/v1")
        # The real pairing is accepted.
        DirectoryCacheStore._check_sibling(
            "libX", "v1", "/cache/libX/v1.staging.abc", "/cache/libX/v1")

    def test_sibling_guard_survives_optimized_python(self):
        """Guards the specific failure mode: `python -O` strips `assert`."""
        import subprocess
        code = (
            "import sys; sys.path.insert(0, %r)\n"
            "from ivpm.cache import DirectoryCacheStore, CacheStoreError\n"
            "try:\n"
            "    DirectoryCacheStore._check_sibling('p','v','/a/s','/b/v')\n"
            "except CacheStoreError:\n"
            "    print('raised')\n" % SRCDIR)
        out = subprocess.run([sys.executable, "-O", "-c", code],
                             capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "raised", out.stderr)


# --- K1: cache-key soundness -------------------------------------------------

class TestHttpVersionKey(unittest.TestCase):
    def _pkg(self):
        from ivpm.pkg_types.package_http import PackageHttp
        pkg = PackageHttp("libX")
        pkg.url = "https://example.com/a.tar.gz"
        return pkg

    def test_no_validator_yields_no_version(self):
        """K1: md5(url) is a permanently immutable key for mutable content."""
        pkg = self._pkg()

        class _Resp:
            headers = {}

        with patch("ivpm.pkg_types.package_http.httpx.head", return_value=_Resp()):
            self.assertIsNone(pkg._get_url_version(pkg.url))
            self.assertIsNone(pkg._cache_version())
        self.assertIn("neither a Last-Modified nor an ETag",
                      pkg._no_version_reason())

    def test_failed_head_yields_no_version_and_a_distinct_reason(self):
        pkg = self._pkg()
        with patch("ivpm.pkg_types.package_http.httpx.head",
                   side_effect=RuntimeError("connection reset")):
            self.assertIsNone(pkg._get_url_version(pkg.url))
        reason = pkg._no_version_reason()
        self.assertIn("HEAD request", reason)
        self.assertIn("connection reset", reason)

    def test_last_modified_still_keys_the_entry(self):
        pkg = self._pkg()

        class _Resp:
            headers = {"Last-Modified": "Wed, 21 Oct 2015 07:28:00 GMT"}

        with patch("ivpm.pkg_types.package_http.httpx.head", return_value=_Resp()):
            v = pkg._cache_version()
        # The validator names the version; the URL digest names the source.
        self.assertTrue(v.startswith("Wed_21_Oct_2015_07-28-00_GMT_"), v)

    def test_etag_still_keys_the_entry(self):
        pkg = self._pkg()

        class _Resp:
            headers = {"ETag": 'W/"abc123"'}

        with patch("ivpm.pkg_types.package_http.httpx.head", return_value=_Resp()):
            self.assertTrue(pkg._cache_version().startswith("abc123_"))

    def test_two_urls_sharing_a_validator_do_not_share_a_key(self):
        """K2: an ETag says nothing about which URL produced the bytes, so two
        same-named packages on different servers must not collide."""
        a, b = self._pkg(), self._pkg()
        b.url = "https://elsewhere.example/a.tar.gz"

        class _Resp:
            headers = {"ETag": 'W/"abc123"'}

        with patch("ivpm.pkg_types.package_http.httpx.head", return_value=_Resp()):
            self.assertNotEqual(a._cache_version(), b._cache_version())


# --- K3: names and version keys are path components --------------------------

class TestPathComponentSafety(unittest.TestCase):
    """A package name and a version key are joined straight into a filesystem
    path. Nothing checked either of them."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.test_dir, True)
        self.cache_dir = os.path.join(self.test_dir, "cache")
        self.store = DirectoryCacheStore(self.cache_dir)

    def _source(self, content="c"):
        src = os.path.join(self.test_dir, "src_%d" % id(content))
        os.makedirs(src, exist_ok=True)
        with open(os.path.join(src, "f.txt"), "w") as f:
            f.write(content)
        return src

    def test_a_traversing_package_name_is_refused(self):
        for bad in ("..", ".", "../../etc", "a/b", "", "a\0b"):
            with self.subTest(name=bad):
                with self.assertRaises(ValueError):
                    self.store.get_package_cache_dir(bad)

    def test_an_ordinary_name_is_untouched(self):
        for good in ("libX", "my.pkg", "a_b-c", "v1+2"):
            with self.subTest(name=good):
                self.assertEqual(
                    self.store.get_package_cache_dir(good),
                    os.path.join(self.cache_dir, good))

    def test_a_slash_in_a_version_key_stays_one_directory(self):
        # An ETag may legally contain '/'.  It used to produce
        # <cache>/<pkg>/abc/def -- ENOENT on publish, and a cache listing that
        # reported 'abc' as a version.
        entry = self.store.store_version("libX", 'W/"abc/def"', self._source())
        self.assertEqual(os.path.dirname(entry),
                         self.store.get_package_cache_dir("libX"))
        self.assertTrue(self.store.has_version("libX", 'W/"abc/def"'))
        self.assertEqual(
            [n for n in os.listdir(self.store.get_package_cache_dir("libX"))
             if not n.endswith(".meta.json")],
            [os.path.basename(entry)])

    def test_an_escaped_entry_verifies_as_itself(self):
        # The manifest records the escaped key, and both a lookup (raw version)
        # and a cache scan (version read off the directory name) must agree
        # with it -- otherwise an escaped entry reads as a key collision.
        import ivpm.cache_verify as cv
        self.store.store_version("libX", 'W/"abc/def"', self._source())
        self.assertTrue(
            cv.verify_entry(self.store, "libX", 'W/"abc/def"',
                            cv.LEVEL_SHAPE).ok)
        self.assertTrue(cv.verify_cache(self.store, cv.LEVEL_SHAPE).ok)

    def test_the_sidecar_is_the_escaped_entrys_sibling(self):
        entry = self.store.store_version("libX", 'W/"abc/def"', self._source())
        self.store.touch_linked("libX", 'W/"abc/def"')
        self.assertTrue(os.path.isfile(entry + ".meta.json"))
        self.assertIsNotNone(self.store._read_meta("libX", 'W/"abc/def"'))

    def test_an_already_safe_key_is_byte_identical(self):
        # The guard against re-keying every existing entry: escaping must be a
        # no-op for every key that works today.
        from ivpm.utils import safe_version_key
        for k in ("abc123", "Wed_21_Oct_2015_07-28-00_GMT",
                  "v1.2.3_9f2c1ab34de0", "base1+patch"):
            self.assertEqual(safe_version_key(k), k)

    def test_escaping_is_idempotent(self):
        # A lookup arrives with the raw version; a cache scan reads the
        # already-escaped name off the directory. Both go through this
        # function, so escaping twice must equal escaping once -- otherwise
        # every escaped entry reports as a key collision against itself.
        from ivpm.utils import safe_version_key
        for k in ('W/"abc/def"', "a b", "..", ".", "v1:2", "naïve"):
            once = safe_version_key(k)
            with self.subTest(key=k):
                self.assertEqual(safe_version_key(once), once)


class TestConcurrentSidecarWrites(unittest.TestCase):
    """C8: the sidecar temp name used the PID. Worker threads share one, and
    PID namespaces make two containers on one NFS cache collide routinely --
    two writers then interleave their JSON into a single file and the rename
    publishes it."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.test_dir, True)
        self.store = DirectoryCacheStore(os.path.join(self.test_dir, "cache"))
        src = os.path.join(self.test_dir, "src")
        os.makedirs(src)
        with open(os.path.join(src, "f.txt"), "w") as f:
            f.write("c")
        self.store.store_version("libX", "v1", src)

    def test_parallel_touches_leave_one_parseable_sidecar(self):
        import json
        import threading
        barrier = threading.Barrier(8)

        def touch():
            barrier.wait()
            for _ in range(20):
                self.store.touch_linked("libX", "v1")

        threads = [threading.Thread(target=touch) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)

        pkg_dir = self.store.get_package_cache_dir("libX")
        self.assertEqual([n for n in os.listdir(pkg_dir) if ".tmp." in n], [],
                         "a temp sidecar was left behind")
        with open(self.store._meta_path("libX", "v1")) as fp:
            meta = json.load(fp)        # raises if two writers interleaved
        self.assertIn("last_linked", meta)


# --- C2: parallel fetches with colliding artifact names ----------------------

class _FakeUpdateInfo:
    def __init__(self, deps_dir, provider):
        self.deps_dir = deps_dir
        self._provider = provider
        self.deps_source = None
        self.hits = self.misses = self.unconfigured = 0
        self._load_planner = None

    def get_cache_provider(self):
        return self._provider

    def get_load_planner(self):
        if self._load_planner is None:
            from ivpm.load_plan import LoadPlanner
            self._load_planner = LoadPlanner(self.deps_dir, None)
        return self._load_planner

    def report_package(self, cacheable=False, editable=False):
        pass

    def report_cache_hit(self):
        self.hits += 1

    def report_cache_miss(self):
        self.misses += 1

    def report_cache_unconfigured(self):
        self.unconfigured += 1


class TestParallelArtifactNames(_Base):
    """C2: two packages whose URLs end in the same filename.

    The download target used to be ``deps_dir/.download/<basename>`` (HTTP) and
    ``deps_dir/<asset name>`` (gh-release) — keyed on the *URL basename*, so two
    different packages that both ship a ``main.zip`` shared one download path.
    Two worker threads racing there could unpack one package from the other's
    bytes, and the wrong content would then be published under a *correct*
    cache key, where verification would never look again.
    """

    def _tarball(self, name, content):
        import tarfile
        srcdir = os.path.join(self.test_dir, "srctree_" + name)
        os.makedirs(os.path.join(srcdir, "wrapper"))
        with open(os.path.join(srcdir, "wrapper", "marker.txt"), "w") as f:
            f.write(content)
        tgz = os.path.join(self.test_dir, "%s_main.tar.gz" % name)
        with tarfile.open(tgz, "w:gz") as tf:
            tf.add(os.path.join(srcdir, "wrapper"), arcname="wrapper")
        return tgz

    def _pkg(self, name, tarball, version):
        from ivpm.pkg_types.package_http import PackageHttp
        pkg = PackageHttp(name)
        # Same basename, different package -- the collision this test is about.
        pkg.url = "http://example.invalid/%s/main.tar.gz" % name
        pkg.src_type = ".tar.gz"
        pkg.unpack = True
        pkg.cache = True
        pkg.patches = []
        pkg._get_url_version = lambda url, _v=version: _v
        # A deliberately slow, interleaved write: a shared download path makes
        # the two packages' bytes overlap in time, which is the actual bug.
        def _dl(url, dest, _t=tarball):
            import time as _time
            with open(_t, "rb") as src, open(dest, "wb") as out:
                data = src.read()
                out.write(data[:len(data) // 2])
                out.flush()
                _time.sleep(0.05)
                out.write(data[len(data) // 2:])
        pkg._download_file = _dl
        return pkg

    def test_same_basename_artifacts_parallel_threads(self):
        import threading
        provider = self._provider()
        pkgs = [self._pkg("libA", self._tarball("libA", "A"), "va"),
                self._pkg("libB", self._tarball("libB", "B"), "vb")]

        errors = []

        def run(pkg):
            try:
                pkg.update(_FakeUpdateInfo(self.deps_dir, provider))
            except BaseException as e:      # noqa: BLE001 - surfaced below
                errors.append(e)

        threads = [threading.Thread(target=run, args=(p,)) for p in pkgs]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)

        self.assertEqual(errors, [])
        for name, expected in (("libA", "A"), ("libB", "B")):
            marker = os.path.join(self.deps_dir, name, "marker.txt")
            self.assertTrue(os.path.isfile(marker), "%s not materialized" % name)
            with open(marker) as f:
                self.assertEqual(f.read(), expected,
                                 "%s was built from the other package's bytes" % name)

    def test_no_download_residue_in_deps_dir(self):
        pkg = self._pkg("libA", self._tarball("libA", "A"), "va")
        pkg.update(_FakeUpdateInfo(self.deps_dir, self._provider()))
        self.assertEqual(
            [n for n in os.listdir(self.deps_dir) if n.startswith(".")], [],
            "cache fetch left scratch state in the deps dir")
        # ...nor is the archive published into the entry.
        entry = self.store.get_version_cache_dir("libA", pkg._cache_version())
        self.assertEqual(_content(entry), ["marker.txt"])

    def test_failed_download_leaves_no_staging(self):
        pkg = self._pkg("libA", self._tarball("libA", "A"), "va")

        def boom(url, dest):
            with open(dest, "wb") as f:
                f.write(b"partial")
            raise RuntimeError("connection reset")

        pkg._download_file = boom
        with self.assertRaises(RuntimeError):
            pkg.update(_FakeUpdateInfo(self.deps_dir, self._provider()))

        self.assertFalse(self.store.has_version("libA", "va"))
        pkg_dir = self.store.get_package_cache_dir("libA")
        self.assertEqual(os.listdir(pkg_dir) if os.path.isdir(pkg_dir) else [], [])
        self.assertEqual([n for n in os.listdir(self.deps_dir)
                          if n.startswith(".")], [])


# --- P0.6: unpack=false is not cacheable -------------------------------------

class TestUnpackFalseNotCacheable(_Base):
    def test_file_entry_is_never_a_hit(self):
        """The property that made unpack=false + cache=true fail every run: a
        cache entry must be a directory."""
        src = os.path.join(self.test_dir, "afile")
        with open(src, "w") as f:
            f.write("bytes")
        with self.assertRaises(CacheStoreError):
            self.store.store_version("libX", "v1", src)

    def test_update_falls_back_to_readonly_download(self):
        from ivpm.pkg_types.package_http import PackageHttp
        pkg = PackageHttp("libX")
        pkg.url = "http://example.invalid/tool.bin"
        pkg.unpack = False
        pkg.cache = True
        pkg.patches = []
        pkg._get_url_version = lambda url: "v1"
        pkg._download_file = lambda url, dest: open(dest, "w").write("bytes")

        ui = _FakeUpdateInfo(self.deps_dir, self._provider())
        pkg.update(ui)

        self.assertEqual(ui.unconfigured, 1)
        self.assertEqual(ui.hits + ui.misses, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.deps_dir, "libX")))
        self.assertFalse(self.store.has_version("libX", "v1"))


# --- P1.1: the entry manifest ------------------------------------------------

class TestEntryManifest(_Base):
    def test_manifest_written_and_describes_the_entry(self):
        path = self._store_entry("libX", "v1", {"a.txt": "aa", "sub/b.txt": "bbb"})
        m = self.store.read_entry_manifest(path)
        self.assertIsNotNone(m)
        self.assertEqual(m["package"], "libX")
        self.assertEqual(m["version"], "v1")
        self.assertEqual(m["schema"], self.store._ENTRY_SCHEMA)
        self.assertEqual(m["content"]["files"], 2)
        self.assertEqual(m["content"]["dirs"], 1)
        self.assertEqual(m["content"]["bytes"], 5)
        self.assertIsNone(m["content"]["merkle"])
        self.assertIn("ivpm", m["creator"])

    def test_manifest_exists_before_the_entry_is_visible(self):
        """The seal must precede the publish: there must be no instant at which
        a concurrent reader can resolve an unsealed entry."""
        seen = {}
        real_rename = os.rename

        def watching_rename(src, dst):
            if str(dst).endswith(os.sep + "v1"):
                seen["manifest"] = os.path.isfile(
                    os.path.join(src, self.store._ENTRY_MANIFEST))
                seen["readonly"] = not os.access(
                    os.path.join(src, "a.txt"), os.W_OK)
            return real_rename(src, dst)

        with patch("ivpm.cache.os.rename", side_effect=watching_rename):
            self._store_entry("libX", "v1", {"a.txt": "a"})

        self.assertTrue(seen.get("manifest"), "entry published before it was sealed")
        self.assertTrue(seen.get("readonly"), "entry published before it was locked")

    def test_manifest_does_not_count_itself(self):
        path = self._store_entry("libX", "v1", {"a.txt": "a"})
        m = self.store.read_entry_manifest(path)
        self.assertEqual(m["content"]["files"], 1)

    def test_manifest_is_read_only(self):
        path = self._store_entry("libX", "v1")
        self.assertFalse(os.access(self.store.entry_manifest_path(path), os.W_OK))

    def test_derived_entry_does_not_inherit_the_base_manifest(self):
        """A patched variant is built by copying a cached base, so it arrives
        carrying the base's manifest -- read-only, and describing the wrong
        version.  Left in place it would make every lookup of the variant a
        miss, forever."""
        base = self._store_entry("libX", "base1", {"a.txt": "a"})
        staging = self.store.new_staging("libX")
        # _copy_tree, not copytree: the production patch resolver re-opens the
        # copy for writing, because a cached base is sealed unwritable.
        from ivpm.patch import _copy_tree
        _copy_tree(base, staging)
        with open(os.path.join(staging, "patched.txt"), "w") as f:
            f.write("p")
        entry = self.store.store_version("libX", "base1+patch", staging)

        m = self.store.read_entry_manifest(entry)
        self.assertEqual(m["version"], "base1+patch")
        self.assertEqual(m["content"]["files"], 2)      # not 3 — no stale seal
        self.assertTrue(self.store.has_version("libX", "base1+patch"))

    def test_legacy_entry_is_a_hit_while_permitted(self):
        path = self._store_entry("libX", "v1")
        os.chmod(path, 0o755)
        os.remove(self.store.entry_manifest_path(path))
        self.assertTrue(self.store.has_version("libX", "v1"))

        self.store._legacy_entries_ok = False
        self.assertFalse(self.store.has_version("libX", "v1"))

    def test_symlinked_entry_is_never_a_hit(self):
        """isdir() follows symlinks, so an entry path that is an ln -s used to
        read as a HIT -- and a losing builder adopted it."""
        real = self._store_entry("libX", "real")
        pkg_dir = self.store.get_package_cache_dir("libX")
        link = os.path.join(pkg_dir, "v1")
        os.symlink(real, link)
        self.assertFalse(self.store.has_version("libX", "v1"))

        # ...and store_version does not adopt it either.
        src = os.path.join(self.test_dir, "src_new")
        os.makedirs(src)
        _tree(src, {"a.txt": "new"})
        with self.assertRaises(CacheStoreError):
            self.store.store_version("libX", "v1", src)

    def test_unwritable_cache_still_produces_a_usable_entry(self):
        """A manifest write that fails must degrade the entry to 'legacy', not
        fail the store outright."""
        src = os.path.join(self.test_dir, "src")
        os.makedirs(src)
        _tree(src, {"a.txt": "a"})
        with patch("ivpm.cache.DirectoryCacheStore._write_entry_manifest"):
            path = self.store.store_version("libX", "v1", src)
        self.assertTrue(self.store.has_version("libX", "v1"))
        self.assertIsNone(self.store.read_entry_manifest(path))


# --- P1.2: validating the manifest on a HIT ----------------------------------

class TestManifestOnHit(_Base):
    def _pkg(self, name="libX", **kw):
        pkg = _Pkg(name)
        pkg.src_type = "http"
        pkg.url = "https://example.com/a.tar.gz"
        for k, v in kw.items():
            setattr(pkg, k, v)
        return pkg

    def _rewrite_manifest(self, path, **changes):
        m = self.store.read_entry_manifest(path)
        m.update(changes)
        mp = self.store.entry_manifest_path(path)
        os.chmod(mp, 0o644)
        with open(mp, "w") as f:
            import json as _json
            _json.dump(m, f)

    def test_hit_when_manifest_agrees(self):
        pkg = self._pkg()
        prov = self._provider()
        src = os.path.join(self.test_dir, "src")
        os.makedirs(src)
        _tree(src, {"a.txt": "a"})
        prov.store(pkg, "v1", src)
        self.assertTrue(prov.lookup(pkg, "v1").is_hit)

    def test_relocated_entry_is_a_miss(self):
        path = self._store_entry("libX", "v1")
        self._rewrite_manifest(path, version="somethingelse")
        self.assertFalse(self._provider().lookup(self._pkg(), "v1").is_hit)

    def test_source_mismatch_is_a_miss_for_a_non_content_addressed_key(self):
        """K2: an ETag/Last-Modified key says nothing about *which* URL the
        bytes came from, so two packages can collide on it."""
        pkg = self._pkg()
        prov = self._provider()
        src = os.path.join(self.test_dir, "src")
        os.makedirs(src)
        _tree(src, {"a.txt": "a"})
        prov.store(pkg, "v1", src)

        other = self._pkg(url="https://elsewhere.example.com/a.tar.gz")
        self.assertFalse(prov.lookup(other, "v1").is_hit)

    def test_source_mismatch_is_tolerated_for_a_commit_key(self):
        """A commit hash IS a digest of the content, so two URLs resolving to
        it are mirrors of one repository -- not a collision.  Rejecting them
        would make every mirror user re-fetch on every run and never converge,
        since the entry keeps whichever URL published it first."""
        sha = "a" * 40
        pkg = self._pkg(name="libG", src_type="git",
                        url="https://github.com/o/r.git", resolved_commit=sha)
        prov = self._provider()
        src = os.path.join(self.test_dir, "src")
        os.makedirs(src)
        _tree(src, {"a.txt": "a"})
        prov.store(pkg, sha, src)

        mirror = self._pkg(name="libG", src_type="git",
                           url="https://mirror.example.org/o/r.git",
                           resolved_commit=sha)
        self.assertTrue(prov.lookup(mirror, sha).is_hit)

    def test_legacy_entry_without_a_source_block_still_hits(self):
        """Absence of evidence about an entry is not evidence against it --
        otherwise the upgrade invalidates every existing cache at once."""
        path = self._store_entry("libX", "v1")
        m = self.store.read_entry_manifest(path)
        m.pop("source", None)
        mp = self.store.entry_manifest_path(path)
        os.chmod(mp, 0o644)
        with open(mp, "w") as f:
            import json as _json
            _json.dump(m, f)
        self.assertTrue(self._provider().lookup(self._pkg(), "v1").is_hit)


if __name__ == "__main__":
    unittest.main()

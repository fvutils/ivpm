"""Cache verification, reporting, and repair.

Companion to cache-integrity-impl-plan.md P1.3 (verification tiers), P1.4
(error reporting) and P1.5 (``ivpm cache verify``, two modes).  Where
test_cache_integrity.py proves a bad entry cannot be *created*, this file
proves one that exists anyway is found, named, reported, and repaired.
"""
import builtins
import errno
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOTDIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRCDIR = os.path.join(ROOTDIR, 'src')
sys.path.insert(0, SRCDIR)

from ivpm import cache_verify as cv
from ivpm.cache import DirectoryCacheStore
from ivpm.cache_provider import CacheContext, DirectoryCacheProvider
from ivpm.diagnostics import Severity


def _tree(root, files):
    for rel, content in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(content)
    return root


class _ReadOnlyFilesystem:
    """Make every write under *root* fail with ``EROFS``, and record it.

    A snapshot proves the tree *looks* unchanged afterwards. This proves no
    write was ever attempted -- which is a different and stronger claim: it
    catches a write-then-restore, a chmod that happens to reinstate the same
    mode, and an ``open(..., 'w')`` on a file whose contents are rewritten
    identically. None of those move a snapshot.

    A real read-only mount would be the obvious way to get this, and it is not
    available: CI runs as uid 0 (which bypasses permission bits entirely), the
    job container has no ``CAP_SYS_ADMIN`` so ``mount --bind -o ro`` fails, and
    user namespaces are blocked so ``unshare -Urm`` fails too. Simulating the
    failure at the Python boundary works everywhere instead, and reports *which
    call* tried to write rather than an anonymous ``EROFS`` from inside a
    traceback.

    This is faithful because the surface is complete: the verify path is pure
    Python and shells out to nothing, so these entry points are the only way it
    could reach the filesystem.
    """

    _OS_WRITERS = ("chmod", "rename", "replace", "remove", "unlink", "mkdir",
                   "makedirs", "rmdir", "utime", "symlink", "link", "truncate")
    _WRITE_MODES = set("wax+")

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.attempts = []
        self._patches = []

    def _under(self, path):
        try:
            return os.path.abspath(os.fspath(path)).startswith(self.root)
        except (TypeError, ValueError):
            return False        # an fd, or something that is not a path

    def _refuse(self, what, path):
        self.attempts.append("%s(%s)" % (what, os.path.relpath(str(path), self.root)))
        raise OSError(errno.EROFS, "Read-only file system", str(path))

    def __enter__(self):
        real_os = {n: getattr(os, n) for n in self._OS_WRITERS}
        real_os_open, real_open, real_rmtree = os.open, builtins.open, shutil.rmtree

        def guard(name, real):
            def f(path, *a, **kw):
                if self._under(path):
                    self._refuse("os." + name, path)
                return real(path, *a, **kw)
            return f

        def guarded_open(file, mode="r", *a, **kw):
            if self._under(file) and (self._WRITE_MODES & set(mode)):
                self._refuse("open[%s]" % mode, file)
            return real_open(file, mode, *a, **kw)

        def guarded_os_open(path, flags, *a, **kw):
            writing = flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
            if self._under(path) and writing:
                self._refuse("os.open", path)
            return real_os_open(path, flags, *a, **kw)

        def guarded_rmtree(path, *a, **kw):
            if self._under(path):
                self._refuse("shutil.rmtree", path)
            return real_rmtree(path, *a, **kw)

        self._patches = [patch("builtins.open", guarded_open),
                         patch("shutil.rmtree", guarded_rmtree),
                         patch.object(os, "open", guarded_os_open)]
        self._patches += [patch.object(os, n, guard(n, real_os[n]))
                          for n in self._OS_WRITERS]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()
        return False


def _snapshot(root):
    """Everything observable about a tree: paths, kinds, sizes, modes, mtimes.

    Deliberately thorough -- a check-mode run that "only" refreshed a sidecar
    or swept one stale directory would still be a violation of the guarantee,
    and a coarse snapshot would not catch it.
    """
    out = set()
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            st = os.lstat(p)
            out.add((os.path.relpath(p, root), stat.S_ISDIR(st.st_mode),
                     0 if stat.S_ISDIR(st.st_mode) else st.st_size,
                     stat.S_IMODE(st.st_mode), st.st_mtime))
    return out


class _Pkg:
    def __init__(self, name, **kw):
        self.name = name
        self.cache = True
        for k, v in kw.items():
            setattr(self, k, v)


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

    def _store_entry(self, pkg="libX", version="v1", files=None, source=None):
        src = os.path.join(self.test_dir, "src_%s_%s" % (pkg, version))
        os.makedirs(src, exist_ok=True)
        _tree(src, files or {"a.txt": "a"})
        return self.store.store_version(pkg, version, src, source=source)

    def _legacy_entry(self, pkg="libX", version="v1", files=None):
        """A genuine pre-manifest entry: sealed exactly as an older IVPM left
        it, then stripped of the manifest.  Hand-building one with plain
        makedirs would also trip the seal checks, and the test would then be
        measuring its own fixture rather than the migration window."""
        path = self._store_entry(pkg, version, files)
        mp = self.store.entry_manifest_path(path)
        os.chmod(mp, 0o644)
        # The entry root is sealed unwritable, so the manifest cannot be
        # unlinked until it is re-opened -- and must be re-sealed afterwards,
        # or the fixture would carry a permissions finding of its own.
        os.chmod(path, 0o755)
        os.remove(mp)
        os.chmod(path, self.store._ENTRY_DIR_MODE)
        return path

    def _age_out(self):
        """Make everything on disk look old to the verifier.

        Staleness keys on max(mtime, ctime) and ctime cannot be back-dated, so
        aging a directory with utime does not work -- the clock has to move
        instead.
        """
        real = time.time
        return patch("ivpm.cache_verify.time.time",
                     lambda: real() + 365 * 24 * 3600)

    def _unseal(self, path):
        """Make an entry writable so a test can damage it."""
        os.chmod(path, 0o755)
        for root, dirs, files in os.walk(path):
            for d in dirs:
                os.chmod(os.path.join(root, d), 0o755)
            for f in files:
                os.chmod(os.path.join(root, f), 0o644)

    def _provider(self, session=None, level=None):
        ctx = CacheContext(root_name=None, root_version=None, root_dir=None,
                           deps_dir=self.deps_dir)
        prov = DirectoryCacheProvider(ctx, self.store, verify_level=level)
        prov.session = session
        return prov

    def _problems(self, result):
        return sorted(f.problem.value for f in result.findings)


# --- P1.3: the shape tier ---------------------------------------------------

class TestShapeTier(_Base):
    def test_shape_counts_match_the_seal_walk(self):
        """The load-bearing agreement: an entry sealed by the store verifies
        clean immediately afterwards.

        measure_tree and _make_readonly_and_measure must count node for node --
        same lstat rule, same symlink handling, same exclusion of the manifest.
        If they ever diverge, *every* entry fails its own shape check, which is
        the loudest possible way for a safety feature to become an outage.
        """
        src = os.path.join(self.test_dir, "src")
        _tree(src, {"a.txt": "aaa", "sub/b.txt": "bb", "sub/deep/c.bin": "cccc",
                    "sub/deep/empty": ""})
        os.makedirs(os.path.join(src, "emptydir"))
        os.symlink("a.txt", os.path.join(src, "link"))
        os.symlink("/nowhere/at/all", os.path.join(src, "broken"))
        path = self.store.store_version("libX", "v1", src)

        result = cv.verify_entry(self.store, "libX", "v1", cv.LEVEL_SHAPE)
        self.assertEqual(result.findings, [], self._problems(result))

        # And the census itself is the one the manifest recorded.
        manifest = self.store.read_entry_manifest(path)
        m = cv.measure_tree(path, skip_name=DirectoryCacheStore._ENTRY_MANIFEST)
        self.assertEqual(
            (m.files, m.dirs, m.bytes),
            (manifest["content"]["files"], manifest["content"]["dirs"],
             manifest["content"]["bytes"]))

    def test_a_symlink_contributes_no_bytes(self):
        """lstat, not stat: an entry's size is its own, never its links'
        targets -- otherwise a link to a large file outside the cache inflates
        the count and the entry fails verification for no reason."""
        src = os.path.join(self.test_dir, "src")
        _tree(src, {"a.txt": "12345"})
        os.symlink(os.path.abspath(__file__), os.path.join(src, "big"))
        path = self.store.store_version("libX", "v1", src)
        m = cv.measure_tree(path, skip_name=DirectoryCacheStore._ENTRY_MANIFEST)
        self.assertEqual(m.files, 2)
        self.assertEqual(m.bytes, 5)

    def test_a_removed_file_is_a_shape_mismatch(self):
        path = self._store_entry(files={"a.txt": "a", "b.txt": "bb"})
        self._unseal(path)
        os.remove(os.path.join(path, "b.txt"))
        result = cv.verify_entry(self.store, "libX", "v1", cv.LEVEL_SHAPE)
        self.assertEqual(self._problems(result), ["shape-mismatch"])
        self.assertEqual(result.findings[0].repair, cv.REPAIR_EVICT)
        self.assertIs(result.status, cv.Status.BROKEN)

    def test_an_added_file_is_a_shape_mismatch(self):
        path = self._store_entry()
        self._unseal(path)
        with open(os.path.join(path, "extra.txt"), "w") as f:
            f.write("x")
        result = cv.verify_entry(self.store, "libX", "v1", cv.LEVEL_SHAPE)
        self.assertEqual(self._problems(result), ["shape-mismatch"])

    def test_a_truncated_file_is_a_shape_mismatch(self):
        """The C1/C2/C3 signature: right names, wrong bytes-on-disk."""
        path = self._store_entry(files={"a.txt": "aaaaaaaaaa"})
        self._unseal(path)
        with open(os.path.join(path, "a.txt"), "w") as f:
            f.write("a")
        result = cv.verify_entry(self.store, "libX", "v1", cv.LEVEL_SHAPE)
        self.assertEqual(self._problems(result), ["shape-mismatch"])

    def test_an_empty_entry_is_reported(self):
        pkg_dir = self.store.ensure_cache_dir("libX")
        os.makedirs(os.path.join(pkg_dir, "v1"))
        result = cv.verify_entry(self.store, "libX", "v1", cv.LEVEL_SHAPE)
        self.assertEqual(self._problems(result), ["entry-empty"])

    def test_an_absent_entry_is_not_a_problem(self):
        self.store.ensure_cache_dir("libX")
        result = cv.verify_entry(self.store, "libX", "nope", cv.LEVEL_SHAPE)
        self.assertTrue(result.ok)
        self.assertEqual(result.entries_checked, 0)


# --- P1.3: the content tier -------------------------------------------------

class TestContentTier(_Base):
    def _store_with_merkle(self, files, version="v1"):
        with patch("ivpm.site_config.resolve_cache_verify_level",
                   return_value="content"):
            src = os.path.join(self.test_dir, "src_" + version)
            _tree(src, files)
            return self.store.store_version("libX", version, src)

    def test_same_size_edit_passes_shape_and_fails_content(self):
        """Precisely the gap the content tier exists to close: bit rot and
        tampering keep the shape intact."""
        path = self._store_with_merkle({"a.txt": "AAAA"})
        self._unseal(path)
        with open(os.path.join(path, "a.txt"), "w") as f:
            f.write("BBBB")
        # Re-lock the tree, so the only thing wrong with it is its *contents*.
        # This is also the realistic shape of bit rot and tampering: nothing
        # about the permissions looks disturbed.
        self.store._make_readonly(path)

        self.assertTrue(
            cv.verify_entry(self.store, "libX", "v1", cv.LEVEL_SHAPE).ok)
        result = cv.verify_entry(self.store, "libX", "v1", cv.LEVEL_CONTENT)
        self.assertEqual(self._problems(result), ["content-mismatch"])
        self.assertGreater(result.bytes_hashed, 0)

    def test_content_level_is_clean_on_an_untouched_entry(self):
        self._store_with_merkle({"a.txt": "AAAA", "sub/b.txt": "B"})
        result = cv.verify_entry(self.store, "libX", "v1", cv.LEVEL_CONTENT)
        self.assertEqual(result.findings, [], self._problems(result))

    def test_no_merkle_recorded_degrades_to_shape(self):
        """An entry published under 'shape' has no recorded hash.  Verifying it
        at 'content' must not invent a mismatch -- there is nothing to compare
        against, and reporting one would condemn every pre-existing entry the
        moment a site raised its verification level."""
        self._store_entry()
        result = cv.verify_entry(self.store, "libX", "v1", cv.LEVEL_CONTENT)
        self.assertTrue(result.ok, self._problems(result))
        self.assertEqual(result.bytes_hashed, 0)

    def test_merkle_is_only_recorded_at_content_level(self):
        path = self._store_entry()
        self.assertIsNone(
            self.store.read_entry_manifest(path)["content"]["merkle"])
        path2 = self._store_with_merkle({"a.txt": "A"}, version="v2")
        self.assertIsNotNone(
            self.store.read_entry_manifest(path2)["content"]["merkle"])

    def test_merkle_notices_a_rename_that_preserves_bytes(self):
        """A merkle over (path, kind, digest) rather than over content alone:
        moving a file changes the tree even though every byte survives."""
        src = os.path.join(self.test_dir, "a")
        _tree(src, {"one.txt": "same"})
        first = cv.entry_merkle(src)
        os.rename(os.path.join(src, "one.txt"), os.path.join(src, "two.txt"))
        self.assertNotEqual(first, cv.entry_merkle(src))


# --- P1.3: the 'off' tier ---------------------------------------------------

class TestOffTier(_Base):
    def test_off_skips_the_walk(self):
        path = self._store_entry(files={"a.txt": "a", "b.txt": "b"})
        self._unseal(path)
        os.remove(os.path.join(path, "b.txt"))
        self.assertTrue(cv.verify_entry(self.store, "libX", "v1", cv.LEVEL_OFF).ok)

    def test_off_still_catches_a_contradicted_entry(self):
        """Turning verification off is a statement about cost.  It is not a
        request to be handed bytes that say they are something else -- and the
        manifest check is one small file read, not a walk."""
        path = self._store_entry()
        mp = self.store.entry_manifest_path(path)
        os.chmod(mp, 0o644)
        m = self.store.read_entry_manifest(path)
        m["version"] = "some-other-version"
        with open(mp, "w") as f:
            json.dump(m, f)
        result = cv.verify_entry(self.store, "libX", "v1", cv.LEVEL_OFF)
        self.assertEqual(self._problems(result), ["manifest-mismatch"])

    def test_off_does_not_report_legacy_entries(self):
        self._legacy_entry("libX", "v1")
        self.assertTrue(cv.verify_entry(self.store, "libX", "v1", cv.LEVEL_OFF).ok)


# --- P1.3: level resolution -------------------------------------------------

class TestLevelResolution(unittest.TestCase):
    def setUp(self):
        from ivpm.site_config import reset_site_config
        self.addCleanup(reset_site_config)
        reset_site_config()
        self._env = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(),
                                 os.environ.update(self._env)))
        os.environ.pop("IVPM_CACHE_VERIFY", None)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.environ["IVPM_USER_CONFIG"] = os.path.join(self.tmp, "user.yaml")
        os.environ["IVPM_SITE_CONFIG"] = os.path.join(self.tmp, "site.yaml")

    def _resolve(self, override=None):
        from ivpm.site_config import reset_site_config, resolve_cache_verify_level
        reset_site_config()
        return resolve_cache_verify_level(override)

    def _write_user_config(self, text):
        with open(os.environ["IVPM_USER_CONFIG"], "w") as f:
            f.write(text)

    def test_default_is_shape(self):
        self.assertEqual(self._resolve(), "shape")

    def test_config_file_overrides_the_default(self):
        self._write_user_config("cache-verify: content\n")
        self.assertEqual(self._resolve(), "content")

    def test_env_overrides_the_config_file(self):
        self._write_user_config("cache-verify: content\n")
        os.environ["IVPM_CACHE_VERIFY"] = "off"
        self.assertEqual(self._resolve(), "off")

    def test_explicit_override_wins(self):
        os.environ["IVPM_CACHE_VERIFY"] = "off"
        self.assertEqual(self._resolve("content"), "content")

    def test_a_bad_value_is_ignored_not_fatal(self):
        """A typo in a site-wide config file must not make IVPM unusable for
        everyone who reads it; the fallback is the safe default."""
        os.environ["IVPM_CACHE_VERIFY"] = "paranoid"
        self.assertEqual(self._resolve(), "shape")


# --- P1.5 mode 1: check never mutates ---------------------------------------

class TestCheckIsNonMutating(_Base):
    def _messy_cache(self):
        """A cache in exactly the state that tempts an opportunistic sweep."""
        self._store_entry("libX", "v1")
        self._store_entry("libY", "v1")

        pkg_dir = self.store.get_package_cache_dir("libX")
        # a legacy (manifest-less) entry
        self._legacy_entry("libX", "legacy")
        # an empty entry
        os.makedirs(os.path.join(pkg_dir, "hollow"))
        # stale staging and a stale tombstone
        for name in ("build.staging.abc123", ".gc.def456"):
            _tree(os.path.join(pkg_dir, name), {"junk": "j"})
        # an orphan sidecar and an unparseable one
        with open(os.path.join(pkg_dir, "gone.meta.json"), "w") as f:
            f.write('{"schema": 1}')
        with open(os.path.join(pkg_dir, "v1.meta.json"), "w") as f:
            f.write("{not json")

    def test_check_changes_nothing(self):
        self._messy_cache()
        before = _snapshot(self.cache_dir)
        with self._age_out():
            result = cv.verify_cache(self.store)
        self.assertTrue(result.findings)        # it really did find things
        self.assertEqual(_snapshot(self.cache_dir), before)

    def test_check_refuses_to_be_asked_to_mutate(self):
        """Enforced by construction, not by discipline."""
        with self.assertRaises(ValueError):
            cv.verify_cache(self.store, mutate=True)

    def test_check_attempts_no_write_at_any_level(self):
        """The guarantee stated as "no write was attempted", not "the tree
        looks the same afterwards"."""
        self._messy_cache()
        for level in (cv.LEVEL_OFF, cv.LEVEL_SHAPE, cv.LEVEL_CONTENT):
            with self.subTest(level=level):
                with _ReadOnlyFilesystem(self.cache_dir) as ro:
                    with self._age_out():
                        result = cv.verify_cache(self.store, level)
                self.assertEqual(ro.attempts, [])
                if level != cv.LEVEL_OFF:
                    # ...and it still did its job while forbidden to write.
                    self.assertTrue(result.findings)

    def test_a_single_entry_check_attempts_no_write(self):
        # The update-time path, which runs on every cache hit -- the one place
        # a stray write would be paid for over and over.
        self._store_entry("libX", "v1")
        with _ReadOnlyFilesystem(self.cache_dir) as ro:
            cv.verify_entry(self.store, "libX", "v1", cv.LEVEL_CONTENT)
        self.assertEqual(ro.attempts, [])

    def test_the_readonly_guard_itself_has_teeth(self):
        """Without this, the two tests above could pass because the guard had
        quietly stopped intercepting anything."""
        self._store_entry("libX", "v1")
        with _ReadOnlyFilesystem(self.cache_dir) as ro:
            self.store._touch_last_linked("libX", "v1")   # a known writer
        self.assertTrue(ro.attempts, "the guard intercepted nothing")

    def test_check_does_not_touch_a_sidecar(self):
        """has_version -> touch_linked is the trap: a checker written casually
        against the store would refresh last_linked on every entry it looked
        at, silently resetting the GC clock for the whole cache."""
        self._store_entry("libX", "v1")
        self.store._touch_last_linked("libX", "v1")
        before = self.store._read_meta("libX", "v1")["last_linked"]
        time.sleep(0.01)
        cv.verify_cache(self.store)
        self.assertEqual(self.store._read_meta("libX", "v1")["last_linked"],
                         before)


# --- P1.3/P1.5: whole-cache scanning ----------------------------------------

class TestCacheScan(_Base):
    def test_a_clean_cache_is_healthy(self):
        self._store_entry("libX", "v1")
        self._store_entry("libY", "v2")
        result = cv.verify_cache(self.store)
        self.assertEqual(result.findings, [], self._problems(result))
        self.assertIs(result.status, cv.Status.HEALTHY)
        self.assertEqual((result.packages, result.entries, result.sealed), (2, 2, 2))

    def test_a_missing_cache_dir_is_empty_not_an_error(self):
        result = cv.verify_cache(DirectoryCacheStore(
            os.path.join(self.test_dir, "nope")))
        self.assertTrue(result.ok)
        self.assertEqual(result.entries, 0)

    def test_legacy_entries_are_info_and_still_healthy(self):
        """The migration window: hundreds of pre-manifest entries must not make
        a working cache report as damaged."""
        for i in range(5):
            self._legacy_entry("libX", "v%d" % i)
        result = cv.verify_cache(self.store)
        self.assertEqual(set(self._problems(result)), {"manifest-missing"})
        self.assertIs(result.status, cv.Status.HEALTHY)
        self.assertTrue(all(f.severity is Severity.INFO for f in result.findings))
        self.assertEqual(result.legacy, 5)

    def test_fresh_staging_is_not_reported(self):
        """A build in flight -- quite possibly another user's -- is not damage.
        Reporting it would make a busy cache look broken and invite --repair to
        delete a tree somebody is actively writing into."""
        pkg_dir = self.store.ensure_cache_dir("libX")
        _tree(os.path.join(pkg_dir, "build.staging.live"), {"partial": "p"})
        result = cv.verify_cache(self.store)
        self.assertEqual(result.findings, [], self._problems(result))

    def test_stale_staging_and_tombstones_are_residue(self):
        pkg_dir = self.store.ensure_cache_dir("libX")
        for name in ("build.staging.old", ".gc.old"):
            _tree(os.path.join(pkg_dir, name), {"junk": "j"})
        with self._age_out():
            result = cv.verify_cache(self.store)
        self.assertEqual(self._problems(result),
                         ["staging-residue", "tomb-residue"])
        self.assertTrue(all(f.repair == cv.REPAIR_REMOVE for f in result.findings))
        # Untidy, not incorrect: nothing here can be served to a consumer.
        self.assertIs(result.status, cv.Status.DEGRADED)
        self.assertGreater(result.reclaimable_bytes, 0)

    def test_an_orphan_sidecar_is_reported(self):
        self._store_entry("libX", "v1")
        pkg_dir = self.store.get_package_cache_dir("libX")
        with open(os.path.join(pkg_dir, "ghost.meta.json"), "w") as f:
            f.write('{"schema": 1}')
        result = cv.verify_cache(self.store)
        self.assertEqual(self._problems(result), ["sidecar-orphan"])

    def test_an_unparseable_sidecar_is_reported(self):
        self._store_entry("libX", "v1")
        pkg_dir = self.store.get_package_cache_dir("libX")
        with open(os.path.join(pkg_dir, "v1.meta.json"), "w") as f:
            f.write("{{{")
        result = cv.verify_cache(self.store)
        self.assertEqual(self._problems(result), ["sidecar-unparseable"])

    def test_a_symlinked_entry_is_broken_and_manual(self):
        """IVPM will not follow a hand-placed link into unverified content, and
        will not remove someone else's link either."""
        pkg_dir = self.store.ensure_cache_dir("libX")
        target = os.path.join(self.test_dir, "elsewhere")
        _tree(target, {"a.txt": "a"})
        os.symlink(target, os.path.join(pkg_dir, "v1"))
        result = cv.verify_cache(self.store)
        self.assertEqual(self._problems(result), ["entry-symlink"])
        self.assertEqual(result.findings[0].repair, cv.REPAIR_MANUAL)
        self.assertIs(result.status, cv.Status.BROKEN)

    def test_an_unrecognized_dot_directory_is_flagged(self):
        pkg_dir = self.store.ensure_cache_dir("libX")
        _tree(os.path.join(pkg_dir, ".mystery"), {"a": "a"})
        result = cv.verify_cache(self.store)
        self.assertEqual(self._problems(result), ["version-key-invalid"])

    def test_scanning_one_package_ignores_the_rest(self):
        self._store_entry("libX", "v1")
        bad = self._store_entry("libY", "v1")
        self._unseal(bad)
        os.remove(os.path.join(bad, "a.txt"))
        self.assertTrue(cv.verify_cache(self.store, package="libX").ok)
        self.assertFalse(cv.verify_cache(self.store, package="libY").ok)

    def test_seal_drift_is_degraded_not_broken(self):
        """A writable file in a shared entry is a real problem -- but it is a
        problem with the lock, not with the content, so it earns a reseal
        rather than throwing the bytes away."""
        path = self._store_entry()
        os.chmod(os.path.join(path, "a.txt"), 0o644)
        result = cv.verify_cache(self.store)
        self.assertEqual(self._problems(result), ["entry-writable"])
        self.assertEqual(result.findings[0].repair, cv.REPAIR_RESEAL)
        self.assertIs(result.status, cv.Status.DEGRADED)


# --- P1.5 mode 2: the repair loop -------------------------------------------

class TestRepairLoop(_Base):
    def test_repair_converges_and_is_idempotent(self):
        self._store_entry("libX", "v1")
        pkg_dir = self.store.get_package_cache_dir("libX")
        with open(os.path.join(pkg_dir, "ghost.meta.json"), "w") as f:
            f.write("{}")
        bad = self._store_entry("libY", "v1")
        self._unseal(bad)
        os.remove(os.path.join(bad, "a.txt"))

        report = cv.repair_cache(self.store)
        self.assertIs(report.outcome, cv.Outcome.CONVERGED)
        self.assertFalse(self.store.has_version("libY", "v1"))
        self.assertTrue(self.store.has_version("libX", "v1"))

        again = cv.repair_cache(self.store)
        self.assertIs(again.outcome, cv.Outcome.CONVERGED)
        self.assertEqual(again.repaired, 0)
        self.assertEqual(len(again.passes), 0)

    def test_reseal_fixes_drift_without_discarding_content(self):
        path = self._store_entry(files={"a.txt": "precious"})
        os.chmod(os.path.join(path, "a.txt"), 0o666)
        report = cv.repair_cache(self.store)
        self.assertIs(report.outcome, cv.Outcome.CONVERGED)
        self.assertTrue(self.store.has_version("libX", "v1"))
        with open(os.path.join(path, "a.txt")) as f:
            self.assertEqual(f.read(), "precious")

    def test_residue_is_removed(self):
        pkg_dir = self.store.ensure_cache_dir("libX")
        d = os.path.join(pkg_dir, ".gc.old")
        _tree(d, {"junk": "j"})
        with self._age_out():
            cv.repair_cache(self.store)
        self.assertFalse(os.path.exists(d))

    def test_backfill_requires_upgrade(self):
        """A legacy entry is not damage, and a plain --repair must not start
        stamping manifests onto content whose provenance nobody established."""
        entry = self._legacy_entry("libX", "v1")

        report = cv.repair_cache(self.store)
        self.assertIs(report.outcome, cv.Outcome.STALLED)
        self.assertFalse(os.path.isfile(self.store.entry_manifest_path(entry)))

        report = cv.repair_cache(self.store, upgrade=True)
        self.assertIs(report.outcome, cv.Outcome.CONVERGED)
        manifest = self.store.read_entry_manifest(entry)
        self.assertEqual(manifest["package"], "libX")
        self.assertEqual(manifest["version"], "v1")
        # A baseline, not a certificate: it records what is there now and
        # claims nothing about the past.
        self.assertNotIn("source", manifest)

    def test_dry_run_changes_nothing(self):
        bad = self._store_entry("libY", "v1")
        self._unseal(bad)
        os.remove(os.path.join(bad, "a.txt"))
        before = _snapshot(self.cache_dir)

        report = cv.repair_cache(self.store, dry_run=True)
        self.assertEqual(_snapshot(self.cache_dir), before)
        self.assertEqual(report.repaired, 1)
        self.assertEqual(len(report.passes), 1)
        self.assertTrue(report.dry_run)
        # It must not claim a convergence it never tested.
        self.assertIsNot(report.outcome, cv.Outcome.CONVERGED)

    def test_an_ineffective_repair_stalls_instead_of_spinning(self):
        """The attempted-once bound.  A reseal on a read-only mount 'succeeds'
        -- the chmod raises EACCES, which _make_readonly swallows by design --
        while changing nothing.  A pass-count bound alone would retry it until
        exhausted; attempted-once turns it into one honest attempt."""
        path = self._store_entry()
        os.chmod(os.path.join(path, "a.txt"), 0o666)
        with patch.object(DirectoryCacheStore, "_make_readonly",
                          lambda self, p: None):
            report = cv.repair_cache(self.store, max_passes=5)
        self.assertIs(report.outcome, cv.Outcome.STALLED)
        self.assertEqual(len(report.passes), 1)
        self.assertEqual(report.failed, 1)
        # And the surviving finding says a human has to look at it, rather
        # than reappearing in the final report looking untried and
        # auto-repairable -- which would tell the user to run the very command
        # that just gave up on it.
        self.assertTrue(report.final.findings)
        for f in report.final.findings:
            self.assertFalse(f.auto_repairable)
            self.assertTrue(f.attempted)
            self.assertIn("did not take effect", f.repair_error)

    def test_manual_findings_stall_rather_than_exhaust(self):
        pkg_dir = self.store.ensure_cache_dir("libX")
        target = os.path.join(self.test_dir, "elsewhere")
        _tree(target, {"a.txt": "a"})
        os.symlink(target, os.path.join(pkg_dir, "v1"))
        report = cv.repair_cache(self.store, max_passes=5)
        self.assertIs(report.outcome, cv.Outcome.STALLED)
        self.assertEqual(len(report.passes), 0)
        self.assertEqual(report.manual, 1)
        self.assertTrue(os.path.islink(os.path.join(pkg_dir, "v1")))

    def test_exhausted_when_damage_keeps_reappearing(self):
        """Something is actively re-corrupting the cache.  The loop must give
        up with a distinguishable outcome rather than run forever."""
        original = cv.verify_cache
        state = {"n": 0}

        def _always_broken(store, level=None, package=None, mutate=False):
            state["n"] += 1
            r = original(store, level, package=package)
            r.findings.append(cv.make_finding(
                cv.Problem.SHAPE_MISMATCH, "libX", "v%d" % state["n"],
                os.path.join(self.cache_dir, "libX", "v%d" % state["n"]),
                "synthetic"))
            return r

        with patch.object(cv, "verify_cache", _always_broken):
            report = cv.repair_cache(self.store, max_passes=3)
        self.assertIs(report.outcome, cv.Outcome.EXHAUSTED)
        self.assertEqual(len(report.passes), 3)

    def test_a_concurrent_republish_is_not_a_failed_repair(self):
        """A shared cache has other writers.  An entry evicted in pass 1 and
        legitimately rebuilt by a concurrent 'ivpm update' before pass 2 is the
        system working -- the loop must converge, not report a stall."""
        bad = self._store_entry("libX", "v1")
        self._unseal(bad)
        os.remove(os.path.join(bad, "a.txt"))

        real_evict = DirectoryCacheStore._evict

        def _evict_then_republish(store, package, version):
            ok = real_evict(store, package, version)
            if ok:
                src = os.path.join(self.test_dir, "republished")
                os.makedirs(src, exist_ok=True)
                _tree(src, {"a.txt": "fresh"})
                store.store_version(package, version, src)
            return ok

        with patch.object(DirectoryCacheStore, "_evict", _evict_then_republish):
            report = cv.repair_cache(self.store, max_passes=3)

        self.assertIs(report.outcome, cv.Outcome.CONVERGED)
        self.assertTrue(self.store.has_version("libX", "v1"))


# --- P1.4: reporting at update time -----------------------------------------

class _FakeSession:
    """The slice of ProjectUpdateInfo the provider reports through."""

    def __init__(self):
        self.perf = None
        self.cache_invalidated = 0
        self.cache_findings = []

    def report_cache_invalidated(self, finding=None):
        self.cache_invalidated += 1
        if finding is not None:
            self.cache_findings.append(finding)


class TestUpdateTimeVerification(_Base):
    def test_a_healthy_entry_still_hits(self):
        self._store_entry()
        prov = self._provider(_FakeSession())
        self.assertTrue(prov.lookup(_Pkg("libX"), "v1").is_hit)

    def test_a_corrupt_entry_misses_and_is_evicted(self):
        """verify-fail -> evict -> miss -> refetch -> publish.  The eviction is
        what makes an ordinary 'ivpm update' the repair mechanism: without it,
        store_version's already-populated fast path would hand the bad entry
        straight back."""
        path = self._store_entry(files={"a.txt": "a", "b.txt": "b"})
        self._unseal(path)
        os.remove(os.path.join(path, "b.txt"))

        session = _FakeSession()
        prov = self._provider(session)
        self.assertTrue(prov.lookup(_Pkg("libX"), "v1").is_miss)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(session.cache_invalidated, 1)
        self.assertEqual(session.cache_findings[0].problem,
                         cv.Problem.SHAPE_MISMATCH)

    def test_invalidation_is_not_counted_as_a_plain_miss(self):
        """Without its own counter, a systematically corrupt shared cache is
        indistinguishable from a cold one in every statistic IVPM reports."""
        from ivpm.project_ops_info import ProjectUpdateInfo
        info = ProjectUpdateInfo(args=None, deps_dir=self.deps_dir)
        info.report_cache_invalidated(cv.make_finding(
            cv.Problem.SHAPE_MISMATCH, "libX", "v1", "/p", "detail"))
        self.assertEqual(info.cache_invalidated, 1)
        self.assertEqual(info.cache_misses, 0)
        self.assertIn("failed verification", info.cache_health_summary())

    def test_auto_repair_is_bounded_to_once_per_entry_per_run(self):
        """An entry failing for an environmental reason must not turn into an
        evict/refetch loop that hammers the network."""
        path = self._store_entry(files={"a.txt": "a", "b.txt": "b"})
        self._unseal(path)
        os.remove(os.path.join(path, "b.txt"))

        session = _FakeSession()
        prov = self._provider(session)
        pkg = _Pkg("libX")
        self.assertTrue(prov.lookup(pkg, "v1").is_miss)

        # The rebuild lands and is corrupt again.
        path = self._store_entry(files={"a.txt": "a", "b.txt": "b"})
        self._unseal(path)
        os.remove(os.path.join(path, "b.txt"))

        evictions = []
        real_evict = DirectoryCacheStore._evict
        with patch.object(DirectoryCacheStore, "_evict",
                          lambda s, p, v: (evictions.append((p, v)),
                                           real_evict(s, p, v))[1]):
            self.assertTrue(prov.lookup(pkg, "v1").is_miss)
        self.assertEqual(evictions, [])
        self.assertEqual(session.cache_invalidated, 1)   # not 2

    def test_legacy_entries_report_once_not_once_per_entry(self):
        """A cache with hundreds of pre-manifest entries must produce one
        line, not hundreds."""
        for i in range(4):
            self._legacy_entry("libX", "v%d" % i)
        prov = self._provider(_FakeSession())
        notes = []
        with patch("ivpm.cache_provider.note", notes.append):
            for i in range(4):
                self.assertTrue(prov.lookup(_Pkg("libX"), "v%d" % i).is_hit)
        self.assertEqual(len(notes), 1)

    def test_a_source_collision_is_a_miss_and_names_both_sources(self):
        self._store_entry(source={"src_type": "http",
                                  "url": "https://a.example/x.tgz"})
        prov = self._provider(_FakeSession())
        other = _Pkg("libX", src_type="http", url="https://b.example/x.tgz")
        warnings = []
        with patch("ivpm.cache_provider.warning", warnings.append):
            self.assertTrue(prov.lookup(other, "v1").is_miss)
        self.assertEqual(len(warnings), 1)
        self.assertIn("a.example", warnings[0])
        self.assertIn("b.example", warnings[0])

    def test_a_mirror_of_a_commit_key_still_hits(self):
        sha = "a" * 40
        self._store_entry(pkg="libG", version=sha,
                          source={"src_type": "git",
                                  "url": "https://github.com/o/r.git",
                                  "resolved_commit": sha})
        prov = self._provider(_FakeSession())
        mirror = _Pkg("libG", src_type="git",
                      url="https://mirror.example/o/r.git", resolved_commit=sha)
        self.assertTrue(prov.lookup(mirror, sha).is_hit)

    def test_verify_off_makes_lookup_cheap(self):
        path = self._store_entry(files={"a.txt": "a", "b.txt": "b"})
        self._unseal(path)
        os.remove(os.path.join(path, "b.txt"))
        prov = self._provider(_FakeSession(), level="off")
        self.assertTrue(prov.lookup(_Pkg("libX"), "v1").is_hit)


# --- P1.5: the command ------------------------------------------------------

class _Args:
    cache_cmd = "verify"
    cache_dir = None
    package = None
    content = False
    json = False
    verbose = 0
    repair = False
    max_passes = 3
    dry_run = False
    upgrade = False

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class TestVerifyCommand(_Base):
    def _run(self, **kw):
        from ivpm.cmds.cmd_cache import CmdCache
        args = _Args(cache_dir=self.cache_dir, **kw)
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit) as cm:
                CmdCache()(args)
        return cm.exception.code, buf.getvalue()

    def test_healthy_cache_exits_zero(self):
        self._store_entry()
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("HEALTHY", out)

    def test_degraded_exits_one(self):
        """Exit 1 says 'run --repair'."""
        self._store_entry("libX", "v1")
        pkg_dir = self.store.get_package_cache_dir("libX")
        with open(os.path.join(pkg_dir, "ghost.meta.json"), "w") as f:
            f.write("{}")
        code, out = self._run()
        self.assertEqual(code, 1)
        self.assertIn("DEGRADED", out)
        self.assertIn("ivpm cache verify --repair", out)

    def test_broken_exits_three(self):
        """Exit 3 says 'wake a human'."""
        bad = self._store_entry()
        self._unseal(bad)
        os.remove(os.path.join(bad, "a.txt"))
        code, out = self._run()
        self.assertEqual(code, 3)
        self.assertIn("BROKEN", out)

    def test_missing_cache_dir_exits_two(self):
        """Operational, not a statement about cache health."""
        from ivpm.cmds.cmd_cache import CmdCache
        args = _Args(cache_dir=os.path.join(self.test_dir, "nope"))
        with self.assertRaises(SystemExit) as cm:
            CmdCache()(args)
        self.assertEqual(cm.exception.code, 2)

    def test_repair_alias_repairs(self):
        bad = self._store_entry()
        self._unseal(bad)
        os.remove(os.path.join(bad, "a.txt"))
        code, out = self._run(cache_cmd="repair")
        self.assertEqual(code, 0)
        self.assertIn("CONVERGED", out)
        self.assertFalse(self.store.has_version("libX", "v1"))

    def test_json_is_the_documented_contract(self):
        bad = self._store_entry()
        self._unseal(bad)
        os.remove(os.path.join(bad, "a.txt"))
        code, out = self._run(json=True)
        payload = json.loads(out)
        self.assertEqual(payload["schema"], 1)
        self.assertEqual(payload["mode"], "check")
        self.assertEqual(payload["status"], "BROKEN")
        self.assertIsNone(payload["repair"])
        self.assertEqual(payload["by_problem"], {"shape-mismatch": 1})
        for field in ("cache_dir", "level", "inventory", "entries_checked",
                      "bytes_hashed", "elapsed_s", "totals", "findings"):
            self.assertIn(field, payload)

    def test_json_repair_block_is_populated_in_repair_mode(self):
        self._store_entry()
        code, out = self._run(repair=True, json=True)
        payload = json.loads(out)
        self.assertEqual(payload["mode"], "repair")
        self.assertEqual(payload["repair"]["outcome"], "CONVERGED")
        self.assertFalse(payload["repair"]["dry_run"])

    def test_dry_run_reports_without_changing_anything(self):
        bad = self._store_entry()
        self._unseal(bad)
        os.remove(os.path.join(bad, "a.txt"))
        before = _snapshot(self.cache_dir)
        code, out = self._run(repair=True, dry_run=True)
        self.assertEqual(_snapshot(self.cache_dir), before)
        self.assertIn("would repair", out)
        self.assertNotIn("CONVERGED", out)

    def test_verbose_lists_every_finding(self):
        bad = self._store_entry()
        self._unseal(bad)
        os.remove(os.path.join(bad, "a.txt"))
        code, out = self._run(verbose=1)
        self.assertIn("shape-mismatch", out)
        self.assertIn(bad, out)
        self.assertIn("expected", out)

    def test_verify_never_runs_at_level_off(self):
        """'off' is a policy for update-time lookups.  Asking to verify and
        then verifying nothing would be a silently useless run."""
        self._store_entry()
        os.environ["IVPM_CACHE_VERIFY"] = "off"
        self.addCleanup(os.environ.pop, "IVPM_CACHE_VERIFY", None)
        code, out = self._run()
        self.assertIn("level 'shape'", out)


class TestCliWiring(unittest.TestCase):
    def test_verify_and_repair_are_registered(self):
        from ivpm.__main__ import get_parser
        parser = get_parser()
        for cmd in ("verify", "repair"):
            args = parser.parse_args(["cache", cmd, "-c", "/tmp/x"])
            self.assertEqual(args.cache_cmd, cmd)
            self.assertEqual(args.cache_dir, "/tmp/x")

    def test_update_accepts_a_verify_level(self):
        from ivpm.__main__ import get_parser
        args = get_parser().parse_args(["update", "--verify", "content"])
        self.assertEqual(args.cache_verify, "content")


if __name__ == "__main__":
    unittest.main()

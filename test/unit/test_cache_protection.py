"""Protection-aware caching: who can read a cache entry.

The failure this file exists to prevent: a dependency protected by a Unix group
is published into the shared cache carrying a *different* group, so the people
meant to read it cannot and the people not meant to read it can.  Read-only
controls whether someone can write; the group controls who can read.  One does
not imply the other, and the cache used to get the second one wrong in two
independent ways (see cache-protection-policy-design.md §1).

Running these for real needs a second group to chgrp into.  Where the invoking
user belongs to no secondary group, the ownership assertions skip and the
mode/layout ones still run -- those are the majority and they are what fails
silently in CI otherwise.
"""
import os
import shutil
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

SRCDIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "src"))
if SRCDIR not in sys.path:
    sys.path.insert(0, SRCDIR)

from ivpm.cache import CacheStoreError, DirectoryCacheStore
from ivpm.protection import (PARTITION_PREFIX, ProtectionError,
                             ProtectionPolicy, file_seal_mode,
                             policy_file_mode, seal_mode)


def _secondary_gid():
    """A gid we can chgrp into that is not the one we would get by default."""
    gids = [g for g in os.getgroups() if g != os.getgid()]
    return gids[0] if gids else None


SECOND_GID = _secondary_gid()
needs_group = unittest.skipIf(
    SECOND_GID is None,
    "no secondary group available to test cross-group protection")


def _tree(root, files):
    for rel, content in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fp:
            fp.write(content)


class _Base(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)
        self.cache_dir = os.path.join(self.test_dir, "cache")
        self.store = DirectoryCacheStore(self.cache_dir)

    def _cleanup(self):
        # Entries are sealed unwritable, so a plain rmtree cannot remove them.
        for root, dirs, _ in os.walk(self.test_dir):
            for d in dirs:
                p = os.path.join(root, d)
                if not os.path.islink(p):
                    try:
                        os.chmod(p, 0o700)
                    except OSError:
                        pass
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _src(self, files=None, name="src"):
        src = os.path.join(self.test_dir, name)
        os.makedirs(src)
        _tree(src, files or {"a.txt": "a"})
        return src

    def _policy(self, **kw):
        kw.setdefault("gid", SECOND_GID)
        return ProtectionPolicy(**kw)


# --- the reported bug --------------------------------------------------------

class TestPublishedEntryCarriesItsGroup(_Base):
    """The `album.dj` case: identical content and mode, wrong access group."""

    @needs_group
    def test_same_filesystem_publish_uses_the_policy_group(self):
        """The mechanism the original report missed.

        Staging is cache-side, so the fetch runs inside <cache>/<pkg>/, which
        is setgid to the *cache's* group -- content was created with the wrong
        group and the publish rename preserved it faithfully.  No copy, no
        second filesystem, and the prepared deps-dir never involved.
        """
        policy = self._policy()
        staging = self.store.new_staging("libX", policy)
        os.makedirs(staging)
        _tree(staging, {"src/meta/album.dj": "secret"})

        entry = self.store.store_version("libX", "v1", staging, policy=policy)

        for node in (entry,
                     os.path.join(entry, "src"),
                     os.path.join(entry, "src", "meta", "album.dj")):
            self.assertEqual(os.stat(node).st_gid, policy.gid,
                             "%s carries the wrong group" % node)

    def test_staging_is_beside_the_partition_not_inside_it(self):
        """The partition name contains ``@``; fetch tools that name things
        after their cwd (Perforce clients) refuse to run under it."""
        policy = ProtectionPolicy(gid=os.getgid())
        staging = self.store.new_staging("libX", policy)
        pkg_dir = self.store.get_package_cache_dir("libX")
        private = os.path.dirname(staging)

        self.assertEqual(os.path.dirname(private), pkg_dir)
        self.assertNotIn("@", os.path.relpath(staging, self.cache_dir))
        # The partition is still created up front, so a policy problem is
        # reported before the fetch rather than after it.
        self.assertTrue(os.path.isdir(
            self.store.get_partition_dir("libX", policy)))
        st = os.stat(private)
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o2700)

    @needs_group
    def test_staging_parent_carries_the_policy_group(self):
        policy = self._policy()
        private = os.path.dirname(self.store.new_staging("libX", policy))
        self.assertEqual(os.stat(private).st_gid, policy.gid)

    @needs_group
    def test_cross_filesystem_copy_uses_the_policy_group(self):
        """shutil's copy2 never touches ownership, so a copy out of the cache
        used to re-group content to whatever the destination inherited."""
        policy = self._policy()
        src = self._src({"src/meta/album.dj": "secret"})
        entry = self._copy_with_policy("libX", "v1", src, policy)
        self.assertEqual(
            os.stat(os.path.join(entry, "src", "meta", "album.dj")).st_gid,
            policy.gid)

    def _copy_with_policy(self, pkg, version, src, policy):
        """What a cross-filesystem copy produces.

        Publishing never copies any more -- a cross-filesystem publish is
        refused -- but the same copy primitive still carries cached content
        out of the cache when a nested boundary is promoted to a writable
        tree, so its protection guarantees are tested directly.
        """
        from ivpm.fscopy import copy_tree
        dst = os.path.join(self.test_dir, "copy_%s_%s" % (pkg, version))
        copy_tree(src, dst, policy)
        return dst

    def test_cross_filesystem_publish_is_refused(self):
        """Content is fetched onto the cache filesystem; a tree that is not
        there is a bug or a misconfiguration, never something to copy in."""
        import errno
        from unittest.mock import patch
        src = self._src({"a.txt": "a"})
        real = os.rename

        def exdev(a, b, *args, **kw):
            if os.path.abspath(a) == os.path.abspath(src):
                raise OSError(errno.EXDEV, "cross-device link")
            return real(a, b, *args, **kw)

        with patch("ivpm.cache.os.rename", side_effect=exdev):
            with self.assertRaises(CacheStoreError) as cm:
                self.store.store_version("libX", "v1", src)
        self.assertIn("not on the cache filesystem", str(cm.exception))
        self.assertFalse(self.store.has_version("libX", "v1"))
        pkg_dir = self.store.get_package_cache_dir("libX")
        self.assertEqual(
            [n for n in os.listdir(pkg_dir) if not n.startswith(".")], [])

    @needs_group
    def test_cross_filesystem_copy_preserves_symlinks(self):
        policy = self._policy()
        src = self._src({"real/f.txt": "x"})
        os.symlink("real", os.path.join(src, "alias"))
        os.symlink("nowhere", os.path.join(src, "broken"))

        entry = self._copy_with_policy("libX", "v1", src, policy)
        self.assertTrue(os.path.islink(os.path.join(entry, "alias")))
        self.assertTrue(os.path.islink(os.path.join(entry, "broken")))
        self.assertEqual(os.readlink(os.path.join(entry, "alias")), "real")


# --- sealing must never widen ------------------------------------------------

class TestSealingPreservesRestrictions(_Base):
    def test_a_restricted_directory_is_not_published_world_readable(self):
        """Forcing 2555 made every entry *more* accessible than its source."""
        src = self._src({"private/secret.txt": "s"})
        os.chmod(os.path.join(src, "private"), 0o750)

        entry = self.store.store_version("libX", "v1", src)
        mode = stat.S_IMODE(os.stat(os.path.join(entry, "private")).st_mode)
        self.assertEqual(mode & 0o007, 0,
                         "0o%o: a 0750 source directory was published "
                         "world-traversable" % mode)
        self.assertEqual(mode & 0o222, 0, "the entry is not sealed")

    def test_a_restricted_file_is_not_published_world_readable(self):
        src = self._src({"secret.txt": "s"})
        os.chmod(os.path.join(src, "secret.txt"), 0o640)

        entry = self.store.store_version("libX", "v1", src)
        mode = stat.S_IMODE(os.stat(os.path.join(entry, "secret.txt")).st_mode)
        self.assertEqual(mode, 0o440)

    def test_sealing_only_ever_removes_write(self):
        for src_mode in (0o755, 0o750, 0o700, 0o644, 0o640):
            with self.subTest(mode=oct(src_mode)):
                sealed = seal_mode(src_mode)
                self.assertEqual(sealed & 0o222, 0)
                self.assertEqual(sealed, src_mode & ~0o222)

    @needs_group
    def test_policy_modes_are_applied_to_published_content(self):
        policy = self._policy(dir_mode=0o2770, file_mode=0o0640)
        src = self._src({"sub/f.txt": "x"})
        entry = self.store.store_version("libX", "v1", src, policy=policy)

        self.assertEqual(
            stat.S_IMODE(os.stat(os.path.join(entry, "sub", "f.txt")).st_mode),
            0o440, "file_mode 0o640 should seal to 0o440")
        d = stat.S_IMODE(os.stat(os.path.join(entry, "sub")).st_mode)
        self.assertEqual(d & 0o777, 0o550)
        self.assertEqual(d & 0o007, 0, "other must not reach a 2770 policy")


class TestExecuteBitsSurviveAPolicy(_Base):
    """A policy used to stamp ``file_mode`` on every file, stripping execute
    from tool wrappers and hooks.  Execute now carries over ``chmod X``-style:
    for the classes the policy lets read the file, and only if the source had
    any execute bit."""

    def test_policy_file_mode_table(self):
        cases = [
            # (file_mode, source, building, sealed)
            (0o660, 0o644, 0o660, 0o440),
            (0o660, 0o755, 0o770, 0o550),
            (0o660, 0o700, 0o770, 0o550),
            (0o660, 0o4755, 0o770, 0o550),
            (0o660, 0o2755, 0o770, 0o550),
            (0o660, 0o001, 0o770, 0o550),
            (0o640, 0o755, 0o750, 0o550),
            (0o640, 0o644, 0o640, 0o440),
            (0o664, 0o755, 0o775, 0o555),
            (0o600, 0o755, 0o700, 0o500),
        ]
        for file_mode, src, building, sealed in cases:
            with self.subTest(file_mode=oct(file_mode), src=oct(src)):
                p = ProtectionPolicy(gid=0, file_mode=file_mode)
                self.assertEqual(oct(policy_file_mode(src, p)), oct(building))
                self.assertEqual(oct(file_seal_mode(src, p)), oct(sealed))

    def test_seal_is_idempotent(self):
        """The verifier recomputes from the *sealed* mode; it must agree."""
        for file_mode in (0o660, 0o640, 0o600):
            p = ProtectionPolicy(gid=0, file_mode=file_mode)
            for src in (0o644, 0o755, 0o700, 0o600):
                with self.subTest(file_mode=oct(file_mode), src=oct(src)):
                    once = file_seal_mode(src, p)
                    self.assertEqual(oct(file_seal_mode(once, p)), oct(once))

    def test_no_policy_keeps_the_source_mode(self):
        for src in (0o755, 0o750, 0o644, 0o600):
            with self.subTest(src=oct(src)):
                self.assertEqual(file_seal_mode(src, None), src & ~0o222)

    def test_special_bits_in_file_mode_are_rejected(self):
        for bad in (0o4660, 0o2660, 0o1660):
            with self.subTest(file_mode=oct(bad)):
                with self.assertRaises(ProtectionError):
                    ProtectionPolicy(gid=0, file_mode=bad)

    def _exec_tree(self):
        src = self._src({"bin/tool": "#!/bin/sh\n", "data.txt": "d",
                         "bin/suid": "#!/bin/sh\n"})
        os.chmod(os.path.join(src, "bin", "tool"), 0o755)
        os.chmod(os.path.join(src, "data.txt"), 0o644)
        os.chmod(os.path.join(src, "bin", "suid"), 0o4755)
        return src

    def _mode(self, *parts):
        return stat.S_IMODE(os.lstat(os.path.join(*parts)).st_mode)

    def _check(self, entry, other_read):
        exe = 0o555 if other_read else 0o550
        self.assertEqual(oct(self._mode(entry, "bin", "tool")), oct(exe))
        self.assertEqual(oct(self._mode(entry, "bin", "suid")), oct(exe),
                         "setuid must never be carried into the cache")
        self.assertEqual(oct(self._mode(entry, "data.txt")),
                         oct(0o444 if other_read else 0o440))

    @needs_group
    def test_same_filesystem_publish_keeps_execute(self):
        policy = self._policy(file_mode=0o640)
        entry = self.store.store_version("libX", "v1", self._exec_tree(),
                                         policy=policy)
        self._check(entry, other_read=False)

    @needs_group
    def test_cross_filesystem_copy_keeps_execute(self):
        policy = self._policy(file_mode=0o640)
        copy = TestPublishedEntryCarriesItsGroup._copy_with_policy(
            self, "libX", "v1", self._exec_tree(), policy)
        # A copy is the writable form; sealing (when it applies) removes write.
        self.assertEqual(oct(self._mode(copy, "bin", "tool")), oct(0o750))
        self.assertEqual(oct(self._mode(copy, "bin", "suid")), oct(0o750))
        self.assertEqual(oct(self._mode(copy, "data.txt")), oct(0o640))

    @needs_group
    def test_execute_is_only_added_where_the_policy_grants_read(self):
        policy = self._policy(file_mode=0o660)
        entry = self.store.store_version("libX", "v1", self._exec_tree(),
                                         policy=policy)
        self._check(entry, other_read=False)

    @needs_group
    def test_an_entry_with_executables_verifies_clean(self):
        import ivpm.cache_verify as cv
        policy = self._policy(file_mode=0o660)
        self.store.store_version("libX", "v1", self._exec_tree(),
                                 policy=policy)
        result = cv.verify_cache(self.store, level="content")
        self.assertTrue(result.ok, [str(f) for f in result.findings])
        # And the publish-time seal check, which recomputes every mode from
        # the sealed tree, agrees with what the seal produced.
        self.store._verify_sealed(
            self.store.get_version_cache_dir("libX", "v1", policy), policy)


# --- symlinks must not be doors out of the tree ------------------------------

class TestSymlinksAreNeverFollowed(_Base):
    """chmod/chown follow symlinks; os.walk files a symlink-to-dir under
    `dirnames`.  Together those modified files outside the cache entirely."""

    def _outside(self, mode=0o777):
        out = os.path.join(self.test_dir, "outside")
        os.makedirs(out)
        os.chmod(out, mode)
        victim = os.path.join(out, "victim.txt")
        with open(victim, "w") as fp:
            fp.write("x")
        os.chmod(victim, 0o666)
        return out, victim

    def test_sealing_does_not_chmod_an_external_directory(self):
        out, _ = self._outside(0o777)
        src = self._src({"a.txt": "a"})
        os.symlink(out, os.path.join(src, "escape"))

        self.store.store_version("libX", "v1", src)
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o777,
                         "sealing reached through a symlink and chmod'd a "
                         "directory outside the cache")

    def test_eviction_does_not_widen_an_external_directory(self):
        out, victim = self._outside(0o700)
        os.chmod(victim, 0o400)
        src = self._src({"a.txt": "a"})
        os.symlink(out, os.path.join(src, "escape"))
        self.store.store_version("libX", "v1", src)

        self.assertTrue(self.store._evict("libX", "v1"))
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o700,
                         "eviction widened an external directory")
        self.assertEqual(stat.S_IMODE(os.stat(victim).st_mode), 0o400,
                         "eviction made an external read-only file writable")

    def test_an_entry_with_a_symlinked_dir_verifies_clean(self):
        """`lib -> lib64` used to report permanent, unrepairable seal drift:
        the reseal chmod'd the target, the verifier lstat'd the link."""
        from ivpm import cache_verify as cv
        src = self._src({"lib64/f.so": "x"})
        os.symlink("lib64", os.path.join(src, "lib"))
        self.store.store_version("libX", "v1", src)

        result = cv.verify_entry(self.store, "libX", "v1", "shape")
        self.assertEqual(
            [f.problem for f in result.findings], [],
            "an entry containing a symlinked directory reports drift")

    def test_dir_size_does_not_follow_symlinks(self):
        src = self._src({"big.bin": "x" * 5000})
        os.symlink("big.bin", os.path.join(src, "alias"))
        entry = self.store.store_version("libX", "v1", src)
        # 5000 bytes + the manifest, and emphatically not 10000.
        self.assertLess(self.store._get_dir_size(entry), 7000)


# --- partitioning ------------------------------------------------------------

class TestPartitioning(_Base):
    def test_no_policy_keeps_the_historical_layout(self):
        """A site with no preparer must not have its cache reshaped."""
        entry = self.store.store_version("libX", "v1", self._src())
        self.assertEqual(
            entry, os.path.join(self.cache_dir, "libX", "v1"))

    @needs_group
    def test_a_policy_inserts_a_partition_level(self):
        policy = self._policy()
        entry = self.store.store_version("libX", "v1", self._src(),
                                         policy=policy)
        rel = os.path.relpath(entry, self.cache_dir).split(os.sep)
        self.assertEqual(len(rel), 3)
        self.assertEqual(rel[0], "libX")
        self.assertTrue(rel[1].startswith(PARTITION_PREFIX))
        self.assertEqual(rel[2], "v1")

    @needs_group
    def test_two_policies_do_not_evict_each_other(self):
        """The thrash a manifest-field check would have produced: workspace A
        rejects B's entry, evicts, rebuilds; B does the same forever."""
        a = self._policy(file_mode=0o0640)
        b = self._policy(file_mode=0o0660)
        self.assertNotEqual(a.partition_key(), b.partition_key())

        ea = self.store.store_version("libX", "v1", self._src(name="sa"),
                                      policy=a)
        eb = self.store.store_version("libX", "v1", self._src(name="sb"),
                                      policy=b)
        self.assertNotEqual(ea, eb)
        self.assertTrue(self.store.has_version("libX", "v1", a))
        self.assertTrue(self.store.has_version("libX", "v1", b))

    @needs_group
    def test_a_version_key_cannot_be_mistaken_for_a_partition(self):
        """A release tag may legitimately look like a partition name.

        With a `p.` prefix, a tag named `p.1.0` -- which `safe_version_key`
        passes through untouched -- was rejected by `_is_populated` forever:
        published on every run, never read back, never reported.  Now no key
        is ever stored under a partition-shaped name.
        """
        from ivpm.utils import safe_version_key
        from ivpm.protection import is_partition_name
        for tag in ("p.1.0", "protect.9", "p.abc", "_protect.9", "@protect.9",
                    "protect_p0123456789ab", "@protect.0123456789ab"):
            with self.subTest(tag=tag):
                self.assertFalse(is_partition_name(safe_version_key(tag)))

        policy = self._policy()
        self.store.store_version("libX", "p.1.0", self._src(), policy=policy)
        self.assertTrue(self.store.has_version("libX", "p.1.0", policy))
        # ...and it is reported as an entry, not skipped as machinery.
        info = self.store.get_cache_info()
        self.assertEqual(
            [v["version"] for v in info["packages"][0]["versions"]], ["p.1.0"])

    def test_same_policy_is_the_same_partition(self):
        a = ProtectionPolicy(gid=1234, group_name="alpha")
        b = ProtectionPolicy(gid=1234, group_name="beta-on-another-host")
        self.assertEqual(a.partition_key(), b.partition_key(),
                         "the group NAME must not split one policy across "
                         "hosts whose group databases disagree")

    @needs_group
    def test_a_partition_is_setgid_and_closed_to_others(self):
        policy = self._policy()
        part = self.store.ensure_partition_dir("libX", policy)
        st = os.stat(part)
        self.assertEqual(st.st_gid, policy.gid)
        self.assertTrue(st.st_mode & stat.S_ISGID,
                        "without setgid, content would not inherit the group")
        self.assertEqual(stat.S_IMODE(st.st_mode) & 0o007, 0)

    @needs_group
    def test_a_partition_is_never_served_as_an_entry(self):
        """_legacy_entries_ok makes any non-empty manifest-less directory look
        like a valid entry -- which a partition directory is."""
        policy = self._policy()
        self.store.store_version("libX", "v1", self._src(), policy=policy)
        part = self.store.get_partition_dir("libX", policy)
        self.assertFalse(self.store._is_populated(part))
        self.assertFalse(
            self.store.has_version("libX", os.path.basename(part)))

    @needs_group
    def test_partition_records_what_its_digest_means(self):
        import json
        policy = self._policy()
        part = self.store.ensure_partition_dir("libX", policy)
        with open(os.path.join(part, self.store._POLICY_FILE)) as fp:
            described = json.load(fp)
        self.assertEqual(described["gid"], policy.gid)
        self.assertEqual(described["partition"], policy.partition_key())


# --- fail closed -------------------------------------------------------------

class TestFailsClosed(_Base):
    def test_an_unapplicable_policy_publishes_nothing(self):
        """A gid the caller cannot chgrp into must abort the store, not fall
        back to publishing the content unprotected."""
        policy = ProtectionPolicy(gid=0)     # root; unusable unless we are root
        if os.getuid() == 0:
            self.skipTest("running as root; every chgrp succeeds")
        src = self._src()
        with self.assertRaises(CacheStoreError):
            self.store.store_version("libX", "v1", src, policy=policy)
        self.assertFalse(self.store.has_version("libX", "v1", policy))
        self.assertFalse(self.store.has_version("libX", "v1"),
                         "content was published outside the partition")

    def test_unknown_group_is_reported_by_name(self):
        with self.assertRaises(ProtectionError) as cm:
            ProtectionPolicy.for_group("no-such-group-here")
        self.assertIn("no-such-group-here", str(cm.exception))

    @needs_group
    def test_a_misprotected_partition_is_never_used_as_found(self):
        """Verify, never trust.  A partition with the right name and the wrong
        group is undetectable afterwards -- every entry inside inherits it and
        looks perfectly consistent.

        The requirement is that content is never published under protection
        nobody asked for.  Repairing a directory we own satisfies that (the
        path is a digest of this exact policy, so it is unambiguously supposed
        to carry it); using it as found would not.
        """
        policy = self._policy()
        part = self.store.get_partition_dir("libX", policy)
        os.makedirs(part)
        os.chmod(part, 0o2775)               # right name, wrong protection
        self.store._PARTITION_SETTLE_S = 0.1

        self.store.ensure_partition_dir("libX", policy)
        st = os.stat(part)
        self.assertEqual(st.st_gid, policy.gid)
        self.assertEqual(stat.S_IMODE(st.st_mode) & 0o007, 0,
                         "the partition was used with its found (open) mode")


# --- scanning a cache you cannot fully read ----------------------------------

class TestScansTolerateUnreadablePartitions(_Base):
    def test_cache_info_reports_rather_than_raises(self):
        """A 2770 partition is invisible to non-members by design.  Reporting
        that as an empty cache would be a lie; raising would make `cache info`
        unusable on the shared caches that need it most."""
        self.store.store_version("libX", "v1", self._src())
        closed = os.path.join(self.cache_dir, "libY")
        os.makedirs(closed)
        os.chmod(closed, 0o000)
        self.addCleanup(os.chmod, closed, 0o755)

        info = self.store.get_cache_info()
        names = [p["name"] for p in info["packages"]]
        self.assertIn("libX", names)
        if os.getuid() != 0:
            self.assertTrue(info["unreadable"],
                            "an unreadable package was silently skipped")

    @needs_group
    def test_cache_info_reports_the_partition_of_each_entry(self):
        policy = self._policy()
        self.store.store_version("libX", "v1", self._src(), policy=policy)
        info = self.store.get_cache_info()
        versions = info["packages"][0]["versions"]
        self.assertEqual([v["version"] for v in versions], ["v1"])
        self.assertEqual(versions[0]["partition"], policy.partition_key())

    @needs_group
    def test_gc_finds_and_evicts_partitioned_entries(self):
        import time
        policy = self._policy()
        self.store.store_version("libX", "v1", self._src(), policy=policy)
        self.assertEqual(self.store.clean_older_than(0, dry_run=True), 1,
                         "GC never descended into the partition")
        self.assertEqual(self.store.clean_older_than(0), 1)
        self.assertFalse(self.store.has_version("libX", "v1", policy))

    @needs_group
    def test_touch_linked_target_resolves_a_partitioned_link(self):
        policy = self._policy()
        deps = os.path.join(self.test_dir, "deps")
        os.makedirs(deps)
        self.store.store_version("libX", "v1", self._src(), policy=policy)
        link = self.store.link_to_deps("libX", "v1", deps, policy)
        self.assertTrue(self.store.touch_linked_target(link))


# --- naming for a cache shared with older releases ---------------------------

class TestCacheNames(_Base):
    """Names a shared cache's older readers can never compute for anything else.

    See cache-naming-design.md.  Every name is ``[A-Za-z0-9._-]``; the names
    only 2.41+ creates avoid every key shape 2.33-2.40 produce (all-hex,
    ``_<12 hex>`` at the end, ``+patch.``), so those releases cannot reach them.
    """

    #: The key shapes 2.33-2.40 can compute (git, http, gh-rls, patched).
    _OLD_SHAPES = (r"^[0-9a-f]+$", r"_[0-9a-f]{12}$", r"\+patch\.")

    def _own_policy(self):
        # The invoking user's own group: no secondary group needed.
        return ProtectionPolicy(gid=os.getgid())

    def _new_forms(self):
        from ivpm import cache_names as cn
        from ivpm.utils import safe_version_key
        return {
            "partition": self._own_policy().partition_key(),
            "escaped": safe_version_key('W/"abc/def"'),
            "escaped-long": safe_version_key("x" * 300),
            "patched": "0123456789abcdef0123456789abcdef01234567"
                       + cn.PATCH_SEP + "0123456789abcdef",
            "divergent": cn.divergent_name("v1", "0" * 32),
        }

    def test_new_forms_use_only_portable_characters(self):
        for what, name in self._new_forms().items():
            with self.subTest(what=what):
                self.assertRegex(name, r"^[A-Za-z0-9._-]+$")
                self.assertLessEqual(len(name), 128)

    def test_no_older_release_can_compute_a_new_form(self):
        import re
        for what, name in self._new_forms().items():
            for shape in self._OLD_SHAPES:
                with self.subTest(what=what, shape=shape):
                    self.assertIsNone(re.search(shape, name), name)

    def test_new_forms_are_recognised_as_such(self):
        from ivpm import cache_names as cn
        for what, name in self._new_forms().items():
            with self.subTest(what=what):
                self.assertTrue(cn.is_new_form(name), name)
                # A patched key is stored as itself; every other new form is
                # a name no version can be stored under.
                if what != "patched":
                    self.assertFalse(cn.is_plain_key(name), name)

    def test_common_keys_stay_plain_and_shared(self):
        from ivpm.utils import safe_version_key
        for k in ("0123456789abcdef0123456789abcdef01234567",
                  "v1.2.3_0123456789ab",
                  "Wed_21_Oct_2015_07-28-00_GMT_0123456789ab"):
            with self.subTest(key=k):
                self.assertEqual(safe_version_key(k), k)

    def test_machinery_shaped_keys_are_escaped(self):
        """A key spelling a machinery name was swept (``.staging.``, ``.gc.``)
        or deleted by repair (``.meta.json``) while workspaces used it."""
        from ivpm import cache_names as cn
        from ivpm.utils import safe_version_key
        for k in ("v1.staging.2", "rel.gc.3", "x.meta.json", ".hidden",
                  "policy.json", "-rf", "_tag", "1.0+build", "a~b", "a%3Ab",
                  "a@b"):
            with self.subTest(key=k):
                key = safe_version_key(k)
                self.assertNotEqual(key, k)
                self.assertRegex(key, r"^[A-Za-z0-9][A-Za-z0-9_-]*_k[0-9a-f]{24}$")
                self.assertFalse(self.store._is_transient(key))
                self.assertFalse(cn.is_partition_name(key))

    def test_machinery_shaped_keys_survive_the_sweep(self):
        import time
        self.store.store_version("libX", "v1.staging.2", self._src())
        later = time.time() + 2 * 86400
        with patch("ivpm.cache.time.time", lambda: later):
            self.store.ensure_cache_dir("libX")
        self.assertTrue(self.store.has_version("libX", "v1.staging.2"))
        versions = self.store.get_cache_info()["packages"][0]["versions"]
        self.assertEqual(len(versions), 1, "the entry is invisible to GC")

    def test_escaped_entries_verify_and_repair_clean(self):
        from ivpm import cache_verify as cv
        self.store.store_version("libX", "x.meta.json", self._src())
        self.store.store_version("libX", "a:b", self._src(name="s2"))
        res = cv.verify_cache(self.store, level="shape")
        self.assertTrue(res.ok, [f.__dict__ for f in res.findings])
        cv.repair_cache(self.store, level="shape")
        self.assertTrue(self.store.has_version("libX", "x.meta.json"))
        self.assertTrue(self.store.has_version("libX", "a:b"))

    def test_raw_version_is_recorded_and_compared(self):
        from ivpm import cache_verify as cv
        entry = self.store.store_version("libX", "a:b", self._src())
        self.assertEqual(self.store.read_entry_manifest(entry)["raw_version"],
                         "a:b")
        # Simulate two versions sharing one escaped name.
        res = cv.verify_entry(self.store, "libX", "a:b", "off")
        self.assertTrue(res.ok)
        from unittest.mock import patch as _patch
        with _patch("ivpm.cache.DirectoryCacheStore.get_version_cache_dir",
                    lambda st, p, v, pol=None: entry):
            res = cv.verify_entry(self.store, "libX", "c:d", "off")
        self.assertEqual([f.problem for f in res.findings],
                         [cv.Problem.MANIFEST_MISMATCH])

    def test_a_manifestless_entry_at_a_new_form_name_is_not_served(self):
        """Only 2.41+ writes these names, always with a manifest."""
        from ivpm import cache_verify as cv
        from ivpm.utils import safe_version_key
        key = safe_version_key("a:b")
        bare = os.path.join(self.store.get_package_cache_dir("libX"), key)
        os.makedirs(bare)
        _tree(bare, {"f": "not ivpm's"})
        self.assertFalse(self.store.has_version("libX", "a:b"))
        res = cv.verify_entry(self.store, "libX", "a:b", "off")
        self.assertEqual([f.problem for f in res.findings],
                         [cv.Problem.MANIFEST_MISMATCH])

    def test_a_manifestless_entry_at_a_plain_name_is_still_shared(self):
        """Pre-2.33 releases wrote no manifest; at a plain name every release
        agrees on what the entry is."""
        path = self.store.store_version("libX", "v1", self._src())
        os.chmod(path, 0o755)
        os.remove(self.store.entry_manifest_path(path))
        self.assertTrue(self.store.has_version("libX", "v1"))

    # -- what older releases left behind --------------------------------

    def _legacy_entry(self):
        """A partitioned entry as 2.36-2.40 left it: under ``@protect.``."""
        policy = self._own_policy()
        entry = self.store.store_version("libX", "v1", self._src(),
                                         policy=policy)
        part = os.path.dirname(entry)
        legacy = os.path.join(os.path.dirname(part),
                              "@protect.0123456789ab")
        os.rename(part, legacy)
        return policy, os.path.join(legacy, "v1")

    def test_a_legacy_partition_is_never_served(self):
        policy, _ = self._legacy_entry()
        self.assertFalse(self.store.has_version("libX", "v1", policy))

    def test_a_legacy_partition_is_scanned_as_a_partition(self):
        self._legacy_entry()
        versions = self.store.get_cache_info()["packages"][0]["versions"]
        self.assertEqual([v["version"] for v in versions], ["v1"])
        self.assertTrue(versions[0]["partition"].startswith("@protect."))

        from ivpm import cache_verify as cv
        res = cv.verify_cache(self.store, level="shape")
        self.assertTrue(res.ok, [f.__dict__ for f in res.findings])

    def test_a_legacy_link_is_still_touched(self):
        """Workspaces made by 2.36-2.40 link into ``@protect.``; while they
        are in use their entries must not look abandoned."""
        _, entry = self._legacy_entry()
        self.assertTrue(self.store.touch_linked_target(entry))

    def test_legacy_entries_age_out_through_gc(self):
        _, entry = self._legacy_entry()
        self.assertEqual(self.store.clean_older_than(0), 1)
        self.assertFalse(os.path.exists(entry))

    def test_a_legacy_divergent_copy_is_recognised(self):
        from ivpm import cache_names as cn
        self.assertTrue(cn.is_divergent("v1~alt~0123"))
        self.assertEqual(cn.canonical_key("v1~alt~0123"), "v1")
        self.assertEqual(cn.canonical_key(cn.divergent_name("v1", "0" * 32)),
                         "v1")

    def test_an_old_percent_escaped_entry_is_scanned_not_served(self):
        """2.33-2.40 stored ``a:b`` as ``a%3Ab``; 2.41 never looks there."""
        from ivpm import cache_verify as cv
        with patch("ivpm.utils.safe_version_key", lambda v: "a%3Ab"):
            self.store.store_version("libX", "a:b", self._src())
        self.assertFalse(self.store.has_version("libX", "a:b"))
        res = cv.verify_cache(self.store, level="shape")
        self.assertTrue(res.ok, [f.__dict__ for f in res.findings])


if __name__ == "__main__":
    unittest.main()


# --- a partitioned entry is checked and repaired where it actually is -------

class TestPartitionedVerifyAndRepair(_Base):
    """Both used to rebuild a path from (package, version) without the
    partition, so they looked at -- or evicted -- the unprotected entry."""

    def _unseal(self, path):
        os.chmod(path, 0o755)
        for root, dirs, files in os.walk(path):
            for d in dirs:
                os.chmod(os.path.join(root, d), 0o755)
            for f in files:
                os.chmod(os.path.join(root, f), 0o644)

    def _both(self):
        policy = ProtectionPolicy(gid=os.getgid())
        plain = self.store.store_version("libX", "v1", self._src(name="a"))
        prot = self.store.store_version("libX", "v1", self._src(name="b"),
                                        policy=policy)
        self.assertNotEqual(plain, prot)
        return policy, plain, prot

    def test_lookup_verification_checks_the_protected_entry(self):
        import json
        from ivpm import cache_verify as cv
        policy, plain, prot = self._both()
        self._unseal(prot)
        mpath = self.store.entry_manifest_path(prot)
        with open(mpath) as fp:
            m = json.load(fp)
        m["version"] = "v2"
        with open(mpath, "w") as fp:
            json.dump(m, fp)

        res = cv.verify_entry(self.store, "libX", "v1", "off", policy=policy)
        self.assertEqual([f.problem for f in res.findings],
                         [cv.Problem.MANIFEST_MISMATCH])
        self.assertEqual(res.findings[0].path, prot)
        self.assertTrue(cv.verify_entry(self.store, "libX", "v1", "off").ok,
                        "the unprotected entry was blamed")

    def test_repair_evicts_the_damaged_partitioned_entry_only(self):
        from ivpm import cache_verify as cv
        policy, plain, prot = self._both()
        self._unseal(prot)
        os.remove(os.path.join(prot, "a.txt"))

        cv.repair_cache(self.store, level="shape")
        self.assertFalse(os.path.exists(prot))
        self.assertTrue(self.store.has_version("libX", "v1"),
                        "repair evicted the healthy unprotected entry")


# --- concurrency around the partition directory ------------------------------

class TestPartitionCreationRaces(_Base):
    """Creating a partition private-then-promoted makes its intermediate state
    visible: 0700 with the cache's group, which is indistinguishable from a
    mis-protected partition.  A second worker must wait it out, not fail."""

    @needs_group
    def test_a_worker_waits_out_an_in_flight_promotion(self):
        import threading
        import time
        from ivpm.protection import apply_to_dir

        policy = self._policy()
        part = self.store.get_partition_dir("libX", policy)
        self.store.ensure_cache_dir("libX")
        os.mkdir(part, 0o700)              # another worker's mkdir, mid-promote

        def finish_late():
            time.sleep(0.2)
            apply_to_dir(part, policy)

        t = threading.Thread(target=finish_late)
        t.start()
        self.addCleanup(t.join)
        self.store.ensure_partition_dir("libX", policy)   # must not raise

    @needs_group
    def test_an_abandoned_promotion_is_completed_not_wedged(self):
        """A creator killed between its mkdir and its chown must not wedge the
        partition forever.

        Waiting alone turns a millisecond race into a permanent outage: the
        directory exists, so nobody retries the create, and every fetch of this
        package waits the full settle and fails until someone rm -rf's it by
        hand.
        """
        policy = self._policy()
        part = self.store.get_partition_dir("libX", policy)
        self.store.ensure_cache_dir("libX")
        os.mkdir(part, 0o700)              # exactly what a killed worker leaves
        self.store._PARTITION_SETTLE_S = 0.2

        self.store.ensure_partition_dir("libX", policy)      # must self-heal
        st = os.stat(part)
        self.assertEqual(st.st_gid, policy.gid)
        self.assertTrue(st.st_mode & stat.S_ISGID)

    @needs_group
    def test_a_foreign_misconfigured_partition_still_fails(self):
        """Self-healing stops at ownership: a directory another user left
        wrong is not ours to rewrite, and pretending otherwise would report
        success while publishing under whatever protection they set."""
        from unittest.mock import patch
        policy = self._policy()
        part = self.store.get_partition_dir("libX", policy)
        self.store.ensure_cache_dir("libX")
        os.mkdir(part, 0o2775)
        self.store._PARTITION_SETTLE_S = 0.1
        with patch("ivpm.cache.os.getuid", return_value=0x7FFFFFFE):
            with self.assertRaises(ProtectionError):
                self.store.ensure_partition_dir("libX", policy)

    @needs_group
    def test_concurrent_stores_of_one_package_all_succeed(self):
        """The realistic shape: several worker threads fetching versions of one
        package, all racing to create its single partition."""
        import threading
        policy = self._policy()
        errors = []

        def worker(n):
            try:
                src = self._src(name="src%d" % n)
                self.store.store_version("libX", "v%d" % n, src, policy=policy)
            except Exception as e:                 # noqa: BLE001 - reported
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual([str(e) for e in errors], [])
        for n in range(8):
            self.assertTrue(self.store.has_version("libX", "v%d" % n, policy))

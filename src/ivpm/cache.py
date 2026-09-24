#****************************************************************************
#* cache.py
#*
#* Copyright 2024 Matthew Ballance and Contributors
#*
#* Licensed under the Apache License, Version 2.0 (the "License"); you may 
#* not use this file except in compliance with the License.  
#* You may obtain a copy of the License at:
#*
#*   http://www.apache.org/licenses/LICENSE-2.0
#*
#* Unless required by applicable law or agreed to in writing, software 
#* distributed under the License is distributed on an "AS IS" BASIS, 
#* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.  
#* See the License for the specific language governing permissions and 
#* limitations under the License.
#*
#****************************************************************************
import os
import errno
import stat
import json
import time
import uuid
import shutil
from typing import Optional
from .msg import note
from .site_config import get_site_config
from .protection import (
    PARTITION_PREFIX, ProtectionError, ProtectionPolicy,
    apply_to_dir, dir_seal_mode, file_seal_mode, verify_dir,
)


def _ivpm_version() -> str:
    """The running IVPM version, for the entry manifest's provenance block."""
    try:
        from .__version__ import get_version
        return str(get_version())
    except Exception:
        return "unknown"


def _hostname() -> str:
    """Which machine published an entry — the first question asked of a shared
    cache when one host starts producing bad entries."""
    try:
        import socket
        return socket.gethostname()
    except Exception:
        return "unknown"


class CacheStoreError(Exception):
    """A cache publish failed for a reason other than losing the store race.

    The benign lost-race path (another worker published the same entry first)
    adopts the winner's entry instead of raising.  This exception is reserved
    for *genuine* failures — ``ENOSPC``, ``EACCES``, ``EROFS``, a vanished
    entry at link time — so they surface with context rather than being
    silently mistaken for a cache miss.
    """

    def __init__(self, package_name: str, version: str, cause):
        super().__init__("failed to store %s/%s: %s" % (
            package_name, version, cause))
        self.package_name = package_name
        self.version = version
        self.cause = cause


class DirectoryCacheStore:
    """Filesystem mechanics for a cache rooted at a directory.

    The cache is organized by package name, with version-specific
    subdirectories. For git packages, the version is the commit hash.
    For HTTP packages, the version is derived from the Last-Modified
    header or ETag.

    The store is always constructed with an explicit ``cache_dir``;
    resolving the env/site defaults (and the "disabled" decision) is the
    job of the cache provider / site config, not the store.
    """

    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir

    def is_enabled(self) -> bool:
        """Check if the cache is properly configured and enabled."""
        return self.cache_dir is not None

    def get_package_cache_dir(self, package_name: str) -> str:
        """Get the cache directory for a specific package.

        The name is checked here, not only at manifest-parse time, because this
        is the choke point every path in the cache goes through and the store
        is also a public API. A name containing a separator escapes the cache
        layout; ``.`` and ``..`` are worse, because they resolve to a directory
        that already exists and everything downstream then succeeds against the
        wrong tree.
        """
        from .utils import package_name_problem
        problem = package_name_problem(package_name)
        if problem is not None:
            raise ValueError("invalid package name for the cache: %s" % problem)
        return os.path.join(self.cache_dir, package_name)

    def get_partition_dir(self, package_name: str,
                          policy: Optional[ProtectionPolicy]) -> str:
        """The directory a *policy*'s entries for this package live under.

        With no policy this is the package directory itself, so a site with no
        registered preparer keeps the historical two-level layout byte for byte
        and needs no migration.  With a policy, a ``p.<digest>`` level is
        inserted: two workspaces wanting the same package under different
        protection then get separate entries instead of evicting each other's
        on every run (see cache-protection-policy-design.md §2).
        """
        pkg_dir = self.get_package_cache_dir(package_name)
        if policy is None:
            return pkg_dir
        return os.path.join(pkg_dir, policy.partition_key())

    def get_version_cache_dir(self, package_name: str, version: str,
                              policy: Optional[ProtectionPolicy] = None) -> str:
        """Get the cache directory for a specific package version.

        The version key is *escaped* rather than rejected: it is machine-made
        (an ETag, a commit hash, a release tag), and refusing to cache a
        package because its server returned a validator with a ``/`` in it
        would be a worse answer than storing it under an escaped name. Keys
        that are already safe pass through byte-identical, so no existing entry
        moves. Every caller reaches the cache through here, so publishing,
        lookup and eviction cannot disagree about where an entry lives --
        including about which protection partition an entry belongs to.
        """
        from .utils import safe_version_key
        return os.path.join(self.get_partition_dir(package_name, policy),
                            safe_version_key(version))

    _POLICY_FILE = "policy.json"

    def ensure_partition_dir(self, package_name: str,
                             policy: Optional[ProtectionPolicy]) -> str:
        """The partition directory, created under *policy* if it is not there.

        Created **private then promoted** -- ``mkdir`` at 0700, set the group,
        then the real mode -- because the package directory above it is setgid
        to the *cache's* group.  Creating it at its final mode would leave a
        window in which it is group-writable while still carrying the cache's
        group, which is the whole bug in miniature.

        Setgid on this one directory is what makes the policy free: everything
        a fetch creates inside it inherits the group as it is written, so there
        is no O(files) ``chgrp`` pass anywhere, and it works identically for a
        same-filesystem publish and a cross-filesystem copy.

        An existing directory is *verified*, never trusted: another worker may
        have created it, and a partition with the right name and the wrong
        protection is undetectable afterwards -- every entry inside inherits
        the wrong group and looks perfectly self-consistent.
        """
        self.ensure_cache_dir(package_name)
        if policy is None:
            return self.get_package_cache_dir(package_name)

        path = self.get_partition_dir(package_name, policy)
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            # Another worker created it -- possibly microseconds ago, and
            # possibly still between its own mkdir and its chown/chmod.  That
            # intermediate state is 0700 with the cache's group, which is
            # exactly what a *mis*-protected partition looks like, so verifying
            # immediately would fail a partition that is about to be correct.
            # Creating it private-then-promoted is what makes the window
            # visible; waiting it out is what makes it harmless.
            self._await_partition(path, policy)
            return path
        except OSError as e:
            raise ProtectionError(
                "could not create protection partition %s: %s" % (path, e))

        # We created it, so we are the one that must make it correct.
        apply_to_dir(path, policy)
        self._write_policy_file(path, policy)
        verify_dir(path, policy)
        return path

    #: How long to let another worker finish promoting a partition it created.
    #: Three chown/chmod syscalls, so this is orders of magnitude of slack; it
    #: only has to outlast a descheduled thread, never a slow operation.
    _PARTITION_SETTLE_S = 2.0

    def _await_partition(self, path: str, policy: ProtectionPolicy):
        """Verify a partition someone else created, finishing it if they did not.

        Two failures look identical from here and must be handled differently:

        * **Still in flight.**  The creator is between its ``mkdir`` and its
          ``chown``/``chmod``.  Waiting is correct -- promoting it ourselves
          would race the creator doing the same thing.
        * **Abandoned.**  The creator was killed in that window and is never
          coming back.  Waiting is then useless, and *only* waiting turns a
          millisecond-wide race into a permanently wedged partition: the
          directory exists, so nobody ever retries the create, and every fetch
          of this package forever after waits the full settle and fails.  That
          is worse than the race it was meant to fix.

        So: wait out the window, and if it is still wrong afterwards, finish
        the job ourselves when we are allowed to.  The path is a digest of this
        exact policy, so a directory there is unambiguously *supposed* to carry
        it -- adopting and completing it is not a guess.  If we do not own it,
        we cannot repair it and the original error stands, which is the honest
        outcome for a directory some other user left misconfigured.
        """
        deadline = time.time() + self._PARTITION_SETTLE_S
        delay = 0.005
        while True:
            try:
                verify_dir(path, policy)
                return
            except ProtectionError:
                if time.time() >= deadline:
                    break
            time.sleep(delay)
            delay = min(delay * 2, 0.1)

        try:
            owned = os.lstat(path).st_uid == os.getuid()
        except OSError:
            owned = False
        if not owned:
            verify_dir(path, policy)     # re-raise with the real reason
        note("Completing an abandoned protection partition at %s" % path)
        apply_to_dir(path, policy)
        self._write_policy_file(path, policy)
        verify_dir(path, policy)

    def _write_policy_file(self, partition_dir: str,
                           policy: ProtectionPolicy) -> None:
        """Record what ``p.<digest>`` means, beside the entries it governs.

        A digest is unexplainable from a directory listing alone; this makes
        ``ls`` plus one ``cat`` enough to audit a shared cache.  Best-effort:
        an unwritable policy file does not make the partition wrong.
        """
        path = os.path.join(partition_dir, self._POLICY_FILE)
        if os.path.exists(path):
            return
        # tmp + rename, like the sidecar: two workers racing here would
        # otherwise both open("w") and interleave into one truncated file, and
        # the thing that explains what a partition means would be the one file
        # in it that cannot be read.
        tmp = path + ".tmp." + uuid.uuid4().hex
        try:
            with open(tmp, "w") as fp:
                json.dump(policy.describe(), fp, sort_keys=True, indent=2)
            # The policy's own file mode, not a flat 0444: this file is the
            # one thing in the partition that describes the partition, and it
            # would be odd for it to be the one thing that ignores it.
            os.chmod(tmp, file_seal_mode(0o444, policy))
            os.rename(tmp, path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass


    def _is_populated(self, version_dir: str) -> bool:
        """Is there a *complete* entry at ``version_dir``?

        Three conditions, each closing a distinct way a non-entry can pose as
        one:

        * **Not a symlink.**  ``os.path.isdir`` follows symlinks, so a
          hand-placed ``ln -s`` at an entry path used to read as a HIT --
          consumers were then linked to content this cache never verified, and
          a losing builder *adopted* it (the rename onto a symlink fails
          ``ENOTDIR``, which the adopt branch treats as a lost race).  An entry
          is a real directory or it is nothing.
        * **Non-empty.**  An interrupted external ``rmtree`` or a crashed run
          can leave an empty ``version_dir``; the atomic rename in
          :meth:`store_version` replaces it on rebuild.
        * **Sealed.**  A manifest is positive evidence that IVPM published this
          tree, which "non-empty" cannot provide -- a half-copied tree is
          non-empty too.  Manifest-less entries predate the feature and are
          still accepted while :attr:`_legacy_entries_ok` holds.
        """
        try:
            if os.path.islink(version_dir):
                return False
            # A protection partition is machinery, not content.  It has to be
            # rejected by NAME rather than by inspecting what is inside it:
            # a partition holds version directories, so it is a non-empty
            # manifest-less directory, and ``_legacy_entries_ok`` would
            # therefore serve the partition itself as a cache hit.
            if os.path.basename(version_dir).startswith(PARTITION_PREFIX):
                return False
            if not os.path.isdir(version_dir):
                return False
            if not os.listdir(version_dir):
                return False
            if os.path.isfile(self.entry_manifest_path(version_dir)):
                return True
            return self._legacy_entries_ok
        except OSError:
            return False

    def has_version(self, package_name: str, version: str,
                    policy: Optional[ProtectionPolicy] = None) -> bool:
        """Check if a specific version is cached (present and non-empty)."""
        version_dir = self.get_version_cache_dir(package_name, version, policy)
        return self._is_populated(version_dir)
    
    def ensure_cache_dir(self, package_name: str) -> str:
        """Ensure the package cache directory exists (with setgid)."""
        pkg_cache_dir = self.get_package_cache_dir(package_name)
        if not os.path.isdir(pkg_cache_dir):
            os.makedirs(pkg_cache_dir, exist_ok=True)
            try:
                os.chmod(pkg_cache_dir, self._DIR_MODE)
            except OSError:
                pass
        else:
            # Opportunistically clear crash-leftover staging trees and
            # undeleted tombstones so unique (uuid4) names don't accumulate.
            self._sweep_stale_staging(pkg_cache_dir)
            self._sweep_stale_tombs(pkg_cache_dir)
        return pkg_cache_dir

    _STAGING_MARKER = ".staging."
    _STALE_STAGING_AGE_S = 24 * 60 * 60  # 24h — far beyond any live build

    # A tombstone is an entry that has already been removed from view by a
    # rename and is awaiting deletion of its bytes.  Short-lived by design: the
    # only tombstones that outlive their own ``rmtree`` are ones we lacked the
    # permission (or the space) to delete.
    _TOMB_MARKER = ".gc."
    _STALE_TOMB_AGE_S = 60 * 60

    def _is_transient(self, name: str) -> bool:
        """Is *name* cache machinery (staging / tombstone) rather than a version?

        One predicate, so a scan can never learn about staging but not about
        tombstones -- which would make a tombstone read as a version directory
        whose "version" is a uuid.
        """
        return self._STAGING_MARKER in name or self._TOMB_MARKER in name

    def _evict(self, package_name: str, version: str,
               policy: Optional[ProtectionPolicy] = None) -> bool:
        """Atomically remove an entry from view, then delete its bytes.

        **The rename is the eviction.**  After it, no lookup can see the entry,
        so a concurrent reader either resolved the intact tree (and holds a
        symlink to what is now a tombstone, which stays readable until its last
        user goes away) or sees a clean MISS and rebuilds.  It never observes a
        tree being dismantled underneath it -- which is what a bare ``rmtree``
        did, and worse: a partial ``rmtree`` left a non-empty fragment that
        ``_is_populated`` reported as a HIT forever.

        The rename needs write permission on ``<cache>/<pkg>/`` only, not on the
        entry, so a group member can evict an entry whose internals they cannot
        ``chmod``.  A tombstone whose ``rmtree`` then fails (cross-user
        permissions, ENOSPC) is inert rather than dangerous: the marker keeps it
        out of every listing, and the sweep retries later.

        Returns True iff this caller performed the eviction, so two concurrent
        GCs cannot both count the same entry.
        """
        version_dir = self.get_version_cache_dir(package_name, version, policy)
        # The tombstone is a sibling of the entry -- inside the partition when
        # there is one -- so the rename stays on one filesystem and the
        # awaiting-delete bytes keep the partition's protection rather than
        # being exposed at the package level on their way out.
        tomb = os.path.join(os.path.dirname(version_dir),
                            self._TOMB_MARKER + uuid.uuid4().hex)
        try:
            os.rename(version_dir, tomb)
        except OSError:
            return False           # already gone, or another evictor won
        self._delete_meta(package_name, version, policy)
        self._discard(tomb)
        return True

    def _sweep_stale_tombs(self, pkg_cache_dir: str):
        """Retry deletion of tombstones whose bytes outlived their eviction."""
        try:
            entries = os.listdir(pkg_cache_dir)
        except OSError:
            return
        cutoff = time.time() - self._STALE_TOMB_AGE_S
        for name in entries:
            if self._TOMB_MARKER not in name:
                continue
            path = os.path.join(pkg_cache_dir, name)
            if not os.path.isdir(path):
                continue
            try:
                st = os.stat(path)
                if max(st.st_mtime, st.st_ctime) >= cutoff:
                    continue
            except OSError:
                continue
            self._discard(path)

    def _sweep_stale_staging(self, pkg_cache_dir: str):
        """Best-effort removal of orphaned ``<version>.staging.<uuid>`` trees.

        Unique staging names mean a leftover never collides with a live build;
        this only keeps crashed/killed-run residue from piling up.  Only trees
        older than :attr:`_STALE_STAGING_AGE_S` are removed, so an in-flight
        publish is never disturbed.  Fully best-effort (``OSError``-tolerant).

        Staleness keys on ``max(mtime, ctime)``: a staging dir built with
        ``copytree`` inherits the *source* tree's (possibly old) mtime via
        ``copystat``, but its ``ctime`` reflects its actual just-now creation —
        so a live build is never mistaken for stale, while a genuine leftover
        (untouched since a crash) still ages out on both stamps.
        """
        try:
            entries = os.listdir(pkg_cache_dir)
        except OSError:
            return
        cutoff = time.time() - self._STALE_STAGING_AGE_S
        for name in entries:
            if self._STAGING_MARKER not in name:
                continue
            path = os.path.join(pkg_cache_dir, name)
            if not os.path.isdir(path):
                continue
            try:
                st = os.stat(path)
                if max(st.st_mtime, st.st_ctime) >= cutoff:
                    continue
            except OSError:
                continue
            self._discard(path)
    
    def new_staging(self, package_name: str,
                    policy: Optional[ProtectionPolicy] = None) -> str:
        """A unique, not-yet-created staging path on the CACHE filesystem.

        Returned from *inside* the package's protection partition, so a tree
        built here and handed to :meth:`store_version` publishes with a
        same-filesystem ``rename`` instead of a cross-device copy, **and**
        inherits the partition's group as every file is written.  Group
        ownership is therefore correct by construction on the ordinary fetch
        path -- which is exactly where it used to be wrong, because staging
        sat directly under a package directory that is setgid to the cache's
        own group.

        The returned path does not exist, but its *parent* does and is 0700:
        content is unreadable by other cache users while it is being fetched
        and sealed.  Callers cannot simply be handed a pre-created directory --
        ``git clone``, ``copytree`` and the zip extractor each want to create
        the target themselves -- so privacy is provided one level up.

        The ``.staging.`` marker on that parent keeps it out of cache scans
        (:meth:`get_cache_info`, :meth:`clean_older_than`) and makes it
        eligible for the stale-staging sweep if a build crashes.
        """
        partition = self.ensure_partition_dir(package_name, policy)
        private = os.path.join(partition, "build.staging." + uuid.uuid4().hex)
        self._mk_private(private, policy)
        return os.path.join(private, "tree")

    def _mk_private(self, path: str, policy: Optional[ProtectionPolicy]):
        """Create a staging parent nothing else can look inside.

        0700 regardless of policy -- an in-progress tree has no business being
        readable even by the group that will own the finished entry, since
        until the seal runs its modes are whatever the fetch happened to
        create.  The group is still set so that content created inside
        inherits it via setgid.
        """
        os.mkdir(path, 0o700)
        if policy is not None:
            from .protection import chgrp
            chgrp(path, policy.gid)
        os.chmod(path, 0o2700)

    def _reap_staging_parent(self, source_path: str):
        """Remove the private parent of a consumed staging tree.

        ``rmdir``, never ``rmtree``: it succeeds only when the directory is
        empty, so a parent that still holds something (a download scratch a
        caller forgot, a tree that was not actually consumed) is left for the
        stale-staging sweep rather than deleted on a guess.
        """
        parent = os.path.dirname(os.path.abspath(source_path))
        if not self._is_transient(os.path.basename(parent)):
            return
        try:
            os.rmdir(parent)
        except OSError:
            pass

    def _consume_source(self, source_path: str):
        """Finish with a source tree, however this store call ended.

        Every exit from :meth:`store_version` reaches here -- published,
        already-cached, lost race, hard failure -- because every one of them
        leaves the caller's staging tree behind otherwise.  The lost-race path
        is the one that matters in practice: with N workers racing, N-1 take it
        on every single miss, so a leak there is not an edge case but the
        common case.
        """
        self._discard(source_path)
        self._reap_staging_parent(source_path)

    def store_version(self, package_name: str, version: str, source_path: str,
                      source: Optional[dict] = None,
                      policy: Optional[ProtectionPolicy] = None) -> str:
        """Store a package version in the cache.

        Args:
            package_name: Name of the package
            version: Version identifier (e.g., commit hash)
            source_path: Path to the source directory to cache
            source: Optional description of where the content came from
                (url/ref/etag/...), recorded in the entry manifest.  Purely
                diagnostic: it lets a later lookup notice that an entry was
                published from a *different* source than the one now asking
                for it, which is the only externally visible symptom of a
                cache-key collision.

        Returns:
            Path to the cached version directory
        """
        version_dir = self.get_version_cache_dir(package_name, version, policy)

        # A cache entry is a DIRECTORY -- _is_populated, link_to_deps and the
        # publish rename all depend on it.  Storing a plain file produced an
        # entry that no lookup could ever see (has_version stayed False
        # forever) and that materialize() then rejected, so the caller failed
        # on every run rather than on the first.  Refuse it here, where the
        # message can name the cause.
        if not os.path.isdir(source_path):
            raise CacheStoreError(
                package_name, version,
                "source %s is not a directory; cache entries are directories"
                % source_path)

        # An empty leftover version_dir (interrupted rmtree / crashed run) is
        # NOT a hit — fall through and rebuild; the atomic rename below replaces
        # an empty target.
        if self._is_populated(version_dir):
            # Already cached — clean up the source that is no longer needed
            if os.path.exists(source_path):
                self._consume_source(source_path)
            # Re-storing an extant entry still counts as using it.
            self._touch_last_linked(package_name, version, policy)
            return version_dir

        try:
            self.ensure_partition_dir(package_name, policy)
        except ProtectionError as e:
            # Fail closed.  The alternative -- publishing into the package
            # directory because the partition could not be protected -- is the
            # exact outcome the policy exists to prevent.
            self._consume_source(source_path)
            raise CacheStoreError(package_name, version, e) from e

        # --- Atomic publish (the mutual-exclusion primitive) --------------
        # Build under a unique staging name, then publish with a single
        # ``os.rename``.  Two invariants make this race-safe WITHOUT a lock:
        #
        #  * INVARIANT (H1): staging is on the SAME FILESYSTEM as version_dir,
        #    guaranteed by placing it inside the same partition directory, so
        #    ``os.rename`` is atomic and its ``ENOTEMPTY`` failure when
        #    version_dir already exists IS the serialization point.  Do not
        #    relocate staging off this filesystem.
        #  * The staging name is uuid4-unique (H2), so concurrent worker
        #    threads (which share a PID), separate processes, and reused PIDs
        #    from a prior crashed run can never collide — the transfer can
        #    never nest ``source_path`` inside a stale staging dir.
        #
        # GROUP OWNERSHIP is handled by construction, not by correction.  The
        # staging tree is created inside the partition directory, which is
        # setgid to the policy's group, so content carries the right group as
        # it is written.  A cross-filesystem transfer cannot rely on that (the
        # bytes are created by a copy, not inherited), so it applies the policy
        # per node and verifies -- see ``_transfer``.  Either way the tree is
        # correct *before* the seal runs, which is the only ordering that
        # works: a sealed entry is unwritable, so nothing can be corrected
        # afterwards.
        #
        # Publish staging is a plain SIBLING of the entry, not a private
        # subdirectory.  It has to be: renaming a directory into a *different*
        # parent requires write permission on the directory being moved (the
        # kernel updates its ``..`` entry), and the seal has just removed every
        # write bit -- so a sealed tree can only ever be renamed within the
        # directory it already sits in.
        #
        # Nothing is lost by that.  Privacy during the slow, unsealed part --
        # the clone or download -- is provided by ``new_staging``'s 0700
        # parent.  What sits here is already sealed and already carries the
        # policy's group, inside a partition directory that is 2770, so the
        # only people who can see it are the ones entitled to read the entry it
        # is about to become.
        staging_dir = version_dir + ".staging." + uuid.uuid4().hex
        self._check_sibling(package_name, version, staging_dir, version_dir)
        try:
            self._move_and_publish(
                package_name, source_path, staging_dir, version_dir,
                policy=policy,
                seal=lambda staging: self._seal(
                    staging, package_name, version, source, policy))
        except ProtectionError as e:
            self._discard(staging_dir)
            self._consume_source(source_path)
            raise CacheStoreError(package_name, version, e) from e
        except OSError as e:
            # ``_discard``, not a bare rmtree: by the time the publish rename
            # can fail, the staging tree has already been *sealed* -- its
            # directories are 2555, so nothing inside them can be unlinked
            # until write permission comes back.  A plain rmtree here silently
            # left the whole tree behind on every lost race.
            self._discard(staging_dir)
            # Only a lost race adopts the winner's (atomically complete) entry;
            # any other errno (ENOSPC/EACCES/EROFS/...) is a genuine failure.
            if e.errno in (errno.ENOTEMPTY, errno.EEXIST, errno.ENOTDIR) \
                    and self._is_populated(version_dir):
                self._consume_source(source_path)
                return version_dir
            self._consume_source(source_path)
            raise CacheStoreError(package_name, version, e) from e

        self._reap_staging_parent(source_path)

        # NOTE: the entry is sealed (read-only + manifest) *inside*
        # _move_and_publish, before the rename -- so it is complete and
        # self-describing at the instant it becomes visible.  Sealing after the
        # rename, as this used to, left a window in which a concurrent reader
        # could resolve a writable, manifest-less entry.

        # Seed the stale-tracking sidecar next to (never inside) the locked
        # entry.  stored == last_linked at creation time.
        now = time.time()
        self._write_meta(package_name, version, {
            "schema": self._META_SCHEMA,
            "stored": now,
            "last_linked": now,
        }, policy)

        note(f"Cached {package_name} version {version}")
        return version_dir

    @staticmethod
    def _check_sibling(package_name: str, version: str,
                           staging_dir: str, version_dir: str):
        """Enforce INVARIANT H1: staging is a sibling of the entry.

        Two things follow from "same directory", and the publish needs both.
        Same directory means same filesystem, so ``os.rename`` is atomic and
        its ``ENOTEMPTY`` failure is the serialization point that keeps the
        whole store lock-free.  And same directory means the rename does not
        rewrite the moved directory's ``..`` entry, so it works on a tree that
        has already been sealed unwritable -- which a cross-parent rename does
        not.

        Checked unconditionally rather than with an ``assert``, which
        disappears under ``python -O`` -- exactly the configuration in which a
        silent non-atomic publish would do the most damage.
        """
        if os.path.dirname(staging_dir) != os.path.dirname(version_dir):
            raise CacheStoreError(
                package_name, version,
                "staging %s is not a sibling of the entry %s -- atomic "
                "publish is not possible" % (staging_dir, version_dir))

    def _move_and_publish(self, package_name: str, source_path: str,
                          staging_dir: str, version_dir: str, seal=None,
                          policy: Optional[ProtectionPolicy] = None):
        """``move`` the built tree into staging, seal it, then publish by rename.

        ``seal`` runs on the staging tree *between* the move and the rename, so
        whatever it does (locking permissions, writing the entry manifest) is
        already true the moment the entry becomes visible.  There is no window
        in which a reader can resolve a half-sealed entry, because until the
        rename there is nothing at ``version_dir`` to resolve.

        Retried once on ``ENOENT``: a concurrent GC that removed a now-empty
        ``<cache>/<pkg>/`` between :meth:`ensure_cache_dir` and the move makes
        the move fail for a completely recoverable reason, which used to
        surface as a ``CacheStoreError``.  Bounded at one retry -- a second
        ``ENOENT`` is not a race, it is a real problem (and a rename that fails
        ``ENOENT`` after a successful move would mean staging itself vanished).
        """
        for attempt in (0, 1):
            try:
                self._transfer(source_path, staging_dir, policy)
                if seal is not None:
                    seal(staging_dir)
                os.rename(staging_dir, version_dir)
                return
            except OSError as e:
                if e.errno != errno.ENOENT or attempt == 1:
                    raise
                if not os.path.exists(source_path):
                    raise            # the source is gone; retrying cannot help
                self.ensure_partition_dir(package_name, policy)

    def _transfer(self, source_path: str, staging_dir: str,
                  policy: Optional[ProtectionPolicy]):
        """Move the built tree into staging without changing who can read it.

        ``shutil.move`` was the wrong primitive.  On one filesystem it renames,
        which is perfect; across filesystems it silently degrades to
        ``copytree`` + ``copy2``, which preserves mode and xattrs but **not
        ownership** -- so every file was re-grouped to whatever the destination
        inherited.  That is one of the two mechanisms behind wrong-group cache
        entries, and it is invisible afterwards because the result looks
        entirely self-consistent.

        So the two cases are made explicit: rename when we can, and when we
        cannot, copy with a function that reproduces ownership and protection
        and *verifies* each node it writes.
        """
        try:
            os.rename(source_path, staging_dir)
            if policy is not None:
                # A rename carries the source's group, which is right when the
                # source was built inside this partition and wrong when it was
                # not (the deps-dir staging fallback).  Cheap to confirm; a
                # mismatch is corrected by the seal walk, which is already
                # about to touch every node.
                self._retag_root(staging_dir, policy)
            return
        except OSError as e:
            if e.errno != errno.EXDEV:
                raise
        from .fscopy import copy_tree
        copy_tree(source_path, staging_dir, policy)
        self._discard(source_path)

    def _retag_root(self, path: str, policy: ProtectionPolicy):
        from .protection import chgrp
        chgrp(path, policy.gid)

    # --- entry manifest ----------------------------------------------------
    #
    # Written INTO the entry (unlike the mutable sidecar, which lives beside
    # it) as the last step before the publish rename.  Two jobs:
    #
    #  * **Completion sentinel.**  Its presence is the only positive evidence
    #    that a directory at <cache>/<pkg>/<version> was published by IVPM
    #    rather than left behind by an interrupted copy.  "Non-empty" cannot
    #    distinguish those.
    #  * **Self-description.**  It records what the entry claims to be, so a
    #    later reader can detect an entry that was moved, hand-edited, or
    #    published under a colliding key -- none of which is visible from the
    #    bytes alone.
    #
    # It never records anything mutable: an immutable entry with a mutable
    # description inside it would need the concurrency story this design
    # exists to avoid.

    _ENTRY_MANIFEST = ".ivpm-cache-entry.json"
    _ENTRY_SCHEMA = 1

    # Manifest-less entries predate this feature.  While True they are still
    # trusted, so an existing cache keeps working across the upgrade; a later
    # release flips this to False, at which point such an entry is treated as
    # absent and rebuilt.  ``ivpm cache verify --upgrade`` backfills them.
    _legacy_entries_ok = True

    def entry_manifest_path(self, version_dir: str) -> str:
        return os.path.join(version_dir, self._ENTRY_MANIFEST)

    def read_entry_manifest(self, version_dir: str) -> Optional[dict]:
        """The entry's manifest, or None if absent/unreadable/not a dict.

        A pure reader: it never creates, repairs, or touches anything, so it is
        safe to call from a non-mutating verification pass.
        """
        try:
            with open(self.entry_manifest_path(version_dir)) as fp:
                data = json.load(fp)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _seal(self, staging_dir: str, package_name: str, version: str,
              source: Optional[dict] = None,
              policy: Optional[ProtectionPolicy] = None):
        """Lock a staging tree and describe it, in that order.

        Ordering matters and is easy to get backwards:

        1. **Drop any inherited manifest.**  A staging tree is often *derived*
           from an existing entry -- the patch resolver copies a cached
           pristine base and patches it -- so it can arrive already carrying
           the base's manifest, read-only, describing the wrong version.  It
           has to go before anything else, both so the write below is not
           blocked by its ``0444`` mode and so it is not counted as content.
        2. **Walk once**, applying protection, making the tree read-only and
           measuring it in the same pass; the manifest does not exist yet, so
           it cannot count itself.  This is also the last moment anything can
           be changed -- a sealed entry is unwritable -- so the policy's group
           and modes have to land here, not afterwards.
        3. **Write the manifest** into the still-writable entry root, then seal
           the root itself.  The root goes last precisely because a sealed root
           has no write bit for anyone -- including us -- so creating the
           manifest in it afterwards would fail.
        4. **Verify.**  Confirm the tree really carries the protection it was
           supposed to get before it becomes visible.  A wrong-group entry is
           indistinguishable from a correct one once published, so the check
           has to happen while refusing to publish is still an option.
        """
        root_mode = stat.S_IMODE(os.lstat(staging_dir).st_mode)
        self._drop_inherited_manifest(staging_dir, root_mode)
        counts = self._make_readonly_and_measure(staging_dir, seal_root=False,
                                                 policy=policy)
        counts["merkle"] = self._entry_merkle(staging_dir)
        self._write_entry_manifest(staging_dir, package_name, version,
                                   source, counts, policy)
        self._seal_entry_root(staging_dir, policy, root_mode)
        if policy is not None:
            self._verify_sealed(staging_dir, policy)

    def _verify_sealed(self, path: str, policy: ProtectionPolicy):
        """Confirm every node carries *policy*, or raise before publishing.

        Only runs when a policy is in force -- without one there is nothing to
        assert beyond "we did not add write bits", which the seal guarantees
        structurally by only ever masking them off.

        Symlinks are checked by ``lstat`` and never followed: their own mode is
        meaningless on Linux, and the group that matters is the link's, not its
        target's.
        """
        bad = []
        for node, st in self._walk_nodes(path):
            if st.st_gid != policy.gid:
                bad.append((node, "group %d" % st.st_gid))
            elif not stat.S_ISLNK(st.st_mode):
                want = (dir_seal_mode(st.st_mode, policy)
                        if stat.S_ISDIR(st.st_mode)
                        else file_seal_mode(st.st_mode, policy))
                if stat.S_IMODE(st.st_mode) != want:
                    bad.append((node, "mode 0o%o, wanted 0o%o"
                                % (stat.S_IMODE(st.st_mode), want)))
            if len(bad) >= 3:
                break
        if bad:
            raise ProtectionError(
                "cache entry does not carry its protection policy (%s); "
                "refusing to publish it.  Offending nodes: %s"
                % (policy, "; ".join("%s has %s" % (os.path.relpath(n, path), w)
                                     for n, w in bad)))

    def _walk_nodes(self, path: str):
        """Every node in the tree, root included, as ``(path, lstat)``.

        ``lstat`` throughout: a symlink is a node in its own right here, never
        a door into whatever it points at.
        """
        try:
            yield path, os.lstat(path)
        except OSError:
            return
        for root, dirnames, filenames in os.walk(path):
            for name in dirnames + filenames:
                p = os.path.join(root, name)
                try:
                    yield p, os.lstat(p)
                except OSError:
                    continue

    def _entry_merkle(self, staging_dir: str) -> Optional[str]:
        """A content hash of the tree, or None when the site does not want one.

        Recorded only under ``cache-verify: content``.  A merkle root is what
        makes bit rot detectable, but computing it reads every byte of every
        entry at publish time -- a real cost on a cache miss, paid on behalf of
        a check most sites will never run.  So the decision is the site's, and
        it is made here, once, rather than by hashing unconditionally and
        hoping nobody notices.
        """
        try:
            from .site_config import resolve_cache_verify_level
            if resolve_cache_verify_level() != "content":
                return None
            from .cache_verify import entry_merkle
            return entry_merkle(staging_dir, skip_name=self._ENTRY_MANIFEST)
        except (OSError, ImportError):
            return None

    def _drop_inherited_manifest(self, staging_dir: str, root_mode: int):
        path = self.entry_manifest_path(staging_dir)
        if not os.path.lexists(path):
            return
        try:
            os.chmod(path, 0o644)
        except OSError:
            pass
        # Unlinking is governed by the *directory*, not the file, and a staging
        # tree copied out of a sealed entry inherits its unwritable root.
        # Callers do re-open the copy for writing, but if one ever forgets,
        # failing here would leave the base's manifest in place and make every
        # lookup of the derived entry a miss -- forever, and silently.
        #
        # Owner-write is added, not _PKG_DIR_MODE: that mode is 2775, so using
        # it here would hand group-write and world-read to a tree whose whole
        # purpose may be to be readable by one group only.  The original mode
        # is restored immediately, and the seal overwrites it regardless.
        try:
            os.chmod(staging_dir, root_mode | stat.S_IRWXU)
        except OSError:
            pass
        try:
            os.remove(path)
        except OSError:
            pass
        try:
            os.chmod(staging_dir, root_mode)
        except OSError:
            pass

    def _write_entry_manifest(self, staging_dir, package_name, version,
                              source, counts, policy=None):
        # The *escaped* key, so the identity check works from either side: a
        # lookup arrives with the raw version, a cache scan reads the version
        # off the directory name.  Recording the raw one would make every
        # escaped entry look like a key collision to whichever side did not
        # normalize.
        from .utils import safe_version_key
        manifest = {
            "schema": self._ENTRY_SCHEMA,
            "package": package_name,
            "version": safe_version_key(version),
            "created": time.time(),
            "creator": {"ivpm": _ivpm_version(), "host": _hostname()},
            "content": {
                "files": counts["files"],
                "dirs": counts["dirs"],
                "bytes": counts["bytes"],
                "merkle": counts.get("merkle"),
            },
        }
        if source:
            manifest["source"] = source
        if policy is not None:
            # Recorded for diagnosis, not for identity -- identity is the
            # partition the entry lives in.  It answers "what was this entry
            # supposed to be protected as?" for an auditor holding only the
            # entry, which is the question a wrong-group investigation starts
            # from.
            manifest["protection"] = policy.describe()
        path = self.entry_manifest_path(staging_dir)
        try:
            with open(path, "w") as fp:
                json.dump(manifest, fp, sort_keys=True)
            if policy is not None:
                # The manifest is created *after* the seal walk, so it is the
                # one file in the entry the walk cannot have protected.  It
                # inherits the entry root's group only if that root is setgid,
                # which it is not when the root arrived here by rename from
                # outside the partition -- so set it explicitly rather than
                # rely on inheritance that holds on some paths and not others.
                from .protection import chgrp
                chgrp(path, policy.gid)
            os.chmod(path, file_seal_mode(0o644, policy))
        except OSError as e:
            # A cache that cannot take a manifest still gets a usable entry --
            # it is simply treated as legacy by later readers.  Failing the
            # whole store here would turn a diagnostic feature into an outage.
            # But say so: silently producing unsealed entries forever is how a
            # misconfigured shared cache stays misconfigured.
            note("Could not write the cache entry manifest for %s/%s (%s); "
                 "the entry is usable but unsealed" % (package_name, version, e))

    def _make_readonly_and_measure(self, path: str,
                                   seal_root: bool = True,
                                   policy: Optional[ProtectionPolicy] = None
                                   ) -> dict:
        """Apply protection, lock the tree, and census it -- in ONE walk.

        Three jobs share a walk because the tree is large and the walk is the
        expensive part: the manifest needs a file/dir/byte census, the seal
        needs a chmod of every node, and a policy (when there is one) needs the
        group applied to any node that did not inherit it.  The manifest file
        itself does not exist yet at this point, so it is naturally excluded
        from its own counts.

        **Directories are sealed by masking off write, not by forcing a fixed
        mode.**  Forcing ``_ENTRY_DIR_MODE`` (2555) published a ``0750`` source
        tree world-traversable -- it made every entry *more* accessible than
        its source, which is the opposite of what sealing is for.  Files were
        already handled this way; directories now match.

        **Symlinks are never followed.**  ``os.walk`` classifies a
        symlink-to-directory into ``dirnames``, and ``chmod``/``chown`` follow
        symlinks, so the old code modified whatever the link pointed at --
        demonstrably including directories outside the cache entirely.  A
        symlink's own mode is meaningless on Linux; only its group is set, via
        ``lchown``.

        *seal_root* exists because the entry root is sealed unwritable like
        every directory inside it, and :meth:`_seal` still has to create the
        manifest in it.  Publishing defers the root by one step; every other
        caller seals the whole tree in one go.
        """
        from .protection import chgrp
        files = dirs = 0
        total = 0
        for root, dirnames, filenames in os.walk(path):
            for d in dirnames:
                dirs += 1
                self._seal_node(os.path.join(root, d), policy, is_dir=True)
            for f in filenames:
                files += 1
                fp = os.path.join(root, f)
                st = self._seal_node(fp, policy, is_dir=False)
                if st is not None and stat.S_ISREG(st.st_mode):
                    total += st.st_size
        if seal_root:
            self._seal_entry_root(path, policy)
        return {"files": files, "dirs": dirs, "bytes": total}

    def _seal_node(self, node: str, policy: Optional[ProtectionPolicy],
                   *, is_dir: bool):
        """Protect and lock one node; return its ``lstat`` (None if unreadable).

        A ``chmod`` failure stays best-effort -- a shared cache legitimately
        contains entries owned by other users, and refusing to publish because
        one of them could not be re-sealed would be worse than the drift, which
        ``cache verify`` reports.  A *policy* failure does not: it propagates,
        because publishing content the wrong group can read is the failure this
        whole mechanism exists to prevent.
        """
        from .protection import chgrp
        try:
            st = os.lstat(node)
        except OSError:
            return None
        if policy is not None and st.st_gid != policy.gid:
            # Guarded on the stat we already have: content fetched inside the
            # partition inherited the group via setgid, so on the common path
            # every node is already correct and this walk does no chown at all.
            # It fires for a tree that arrived from elsewhere -- the deps-dir
            # staging fallback -- where inheritance could not apply.
            #
            # Propagates ProtectionError deliberately.  Uses lchown for links.
            chgrp(node, policy.gid, follow_symlinks=False)
        if stat.S_ISLNK(st.st_mode):
            return st                 # no mode of its own worth setting
        want = (dir_seal_mode(st.st_mode, policy) if is_dir
                else file_seal_mode(st.st_mode, policy))
        try:
            os.chmod(node, want)
        except OSError:
            pass
        return st

    def _seal_entry_root(self, path: str,
                         policy: Optional[ProtectionPolicy] = None,
                         root_mode: Optional[int] = None):
        """Seal the entry root, preserving its restrictions.

        *root_mode* is the mode the root had *before* anything in the seal
        touched it, which matters because :meth:`_drop_inherited_manifest` has
        to make it writable in between.  Sealing the post-drop mode instead
        would publish the widened one.
        """
        try:
            current = root_mode if root_mode is not None \
                else stat.S_IMODE(os.lstat(path).st_mode)
        except OSError:
            return
        if policy is not None:
            from .protection import chgrp
            chgrp(path, policy.gid)
        try:
            os.chmod(path, dir_seal_mode(current, policy))
        except OSError:
            pass

    def link_to_deps(self, package_name: str, version: str, deps_dir: str,
                     policy: Optional[ProtectionPolicy] = None) -> str:
        """Create a symlink from the deps directory to the cached version.
        
        Args:
            package_name: Name of the package
            version: Version identifier
            deps_dir: Dependencies directory
            
        Returns:
            Path to the symlink in deps_dir
        """
        version_dir = self.get_version_cache_dir(package_name, version, policy)
        # Defensive backstop: materialize is only called after a HIT or a
        # successful store (both guarantee populated), but a concurrent GC
        # removing the entry between lookup and here would otherwise symlink a
        # vanishing/empty target.
        if not self._is_populated(version_dir):
            raise CacheStoreError(
                package_name, version, "entry missing or empty at link time")
        link_path = os.path.join(deps_dir, package_name)

        # A real directory here is a materialized copy from an earlier run;
        # there is no way to swap that for a symlink without removing it first.
        if os.path.isdir(link_path) and not os.path.islink(link_path):
            shutil.rmtree(link_path)
        elif os.path.exists(link_path) and not os.path.islink(link_path):
            os.unlink(link_path)

        # Create the new link beside the old one and rename over it, rather than
        # unlink-then-symlink.  The old sequence left deps/<pkg> *absent* for
        # the width of two syscalls, so a build reading the deps dir in parallel
        # -- or a second ivpm run for the same workspace -- could see a package
        # that exists in every other respect simply not be there.  os.replace
        # over an existing symlink is atomic: the path names the old target or
        # the new one, never nothing.
        tmp_link = "%s.ivpm-link.%s" % (link_path, uuid.uuid4().hex)
        try:
            os.symlink(version_dir, tmp_link)
            os.replace(tmp_link, link_path)
        except BaseException:
            # Leave no .ivpm-link.* residue behind for the next run to puzzle
            # over; the rename either happened (nothing to clean) or did not.
            try:
                os.unlink(tmp_link)
            except OSError:
                pass
            raise
        # Linking is the single choke point for "this entry was referenced
        # into a workspace" — refresh last_linked here (covers both the cache
        # HIT path and the MISS→store→materialize path).
        self._touch_last_linked(package_name, version, policy)
        note(f"Linked {package_name} from cache")
        return link_path

    # --- stale-tracking sidecar -------------------------------------------
    #
    # Each entry <cache>/<pkg>/<version>/ gets a sibling sidecar
    # <cache>/<pkg>/<version>.meta.json recording when it was first stored and
    # when it was last referenced into a deps/ directory.  The sidecar lives in
    # the writable package directory, never inside the read-only entry, and is
    # advisory: any failure to read/write it degrades gracefully to dir-mtime.

    _META_SUFFIX = ".meta.json"
    _META_SCHEMA = 1
    _META_MODE = (
        stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH
    )  # 0o664 — group-writable so any member of a shared cache can refresh it

    def _meta_path(self, package_name: str, version: str,
                   policy: Optional[ProtectionPolicy] = None) -> str:
        # Built from the entry's own path, so the sidecar is always the entry's
        # sibling -- name validation and version escaping applied identically,
        # rather than a second hand-rolled join that could drift from it.
        return (self.get_version_cache_dir(package_name, version, policy)
                + self._META_SUFFIX)

    def _read_meta(self, package_name: str, version: str,
                   policy: Optional[ProtectionPolicy] = None) -> Optional[dict]:
        """Return the entry's sidecar dict, or None if absent/unreadable."""
        return self._read_meta_at(
            self._meta_path(package_name, version, policy))

    @staticmethod
    def _read_meta_at(path: str) -> Optional[dict]:
        try:
            with open(path) as fp:
                data = json.load(fp)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _write_meta(self, package_name: str, version: str, meta: dict,
                    policy: Optional[ProtectionPolicy] = None):
        """Atomically write the sidecar (tmp + rename), group-writable.

        Best-effort: a failure (e.g. an entry owned by another user in a
        shared cache) is non-fatal and just degrades that entry to dir-mtime.
        """
        self._write_meta_at(self._meta_path(package_name, version, policy), meta)

    def _write_meta_at(self, path: str, meta: dict):
        # uuid4, not the PID: worker threads share a PID, and PID namespaces
        # make two containers on one NFS cache collide routinely.  Two writers
        # sharing a temp name interleave their JSON into one file, and the
        # rename then publishes it -- an unparseable sidecar, which is a
        # reported problem rather than a silently ignored one.
        tmp = path + ".tmp." + uuid.uuid4().hex
        try:
            with open(tmp, "w") as fp:
                json.dump(meta, fp)
            try:
                os.chmod(tmp, self._META_MODE)
            except OSError:
                pass
            os.rename(tmp, path)
        except OSError:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def _delete_meta(self, package_name: str, version: str,
                     policy: Optional[ProtectionPolicy] = None):
        try:
            os.remove(self._meta_path(package_name, version, policy))
        except OSError:
            pass

    def _touch_last_linked(self, package_name: str, version: str,
                           policy: Optional[ProtectionPolicy] = None):
        """Refresh ``last_linked`` to now, preserving ``stored``.

        Lazily creates the sidecar for a pre-existing (sidecar-less) entry,
        seeding ``stored`` from the entry's directory mtime so its real age is
        not lost on the first touch.

        This is a read-modify-write with no compare-and-swap, so two concurrent
        touches can lose one update. That is fine for an advisory timestamp --
        both writers are writing approximately *now*, so the loser's value is
        the one nobody would miss. It would **not** be acceptable for a
        reference map, where a lost update means a live dependency looks
        unreferenced; that is why §6 of the design specifies per-referrer files
        rather than one shared document.
        """
        now = time.time()
        meta = self._read_meta(package_name, version, policy)
        if meta is None:
            try:
                stored = os.path.getmtime(
                    self.get_version_cache_dir(package_name, version, policy))
            except OSError:
                stored = now
            meta = {"schema": self._META_SCHEMA, "stored": stored}
        meta.setdefault("schema", self._META_SCHEMA)
        meta["last_linked"] = now
        self._write_meta(package_name, version, meta, policy)

    def touch_linked(self, package_name: str, version: str,
                     policy: Optional[ProtectionPolicy] = None) -> bool:
        """Refresh last_linked for an already-materialized entry (fast path).

        Returns True when the named entry exists and was touched.
        """
        if not self.has_version(package_name, version, policy):
            return False
        self._touch_last_linked(package_name, version, policy)
        return True

    def touch_linked_target(self, target_path: str) -> bool:
        """Refresh last_linked given a *symlink target* into this cache.

        Used by the already-loaded fast path, which has the existing
        ``deps/<pkg>`` symlink but not the version id.  Resolves the target to
        ``<cache>/<pkg>/<version>`` and touches it.  Returns False (no-op) when
        the path is not a version directory inside this cache — e.g. an
        editable clone or a deps-source link elsewhere.
        """
        if self.cache_dir is None:
            return False
        real_cache = os.path.realpath(self.cache_dir)
        rel = os.path.relpath(os.path.realpath(target_path), real_cache)
        parts = rel.split(os.sep)
        if rel.startswith(".."):
            return False
        # Two shapes now: <pkg>/<version> when no policy is in force, and
        # <pkg>/p.<digest>/<version> when one is.  The caller has a symlink and
        # no policy object, so the partition is read back off the path rather
        # than recomputed -- which is also the only way this works for a link
        # created by a *different* workspace's policy.
        if len(parts) == 2:
            package_name, partition, version = parts[0], None, parts[1]
        elif len(parts) == 3 and parts[1].startswith(PARTITION_PREFIX):
            package_name, partition, version = parts
        else:
            return False
        return self._touch_resolved(package_name, partition, version)

    def _touch_resolved(self, package_name: str, partition: Optional[str],
                        version: str) -> bool:
        """Touch an entry identified by its *path*, not by a policy object."""
        version_dir = os.path.join(self.get_package_cache_dir(package_name),
                                   *(p for p in (partition, version) if p))
        if not self._is_populated(version_dir):
            return False
        meta_path = version_dir + self._META_SUFFIX
        now = time.time()
        meta = self._read_meta_at(meta_path)
        if meta is None:
            try:
                stored = os.path.getmtime(version_dir)
            except OSError:
                stored = now
            meta = {"schema": self._META_SCHEMA, "stored": stored}
        meta.setdefault("schema", self._META_SCHEMA)
        meta["last_linked"] = now
        self._write_meta_at(meta_path, meta)
        return True

    # Permission bits for shared-cache directories.  There are *two* modes,
    # and the difference is the whole point:
    #
    # ``<cache>/<pkg>/`` is machinery.  Publishing renames a staging tree into
    # it and eviction renames an entry out of it, and both need write
    # permission on this directory for every group member -- so it is 2775.
    #
    # Directories *inside* a sealed entry are content, and content is
    # immutable.  Clearing the files' write bits does nothing to protect them:
    # unlink and rename are governed by the *parent* directory, so with a 2775
    # entry any group member could delete or replace any file in a "read-only"
    # entry -- and the next reader would be served the result as a cache hit.
    # 2555 removes that.
    _PKG_DIR_MODE = (
        stat.S_IRWXU | stat.S_IRWXG | stat.S_ISGID |
        stat.S_IROTH | stat.S_IXOTH
    )  # 0o2775 — rwxrwsr-x
    _ENTRY_DIR_MODE = (
        stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP |
        stat.S_ISGID | stat.S_IROTH | stat.S_IXOTH
    )  # 0o2555 — r-xr-sr-x

    #: Backwards-compatible name for the package-directory mode.
    _DIR_MODE = _PKG_DIR_MODE

    def _make_readonly(self, path: str):
        """Lock down a cached tree for shared use (see
        :meth:`_make_readonly_and_measure`, which is the implementation --
        there is deliberately only one sealing walk in this class).

        * **Files** — write bits are cleared so that no user can
          accidentally edit shared content.
        * **Directories** — set to ``r-xr-sr-x`` (2555): traversable and
          readable by everyone, writable by no one, so nothing inside a
          sealed entry can be unlinked or replaced.  The setgid bit
          survives so the group is still inherited if the tree is ever
          re-opened.  Eviction is unaffected: it renames the entry out of
          ``<cache>/<pkg>/`` (which stays 2775) and only then restores
          write permission on the tombstone.

        Silently skips entries that cannot be ``chmod``-ed (e.g. owned
        by another user in a shared cache).
        """
        self._make_readonly_and_measure(path)


    def get_cache_info(self) -> dict:
        """Get information about the cache.

        Returns dict with:
        - packages: list of package info dicts with name, versions, total_size
        - total_size: total size of cache in bytes

        Each version entry carries ``mtime`` plus the sidecar timestamps
        ``stored`` and ``last_linked`` (None when the entry has no sidecar),
        and ``partition`` — the protection partition it lives in, or None for
        an unpartitioned entry.

        ``unreadable`` lists partitions this caller cannot look inside.  A
        protected partition is 2770, so a cache holding several groups' content
        is *expected* to be partly invisible to any one user; reporting that as
        an empty cache would be a lie, and raising would make ``cache info``
        unusable on exactly the shared caches it is most needed for.
        """
        result = {
            "packages": [],
            "total_size": 0,
            "unreadable": [],
        }

        if not os.path.isdir(self.cache_dir):
            return result

        for pkg_name in sorted(self._listdir(self.cache_dir, result)):
            pkg_dir = os.path.join(self.cache_dir, pkg_name)
            if not os.path.isdir(pkg_dir):
                continue

            pkg_info = {
                "name": pkg_name,
                "versions": [],
                "total_size": 0
            }

            for partition, version, version_dir in self._iter_entries(pkg_dir,
                                                                      result):
                size = self._get_dir_size(version_dir)
                try:
                    mtime = os.path.getmtime(version_dir)
                except OSError:
                    continue
                meta = self._read_meta_at(
                    version_dir + self._META_SUFFIX) or {}

                pkg_info["versions"].append({
                    "version": version,
                    "partition": partition,
                    "size": size,
                    "mtime": mtime,
                    "stored": meta.get("stored"),
                    "last_linked": meta.get("last_linked"),
                })
                pkg_info["total_size"] += size

            result["packages"].append(pkg_info)
            result["total_size"] += pkg_info["total_size"]

        return result

    @staticmethod
    def _listdir(path: str, report: Optional[dict] = None) -> list:
        """``os.listdir`` that records what it could not read instead of raising.

        Every scan in this class now has to cross directories the caller may
        have no permission to enter, because that is what protection
        partitioning means.
        """
        try:
            return os.listdir(path)
        except OSError:
            if report is not None:
                report.setdefault("unreadable", []).append(path)
            return []

    def _iter_entries(self, pkg_dir: str, report: Optional[dict] = None):
        """Yield ``(partition, version, path)`` for every entry of a package.

        Flattens the two possible layouts -- ``<pkg>/<version>`` and
        ``<pkg>/p.<digest>/<version>`` -- so that scanning code (info, GC,
        verification) has exactly one shape to handle and cannot accidentally
        treat a partition directory as an entry.
        """
        for name in sorted(self._listdir(pkg_dir, report)):
            path = os.path.join(pkg_dir, name)
            if not os.path.isdir(path) or os.path.islink(path):
                continue          # sidecars and other non-dir siblings
            if self._is_transient(name):
                continue          # staging residue, or an awaiting-delete tomb
            if name.startswith(PARTITION_PREFIX):
                for sub in sorted(self._listdir(path, report)):
                    spath = os.path.join(path, sub)
                    if not os.path.isdir(spath) or os.path.islink(spath):
                        continue
                    if self._is_transient(sub):
                        continue
                    yield name, sub, spath
            else:
                yield None, name, path

    def _get_dir_size(self, path: str) -> int:
        """Total size of a directory in bytes.

        ``lstat``, and regular files only: a symlink contributes its own (tiny)
        size, never its target's.  Following them double-counted every
        internally-symlinked file and could count bytes from outside the cache
        entirely.
        """
        total = 0
        for root, dirs, files in os.walk(path):
            for f in files:
                try:
                    st = os.lstat(os.path.join(root, f))
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode):
                    total += st.st_size
        return total
    
    def entry_last_used(self, package_name: str, version: str,
                        policy: Optional[ProtectionPolicy] = None) -> float:
        """Most-recent "use" timestamp for a cached entry.

        ``max(dir-mtime, stored, last_linked)``, where ``last_linked`` is
        refreshed every time IVPM references the entry into a workspace.  When
        the sidecar is missing or unreadable (a best-effort write that failed,
        or a hand-managed cache) this collapses to the directory mtime, so GC
        degrades safely rather than treating the entry as brand-new or ancient.
        """
        version_dir = self.get_version_cache_dir(package_name, version, policy)
        base = os.path.getmtime(version_dir)
        ts = base
        meta = self._read_meta(package_name, version, policy)
        if meta:
            ts = max(ts, meta.get("stored", base), meta.get("last_linked", base))
        return ts

    def _sweep_orphan_meta(self, pkg_dir: str):
        """Remove ``*.meta.json`` sidecars with no matching version directory.

        Covers entries removed out-of-band (e.g. a manual ``rm -rf``) whose
        sidecar would otherwise orphan.
        """
        try:
            entries = os.listdir(pkg_dir)
        except OSError:
            return
        for name in entries:
            if not name.endswith(self._META_SUFFIX):
                continue
            version = name[:-len(self._META_SUFFIX)]
            if not os.path.isdir(os.path.join(pkg_dir, version)):
                try:
                    os.remove(os.path.join(pkg_dir, name))
                except OSError:
                    pass

    def _partition_dirs(self, pkg_dir: str) -> list:
        return [os.path.join(pkg_dir, n)
                for n in sorted(self._listdir(pkg_dir))
                if n.startswith(PARTITION_PREFIX)
                and os.path.isdir(os.path.join(pkg_dir, n))]

    def _last_used_at(self, version_dir: str) -> float:
        """:meth:`entry_last_used` for an entry identified by path.

        The scanning callers read entries off the filesystem and have no policy
        object to rebuild a path from -- and must not need one, since a GC run
        legitimately sees partitions belonging to policies it knows nothing
        about.
        """
        try:
            base = os.path.getmtime(version_dir)
        except OSError:
            return 0.0
        ts = base
        meta = self._read_meta_at(version_dir + self._META_SUFFIX)
        if meta:
            ts = max(ts, meta.get("stored", base), meta.get("last_linked", base))
        return ts

    def _evict_at(self, version_dir: str) -> bool:
        """:meth:`_evict` for an entry identified by path."""
        tomb = os.path.join(os.path.dirname(version_dir),
                            self._TOMB_MARKER + uuid.uuid4().hex)
        try:
            os.rename(version_dir, tomb)
        except OSError:
            return False
        try:
            os.remove(version_dir + self._META_SUFFIX)
        except OSError:
            pass
        self._discard(tomb)
        return True

    def clean_older_than(self, days: int, dry_run: bool = False) -> int:
        """Remove cache entries whose *last-used* age exceeds ``days``.

        Last-used is :meth:`entry_last_used` — ``max(stored, last_linked,
        dir-mtime)`` — so an entry symlinked into a live workspace survives
        even if it was first cached long ago.  With no sidecar this collapses
        to the directory mtime (legacy behavior).

        Returns the number of entries removed, or — when ``dry_run`` — the
        number that *would* be removed.  Orphaned sidecars are swept alongside.
        """
        cutoff = time.time() - (days * 24 * 60 * 60)
        removed = 0

        if not os.path.isdir(self.cache_dir):
            return removed

        for pkg_name in sorted(self._listdir(self.cache_dir)):
            pkg_dir = os.path.join(self.cache_dir, pkg_name)
            if not os.path.isdir(pkg_dir):
                continue

            for partition, version, version_dir in list(
                    self._iter_entries(pkg_dir)):
                meta_path = version_dir + self._META_SUFFIX

                # An empty version dir is a crash leftover, not a real entry;
                # drop it regardless of age so it never poses as a HIT.
                # ``rmdir``, not ``rmtree``: it fails ENOTEMPTY exactly when a
                # builder won the race and published a real entry into that
                # path between the check and here -- which is the outcome we
                # want, and which ``rmtree`` used to destroy.
                if not self._is_populated(version_dir):
                    if not dry_run:
                        try:
                            os.rmdir(version_dir)
                            os.remove(meta_path)
                        except OSError:
                            pass
                    continue

                # Re-read last-used immediately before evicting, not earlier,
                # to shrink the window against a concurrent touch_linked().
                if self._last_used_at(version_dir) < cutoff:
                    if dry_run:
                        removed += 1
                    elif self._evict_at(version_dir):
                        removed += 1
                    else:
                        continue     # lost to another evictor — don't count it
                    note("%s cached %s/%s" % (
                        "Would remove" if dry_run else "Removed",
                        pkg_name,
                        version if partition is None
                        else "%s/%s" % (partition, version)))

            if dry_run:
                continue

            # Sweep orphaned sidecars, stale staging and undeleted tombstones,
            # in the package directory and in every partition under it -- a
            # partition is where staging and tombstones now live.
            for d in [pkg_dir] + self._partition_dirs(pkg_dir):
                self._sweep_orphan_meta(d)
                self._sweep_stale_staging(d)
                self._sweep_stale_tombs(d)
            # Deliberately NOT removing a now-empty <cache>/<pkg>/: it races a
            # concurrent builder that has just called ensure_cache_dir() into a
            # spurious CacheStoreError, and saves nothing but an empty inode.

        return removed
    
    def _make_writable(self, path: str):
        """Restore *owner* write permission before ``shutil.rmtree``.

        A sealed entry's directories have no write bit at all, so ``rmtree``
        cannot unlink anything inside them until one comes back -- directories
        matter here at least as much as files.  Skips entries that cannot be
        modified (owned by another user in a shared cache); the caller is
        ``_evict``, which has already renamed the entry out of view, so what
        survives is inert residue that ``cache verify`` reports rather than a
        half-dismantled tree that still reads as a HIT.

        Two things this must not do, both of which it used to:

        * **Widen.**  It set directories to ``_PKG_DIR_MODE`` (2775), handing
          group-write and world-read to a tree on its way to deletion -- which
          on a shared cache is a real window, since a failed ``rmtree`` leaves
          it at that mode indefinitely.  Only ``S_IRWXU`` is added now: the
          minimum that lets the owner unlink, visible to nobody new.
        * **Follow symlinks.**  ``os.walk`` puts a symlink-to-directory in
          ``dirs`` and ``os.stat``/``os.chmod`` follow it, so evicting a
          package that contained one modified the *target* -- demonstrably
          turning an external ``0700`` directory into ``2775`` and an external
          read-only file writable.  ``rmtree`` itself never follows links, so
          there was never a reason to.
        """
        for root, dirs, files in os.walk(path, topdown=False):
            for name in files + dirs:
                self._reopen(os.path.join(root, name))
        self._reopen(path)

    @staticmethod
    def _reopen(p: str):
        """Give the owner back the minimum needed to unlink through *p*."""
        try:
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                return                # unlinked via its parent; has no mode
            add = stat.S_IRWXU if stat.S_ISDIR(st.st_mode) else stat.S_IWUSR
            os.chmod(p, stat.S_IMODE(st.st_mode) | add)
        except OSError:
            pass

    def _discard(self, path: str):
        """Delete a tree that may be sealed.

        Every ``rmtree`` in this class is really this: sealed directories are
        2555, and ``rmtree`` unlinks through the *parent* directory, so it
        cannot remove a single file inside a sealed entry.  Pairing the two
        calls in one place means a future rmtree cannot forget the chmod --
        which failed silently, leaving the whole tree behind.
        """
        if not os.path.lexists(path):
            return
        if os.path.islink(path) or not os.path.isdir(path):
            try:
                os.unlink(path)
            except OSError:
                pass
            return
        self._make_writable(path)
        shutil.rmtree(path, ignore_errors=True)


class Cache(DirectoryCacheStore):
    """Deprecated alias for :class:`DirectoryCacheStore`.

    Retained for one release to protect external importers.  Unlike the
    store, it still resolves the cache location (explicit ``cache_dir`` →
    ``IVPM_CACHE`` → site default → ``None``) and tolerates a ``None``
    directory, reporting it as disabled via :meth:`is_enabled`.  New code
    should construct a provider via ``SiteConfig.get_cache_provider`` and
    let the store be created with an explicit directory.
    """

    def __init__(self, cache_dir: Optional[str] = None):
        if cache_dir is None:
            env_val = os.environ.get("IVPM_CACHE")
            if env_val is not None:
                cache_dir = env_val
            else:
                default = get_site_config().get_default_cache_dir()
                cache_dir = default if default else None
        super().__init__(cache_dir)


def is_github_url(url: str) -> bool:
    """Check if a URL is a GitHub URL."""
    return "github.com" in url


def parse_github_url(url: str) -> tuple:
    """Parse a GitHub URL to extract owner and repo.
    
    Supports formats:
    - https://github.com/owner/repo.git
    - git@github.com:owner/repo.git
    - https://github.com/owner/repo
    
    Returns:
        Tuple of (owner, repo) or (None, None) if not a GitHub URL
    """
    if not url:
        return None, None
    
    # Handle https:// URLs
    if "github.com/" in url:
        parts = url.split("github.com/")[-1]
        parts = parts.rstrip(".git").rstrip("/")
        if "/" in parts:
            owner, repo = parts.split("/", 1)
            # Handle additional path components (e.g., /tree/branch)
            repo = repo.split("/")[0]
            return owner, repo
    
    # Handle git@ URLs
    if "github.com:" in url:
        parts = url.split("github.com:")[-1]
        parts = parts.rstrip(".git").rstrip("/")
        if "/" in parts:
            owner, repo = parts.split("/", 1)
            return owner, repo
    
    return None, None

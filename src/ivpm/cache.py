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

    def get_version_cache_dir(self, package_name: str, version: str) -> str:
        """Get the cache directory for a specific package version.

        The version key is *escaped* rather than rejected: it is machine-made
        (an ETag, a commit hash, a release tag), and refusing to cache a
        package because its server returned a validator with a ``/`` in it
        would be a worse answer than storing it under an escaped name. Keys
        that are already safe pass through byte-identical, so no existing entry
        moves. Every caller reaches the cache through here, so publishing,
        lookup and eviction cannot disagree about where an entry lives.
        """
        from .utils import safe_version_key
        return os.path.join(self.get_package_cache_dir(package_name),
                            safe_version_key(version))
    
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
            if not os.path.isdir(version_dir):
                return False
            if not os.listdir(version_dir):
                return False
            if os.path.isfile(self.entry_manifest_path(version_dir)):
                return True
            return self._legacy_entries_ok
        except OSError:
            return False

    def has_version(self, package_name: str, version: str) -> bool:
        """Check if a specific version is cached (present and non-empty)."""
        version_dir = self.get_version_cache_dir(package_name, version)
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

    def _evict(self, package_name: str, version: str) -> bool:
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
        version_dir = self.get_version_cache_dir(package_name, version)
        pkg_dir = self.get_package_cache_dir(package_name)
        tomb = os.path.join(pkg_dir, self._TOMB_MARKER + uuid.uuid4().hex)
        try:
            os.rename(version_dir, tomb)
        except OSError:
            return False           # already gone, or another evictor won
        self._delete_meta(package_name, version)
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
    
    def new_staging(self, package_name: str) -> str:
        """A unique, not-yet-created staging path on the CACHE filesystem.

        Returned as a *sibling* of the package's version directories, so a
        tree built here and handed to :meth:`store_version` publishes with a
        same-filesystem ``rename`` instead of a cross-device copy.  The caller
        populates the path (it does not exist yet) and passes it to
        :meth:`store_version`.  The ``.staging.`` marker keeps it out of cache
        scans (:meth:`get_cache_info`, :meth:`clean_older_than`) and makes it
        eligible for the stale-staging sweep if a build crashes.
        """
        pkg_cache_dir = self.ensure_cache_dir(package_name)
        return os.path.join(pkg_cache_dir, "build.staging." + uuid.uuid4().hex)

    def store_version(self, package_name: str, version: str, source_path: str,
                      source: Optional[dict] = None) -> str:
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
        version_dir = self.get_version_cache_dir(package_name, version)

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
                self._discard(source_path)
            # Re-storing an extant entry still counts as using it.
            self._touch_last_linked(package_name, version)
            return version_dir

        self.ensure_cache_dir(package_name)

        # --- Atomic publish (the mutual-exclusion primitive) --------------
        # Build under a unique staging name, then publish with a single
        # ``os.rename``.  Two invariants make this race-safe WITHOUT a lock:
        #
        #  * INVARIANT (H1): staging is a SIBLING of version_dir (same
        #    directory => same filesystem), so ``os.rename`` is atomic and its
        #    ``ENOTEMPTY`` failure when version_dir already exists IS the
        #    serialization point.  Do not relocate staging off this filesystem.
        #  * The staging name is uuid4-unique (H2), so concurrent worker
        #    threads (which share a PID), separate processes, and reused PIDs
        #    from a prior crashed run can never collide — ``shutil.move`` can
        #    never nest ``source_path`` inside a stale staging dir.
        # NOTE (group ownership): on the same filesystem ``shutil.move`` is a
        # rename, which *preserves* group ownership -- so a tree prepared with a
        # per-package group (see pkg-prepare-design.md) keeps that group when it
        # is published into the cache, and the symlink back into the deps-dir is
        # therefore correct by construction. Across filesystems ``move`` falls
        # back to a copy, and the copied files are *created* under
        # ``<cache>/<pkg>/`` -- which is setgid to the cache's own group -- so the
        # group silently changes. The assertion below constrains staging relative
        # to version_dir only; it says nothing about where source_path lives.
        # Not corrected here: doing so means an O(files) chgrp walk on the cache
        # path, and the group a shared entry should carry is a deployment
        # question (see design §5.4). Recorded so it is not mistaken for
        # working.
        staging_dir = version_dir + ".staging." + uuid.uuid4().hex
        self._check_sibling(package_name, version, staging_dir, version_dir)
        try:
            self._move_and_publish(package_name, source_path, staging_dir,
                                   version_dir, seal=lambda staging: self._seal(
                                       staging, package_name, version, source))
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
                self._discard(source_path)
                return version_dir
            self._discard(source_path)
            raise CacheStoreError(package_name, version, e) from e

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
        })

        note(f"Cached {package_name} version {version}")
        return version_dir

    @staticmethod
    def _check_sibling(package_name: str, version: str,
                       staging_dir: str, version_dir: str):
        """Enforce INVARIANT H1: staging is a sibling of the entry.

        Same directory => same filesystem => ``os.rename`` is atomic, and its
        ``ENOTEMPTY`` failure is the serialization point that makes the whole
        store lock-free.  Checked unconditionally rather than with an
        ``assert``, which disappears under ``python -O`` -- exactly the
        configuration in which a silent non-atomic publish would do the most
        damage.
        """
        if os.path.dirname(staging_dir) != os.path.dirname(version_dir):
            raise CacheStoreError(
                package_name, version,
                "staging %s is not a sibling of the entry %s -- atomic "
                "publish is not possible" % (staging_dir, version_dir))

    def _move_and_publish(self, package_name: str, source_path: str,
                          staging_dir: str, version_dir: str, seal=None):
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
                shutil.move(source_path, staging_dir)
                if seal is not None:
                    seal(staging_dir)
                os.rename(staging_dir, version_dir)
                return
            except OSError as e:
                if e.errno != errno.ENOENT or attempt == 1:
                    raise
                if not os.path.exists(source_path):
                    raise            # the source is gone; retrying cannot help
                self.ensure_cache_dir(package_name)

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
              source: Optional[dict] = None):
        """Lock a staging tree and describe it, in that order.

        Ordering matters and is easy to get backwards:

        1. **Drop any inherited manifest.**  A staging tree is often *derived*
           from an existing entry -- the patch resolver copies a cached
           pristine base and patches it -- so it can arrive already carrying
           the base's manifest, read-only, describing the wrong version.  It
           has to go before anything else, both so the write below is not
           blocked by its ``0444`` mode and so it is not counted as content.
        2. **Walk once**, making the tree read-only and measuring it in the
           same pass; the manifest does not exist yet, so it cannot count
           itself.
        3. **Write the manifest** into the still-writable entry root, then seal
           the root itself.  The root goes last precisely because a 2555 root
           has no write bit for anyone -- including us -- so creating the
           manifest in it afterwards would fail.
        """
        self._drop_inherited_manifest(staging_dir)
        counts = self._make_readonly_and_measure(staging_dir, seal_root=False)
        counts["merkle"] = self._entry_merkle(staging_dir)
        self._write_entry_manifest(staging_dir, package_name, version,
                                   source, counts)
        self._seal_entry_root(staging_dir)

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

    def _drop_inherited_manifest(self, staging_dir: str):
        path = self.entry_manifest_path(staging_dir)
        if not os.path.lexists(path):
            return
        try:
            os.chmod(path, 0o644)
        except OSError:
            pass
        # Unlinking is governed by the *directory*, not the file, and a staging
        # tree copied out of a sealed entry inherits its 2555 root.  Callers do
        # re-open the copy for writing, but if one ever forgets, failing here
        # would leave the base's manifest in place and make every lookup of the
        # derived entry a miss -- forever, and silently.
        try:
            os.chmod(staging_dir, self._PKG_DIR_MODE)
        except OSError:
            pass
        try:
            os.remove(path)
        except OSError:
            pass

    def _write_entry_manifest(self, staging_dir, package_name, version,
                              source, counts):
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
        path = self.entry_manifest_path(staging_dir)
        try:
            with open(path, "w") as fp:
                json.dump(manifest, fp, sort_keys=True)
            os.chmod(path, 0o444)
        except OSError as e:
            # A cache that cannot take a manifest still gets a usable entry --
            # it is simply treated as legacy by later readers.  Failing the
            # whole store here would turn a diagnostic feature into an outage.
            # But say so: silently producing unsealed entries forever is how a
            # misconfigured shared cache stays misconfigured.
            note("Could not write the cache entry manifest for %s/%s (%s); "
                 "the entry is usable but unsealed" % (package_name, version, e))

    def _make_readonly_and_measure(self, path: str,
                                   seal_root: bool = True) -> dict:
        """:meth:`_make_readonly`, plus the content counts, in ONE walk.

        The manifest needs a file/dir/byte census and the seal needs a chmod of
        every node; doing them separately would double the cost of every cache
        miss on a large tree for no reason.  The manifest file itself does not
        exist yet at this point, so it is naturally excluded from its own
        counts.

        *seal_root* exists because the entry root is now sealed unwritable
        (2555) like every directory inside it, and :meth:`_seal` still has to
        create the manifest in it.  Publishing defers the root by one step;
        every other caller seals the whole tree in one go.
        """
        files = dirs = 0
        total = 0
        for root, dirnames, filenames in os.walk(path):
            for d in dirnames:
                dirs += 1
                try:
                    os.chmod(os.path.join(root, d), self._ENTRY_DIR_MODE)
                except OSError:
                    pass
            for f in filenames:
                files += 1
                fp = os.path.join(root, f)
                try:
                    st = os.lstat(fp)
                    if stat.S_ISREG(st.st_mode):
                        total += st.st_size
                        os.chmod(fp, st.st_mode & ~stat.S_IWUSR
                                 & ~stat.S_IWGRP & ~stat.S_IWOTH)
                except OSError:
                    pass
        if seal_root:
            self._seal_entry_root(path)
        return {"files": files, "dirs": dirs, "bytes": total}

    def _seal_entry_root(self, path: str):
        try:
            os.chmod(path, self._ENTRY_DIR_MODE)
        except OSError:
            pass

    def link_to_deps(self, package_name: str, version: str, deps_dir: str) -> str:
        """Create a symlink from the deps directory to the cached version.
        
        Args:
            package_name: Name of the package
            version: Version identifier
            deps_dir: Dependencies directory
            
        Returns:
            Path to the symlink in deps_dir
        """
        version_dir = self.get_version_cache_dir(package_name, version)
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
        self._touch_last_linked(package_name, version)
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

    def _meta_path(self, package_name: str, version: str) -> str:
        # Built from the entry's own path, so the sidecar is always the entry's
        # sibling -- name validation and version escaping applied identically,
        # rather than a second hand-rolled join that could drift from it.
        return (self.get_version_cache_dir(package_name, version)
                + self._META_SUFFIX)

    def _read_meta(self, package_name: str, version: str) -> Optional[dict]:
        """Return the entry's sidecar dict, or None if absent/unreadable."""
        try:
            with open(self._meta_path(package_name, version)) as fp:
                data = json.load(fp)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _write_meta(self, package_name: str, version: str, meta: dict):
        """Atomically write the sidecar (tmp + rename), group-writable.

        Best-effort: a failure (e.g. an entry owned by another user in a
        shared cache) is non-fatal and just degrades that entry to dir-mtime.
        """
        path = self._meta_path(package_name, version)
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

    def _delete_meta(self, package_name: str, version: str):
        try:
            os.remove(self._meta_path(package_name, version))
        except OSError:
            pass

    def _touch_last_linked(self, package_name: str, version: str):
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
        meta = self._read_meta(package_name, version)
        if meta is None:
            try:
                stored = os.path.getmtime(
                    self.get_version_cache_dir(package_name, version))
            except OSError:
                stored = now
            meta = {"schema": self._META_SCHEMA, "stored": stored}
        meta.setdefault("schema", self._META_SCHEMA)
        meta["last_linked"] = now
        self._write_meta(package_name, version, meta)

    def touch_linked(self, package_name: str, version: str) -> bool:
        """Refresh last_linked for an already-materialized entry (fast path).

        Returns True when the named entry exists and was touched.
        """
        if not self.has_version(package_name, version):
            return False
        self._touch_last_linked(package_name, version)
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
        if rel.startswith("..") or len(parts) != 2:
            return False
        package_name, version = parts
        return self.touch_linked(package_name, version)

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
        ``stored`` and ``last_linked`` (None when the entry has no sidecar).
        """
        result = {
            "packages": [],
            "total_size": 0
        }

        if not os.path.isdir(self.cache_dir):
            return result

        for pkg_name in os.listdir(self.cache_dir):
            pkg_dir = os.path.join(self.cache_dir, pkg_name)
            if not os.path.isdir(pkg_dir):
                continue

            pkg_info = {
                "name": pkg_name,
                "versions": [],
                "total_size": 0
            }

            for version in os.listdir(pkg_dir):
                version_dir = os.path.join(pkg_dir, version)
                if not os.path.isdir(version_dir):
                    continue  # skip *.meta.json sidecars and other non-dirs
                if self._is_transient(version):
                    continue  # in-flight/stale staging, or an awaiting-delete tomb

                size = self._get_dir_size(version_dir)
                mtime = os.path.getmtime(version_dir)
                meta = self._read_meta(pkg_name, version) or {}

                pkg_info["versions"].append({
                    "version": version,
                    "size": size,
                    "mtime": mtime,
                    "stored": meta.get("stored"),
                    "last_linked": meta.get("last_linked"),
                })
                pkg_info["total_size"] += size

            result["packages"].append(pkg_info)
            result["total_size"] += pkg_info["total_size"]

        return result
    
    def _get_dir_size(self, path: str) -> int:
        """Get total size of a directory in bytes."""
        total = 0
        for root, dirs, files in os.walk(path):
            for f in files:
                fp = os.path.join(root, f)
                if os.path.isfile(fp):
                    total += os.path.getsize(fp)
        return total
    
    def entry_last_used(self, package_name: str, version: str) -> float:
        """Most-recent "use" timestamp for a cached entry.

        ``max(dir-mtime, stored, last_linked)``, where ``last_linked`` is
        refreshed every time IVPM references the entry into a workspace.  When
        the sidecar is missing or unreadable (a best-effort write that failed,
        or a hand-managed cache) this collapses to the directory mtime, so GC
        degrades safely rather than treating the entry as brand-new or ancient.
        """
        version_dir = self.get_version_cache_dir(package_name, version)
        base = os.path.getmtime(version_dir)
        ts = base
        meta = self._read_meta(package_name, version)
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

        for pkg_name in os.listdir(self.cache_dir):
            pkg_dir = os.path.join(self.cache_dir, pkg_name)
            if not os.path.isdir(pkg_dir):
                continue

            for version in list(os.listdir(pkg_dir)):
                version_dir = os.path.join(pkg_dir, version)
                if not os.path.isdir(version_dir):
                    continue  # skip sidecars and other non-dir siblings
                if self._is_transient(version):
                    continue  # staging/tombstone residue — swept below

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
                            self._delete_meta(pkg_name, version)
                        except OSError:
                            pass
                    continue

                # Re-read last-used immediately before evicting, not earlier,
                # to shrink the window against a concurrent touch_linked().
                if self.entry_last_used(pkg_name, version) < cutoff:
                    if dry_run:
                        removed += 1
                    elif self._evict(pkg_name, version):
                        removed += 1
                    else:
                        continue     # lost to another evictor — don't count it
                    note("%s cached %s/%s" % (
                        "Would remove" if dry_run else "Removed",
                        pkg_name, version))

            if dry_run:
                continue

            # Sweep orphaned sidecars, stale staging and undeleted tombstones.
            self._sweep_orphan_meta(pkg_dir)
            self._sweep_stale_staging(pkg_dir)
            self._sweep_stale_tombs(pkg_dir)
            # Deliberately NOT removing a now-empty <cache>/<pkg>/: it races a
            # concurrent builder that has just called ensure_cache_dir() into a
            # spurious CacheStoreError, and saves nothing but an empty inode.

        return removed
    
    def _make_writable(self, path: str):
        """Restore write permission before ``shutil.rmtree``.

        A sealed entry's directories are 2555, so ``rmtree`` cannot unlink
        anything inside them until the write bit comes back -- directories
        matter here at least as much as files.  Skips entries that cannot be
        modified (owned by another user in a shared cache); the caller is
        ``_evict``, which has already renamed the entry out of view, so what
        survives is inert residue that ``cache verify`` reports rather than a
        half-dismantled tree that still reads as a HIT.
        """
        for root, dirs, files in os.walk(path, topdown=False):
            for f in files:
                try:
                    fp = os.path.join(root, f)
                    mode = os.stat(fp).st_mode
                    os.chmod(fp, mode | stat.S_IWUSR)
                except OSError:
                    pass
            for d in dirs:
                try:
                    dp = os.path.join(root, d)
                    os.chmod(dp, self._PKG_DIR_MODE)
                except OSError:
                    pass
        try:
            os.chmod(path, self._PKG_DIR_MODE)
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

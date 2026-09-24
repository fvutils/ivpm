#****************************************************************************
#* cache_provider.py
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
"""Cache provider seam.

The configuration layer answers *"give me the cache provider for this
session"* rather than *"where is the cache directory?"*.  A provider is
created once per ``ivpm update`` run, is aware of the root project, and
serves cache decisions for every dependency (the dependency is passed to
each method).  The provider owns the cache intelligence:

* :meth:`CacheProvider.is_cacheable` — is caching enabled *and* available
  for this dependency?
* :meth:`CacheProvider.lookup` — is a given version of this dependency
  cached (HIT), absent (MISS), or is the dependency uncacheable (DISABLED)?

A disabled cache is still a real provider (:class:`NullCacheProvider`) whose
every lookup reports DISABLED ("uncacheable"); callers never receive ``None``.
"""
import os
import uuid
import threading
import dataclasses as dc
import enum
from typing import TYPE_CHECKING, Optional

from .msg import error, note, warning
from .protection import policy_for

if TYPE_CHECKING:
    from .cache import DirectoryCacheStore


def acquire_staging(provider, pkg, deps_dir: str) -> str:
    """A unique, not-yet-created build directory, preferring the cache FS.

    Every fetch path that ends in ``provider.store(...)`` must build its tree
    here rather than at a fixed path.  Two properties matter, and both are
    correctness properties rather than conveniences:

    * **Uniqueness.**  A fixed name (the old ``.cache_temp_<pkg>``) is shared by
      every concurrent fetch of the same package -- worker threads in one run,
      and separate ``ivpm update`` processes over one deps-dir.  Whoever arrives
      second used to ``rmtree`` the first one's half-built tree out from under
      it, and could then publish a truncated entry.  A uuid4 suffix makes that
      impossible regardless of thread, process, host, or reused PID.
    * **Locality.**  Cache-side staging makes the subsequent ``store()`` a
      same-directory ``rename`` instead of a cross-device copy of the whole
      tree.

    Falls back to a uniquely-named deps-dir path when the provider offers no
    cache-side staging, or when the cache side is not writable (read-only
    mount, foreign-owned package directory).  The fallback is still uuid-unique,
    so it costs a tree copy but never correctness.
    """
    try:
        staging = provider.new_staging(pkg)
    except OSError as e:
        # OSError only, and deliberately NOT ProtectionError.  A cache side
        # that is merely unavailable (read-only mount, foreign-owned package
        # directory, no space) is a locality problem: staging elsewhere costs a
        # tree copy and nothing else.  A protection policy that cannot be
        # applied is not -- falling back would fetch the whole package and then
        # fail at publish anyway, and a reader skimming this would reasonably
        # mistake the fallback for a safe degradation when it is the opposite.
        # Let it propagate; it already carries a message naming the group.
        note("Cache staging unavailable for %s (%s); staging under %s instead"
             % (getattr(pkg, "name", "?"), e, deps_dir))
        staging = None
    if staging is None:
        os.makedirs(deps_dir, exist_ok=True)
        # Same private-parent shape as cache-side staging: the tree being
        # fetched is not readable by anyone else while it is incomplete, and
        # the caller still receives a path that does not exist yet (git clone,
        # copytree and the zip extractor all insist on creating it).
        private = os.path.join(
            deps_dir,
            ".ivpm-fetch.%s.%s" % (getattr(pkg, "name", "pkg"), uuid.uuid4().hex))
        os.mkdir(private, 0o700)
        policy = policy_for(pkg)
        if policy is not None:
            from .protection import chgrp
            chgrp(private, policy.gid)
            os.chmod(private, 0o2700)
        staging = os.path.join(private, "tree")
    return staging


def discard_staging(staging: str) -> None:
    """Remove a staging tree *and* the private parent it lives in.

    Every fetch path has an error branch that throws away a half-built tree.
    Removing only the tree leaves an empty private directory behind -- in the
    deps-dir, where nothing sweeps it, so it accumulates one per failed fetch.
    """
    import shutil
    shutil.rmtree(staging, ignore_errors=True)
    parent = os.path.dirname(os.path.abspath(staging))
    base = os.path.basename(parent)
    if ".staging." in base or base.startswith(".ivpm-fetch."):
        try:
            os.rmdir(parent)       # empty-only: never guesses at what is left
        except OSError:
            pass


#: Fields that identify *where an entry's bytes came from*.  Recorded in the
#: entry manifest and compared on a later HIT.  Deliberately built from
#: attributes every package type already resolves, rather than from a
#: per-type hook, so no package type can silently opt out of provenance.
_SOURCE_FIELDS = ("src_type", "url", "branch", "tag", "commit",
                  "resolved_commit", "resolved_etag", "resolved_last_modified")


def source_info(pkg) -> Optional[dict]:
    """A JSON-able description of *pkg*'s resolved source, or None.

    This is the only thing that makes a cache-key collision *visible*: two
    packages that share a name and a version string are indistinguishable by
    key, but their sources differ, so a HIT whose manifest names a different
    source can be rejected instead of silently serving the wrong bytes.
    """
    info = {}
    for field in _SOURCE_FIELDS:
        value = getattr(pkg, field, None)
        if value is not None and value != "":
            info[field] = str(value)
    return info or None


#: The subset of :data:`_SOURCE_FIELDS` that identifies the *origin* rather
#: than a label pointing into it.  ``branch``/``tag``/``commit`` legitimately
#: differ between two references to the same content, so comparing them would
#: manufacture collisions that do not exist.
_IDENTITY_FIELDS = ("src_type", "url")


def _content_addressed(pkg, version: str) -> bool:
    """Is *version* a digest of the content itself (a git commit)?

    When it is, two different URLs resolving to the same key necessarily hold
    the same bytes -- that is what a commit hash means -- so a source
    difference is a mirror, not a collision.  When it is not (an ETag, a
    ``Last-Modified`` timestamp), nothing ties the key to the bytes across
    sources, and a difference is exactly the K2 collision.
    """
    commit = (getattr(pkg, "resolved_commit", None)
              or getattr(pkg, "commit", None))
    return bool(commit) and version.startswith(str(commit))


def staging_scratch(staging: str) -> str:
    """A scratch path beside *staging* for bytes that must NOT be published.

    A downloaded archive is an input to the entry, not part of it, so it cannot
    live inside the staging tree -- ``_install_zip`` ``rmtree``s its destination
    before extracting, which would delete the archive it is reading, and any
    residue left behind would be published into the cache.  A sibling keeps the
    two separate while inheriting staging's uniqueness, and (on the cache side)
    its ``.staging.`` marker, so cache scans skip it and the stale-staging sweep
    reclaims it if a run dies mid-download.
    """
    # Named with the ``.staging.`` marker in its own right rather than derived
    # from the tree's name: containment in a marked parent would be enough
    # today, but a scan that learns about staging trees and not about this
    # would read an in-flight download as cache content.
    return os.path.join(os.path.dirname(staging), "download.staging.dl")


class CacheState(enum.Enum):
    HIT = "hit"            # version is present in the cache
    MISS = "miss"          # cache is active but this version is absent
    DISABLED = "disabled"  # this dependency is not cacheable — "uncacheable"


@dc.dataclass
class CacheLookupResult:
    """The outcome of :meth:`CacheProvider.lookup`."""
    state: CacheState
    cached_path: Optional[str] = None    # populated only on HIT

    @property
    def is_hit(self) -> bool:
        return self.state is CacheState.HIT

    @property
    def is_miss(self) -> bool:
        return self.state is CacheState.MISS

    @property
    def is_disabled(self) -> bool:
        return self.state is CacheState.DISABLED


@dc.dataclass(frozen=True)
class CacheContext:
    """Immutable description of a *session* (one ``ivpm update`` run).

    Carries the root project making the references and where dependencies
    land.  It deliberately does **not** name any single dependency — the
    dependency is passed to each provider method, so one provider serves
    every dependency in the session.
    """
    # --- root project (who is making the references) ---
    root_name: Optional[str]          # ProjInfo.name of the top-level project
    root_version: Optional[str]       # ProjInfo.version
    root_dir: Optional[str]           # absolute path to the root project
    deps_dir: str                     # directory deps are materialized into


class CacheProvider:
    """The cache API for one session, serving every dependency.

    Each loading-time method takes the dependency ``pkg`` so the single
    provider can make per-dependency decisions.
    """

    #: The ``ProjectUpdateInfo`` this provider was created for, when there is
    #: one.  Set by the session rather than passed to every method: cache
    #: verification has to report findings and refresh counters, and threading
    #: an update-info argument through ``lookup`` would push that concern into
    #: every package type.  ``None`` when a provider is constructed directly
    #: (tests, ``ivpm cache`` commands), in which case reporting degrades to
    #: plain diagnostics with no counters.
    session = None

    def __init__(self, context: CacheContext):
        self.context = context

    @property
    def cache_dir(self) -> Optional[str]:
        """Where cached content lands, or None when this provider stores none.

        Informational, for callers that need to reason about the *filesystem*
        rather than about cache identity -- a pre-populate step checking
        permissions or free space, say, since with caching on the bytes land
        here first and are linked into the deps-dir.
        """
        return None

    def with_deps_dir(self, deps_dir: str) -> 'CacheProvider':
        """This provider, materializing into *deps_dir* instead.

        A nested dependency scope shares the session's cache (identity is a
        function of source and version, never of where a package is placed) but
        materializes into its own deps-dir. Rather than thread a deps-dir
        argument through every provider call site, the resolver's scope view
        hands out a re-based provider.

        The returned object shares the underlying store, so cache state stays
        single-instance. Returns ``self`` when nothing would change.
        """
        if deps_dir == self.context.deps_dir:
            return self
        import copy
        rebased = copy.copy(self)
        rebased.context = dc.replace(self.context, deps_dir=deps_dir)
        return rebased

    def is_cacheable(self, pkg) -> bool:
        """Is caching enabled AND available for THIS dependency?

        Combines the global cache configuration with the per-dependency
        ``cache`` flag (and anything a site config wants to route on).
        """
        raise NotImplementedError

    def lookup(self, pkg, version: str) -> CacheLookupResult:
        """Report whether ``version`` of ``pkg`` is cached.

        Returns HIT (with ``cached_path``), MISS, or DISABLED.  If ``pkg``
        is not cacheable the result is ALWAYS DISABLED ("uncacheable").
        """
        raise NotImplementedError

    def new_staging(self, pkg) -> Optional[str]:
        """A unique, not-yet-created build directory on the cache filesystem.

        A caller that must *build* a tree before :meth:`store` (e.g. the patch
        resolver copying a base and applying patches) should build here so the
        subsequent ``store`` is a same-filesystem rename rather than a
        cross-device copy.  Returns ``None`` when the provider has no cache-side
        staging (the caller then falls back to its own scratch dir).
        """
        return None

    def store(self, pkg, version: str, source_path: str) -> str:
        """Adopt a freshly fetched tree into the cache.

        Returns the path the caller should treat as canonical.  No-op
        providers return ``source_path`` unchanged.
        """
        raise NotImplementedError

    def materialize(self, pkg, version: str) -> str:
        """Place the cached ``version`` of ``pkg`` into the session deps dir.

        Returns the destination path.  Only valid after a HIT or a
        successful :meth:`store`.
        """
        raise NotImplementedError

    def note_reference(self, pkg) -> None:
        """Record that ``pkg`` is already materialized from the cache.

        Called on the already-loaded fast path — which returns early without
        fetching or calling :meth:`materialize` — so an entry's "last
        referenced into a workspace" timestamp still advances when a stable
        workspace re-runs ``ivpm update``.  Default no-op: only a real cache
        has anything to refresh.
        """
        return None


class NullCacheProvider(CacheProvider):
    """The provider returned when caching is disabled — always uncacheable."""

    def is_cacheable(self, pkg) -> bool:
        return False

    def lookup(self, pkg, version: str) -> CacheLookupResult:
        return CacheLookupResult(CacheState.DISABLED)

    def store(self, pkg, version: str, source_path: str) -> str:
        return source_path            # nothing is stored

    def materialize(self, pkg, version: str) -> str:
        # Not reached in normal flow: a DISABLED lookup steers the caller
        # to its native (non-cached) fetch.  Guard defensively.
        raise RuntimeError("NullCacheProvider cannot materialize a cache entry")


class DirectoryCacheProvider(CacheProvider):
    """Filesystem-backed provider, a context-scoped adapter over a store."""

    def __init__(self, context: CacheContext, store: "DirectoryCacheStore",
                 verify_level: Optional[str] = None):
        super().__init__(context)
        self._store = store
        self._verify_level = verify_level
        # (package, version) pairs already invalidated this run.  The auto-repair
        # bound: an entry that keeps failing verification for an environmental
        # reason (a filesystem serving short reads, a clock-skewed NFS mount)
        # must not turn into an evict/refetch loop that hammers the network.
        self._invalidated = set()
        # Non-blocking problem kinds already reported this run.  A cache with
        # 500 legacy entries must produce one line, not 500.
        self._reported_kinds = set()
        self._verify_lock = threading.Lock()

    @property
    def verify_level(self) -> str:
        """The active verification level, resolved once per provider."""
        if self._verify_level is None:
            from .site_config import resolve_cache_verify_level
            self._verify_level = resolve_cache_verify_level()
        return self._verify_level

    @property
    def cache_dir(self) -> Optional[str]:
        return getattr(self._store, "cache_dir", None)

    def is_cacheable(self, pkg) -> bool:
        # The cache dir resolved (we wouldn't exist otherwise) AND this dep
        # opts in.  A site config may override to route per dependency.
        return getattr(pkg, "cache", None) is True

    def lookup(self, pkg, version: str) -> CacheLookupResult:
        if not self.is_cacheable(pkg):
            return CacheLookupResult(CacheState.DISABLED)
        policy = policy_for(pkg)
        if self._store.has_version(pkg.name, version, policy):
            path = self._store.get_version_cache_dir(pkg.name, version, policy)
            if not self._entry_is_trustworthy(pkg, version):
                return CacheLookupResult(CacheState.MISS)
            return CacheLookupResult(CacheState.HIT, path)
        return CacheLookupResult(CacheState.MISS)

    def _entry_is_trustworthy(self, pkg, version: str) -> bool:
        """Verify a candidate HIT before its bytes are handed to a consumer.

        A HIT is a decision to trust content chosen by *key* alone.  This is
        the last point at which anything can contradict that choice -- past it
        there is nothing left to check against -- so it is where verification
        belongs.

        Failure is not fatal.  A bad entry is evicted and reported, and the
        caller sees a MISS, so the ordinary miss path rebuilds it: an everyday
        ``ivpm update`` is the primary repair mechanism for the common case.
        """
        from .perf import span_or_null
        from . import cache_verify as cv

        level = self.verify_level
        source = source_info(pkg)
        if source is not None and _content_addressed(pkg, version):
            # The key IS a digest of the content (a commit hash), so two URLs
            # resolving to it hold identical bytes -- a mirror, not a
            # collision.  Comparing sources here would make every mirror user
            # re-fetch on every run and never converge, since the winning entry
            # keeps whichever URL got there first.
            source = None

        with span_or_null(getattr(self.session, "perf", None), "cache.verify",
                          package=pkg.name) as s:
            result = cv.verify_entry(self._store, pkg.name, version, level,
                                     source=source)
            if s is not None:
                s.meta["level"] = level
                s.meta["problems"] = len(result.findings)

        if not result.findings:
            return True

        blocking = [f for f in result.findings
                    if f.problem in cv.SERVES_WRONG_CONTENT]
        for f in result.findings:
            if f not in blocking:
                self._report_non_blocking(f)
        if not blocking:
            return True

        self._invalidate(pkg, version, blocking[0])
        return False

    def _report_non_blocking(self, finding):
        """Report a problem that does not stop the entry from being used.

        Once per problem kind per run.  These are seal drift and legacy
        entries: real, worth fixing, and potentially true of *every* entry in
        a large cache -- so repeating them per entry would bury the run's
        actual output under a wall of identical notes.
        """
        with self._verify_lock:
            if finding.problem in self._reported_kinds:
                return
            self._reported_kinds.add(finding.problem)
        note("%s\n  run 'ivpm cache verify' for the full picture"
             % finding.message())

    def _invalidate(self, pkg, version: str, finding):
        """Reject and evict a bad entry, at most once per entry per run."""
        key = (pkg.name, version)
        with self._verify_lock:
            repeat = key in self._invalidated
            self._invalidated.add(key)

        if repeat:
            # Already evicted and rebuilt once this run, and the rebuild failed
            # verification too.  Something other than a one-off bad publish is
            # wrong; refetching again would only spin.
            error("%s\n  this entry already failed verification once this run "
                  "and was rebuilt; %s will be fetched without the cache"
                  % (finding.message(), pkg.name))
            return

        from .cache_verify import REPAIR_EVICT
        evicted = False
        if finding.repair == REPAIR_EVICT:
            evicted = self._store._evict(pkg.name, version, policy_for(pkg))

        warning("%s\n  the entry was %s and will be re-fetched; run "
                "'ivpm cache verify --repair' to check the rest of the cache"
                % (finding.message(),
                   "evicted" if evicted else "rejected"))

        session = self.session
        if session is not None and hasattr(session, "report_cache_invalidated"):
            session.report_cache_invalidated(finding)

    def new_staging(self, pkg) -> Optional[str]:
        # Inside the package's protection partition, so store() renames instead
        # of copying AND the fetch inherits the policy's group as it writes.
        return self._store.new_staging(pkg.name, policy_for(pkg))

    def store(self, pkg, version: str, source_path: str) -> str:
        return self._store.store_version(
            pkg.name, version, source_path, source=source_info(pkg),
            policy=policy_for(pkg))

    def materialize(self, pkg, version: str) -> str:
        return self._store.link_to_deps(pkg.name, version,
                                        self.context.deps_dir, policy_for(pkg))

    def note_reference(self, pkg) -> None:
        # The fast path has the existing deps/<pkg> symlink but not the version
        # id; let the store resolve the link target and refresh last_linked iff
        # it points into this cache (editable clones / deps-source links no-op).
        link_path = os.path.join(self.context.deps_dir, getattr(pkg, "name", ""))
        if os.path.islink(link_path):
            self._store.touch_linked_target(link_path)

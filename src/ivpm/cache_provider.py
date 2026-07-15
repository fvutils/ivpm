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
import dataclasses as dc
import enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .cache import DirectoryCacheStore


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

    def __init__(self, context: CacheContext):
        self.context = context

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

    def __init__(self, context: CacheContext, store: "DirectoryCacheStore"):
        super().__init__(context)
        self._store = store

    def is_cacheable(self, pkg) -> bool:
        # The cache dir resolved (we wouldn't exist otherwise) AND this dep
        # opts in.  A site config may override to route per dependency.
        return getattr(pkg, "cache", None) is True

    def lookup(self, pkg, version: str) -> CacheLookupResult:
        if not self.is_cacheable(pkg):
            return CacheLookupResult(CacheState.DISABLED)
        if self._store.has_version(pkg.name, version):
            path = self._store.get_version_cache_dir(pkg.name, version)
            return CacheLookupResult(CacheState.HIT, path)
        return CacheLookupResult(CacheState.MISS)

    def new_staging(self, pkg) -> Optional[str]:
        # On the cache filesystem, so store() renames instead of copying.
        return self._store.new_staging(pkg.name)

    def store(self, pkg, version: str, source_path: str) -> str:
        return self._store.store_version(pkg.name, version, source_path)

    def materialize(self, pkg, version: str) -> str:
        return self._store.link_to_deps(pkg.name, version, self.context.deps_dir)

    def note_reference(self, pkg) -> None:
        # The fast path has the existing deps/<pkg> symlink but not the version
        # id; let the store resolve the link target and refresh last_linked iff
        # it points into this cache (editable clones / deps-source links no-op).
        link_path = os.path.join(self.context.deps_dir, getattr(pkg, "name", ""))
        if os.path.islink(link_path):
            self._store.touch_linked_target(link_path)

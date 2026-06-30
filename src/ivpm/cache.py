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
import stat
import json
import time
import shutil
from typing import Optional
from .msg import note
from .site_config import get_site_config


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
        """Get the cache directory for a specific package."""
        return os.path.join(self.cache_dir, package_name)
    
    def get_version_cache_dir(self, package_name: str, version: str) -> str:
        """Get the cache directory for a specific package version."""
        return os.path.join(self.cache_dir, package_name, version)
    
    def has_version(self, package_name: str, version: str) -> bool:
        """Check if a specific version is cached."""
        version_dir = self.get_version_cache_dir(package_name, version)
        return os.path.isdir(version_dir)
    
    def ensure_cache_dir(self, package_name: str) -> str:
        """Ensure the package cache directory exists (with setgid)."""
        pkg_cache_dir = self.get_package_cache_dir(package_name)
        if not os.path.isdir(pkg_cache_dir):
            os.makedirs(pkg_cache_dir, exist_ok=True)
            try:
                os.chmod(pkg_cache_dir, self._DIR_MODE)
            except OSError:
                pass
        return pkg_cache_dir
    
    def store_version(self, package_name: str, version: str, source_path: str) -> str:
        """Store a package version in the cache.
        
        Args:
            package_name: Name of the package
            version: Version identifier (e.g., commit hash)
            source_path: Path to the source directory to cache
            
        Returns:
            Path to the cached version directory
        """
        version_dir = self.get_version_cache_dir(package_name, version)
        
        if os.path.exists(version_dir):
            # Already cached — clean up the source that is no longer needed
            if os.path.exists(source_path):
                shutil.rmtree(source_path)
            # Re-storing an extant entry still counts as using it.
            self._touch_last_linked(package_name, version)
            return version_dir
        
        self.ensure_cache_dir(package_name)
        
        # Move to a temporary name first, then atomically rename.
        # This prevents a race where two parallel workers both pass
        # the existence check and try to populate the same directory.
        staging_dir = version_dir + ".staging.%d" % os.getpid()
        try:
            shutil.move(source_path, staging_dir)
            os.rename(staging_dir, version_dir)
        except OSError:
            # Another process won the race — clean up our staging copy
            if os.path.exists(staging_dir):
                shutil.rmtree(staging_dir)
            if os.path.exists(source_path):
                shutil.rmtree(source_path)
            if os.path.exists(version_dir):
                return version_dir
            raise
        
        # Make all files read-only
        self._make_readonly(version_dir)

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
        link_path = os.path.join(deps_dir, package_name)
        
        if os.path.islink(link_path):
            os.unlink(link_path)
        elif os.path.exists(link_path):
            shutil.rmtree(link_path)
        
        os.symlink(version_dir, link_path)
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
        return os.path.join(
            self.cache_dir, package_name, version + self._META_SUFFIX)

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
        tmp = path + ".tmp.%d" % os.getpid()
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

    # Permission bits used for shared-cache directories.
    # rwxrwsr-x: owner+group can read/write/traverse, setgid propagates
    # group ownership to new entries, others can read/traverse.
    _DIR_MODE = (
        stat.S_IRWXU | stat.S_IRWXG | stat.S_ISGID |
        stat.S_IROTH | stat.S_IXOTH
    )  # 0o2775

    def _make_readonly(self, path: str):
        """Lock down a cached tree for shared use.

        * **Files** — write bits are cleared so that no user can
          accidentally edit shared content.
        * **Directories** — set to ``rwxrwsr-x`` (2775) so that any
          group member can traverse and *delete* entries during cache
          cleanup.  The setgid bit ensures new entries inherit the
          directory's group.

        Silently skips entries that cannot be ``chmod``-ed (e.g. owned
        by another user in a shared cache).
        """
        for root, dirs, files in os.walk(path):
            for d in dirs:
                try:
                    os.chmod(os.path.join(root, d), self._DIR_MODE)
                except OSError:
                    pass
            for f in files:
                try:
                    fp = os.path.join(root, f)
                    mode = os.stat(fp).st_mode
                    os.chmod(fp, mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
                except OSError:
                    pass
        try:
            os.chmod(path, self._DIR_MODE)
        except OSError:
            pass
    
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

                if self.entry_last_used(pkg_name, version) < cutoff:
                    if not dry_run:
                        # Need to make writable before removing
                        self._make_writable(version_dir)
                        shutil.rmtree(version_dir)
                        self._delete_meta(pkg_name, version)
                    removed += 1
                    note("%s cached %s/%s" % (
                        "Would remove" if dry_run else "Removed",
                        pkg_name, version))

            if dry_run:
                continue

            # Sweep orphaned sidecars, then drop now-empty package directories.
            self._sweep_orphan_meta(pkg_dir)
            if not os.listdir(pkg_dir):
                os.rmdir(pkg_dir)

        return removed
    
    def _make_writable(self, path: str):
        """Restore write permission before ``shutil.rmtree``.

        Directories in the cache are already writable (2775), so only
        files need the write bit restored.  Skips entries that cannot
        be modified (owned by another user).
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
                    os.chmod(dp, self._DIR_MODE)
                except OSError:
                    pass
        try:
            os.chmod(path, self._DIR_MODE)
        except OSError:
            pass


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

#****************************************************************************
#* package_http.py
#*
#* Copyright 2023 Matthew Ballance and Contributors
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
#* Created on:
#*     Author: 
#*
#****************************************************************************
import os
import httpx
import sys
import urllib
import dataclasses as dc
from .package_file import PackageFile
from ..project_ops_info import ProjectUpdateInfo
from ..proj_info import ProjInfo
from ..utils import note
from ..load_plan import LoadAction
from ..package import SourceType2Ext

class PackageHttp(PackageFile):

    def patch_capability(self):
        # Editable patchable: cache-mode plus in-place rollback by re-extracting
        # the retained base archive (retain_base / restore_pristine below).
        # base_version is the ETag/Last-Modified (a patched http dep must resolve
        # a strong identity -- see the resolver in P4).
        from ..patch import PatchCapability
        return PatchCapability.EDITABLE

    def retain_base(self, pkg_dir, base_version, update_info):
        """Retain the downloaded archive at .ivpm/base<ext> so the pristine tree
        can be re-extracted offline. ``self._pristine_archive`` is the path to
        the archive that was just downloaded (set by the editable fetch)."""
        src = getattr(self, "_pristine_archive", None)
        if src is None or not os.path.isfile(src):
            return                       # nothing to retain; restore returns False
        ext = self.src_type or os.path.splitext(src)[1]
        ivpm_dir = os.path.join(pkg_dir, ".ivpm")
        os.makedirs(ivpm_dir, exist_ok=True)
        dest = os.path.join(ivpm_dir, "base" + ext)
        import shutil as _sh
        _sh.copy2(src, dest)
        from ..patch import md5_file
        self._base_ref = {
            "kind": "archive",
            "file": os.path.join(".ivpm", "base" + ext),
            "md5": md5_file(dest),
        }

    def _find_retained_base(self, pkg_dir):
        ivpm_dir = os.path.join(pkg_dir, ".ivpm")
        if not os.path.isdir(ivpm_dir):
            return None
        for fn in sorted(os.listdir(ivpm_dir)):
            if fn.startswith("base."):
                return os.path.join(ivpm_dir, fn)
        return None

    def restore_pristine(self, pkg_dir, base_version, update_info) -> bool:
        """Re-extract the retained base archive, preserving .ivpm/. Always safe
        for an archive tree (no user-tracked history); returns False only if no
        retained base is present."""
        import shutil as _sh
        base_file = self._find_retained_base(pkg_dir)
        if base_file is None:
            return False
        for entry in os.listdir(pkg_dir):
            if entry == ".ivpm":
                continue
            p = os.path.join(pkg_dir, entry)
            if os.path.islink(p) or os.path.isfile(p):
                os.unlink(p)
            else:
                _sh.rmtree(p)
        self._install(base_file, pkg_dir)
        return True

    def patch_tree_status(self, pkg_dir, base_version, allowed_paths):
        """Clean iff only allowed_paths deviate from the retained base snapshot.
        ('unknown' if no retained base -- archive trees are only drift-checkable
        once they carry a base snapshot.)"""
        base_file = self._find_retained_base(pkg_dir)
        if base_file is None:
            return "unknown"
        from ..patch import _walk_rel, _sha256_file
        import tempfile as _tf
        import shutil as _sh
        tmp = _tf.mkdtemp()
        try:
            self._install(base_file, tmp)
            base_files = _walk_rel(tmp)
            cur_files = _walk_rel(pkg_dir)
            for rel in base_files | cur_files:
                if rel in allowed_paths:
                    continue
                bp = os.path.join(tmp, rel)
                cp = os.path.join(pkg_dir, rel)
                be, ce = os.path.isfile(bp), os.path.isfile(cp)
                if be != ce:
                    return "drift"
                if be and ce and _sha256_file(bp) != _sha256_file(cp):
                    return "drift"
            return "clean"
        finally:
            _sh.rmtree(tmp, ignore_errors=True)

    def fetch_pristine(self, update_info, dest_dir, base_version):
        """Materialize a writable pristine tree of base_version at dest_dir.

        Tier-1 patch hook (patch-source-provider-contract.md §3.2): downloads the
        archive and unpacks it into dest_dir, applying no patches and leaving the
        tree writable (the resolver owns read-only locking). The downloaded
        archive is retained on ``self._pristine_archive`` so retain_base() (the
        tier-2 editable path) can keep a byte-exact copy without re-downloading.
        """
        download_dir = os.path.join(update_info.deps_dir, ".download")
        os.makedirs(download_dir, exist_ok=True)
        if self.unpack:
            pkg_path = os.path.join(download_dir, os.path.basename(self.url))
            self._download_file(self.url, pkg_path)
            self._pristine_archive = pkg_path
            self._install(pkg_path, dest_dir)
        else:
            os.makedirs(dest_dir, exist_ok=True)
            pkg_path = os.path.join(dest_dir, os.path.basename(self.url))
            self._download_file(self.url, pkg_path)
            self._pristine_archive = pkg_path

    def _update_with_patches(self, update_info: ProjectUpdateInfo, pkg_dir: str):
        """Resolve the base version (ETag/Last-Modified), then hand off to the
        patch-aware resolver, which owns the cache interaction."""
        from ..patch import PatchAwareResolver
        base_version = self._get_url_version(self.url)
        return PatchAwareResolver().resolve(update_info, self, base_version)

    def update(self, update_info : ProjectUpdateInfo):
        pkg_dir = os.path.join(update_info.deps_dir, self.name)
        self.path = pkg_dir.replace("\\", "/")

        # Report this package for cache statistics
        # HTTP packages are cacheable if cache=True, editable if cache is not True
        is_cacheable = self.cache is True
        is_editable = self.cache is not True  # Could be cached but isn't
        update_info.report_package(cacheable=is_cacheable, editable=is_editable)

        # The planner owns the "does this need loading?" decision -- an empty
        # directory is not a loaded package (see load_plan.py).
        decision = update_info.get_load_planner().decide(self)

        if decision.action is LoadAction.RECONCILE:
            # A patched (or previously-patched) tree is reconciled, not skipped,
            # so a changed patch set is picked up (mirrors package_git.py). The
            # patch-aware resolver owns the cache interaction and deliberately
            # bypasses the not-yet-patch-aware deps-source probe below.
            return self._update_with_patches(update_info, pkg_dir)

        if decision.is_resident:
            note("Skipping %s, since it is already loaded" % self.name)
            # Refresh the cache entry's last-referenced timestamp when this dep
            # is a cache symlink (no-op otherwise), so stale-GC sees it as used.
            update_info.get_cache_provider().note_reference(self)
        else:
            # Try deps-source: probe URL to populate resolved_etag/last_modified
            # so the matcher has identity to compare against.
            if update_info.deps_source is not None:
                self._get_url_version(self.url)
                if update_info.try_deps_source(self):
                    note("deps-source hit for %s" % self.name)
                    return ProjInfo.mkFromProj(pkg_dir)

            # Check if caching is enabled. The helpers fetch/unpack into pkg_dir
            # for their side effects; we then scan the unpacked tree for a nested
            # ivpm.yaml below so the archive's transitive deps are processed.
            if self.cache is True:
                self._update_with_cache(update_info, pkg_dir)
            elif self.cache is False:
                self._update_no_cache_readonly(update_info, pkg_dir)
            else:
                self._update_normal(update_info, pkg_dir)

        # Scan the unpacked tree for a nested ivpm.yaml so transitive deps are
        # processed (mirrors package_git.py). Returns None when there is none
        # (e.g. unpack=False, where pkg_dir is the downloaded file).
        return ProjInfo.mkFromProj(pkg_dir)
    
    def _get_url_version(self, url: str) -> str:
        """Get version identifier for a URL using HEAD request.
        
        Uses Last-Modified header or ETag as version identifier.
        Also stores resolved_etag / resolved_last_modified on self.
        """
        self.resolved_etag = None
        self.resolved_last_modified = None
        try:
            response = httpx.head(url, follow_redirects=True, timeout=30)
            
            # Prefer Last-Modified as it's more human-readable
            if "Last-Modified" in response.headers:
                # Convert to a safe directory name
                lm = response.headers["Last-Modified"]
                self.resolved_last_modified = lm
                # Replace problematic characters
                return lm.replace(" ", "_").replace(":", "-").replace(",", "")
            
            # Fall back to ETag
            if "ETag" in response.headers:
                etag = response.headers["ETag"]
                # Clean up ETag (remove quotes and W/ prefix)
                # Strip quotes from both ends
                etag = etag.strip('"').strip("'")
                # Handle W/ prefix
                if etag.startswith("W/"):
                    etag = etag[2:]
                # Strip quotes again in case W/"..." format
                etag = etag.strip('"').strip("'")
                self.resolved_etag = etag
                return etag
            
            # Last resort: use URL hash
            import hashlib
            return hashlib.md5(url.encode()).hexdigest()[:16]
        except Exception:
            import hashlib
            return hashlib.md5(url.encode()).hexdigest()[:16]
    
    def _update_with_cache(self, update_info: ProjectUpdateInfo, pkg_dir: str):
        """Update using the cache."""
        note("loading package %s with cache" % self.name)
        
        # Get version from URL metadata
        version = self._get_url_version(self.url)
        
        provider = update_info.get_cache_provider()
        result = provider.lookup(self, version)

        # If this dependency is not cacheable (no cache dir resolved),
        # fall back to a read-only download without the shared cache.
        if result.is_disabled:
            update_info.report_cache_unconfigured()
            return self._update_no_cache_readonly(update_info, pkg_dir)

        # Check if this version is cached
        if result.is_hit:
            note("Cache hit for %s at version %s" % (self.name, version))
            provider.materialize(self, version)
            update_info.report_cache_hit()
            return

        # Cache miss - download and unpack
        note("Cache miss for %s - downloading" % self.name)
        update_info.report_cache_miss()
        
        # Download to temp location
        temp_dir = os.path.join(update_info.deps_dir, f".cache_temp_{self.name}")
        if os.path.exists(temp_dir):
            import shutil
            shutil.rmtree(temp_dir)
        
        download_dir = os.path.join(update_info.deps_dir, ".download")
        os.makedirs(download_dir, exist_ok=True)
        
        if self.unpack:
            pkg_path = os.path.join(download_dir, os.path.basename(self.url))
        else:
            pkg_path = temp_dir
        
        self._download_file(self.url, pkg_path)
        
        if self.unpack:
            self._install(pkg_path, temp_dir)
            os.unlink(pkg_path)
        
        # Store in cache and link
        provider.store(self, version, temp_dir)
        provider.materialize(self, version)

    def _update_no_cache_readonly(self, update_info: ProjectUpdateInfo, pkg_dir: str):
        """Download and make read-only (cache=False)."""
        note("loading package %s (no cache, read-only)" % self.name)
        
        self._update_normal(update_info, pkg_dir)
        
        # Make read-only
        self._make_readonly(pkg_dir)
    
    def _update_normal(self, update_info: ProjectUpdateInfo, pkg_dir: str):
        """Normal download without caching."""
        # Need to fetch, then unpack these
        download_dir = os.path.join(update_info.deps_dir, ".download")
        
        os.makedirs(download_dir, exist_ok=True)

        if self.unpack:
            pkg_path = os.path.join(download_dir, 
                                    os.path.basename(self.url))
        else:
            pkg_path = os.path.join(update_info.deps_dir, self.name)
                
        # TODO: should this be an option?   
        remove_pkg_src = True

        self._download_file(self.url, pkg_path)

        if self.unpack:
            self._install(pkg_path, pkg_dir)
            os.unlink(os.path.join(download_dir, 
                                   os.path.basename(self.url)))
        else:
            # 
            pass
    
    def _make_readonly(self, path: str):
        """Make all files in a directory tree read-only."""
        import stat
        for root, dirs, files in os.walk(path):
            for d in dirs:
                dir_path = os.path.join(root, d)
                mode = os.stat(dir_path).st_mode
                os.chmod(dir_path, mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
            for f in files:
                file_path = os.path.join(root, f)
                mode = os.stat(file_path).st_mode
                os.chmod(file_path, mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
        mode = os.stat(path).st_mode
        os.chmod(path, mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)

    def _download_file(self, url, dest):
        r = httpx.get(url, follow_redirects=True)
        if r.status_code < 200 or r.status_code >= 300:
            raise Exception("Failed to download %s: HTTP %d" % (url, r.status_code))
        with open(dest, "wb") as f:
            f.write(r.content)
            
    @classmethod
    def dep_keys(cls):
        return super().dep_keys() | {"sha256"}

    @staticmethod
    def create(name, opts, si) -> 'PackageHttp':
        pkg = PackageHttp(name)
        pkg.process_options(opts, si)
        return pkg

    @classmethod
    def source_info(cls):
        from ..show.info_types import PkgSourceInfo, ParamInfo
        return PkgSourceInfo(
            name="http",
            description="HTTP/HTTPS archive — downloaded and unpacked into packages/",
            params=[
                ParamInfo("url", "HTTP or HTTPS URL of the archive (.tar.gz, .zip, .jar, etc.)", required=True, type_hint="url"),
                ParamInfo("sha256", "Expected SHA-256 checksum of the downloaded file (hex string)"),
                ParamInfo("unpack", "Unpack the archive (default: true except .jar)", type_hint="bool"),
                ParamInfo("cache", "Cache this download (true=shared cache+symlink, false=no cache)", type_hint="bool"),
            ],
        )






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
from ..utils import note
from ..package import SourceType2Ext
from ..cache import Cache

class PackageHttp(PackageFile):

    def update(self, update_info : ProjectUpdateInfo):
        pkg_dir = os.path.join(update_info.deps_dir, self.name)
        self.path = pkg_dir.replace("\\", "/")

        # Report this package for cache statistics
        # HTTP packages are cacheable if cache=True, editable if cache is not True
        is_cacheable = self.cache is True
        is_editable = self.cache is not True  # Could be cached but isn't
        update_info.report_package(cacheable=is_cacheable, editable=is_editable)

        if os.path.isdir(pkg_dir) or os.path.islink(pkg_dir):
            note("Skipping %s, since it is already loaded" % self.name)
        else:
            # Check if caching is enabled
            if self.cache is True:
                return self._update_with_cache(update_info, pkg_dir)
            elif self.cache is False:
                return self._update_no_cache_readonly(update_info, pkg_dir)
            else:
                return self._update_normal(update_info, pkg_dir)
    
    def _get_url_version(self, url: str) -> str:
        """Get version identifier for a URL using HEAD request.
        
        Uses Last-Modified header or ETag as version identifier.
        """
        try:
            response = httpx.head(url, follow_redirects=True, timeout=30)
            
            # Prefer Last-Modified as it's more human-readable
            if "Last-Modified" in response.headers:
                # Convert to a safe directory name
                lm = response.headers["Last-Modified"]
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
        
        cache = update_info.cache
        if cache is None:
            cache = Cache()
        
        # If cache is not properly configured, fall back to no cache
        if not cache.is_enabled():
            note("IVPM_CACHE not set - falling back to no-cache mode for %s" % self.name)
            return self._update_no_cache_readonly(update_info, pkg_dir)
        
        # Check if this version is cached
        if cache.has_version(self.name, version):
            note("Cache hit for %s at version %s" % (self.name, version))
            cache.link_to_deps(self.name, version, update_info.deps_dir)
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
        if not os.path.isdir(download_dir):
            os.makedirs(download_dir)
        
        if self.unpack:
            pkg_path = os.path.join(download_dir, os.path.basename(self.url))
        else:
            pkg_path = temp_dir
        
        self._download_file(self.url, pkg_path)
        
        if self.unpack:
            self._install(pkg_path, temp_dir)
            os.unlink(pkg_path)
        
        # Store in cache and link
        cache.store_version(self.name, version, temp_dir)
        cache.link_to_deps(self.name, version, update_info.deps_dir)
    
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
            
        if not os.path.isdir(download_dir):
            os.makedirs(download_dir)

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
        with open(dest, "wb") as f:
            f.write(r.content)
        pass
            
    @staticmethod
    def create(name, opts, si) -> 'PackageHttp':
        pkg = PackageHttp(name)
        pkg.process_options(opts, si)
        return pkg






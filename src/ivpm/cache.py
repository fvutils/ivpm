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
import shutil
import dataclasses as dc
from typing import Optional
from .msg import note


@dc.dataclass
class CacheResult:
    """Result of a cache operation."""
    hit: bool = False
    cache_path: Optional[str] = None


class Cache:
    """Manages the IVPM package cache.
    
    The cache is organized by package name, with version-specific
    subdirectories. For git packages, the version is the commit hash.
    For HTTP packages, the version is derived from the Last-Modified
    header or ETag.
    """
    
    def __init__(self, cache_dir: Optional[str] = None):
        if cache_dir is not None:
            self.cache_dir = cache_dir
        else:
            self.cache_dir = os.environ.get("IVPM_CACHE")
    
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
        """Ensure the package cache directory exists."""
        pkg_cache_dir = self.get_package_cache_dir(package_name)
        if not os.path.isdir(pkg_cache_dir):
            os.makedirs(pkg_cache_dir)
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
            # Already cached
            return version_dir
        
        self.ensure_cache_dir(package_name)
        
        # Move the source to the cache
        shutil.move(source_path, version_dir)
        
        # Make all files read-only
        self._make_readonly(version_dir)
        
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
        note(f"Linked {package_name} from cache")
        return link_path
    
    def _make_readonly(self, path: str):
        """Make all files in a directory tree read-only."""
        for root, dirs, files in os.walk(path):
            for d in dirs:
                dir_path = os.path.join(root, d)
                # Remove write permission but keep execute for directories
                mode = os.stat(dir_path).st_mode
                os.chmod(dir_path, mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
            for f in files:
                file_path = os.path.join(root, f)
                # Remove write permission
                mode = os.stat(file_path).st_mode
                os.chmod(file_path, mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
        # Also make the root directory read-only
        mode = os.stat(path).st_mode
        os.chmod(path, mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
    
    def get_cache_info(self) -> dict:
        """Get information about the cache.
        
        Returns dict with:
        - packages: list of package info dicts with name, versions, total_size
        - total_size: total size of cache in bytes
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
                    continue
                
                size = self._get_dir_size(version_dir)
                mtime = os.path.getmtime(version_dir)
                
                pkg_info["versions"].append({
                    "version": version,
                    "size": size,
                    "mtime": mtime
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
    
    def clean_older_than(self, days: int) -> int:
        """Remove cache entries older than specified days.
        
        Returns number of entries removed.
        """
        import time
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
                    continue
                
                mtime = os.path.getmtime(version_dir)
                if mtime < cutoff:
                    # Need to make writable before removing
                    self._make_writable(version_dir)
                    shutil.rmtree(version_dir)
                    removed += 1
                    note(f"Removed cached {pkg_name}/{version}")
            
            # Remove empty package directories
            if not os.listdir(pkg_dir):
                os.rmdir(pkg_dir)
        
        return removed
    
    def _make_writable(self, path: str):
        """Make all files in a directory tree writable."""
        for root, dirs, files in os.walk(path, topdown=False):
            for f in files:
                file_path = os.path.join(root, f)
                mode = os.stat(file_path).st_mode
                os.chmod(file_path, mode | stat.S_IWUSR)
            for d in dirs:
                dir_path = os.path.join(root, d)
                mode = os.stat(dir_path).st_mode
                os.chmod(dir_path, mode | stat.S_IWUSR)
        mode = os.stat(path).st_mode
        os.chmod(path, mode | stat.S_IWUSR)


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

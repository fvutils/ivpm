#****************************************************************************
#* cache_backend/filesystem.py
#*
#* Copyright 2024 Matthew Ballance and Contributors
#*
#* Licensed under the Apache License, Version 2.0 (the "License"); you may
#* not use this file except in compliance with the License.  You may obtain
#* a copy of the License at:
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
import shutil
import stat
import time
from typing import Optional

from .base import CacheBackend
from ..msg import note


class FilesystemCacheBackend(CacheBackend):
    """Local-filesystem cache backend.

    Packages are stored under:
        cache_dir/{package_name}/{version}/

    Each version directory is made read-only after storage.  A symlink in the
    project's deps_dir points into the cache.

    The cache directory is resolved from (in priority order):
      1. The *cache_dir* constructor argument
      2. The ``IVPM_CACHE`` environment variable
    """

    def __init__(self, cache_dir: Optional[str] = None, config=None):
        if cache_dir is not None:
            self.cache_dir = cache_dir
        elif config is not None and config.local_dir:
            self.cache_dir = os.path.expanduser(config.local_dir)
        else:
            self.cache_dir = os.environ.get("IVPM_CACHE")

    # ------------------------------------------------------------------ #
    # Discovery                                                            #
    # ------------------------------------------------------------------ #

    @classmethod
    def is_available(cls) -> bool:
        return bool(os.environ.get("IVPM_CACHE"))

    def is_enabled(self) -> bool:
        """Compatibility helper used by package handlers."""
        return self.cache_dir is not None

    # ------------------------------------------------------------------ #
    # Per-package interface                                                #
    # ------------------------------------------------------------------ #

    def has_version(self, package_name: str, version: str) -> bool:
        return os.path.isdir(self._version_dir(package_name, version))

    def store_version(self, package_name: str, version: str, source_path: str) -> str:
        version_dir = self._version_dir(package_name, version)
        if os.path.exists(version_dir):
            return version_dir

        self._ensure_pkg_dir(package_name)
        shutil.move(source_path, version_dir)
        self._make_readonly(version_dir)
        note(f"Cached {package_name} version {version[:12] if len(version) > 12 else version}")
        return version_dir

    def link_to_deps(self, package_name: str, version: str, deps_dir: str) -> str:
        version_dir = self._version_dir(package_name, version)
        link_path = os.path.join(deps_dir, package_name)

        if os.path.islink(link_path):
            os.unlink(link_path)
        elif os.path.exists(link_path):
            shutil.rmtree(link_path)

        os.symlink(version_dir, link_path)
        note(f"Linked {package_name} from cache")
        return link_path

    # ------------------------------------------------------------------ #
    # Maintenance                                                          #
    # ------------------------------------------------------------------ #

    def clean_older_than(self, days: int) -> int:
        cutoff = time.time() - (days * 24 * 3600)
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
                if os.path.getmtime(version_dir) < cutoff:
                    self._make_writable(version_dir)
                    shutil.rmtree(version_dir)
                    removed += 1
                    note(f"Removed cached {pkg_name}/{version}")
            if not os.listdir(pkg_dir):
                os.rmdir(pkg_dir)

        return removed

    def get_info(self) -> dict:
        result = {"packages": [], "total_size": 0}
        if not self.cache_dir or not os.path.isdir(self.cache_dir):
            return result

        for pkg_name in os.listdir(self.cache_dir):
            pkg_dir = os.path.join(self.cache_dir, pkg_name)
            if not os.path.isdir(pkg_dir):
                continue
            pkg_info = {"name": pkg_name, "versions": [], "total_size": 0}
            for version in os.listdir(pkg_dir):
                version_dir = os.path.join(pkg_dir, version)
                if not os.path.isdir(version_dir):
                    continue
                size = self._dir_size(version_dir)
                pkg_info["versions"].append({
                    "version": version,
                    "size": size,
                    "mtime": os.path.getmtime(version_dir),
                })
                pkg_info["total_size"] += size
            result["packages"].append(pkg_info)
            result["total_size"] += pkg_info["total_size"]

        return result

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _version_dir(self, package_name: str, version: str) -> str:
        return os.path.join(self.cache_dir, package_name, version)

    def _pkg_dir(self, package_name: str) -> str:
        return os.path.join(self.cache_dir, package_name)

    def _ensure_pkg_dir(self, package_name: str) -> str:
        d = self._pkg_dir(package_name)
        os.makedirs(d, exist_ok=True)
        return d

    def _make_readonly(self, path: str) -> None:
        for root, dirs, files in os.walk(path):
            for d in dirs:
                _chmod_remove_write(os.path.join(root, d))
            for f in files:
                _chmod_remove_write(os.path.join(root, f))
        _chmod_remove_write(path)

    def _make_writable(self, path: str) -> None:
        for root, dirs, files in os.walk(path, topdown=False):
            for f in files:
                _chmod_add_write(os.path.join(root, f))
            for d in dirs:
                _chmod_add_write(os.path.join(root, d))
        _chmod_add_write(path)

    @staticmethod
    def _dir_size(path: str) -> int:
        total = 0
        for root, _, files in os.walk(path):
            for f in files:
                fp = os.path.join(root, f)
                if os.path.isfile(fp):
                    total += os.path.getsize(fp)
        return total


def _chmod_remove_write(path: str) -> None:
    try:
        mode = os.stat(path).st_mode
        os.chmod(path, mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    except OSError:
        pass


def _chmod_add_write(path: str) -> None:
    try:
        mode = os.stat(path).st_mode
        os.chmod(path, mode | stat.S_IWUSR)
    except OSError:
        pass

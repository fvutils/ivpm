#****************************************************************************
#* cache_backend/base.py
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
import dataclasses as dc
from abc import ABC, abstractmethod
from typing import Optional


@dc.dataclass
class CacheResult:
    """Result of a cache lookup operation."""
    hit: bool = False
    cache_path: Optional[str] = None


class CacheBackend(ABC):
    """Abstract base class for IVPM cache backends.

    Backends operate at two granularity levels:

    Per-package (called once per package, potentially in parallel):
      has_version / store_version / link_to_deps

    Session-level (called once per ivpm update run):
      activate / deactivate
    """

    # ------------------------------------------------------------------ #
    # Per-package interface                                                #
    # ------------------------------------------------------------------ #

    @abstractmethod
    def has_version(self, package_name: str, version: str) -> bool:
        """Return True if this version of the package is available in the cache."""

    @abstractmethod
    def store_version(self, package_name: str, version: str, source_path: str) -> str:
        """Move source_path into the cache for (package_name, version).

        Returns the path to the cached copy.  The source directory should be
        considered consumed (moved) after this call.
        """

    @abstractmethod
    def link_to_deps(self, package_name: str, version: str, deps_dir: str) -> str:
        """Create a reference (symlink or copy) from the cache into deps_dir.

        Returns the path inside deps_dir that now contains the package.
        """

    # ------------------------------------------------------------------ #
    # Session lifecycle                                                    #
    # ------------------------------------------------------------------ #

    def activate(self) -> None:
        """Called before the update begins.

        Backends may use this to restore state from remote storage (e.g. pull
        the venv and pip wheel cache from GHA before pip/uv runs).
        """

    def deactivate(self, success: bool) -> None:
        """Called after the update finishes.

        success=True  → update completed without errors; safe to persist state.
        success=False → update failed; backends should NOT persist potentially
                        broken state (e.g. a half-built venv).
        """

    # ------------------------------------------------------------------ #
    # Session-level venv / pip-cache hooks                                #
    # (optional – used by GHACacheBackend to expose pip_cache_dir)        #
    # ------------------------------------------------------------------ #

    @property
    def pip_cache_dir(self) -> Optional[str]:
        """Return a directory path to use as pip/uv cache, or None."""
        return None

    def try_restore_venv(self, venv_dir: str, py_version: str, req_hash: str) -> bool:
        """Attempt to restore the venv from cache before creating a fresh one.

        Returns True if the venv was successfully restored (caller should skip
        fresh installation).  Default implementation always returns False.
        """
        return False

    def notify_venv_rebuilt(self) -> None:
        """Called after a fresh venv is created so the backend can persist it."""

    def set_venv_info(self, venv_dir: str, py_version: str, req_hash: str) -> None:
        """Provide venv metadata to the backend (e.g. when venv already exists).

        Called even when the venv was not rebuilt so the backend has the correct
        req_hash for pip-cache key construction.
        """

    # ------------------------------------------------------------------ #
    # Discovery                                                            #
    # ------------------------------------------------------------------ #

    @classmethod
    @abstractmethod
    def is_available(cls) -> bool:
        """Return True if this backend can be used in the current environment."""

    # ------------------------------------------------------------------ #
    # Maintenance (optional overrides)                                     #
    # ------------------------------------------------------------------ #

    def clean_older_than(self, days: int) -> int:
        """Remove cache entries older than *days* days.  Returns count removed."""
        return 0

    def get_info(self) -> dict:
        """Return a dict with cache statistics for display."""
        return {}

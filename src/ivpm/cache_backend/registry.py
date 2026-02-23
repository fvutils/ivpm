#****************************************************************************
#* cache_backend/registry.py
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
import logging
import os
from typing import Optional, TYPE_CHECKING

from .base import CacheBackend
from .filesystem import FilesystemCacheBackend

if TYPE_CHECKING:
    pass

_logger = logging.getLogger("ivpm.cache_backend.registry")

# Backend names in auto-detection priority order.
# GHACacheBackend is imported lazily to avoid pulling in its deps at startup.
_BACKEND_NAMES = ["gha", "filesystem"]


class BackendRegistry:
    """Selects and instantiates a CacheBackend.

    Selection priority (first wins):
      1. *explicit* argument passed to select()
      2. ``IVPM_CACHE_BACKEND`` environment variable
      3. Auto-detect: first available backend in priority order
    """

    @classmethod
    def select(cls, explicit: Optional[str] = None, config=None) -> Optional[CacheBackend]:
        """Return a ready-to-use CacheBackend, or None if caching is disabled.

        *explicit* overrides everything (CLI flag value).
        *config* is an optional ``CacheConfig`` from ivpm.yaml.
        """
        # CLI flag > env var > yaml config > auto
        if explicit is None and config is not None and config.backend:
            explicit = config.backend
        choice = explicit or os.environ.get("IVPM_CACHE_BACKEND") or "auto"
        choice = choice.strip().lower()

        if choice == "none":
            return None

        if choice != "auto":
            return cls._by_name(choice, config=config)

        # Auto-detect: iterate in priority order
        for name in _BACKEND_NAMES:
            backend_cls = cls._class_for(name)
            if backend_cls is not None and backend_cls.is_available():
                _logger.info("Auto-selected cache backend: %s", name)
                return backend_cls(config=config)

        return None

    @classmethod
    def _by_name(cls, name: str, config=None) -> Optional[CacheBackend]:
        backend_cls = cls._class_for(name)
        if backend_cls is None:
            _logger.warning("Unknown cache backend %r – caching disabled", name)
            return None
        return backend_cls(config=config)

    @classmethod
    def _class_for(cls, name: str):
        if name == "filesystem":
            return FilesystemCacheBackend
        if name == "gha":
            try:
                from .gha import GHACacheBackend
                return GHACacheBackend
            except ImportError as exc:
                _logger.debug("GHA backend unavailable: %s", exc)
                return None
        return None

#****************************************************************************
#* cache_backend/gha.py
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
import concurrent.futures
import hashlib
import logging
import os
import shutil
import sys
from typing import List, Optional

from .base import CacheBackend
from .filesystem import FilesystemCacheBackend
from .gha_client import GHACacheClient
from ..msg import note, warning

_logger = logging.getLogger("ivpm.gha_cache")


class GHACacheBackend(CacheBackend):
    """Two-level cache backend: local filesystem (L1) + GitHub Actions (L2).

    Per-package operations map directly to individual GHA cache entries keyed
    by ``ivpm-pkg-{OS}-{name}-{version}``.  Only changed/new packages are ever
    uploaded; unchanged packages are served from the local L1 directory on
    subsequent runs on the same machine.

    The Python venv and pip/uv wheel cache are handled at session level via
    ``activate()`` / ``deactivate()``.

    Parameters
    ----------
    local_dir:
        L1 cache directory.  Resolved from (in priority order):
          1. Constructor argument
          2. ``IVPM_CACHE`` env var
          3. ``~/.cache/ivpm/``
    key_prefix:
        Short string prepended to GHA cache keys (default ``"ivpm"``).
    client:
        Optional pre-constructed ``GHACacheClient`` (mainly for testing).
    """

    def __init__(
        self,
        local_dir: Optional[str] = None,
        key_prefix: str = "ivpm",
        client: Optional[GHACacheClient] = None,
        config=None,
    ):
        # config values are overridden by explicit constructor arguments
        if local_dir is None and config is not None and config.local_dir:
            local_dir = os.path.expanduser(config.local_dir)
        if config is not None and config.key_prefix and key_prefix == "ivpm":
            key_prefix = config.key_prefix

        # Resolve the L1 local cache directory
        if local_dir is not None:
            self._local_dir = local_dir
        else:
            self._local_dir = (
                os.environ.get("IVPM_CACHE")
                or os.path.join(os.path.expanduser("~"), ".cache", "ivpm")
            )

        self._key_prefix = key_prefix
        self._client: Optional[GHACacheClient] = client

        # Inner filesystem backend operating on the local dir
        self._fs = FilesystemCacheBackend(self._local_dir)

        # Thread pool for async per-package uploads
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="ivpm-gha-upload"
        )
        self._pending_uploads: List[concurrent.futures.Future] = []

        # Session-level state
        self._pip_cache_dir: Optional[str] = None
        self._venv_dir: Optional[str] = None
        self._venv_rebuilt = False
        self._py_version: Optional[str] = None
        self._req_hash: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Discovery                                                            #
    # ------------------------------------------------------------------ #

    @classmethod
    def is_available(cls) -> bool:
        return bool(
            os.environ.get("ACTIONS_CACHE_URL")
            and os.environ.get("ACTIONS_RUNTIME_TOKEN")
        )

    # ------------------------------------------------------------------ #
    # Client lazy init                                                     #
    # ------------------------------------------------------------------ #

    def _get_client(self) -> GHACacheClient:
        if self._client is None:
            self._client = GHACacheClient(
                cache_url=os.environ["ACTIONS_CACHE_URL"],
                token=os.environ["ACTIONS_RUNTIME_TOKEN"],
                key_prefix=self._key_prefix,
            )
        return self._client

    # ------------------------------------------------------------------ #
    # Per-package interface                                                #
    # ------------------------------------------------------------------ #

    def has_version(self, package_name: str, version: str) -> bool:
        # L1 hit — no network traffic
        if self._fs.has_version(package_name, version):
            return True

        # L2: ask GHA
        client = self._get_client()
        key = client.pkg_key(package_name, version)
        download_url = client.lookup(key)
        if download_url is None:
            return False

        # Download and populate L1 atomically: extract to a temp dir first,
        # then rename into the final location so a concurrent download to the
        # same entry cannot corrupt the cache directory.
        dest = self._fs._version_dir(package_name, version)
        parent = os.path.dirname(dest)
        try:
            self._fs._ensure_pkg_dir(package_name)
            tmp_dest = dest + ".tmp"
            if os.path.exists(tmp_dest):
                shutil.rmtree(tmp_dest, ignore_errors=True)
            client.download(download_url, tmp_dest)
            if os.path.exists(dest):
                # Another thread/process beat us to it — discard our copy
                shutil.rmtree(tmp_dest, ignore_errors=True)
            else:
                os.rename(tmp_dest, dest)
            self._fs._make_readonly(dest)
            note(f"Restored {package_name} {version[:12]} from GHA cache")
            return True
        except Exception as exc:
            _logger.warning("Failed to restore %s from GHA cache: %s", package_name, exc)
            for path in (dest + ".tmp", dest):
                if os.path.exists(path):
                    shutil.rmtree(path, ignore_errors=True)
            return False

    def store_version(self, package_name: str, version: str, source_path: str) -> str:
        # Store in L1 first (immediate, synchronous)
        cached_path = self._fs.store_version(package_name, version, source_path)

        # Schedule async upload to L2
        client = self._get_client()
        key = client.pkg_key(package_name, version)
        future = self._executor.submit(client.upload, key, cached_path)
        self._pending_uploads.append(future)
        return cached_path

    def link_to_deps(self, package_name: str, version: str, deps_dir: str) -> str:
        return self._fs.link_to_deps(package_name, version, deps_dir)

    # ------------------------------------------------------------------ #
    # Session lifecycle                                                    #
    # ------------------------------------------------------------------ #

    def activate(self) -> None:
        os.makedirs(self._local_dir, exist_ok=True)

        # Set up pip/uv cache directory
        self._pip_cache_dir = os.path.join(self._local_dir, "_pip_cache")
        os.makedirs(self._pip_cache_dir, exist_ok=True)

        # Try to restore pip wheel cache from GHA
        self._try_restore_pip_cache()

    def deactivate(self, success: bool) -> None:
        # Wait for all in-flight per-package uploads
        if self._pending_uploads:
            note(f"Waiting for {len(self._pending_uploads)} GHA cache upload(s) to complete…")
            for fut in concurrent.futures.as_completed(self._pending_uploads):
                try:
                    fut.result()
                except Exception as exc:
                    _logger.warning("GHA upload error: %s", exc)
            self._pending_uploads.clear()

        self._executor.shutdown(wait=True)

        if not success:
            return

        # Upload venv if it was freshly rebuilt
        if self._venv_rebuilt and self._venv_dir and self._req_hash:
            self._try_save_venv()

        # Upload pip cache if it exists and has content
        if self._pip_cache_dir and os.path.isdir(self._pip_cache_dir):
            self._try_save_pip_cache()

    # ------------------------------------------------------------------ #
    # Venv / pip-cache hooks                                               #
    # ------------------------------------------------------------------ #

    @property
    def pip_cache_dir(self) -> Optional[str]:
        return self._pip_cache_dir

    def notify_venv_rebuilt(self) -> None:
        self._venv_rebuilt = True

    def set_venv_info(
        self, venv_dir: str, py_version: str, req_hash: str
    ) -> None:
        """Called by the Python handler after a venv is set up."""
        self._venv_dir = venv_dir
        self._py_version = py_version
        self._req_hash = req_hash

    def try_restore_venv(self, venv_dir: str, py_version: str, req_hash: str) -> bool:
        """Attempt to restore the venv from GHA cache.

        Returns True if the venv was successfully restored (caller should skip
        fresh installation).
        """
        self._venv_dir = venv_dir
        self._py_version = py_version
        self._req_hash = req_hash

        client = self._get_client()
        key = client.venv_key(py_version, req_hash)
        restore_key = client.venv_restore_key(py_version)

        # Try exact key first, then prefix
        for k in (key, restore_key):
            download_url = client.lookup(k)
            if download_url:
                try:
                    client.download(download_url, venv_dir)
                    note(f"Restored Python venv from GHA cache (key: {k})")
                    return True
                except Exception as exc:
                    _logger.warning("Failed to restore venv from GHA: %s", exc)
                    if os.path.exists(venv_dir):
                        shutil.rmtree(venv_dir, ignore_errors=True)
        return False

    # ------------------------------------------------------------------ #
    # Maintenance                                                          #
    # ------------------------------------------------------------------ #

    def clean_older_than(self, days: int) -> int:
        return self._fs.clean_older_than(days)

    def get_info(self) -> dict:
        info = self._fs.get_info()
        info["backend"] = "gha"
        info["local_dir"] = self._local_dir
        return info

    # ------------------------------------------------------------------ #
    # Internal session helpers                                             #
    # ------------------------------------------------------------------ #

    def _try_restore_pip_cache(self) -> None:
        # We don't know the req_hash yet at activate() time; this is a
        # best-effort restore using the prefix fallback key.
        client = self._get_client()
        restore_key = client.pip_restore_key()
        download_url = client.lookup(restore_key)
        if download_url and self._pip_cache_dir:
            try:
                client.download(download_url, self._pip_cache_dir)
                note("Restored pip wheel cache from GHA cache")
            except Exception as exc:
                _logger.warning("Failed to restore pip cache from GHA: %s", exc)

    def _try_save_venv(self) -> None:
        client = self._get_client()
        key = client.venv_key(self._py_version or "unknown", self._req_hash or "")
        note(f"Uploading Python venv to GHA cache…")
        client.upload(key, self._venv_dir)

    def _try_save_pip_cache(self) -> None:
        client = self._get_client()
        req_hash = self._req_hash or "unknown"
        key = client.pip_key(req_hash)
        note("Uploading pip wheel cache to GHA cache…")
        client.upload(key, self._pip_cache_dir)

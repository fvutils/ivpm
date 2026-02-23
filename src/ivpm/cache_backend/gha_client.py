#****************************************************************************
#* cache_backend/gha_client.py
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
#* Pure-Python client for the GitHub Actions cache service REST API.
#* Uses only stdlib (urllib, tarfile, hashlib, json) – no external deps.
#*
#* Protocol reference:
#*   https://github.com/actions/toolkit/tree/main/packages/cache
#*   https://gha-cache-server.falcondev.io/how-it-works
#*
#****************************************************************************
import hashlib
import io
import json
import logging
import os
import shutil
import stat
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

_logger = logging.getLogger("ivpm.gha_client")

# GHA cache API paths
_API_CACHE   = "_apis/artifactcache/cache"
_API_CACHES  = "_apis/artifactcache/caches"

# Maximum chunk size for upload (GHA spec: 32 MB)
_CHUNK_SIZE = 32 * 1024 * 1024


class GHACacheClient:
    """Minimal client for the GitHub Actions cache service.

    Parameters
    ----------
    cache_url:
        Value of the ``ACTIONS_CACHE_URL`` environment variable.
        Must end with '/'.
    token:
        Value of the ``ACTIONS_RUNTIME_TOKEN`` environment variable.
    key_prefix:
        Short string prepended to all cache keys (default ``"ivpm"``).
    os_name:
        Runner OS label used in cache keys (default: ``RUNNER_OS`` env var
        or ``"Linux"``).
    """

    def __init__(
        self,
        cache_url: str,
        token: str,
        key_prefix: str = "ivpm",
        os_name: Optional[str] = None,
    ):
        self._base = cache_url.rstrip("/") + "/"
        self._token = token
        self._prefix = key_prefix
        self._os = os_name or os.environ.get("RUNNER_OS", "Linux")

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def lookup(self, key: str) -> Optional[str]:
        """Return a download URL for *key*, or None on miss.

        The GHA service matches the first key that has an entry; keys are
        tried in order.  Per-package lookups use exact keys only.
        """
        params = urllib.parse.urlencode({
            "keys": key,
            "version": self._cache_version(key),
        })
        url = self._base + _API_CACHE + "?" + params
        try:
            data = self._get_json(url)
            download_url = data.get("archiveLocation") if data else None
            if download_url:
                _logger.debug("Cache hit for key %r", key)
            else:
                _logger.debug("Cache miss for key %r", key)
            return download_url
        except urllib.error.HTTPError as exc:
            if exc.code == 204:
                # 204 No Content = cache miss
                return None
            _logger.warning("Cache lookup failed for %r: %s", key, exc)
            return None

    def download(self, download_url: str, dest_dir: str) -> None:
        """Download and extract the archive at *download_url* into *dest_dir*."""
        os.makedirs(dest_dir, exist_ok=True)
        req = urllib.request.Request(download_url)
        with urllib.request.urlopen(req) as resp:
            with tarfile.open(fileobj=resp, mode="r|gz") as tf:
                tf.extractall(dest_dir)
        _logger.debug("Downloaded and extracted to %s", dest_dir)

    def upload(self, key: str, src_dir: str) -> bool:
        """Archive *src_dir* and upload it under *key*.

        Returns True on success, False on failure (errors are logged but do
        not propagate – a failed upload must not break the build).
        """
        try:
            return self._upload(key, src_dir)
        except Exception as exc:
            _logger.warning("Cache upload failed for %r: %s", key, exc)
            return False

    # ------------------------------------------------------------------ #
    # Key construction helpers                                             #
    # ------------------------------------------------------------------ #

    def pkg_key(self, package_name: str, version: str) -> str:
        """Build the GHA cache key for a single package version."""
        return f"{self._prefix}-pkg-{self._os}-{package_name}-{version}"

    def venv_key(self, py_version: str, req_hash: str) -> str:
        return f"{self._prefix}-pyenv-{self._os}-{py_version}-{req_hash}"

    def venv_restore_key(self, py_version: str) -> str:
        return f"{self._prefix}-pyenv-{self._os}-{py_version}-"

    def pip_key(self, req_hash: str) -> str:
        return f"{self._prefix}-pip-{self._os}-{req_hash}"

    def pip_restore_key(self) -> str:
        return f"{self._prefix}-pip-{self._os}-"

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _upload(self, key: str, src_dir: str) -> bool:
        version = self._cache_version(key)

        # Step 1: reserve a cache slot
        reserve_body = json.dumps({"key": key, "version": version}).encode()
        try:
            resp_data = self._post_json(self._base + _API_CACHES, reserve_body)
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                # Already exists – treat as success (concurrent job won the race)
                _logger.debug("Cache key %r already exists, skipping upload", key)
                return True
            raise
        cache_id = resp_data.get("cacheId")
        if not cache_id:
            _logger.warning("No cacheId in reserve response for %r", key)
            return False

        # Step 2: build tar.gz in memory (stream to temp file to allow chunking)
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            with tarfile.open(tmp_path, "w:gz") as tf:
                tf.add(src_dir, arcname=".")
            archive_size = os.path.getsize(tmp_path)

            # Step 3: upload in chunks
            offset = 0
            with open(tmp_path, "rb") as fh:
                while True:
                    chunk = fh.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    self._patch_chunk(cache_id, offset, chunk)
                    offset += len(chunk)

            # Step 4: commit
            commit_body = json.dumps({"size": archive_size}).encode()
            url = f"{self._base}{_API_CACHES}/{cache_id}"
            self._post_json(url, commit_body)
            _logger.debug("Uploaded cache key %r (%d bytes)", key, archive_size)
            return True
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _patch_chunk(self, cache_id: int, offset: int, data: bytes) -> None:
        end = offset + len(data) - 1
        url = f"{self._base}{_API_CACHES}/{cache_id}"
        req = urllib.request.Request(
            url,
            data=data,
            method="PATCH",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json;api-version=6.0-preview.1",
                "Content-Type": "application/octet-stream",
                "Content-Range": f"bytes {offset}-{end}/*",
            },
        )
        with urllib.request.urlopen(req):
            pass

    def _get_json(self, url: str) -> Optional[dict]:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json;api-version=6.0-preview.1",
            },
        )
        with urllib.request.urlopen(req) as resp:
            if resp.status == 204:
                return None
            return json.loads(resp.read().decode())

    def _post_json(self, url: str, body: bytes) -> dict:
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json;api-version=6.0-preview.1",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw.decode()) if raw.strip() else {}

    @staticmethod
    def _cache_version(key: str) -> str:
        """Compute the GHA cache 'version' field (SHA-256 of key + paths)."""
        return hashlib.sha256(key.encode()).hexdigest()

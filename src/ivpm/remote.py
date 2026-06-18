#****************************************************************************
#* remote.py
#*
#* Copyright 2018-2024 Matthew Ballance and Contributors
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
"""Fetch an IVPM manifest from a local path or URL for ``--from``.

Resolves a ``--from`` argument (a bare local path, a ``file://`` URL, or an
``http(s)://`` URL) to a local file the existing :class:`IvpmYamlReader` can
open. HTTP fetches land in a temporary file that the caller cleans up once the
manifest has been parsed. Uses only the standard library — no new dependency.
"""
import os
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

from .msg import fatal

#: Name looked for when ``--from`` points at a directory or bare URL.
MANIFEST_NAME = "ivpm.yaml"


def _names_manifest(path: str) -> bool:
    """True if *path* names a YAML file rather than a directory/base location."""
    return path.lower().endswith((".yaml", ".yml"))


@dataclass
class FetchedManifest:
    """Result of resolving a ``--from`` source.

    Attributes:
        local_path: filesystem path the reader can ``open()``.
        origin:     the resolved manifest location (recorded in the lock file).
                    When ``--from`` named a directory or bare URL this is the
                    ``ivpm.yaml`` resolved within it, not the bare argument.
        is_remote:  True for http(s) sources; gates remote-only restrictions
                    such as ``include:`` rejection.
        _tmp:       a temporary file to unlink on cleanup, or None for local
                    paths (which are used in place and must not be removed).
    """
    local_path: str
    origin: str
    is_remote: bool
    _tmp: Optional[str] = None

    def cleanup(self) -> None:
        """Remove the temporary file, if any. Safe to call more than once."""
        if self._tmp is not None and os.path.isfile(self._tmp):
            os.unlink(self._tmp)
            self._tmp = None


def fetch_manifest(src: str) -> FetchedManifest:
    """Resolve *src* (path or URL) to a local manifest file.

    *src* may name the manifest directly (``.../ivpm.yaml``) or a directory /
    bare host URL, in which case ``ivpm.yaml`` is looked for within it — so
    ``--from https://edapack.github.io`` works as long as
    ``https://edapack.github.io/ivpm.yaml`` exists.

    Network and filesystem errors are routed through :func:`fatal` so they are
    reported consistently with the rest of IVPM.
    """
    parsed = urllib.parse.urlparse(src)
    scheme = parsed.scheme.lower()

    if scheme in ("http", "https"):
        # A bare host/directory URL (no .yaml/.yml leaf) is treated as a
        # location to look for ivpm.yaml within.
        if _names_manifest(parsed.path):
            url = src
        else:
            url = src.rstrip("/") + "/" + MANIFEST_NAME
        try:
            with urllib.request.urlopen(url) as resp:  # nosec B310 - user-supplied manifest source; see remote-manifest-design.md "Trust"
                status = getattr(resp, "status", None)
                if status is not None and status != 200:
                    fatal("Failed to fetch manifest %s (HTTP %s)" % (url, status))
                data = resp.read()
        except OSError as e:
            fatal("Failed to fetch manifest %s: %s" % (url, e))
        fd, tmp = tempfile.mkstemp(suffix="-ivpm.yaml")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return FetchedManifest(local_path=tmp, origin=url, is_remote=True, _tmp=tmp)

    # file:// URL or a bare local path
    if scheme == "file":
        local = urllib.request.url2pathname(parsed.path)
    else:
        local = src

    # A directory is treated as a location to look for ivpm.yaml within.
    if os.path.isdir(local):
        candidate = os.path.join(local, MANIFEST_NAME)
        if not os.path.isfile(candidate):
            fatal("--from directory has no %s: %s" % (MANIFEST_NAME, src))
        local = candidate
    elif not os.path.isfile(local):
        fatal("--from manifest not found: %s" % src)

    return FetchedManifest(local_path=local, origin=local, is_remote=False, _tmp=None)

#****************************************************************************
#* deps_source.py
#*
#* Copyright 2026 Matthew Ballance and Contributors
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
"""Local deps-dir as a package source.

A deps-source is a sibling workspace's ``deps/`` directory.  When configured,
ivpm consults it *before* the shared cache and before any remote fetch: if the
parent's ``package-lock.json`` records an entry whose resolved identity matches
what the current project would resolve to, ``deps/<pkg>`` is materialized as a
symlink (or copy) of ``<parent>/<pkg>`` and no remote work happens.

See ``local-deps-source-design.md`` for background.
"""
import dataclasses as dc
import json
import logging
import os
from typing import List, Optional

_logger = logging.getLogger("ivpm.deps_source")


# Lock-entry field that carries the resolved-identity for each src_type.
# dir / file packages are intentionally absent — they have no caching identity.
_IDENTITY_FIELD = {
    "git":    ("commit_resolved", "resolved_commit"),
    "gh-rls": ("version_resolved", "resolved_version"),
    "pypi":   ("version_resolved", "resolved_version"),
    "http":   ("etag", "resolved_etag"),
    "tgz":    ("etag", "resolved_etag"),
    "txz":    ("etag", "resolved_etag"),
    "zip":    ("etag", "resolved_etag"),
    "jar":    ("etag", "resolved_etag"),
}

_IDENTITY_FIELD_FALLBACK = {
    "http": ("last_modified", "resolved_last_modified"),
    "tgz":  ("last_modified", "resolved_last_modified"),
    "txz":  ("last_modified", "resolved_last_modified"),
    "zip":  ("last_modified", "resolved_last_modified"),
    "jar":  ("last_modified", "resolved_last_modified"),
}


@dc.dataclass
class DepsSourceEntry:
    """A single parent deps-dir to search."""
    parent_dir: str
    lock: Optional[dict] = None     # parsed package-lock.json, or None
    trust: bool = False             # if True, skip lock verification

    @classmethod
    def load(cls, parent_dir: str, trust: bool = False) -> "DepsSourceEntry":
        real = os.path.realpath(parent_dir)
        lock_path = os.path.join(real, "package-lock.json")
        lock = None
        if os.path.isfile(lock_path):
            try:
                with open(lock_path) as f:
                    lock = json.load(f)
            except (OSError, ValueError) as e:
                _logger.warning(
                    "deps-source %s: failed to read package-lock.json (%s); "
                    "lock-based matching disabled for this source", real, e)
        return cls(parent_dir=real, lock=lock, trust=trust)


class DepsSource:
    """Ordered list of parent deps-dirs searched before the shared cache."""

    def __init__(self, entries: List[DepsSourceEntry]):
        self.entries = list(entries)

    @classmethod
    def from_args(cls,
                  paths: Optional[List[str]],
                  trust: bool = False) -> Optional["DepsSource"]:
        if not paths:
            return None
        return cls([DepsSourceEntry.load(p, trust=trust) for p in paths])

    def lookup(self, pkg) -> Optional[str]:
        """Return realpath of ``<parent>/<pkg.name>`` from the first source
        whose lock entry's resolved identity matches ``pkg`` (or, in trust
        mode, the first source that has a same-named entry).  Returns
        ``None`` if no source satisfies the request.
        """
        name = getattr(pkg, "name", None)
        if not name:
            return None

        # dir/file packages have no caching identity, so they never match.
        src = _normalize_src(getattr(pkg, "src_type", None))
        if src in ("dir", "file"):
            return None

        for entry in self.entries:
            candidate = os.path.join(entry.parent_dir, name)
            if not os.path.lexists(candidate):
                continue

            if entry.trust:
                return os.path.realpath(candidate)

            if entry.lock is None:
                continue

            parent_entry = _find_lock_entry(entry.lock, name)
            if parent_entry is None:
                continue

            if _identity_matches(pkg, src, parent_entry):
                return os.path.realpath(candidate)

        return None


def _normalize_src(src) -> str:
    """Coerce a pkg.src_type (enum or string) to a lock-file string form."""
    if src is None:
        return ""
    if hasattr(src, "name"):
        from .package import SourceType2Spec
        return SourceType2Spec.get(src, src.name.lower())
    return str(src)


def _find_lock_entry(lock: dict, name: str) -> Optional[dict]:
    packages = lock.get("packages") or {}
    return packages.get(name)


def _identity_matches(pkg, src: str, parent_entry: dict) -> bool:
    """Return True if pkg's resolved identity matches the parent lock entry."""
    # Cross-check src_type — a name collision across different sources is a miss.
    parent_src = parent_entry.get("src", "")
    if parent_src and parent_src != src:
        return False

    # Let extension package types override comparison.
    matcher = getattr(pkg, "matches_lock_entry", None)
    if callable(matcher):
        result = matcher(parent_entry)
        if result is not None:
            return bool(result)

    fields = _IDENTITY_FIELD.get(src)
    if fields is None:
        return False

    lock_field, attr_field = fields
    want = getattr(pkg, attr_field, None)
    have = parent_entry.get(lock_field)
    if want is not None and have is not None and want == have:
        return True

    fallback = _IDENTITY_FIELD_FALLBACK.get(src)
    if fallback is not None:
        lock_field, attr_field = fallback
        want = getattr(pkg, attr_field, None)
        have = parent_entry.get(lock_field)
        if want is not None and have is not None and want == have:
            return True

    return False

#****************************************************************************
#* package_lock.py
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
"""
package_lock.py — read/write packages/package-lock.json.

The lock file records the fully-resolved (canonical) identity of every
fetched package.  It is a local artifact written as a side-effect of
``ivpm update`` and ``ivpm sync``.  When passed back via
``ivpm update --lock-file <path>`` the lock file is used as the sole
source of truth for the package list, reproducing the exact workspace.
"""

import hashlib
import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from typing import Dict, Optional

_logger = logging.getLogger("ivpm.package_lock")

LOCK_VERSION = 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry_from_pkg(pkg) -> dict:
    """Build a lock-file entry dict from a resolved Package object."""
    src = getattr(pkg, "src_type", None) or ""
    # Normalize src: may be a SourceType enum or a string
    if hasattr(src, "name"):
        from .package import SourceType2Spec
        src = SourceType2Spec.get(src, src.name.lower())
    else:
        src = str(src)
    entry = {
        "src": src,
        "resolved_by": pkg.resolved_by or "root",
        "dep_set": pkg.dep_set,
        "reproducible": True,
    }

    if src == "git":
        entry["url"] = getattr(pkg, "url", None)
        entry["branch"] = getattr(pkg, "branch", None)
        entry["tag"] = getattr(pkg, "tag", None)
        entry["commit_requested"] = getattr(pkg, "commit", None)
        entry["commit_resolved"] = getattr(pkg, "resolved_commit", None)
        entry["cache"] = getattr(pkg, "cache", None)

    elif src == "gh-rls":
        entry["url"] = getattr(pkg, "url", None)
        entry["version_requested"] = getattr(pkg, "version", None)
        entry["version_resolved"] = getattr(pkg, "resolved_version", None)
        entry["cache"] = getattr(pkg, "cache", None)

    elif src in ("http", "tgz", "txz", "zip", "jar"):
        entry["url"] = getattr(pkg, "url", None)
        entry["etag"] = getattr(pkg, "resolved_etag", None)
        entry["last_modified"] = getattr(pkg, "resolved_last_modified", None)
        entry["cache"] = getattr(pkg, "cache", None)

    elif src == "pypi":
        entry["version_requested"] = getattr(pkg, "version", None)
        entry["version_resolved"] = getattr(pkg, "resolved_version", None)

    elif src in ("dir", "file"):
        url = getattr(pkg, "url", None) or ""
        # Strip file:// prefix; record relative path as-is (no absolute paths)
        if url.startswith("file://"):
            url = url[7:]
        entry["path"] = url
        entry["reproducible"] = False

    return entry


def _spec_matches_lock(pkg, lock_entry: dict) -> bool:
    """Return True if the user-specified fields of *pkg* match *lock_entry*."""
    src = getattr(pkg, "src_type", None) or ""

    if src == "git":
        return (
            getattr(pkg, "url", None) == lock_entry.get("url")
            and getattr(pkg, "branch", None) == lock_entry.get("branch")
            and getattr(pkg, "tag", None) == lock_entry.get("tag")
            and getattr(pkg, "commit", None) == lock_entry.get("commit_requested")
            and getattr(pkg, "cache", None) == lock_entry.get("cache")
        )
    elif src == "gh-rls":
        return (
            getattr(pkg, "url", None) == lock_entry.get("url")
            and getattr(pkg, "version", None) == lock_entry.get("version_requested")
        )
    elif src in ("http", "tgz", "txz", "zip", "jar"):
        return getattr(pkg, "url", None) == lock_entry.get("url")
    elif src == "pypi":
        return getattr(pkg, "version", None) == lock_entry.get("version_requested")
    elif src in ("dir", "file"):
        url = getattr(pkg, "url", None) or ""
        if url.startswith("file://"):
            url = url[7:]
        return url == lock_entry.get("path")

    return False


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def write_lock(
    deps_dir: str,
    all_pkgs,
    handler_contributions: Optional[dict] = None,
) -> None:
    """Write ``<deps_dir>/package-lock.json`` atomically.

    *all_pkgs* is the ``PackagesInfo`` / dict of ``Package`` objects returned
    by ``PackageUpdater.update()``.

    *handler_contributions* is an optional dict of extra top-level keys
    contributed by post-processing handlers (e.g. ``{"python_packages": {...}}``).
    """
    packages = {}
    # PackagesInfo exposes .packages dict; plain dicts are also accepted.
    pkg_dict = getattr(all_pkgs, "packages", all_pkgs)
    for name, pkg in pkg_dict.items():
        if pkg is None:
            continue
        packages[name] = _entry_from_pkg(pkg)

    lock = {
        "ivpm_lock_version": LOCK_VERSION,
        "generated": datetime.now(timezone.utc).isoformat(),
        "packages": packages,
    }

    if handler_contributions:
        lock.update(handler_contributions)

    # Compute integrity checksum (over canonical JSON, excluding the checksum
    # key itself so we can verify without a chicken-and-egg problem).
    body = json.dumps(lock, indent=2, sort_keys=True)
    checksum = hashlib.sha256(body.encode()).hexdigest()
    lock["sha256"] = checksum

    os.makedirs(deps_dir, exist_ok=True)
    lock_path = os.path.join(deps_dir, "package-lock.json")
    tmp_path = lock_path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(lock, indent=2, sort_keys=True, fp=f)
        f.write("\n")
    os.replace(tmp_path, lock_path)
    _logger.info("Wrote package-lock.json (%d packages)", len(packages))

def _write_lock_dict(lock_path: str, lock: dict) -> None:
    """Atomically (re-)write a lock dict to *lock_path*.

    Strips any stale ``sha256`` key, refreshes the ``generated`` timestamp,
    recomputes the checksum over the canonical body (without the checksum
    key), then inserts it before the final write.  This is the single place
    where the lock-file integrity stamp is managed.
    """
    lock = dict(lock)       # shallow copy — don't mutate caller's dict
    lock.pop("sha256", None)
    lock["generated"] = datetime.now(timezone.utc).isoformat()

    body = json.dumps(lock, indent=2, sort_keys=True)
    lock["sha256"] = hashlib.sha256(body.encode()).hexdigest()

    tmp_path = lock_path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(lock, indent=2, sort_keys=True, fp=f)
        f.write("\n")
    os.replace(tmp_path, lock_path)


def patch_lock_after_sync(lock_path: str, sync_results) -> None:
    """Update ``commit_resolved`` for synced packages and re-write the lock.

    Called by ``ProjectOps.sync()`` after a successful (non-dry-run) sync.
    Only packages whose outcome is SYNCED are updated; all others are left
    unchanged.
    """
    from .pkg_sync import SyncOutcome

    if not os.path.isfile(lock_path):
        return

    try:
        lock = read_lock(lock_path)
    except Exception as e:
        _logger.warning("Could not read package-lock.json for sync update: %s", e)
        return

    packages = lock.get("packages", {})
    changed = False
    for result in sync_results:
        if result.outcome != SyncOutcome.SYNCED:
            continue
        if result.name not in packages:
            continue
        entry = packages[result.name]
        if entry.get("src") != "git":
            continue
        # Re-read full commit hash from the working directory
        try:
            r = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, cwd=result.path, timeout=10,
            )
            if r.returncode == 0:
                entry["commit_resolved"] = r.stdout.strip()
                changed = True
        except Exception:
            pass

    if changed:
        _write_lock_dict(lock_path, lock)
        _logger.info("Updated package-lock.json after sync")



def read_lock(lock_path: str) -> dict:
    """Read and validate a lock file.  Returns the parsed dict."""
    with open(lock_path) as f:
        data = json.load(f)

    version = data.get("ivpm_lock_version", 0)
    if version != LOCK_VERSION:
        raise ValueError(
            "package-lock.json version %d is not supported (expected %d). "
            "Please regenerate the lock file with this version of ivpm."
            % (version, LOCK_VERSION)
        )

    # Verify integrity checksum
    recorded = data.pop("sha256", None)
    if recorded is not None:
        body = json.dumps(data, indent=2, sort_keys=True)
        computed = hashlib.sha256(body.encode()).hexdigest()
        if computed != recorded:
            _logger.warning(
                "package-lock.json checksum mismatch — file may have been "
                "modified manually."
            )
        data["sha256"] = recorded  # restore

    return data


def check_lock_changes(deps_dir: str, all_pkgs) -> Dict[str, dict]:
    """Compare *all_pkgs* against an existing lock file in *deps_dir*.

    Returns a dict mapping package name → {"current": entry, "locked": entry}
    for packages whose user-specified fields differ from the lock.  An empty
    dict means everything is up to date.
    """
    lock_path = os.path.join(deps_dir, "package-lock.json")
    if not os.path.isfile(lock_path):
        return {}

    try:
        lock = read_lock(lock_path)
    except Exception as e:
        _logger.warning("Could not read package-lock.json: %s", e)
        return {}

    locked_pkgs = lock.get("packages", {})
    diffs = {}

    pkg_dict = getattr(all_pkgs, "packages", all_pkgs)
    for name, pkg in pkg_dict.items():
        if pkg is None:
            continue
        if name not in locked_pkgs:
            continue
        locked_entry = locked_pkgs[name]
        if not _spec_matches_lock(pkg, locked_entry):
            diffs[name] = {
                "current": _entry_from_pkg(pkg),
                "locked": locked_entry,
            }

    return diffs


# ---------------------------------------------------------------------------
# IvpmLockReader — reconstruct Package objects from a lock file
# ---------------------------------------------------------------------------

class IvpmLockReader:
    """Reconstruct a list of Package objects from a lock file for reproduction mode."""

    def __init__(self, lock_path: str):
        self.lock_path = lock_path
        self._data = read_lock(lock_path)

    def build_packages_info(self):
        """Return a PackagesInfo built from the lock file's complete closure."""
        from .packages_info import PackagesInfo
        from .pkg_types.package_git import PackageGit
        from .pkg_types.package_gh_rls import PackageGhRls
        from .pkg_types.package_http import PackageHttp
        from .pkg_types.package_pypi import PackagePyPi
        from .pkg_types.package_url import PackageURL

        packages = self._data.get("packages", {})
        pkgs_info = PackagesInfo("lock")

        for name, entry in packages.items():
            src = entry.get("src", "")
            pkg = None

            if src == "git":
                p = PackageGit(name)
                p.url = entry.get("url")
                p.branch = entry.get("branch")
                p.tag = entry.get("tag")
                # Use resolved commit for exact reproduction
                p.commit = entry.get("commit_resolved") or entry.get("commit_requested")
                p.resolved_commit = entry.get("commit_resolved")
                p.cache = entry.get("cache")
                pkg = p

            elif src == "gh-rls":
                p = PackageGhRls(name)
                p.url = entry.get("url")
                # Pin to resolved version, not "latest" / requested spec
                p.version = entry.get("version_resolved") or entry.get("version_requested")
                p.resolved_version = entry.get("version_resolved")
                p.cache = entry.get("cache")
                pkg = p

            elif src in ("http", "tgz", "txz", "zip", "jar"):
                p = PackageHttp(name)
                p.url = entry.get("url")
                p.resolved_etag = entry.get("etag")
                p.resolved_last_modified = entry.get("last_modified")
                p.src_type = src
                p.cache = entry.get("cache")
                pkg = p

            elif src == "pypi":
                p = PackagePyPi(name)
                # Pin to resolved version
                p.version = entry.get("version_resolved") or entry.get("version_requested")
                p.resolved_version = entry.get("version_resolved")
                p.src_type = "pypi"
                pkg = p

            elif src in ("dir", "file"):
                p = PackageURL(name)
                path = entry.get("path", "")
                p.url = "file://" + path if not path.startswith("file://") else path
                p.src_type = src
                pkg = p

            else:
                _logger.warning("Unknown src type %r for package %s — skipping", src, name)
                continue

            pkg.resolved_by = entry.get("resolved_by", "root")
            pkg.dep_set = entry.get("dep_set")
            pkgs_info[name] = pkg

        return pkgs_info

    @property
    def python_packages(self) -> dict:
        """Return the locked pip package versions, or empty dict."""
        return self._data.get("python_packages", {})

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
from typing import Dict, List, Optional

_logger = logging.getLogger("ivpm.package_lock")

# Version 2 keys the ``packages`` map by *scope path* rather than bare package
# name, so a nested workspace can record two versions of one package. In a flat
# workspace a scope path IS the bare name, so a v2 lock for a flat workspace is
# shape-identical to v1 apart from this number and the added "name" field.
# Version 1 locks are still read: the key doubles as the name (see
# _entry_name).
LOCK_VERSION = 2

#: Lock versions this build can read. v1 differs only in that the ``packages``
#: key is the bare package name and there is no ``name`` field, so reading it
#: costs exactly one fallback (see _entry_name).
SUPPORTED_LOCK_VERSIONS = frozenset({1, 2})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_patch_fields(entry: dict, pkg) -> None:
    """Record patch identity on a lock entry (generic across all source types).

    Applied for every type -- including extension types that supply their own
    get_lock_entry() -- so a patched dependency always round-trips its patch
    set. effective_version is recorded by the resolver (which knows the base);
    here we record the type-independent patch identity.
    """
    ps = getattr(pkg, "patchset", None)
    if ps is None or ps.is_empty:
        return
    entry["patches"] = [
        dict(
            [("name", s.name), ("source", s.source), ("md5", s.md5),
             ("strip", s.strip), ("directory", s.directory)]
            + ([("tool", s.tool)] if s.tool else [])
        )
        for s in ps.specs
    ]
    entry["patchset_id"] = ps.patchset_id


def _patches_from_entry(entry: dict, base_dirs) -> list:
    """Reconstruct a ``pkg.patches`` list (PatchSpec objects) from a lock entry.

    The inverse of :func:`_add_patch_fields`, used by the lock reader so a
    reproduced workspace re-derives the *same* patch identity it was locked with.
    Patch identity (``patchset_id``) depends only on md5/strip/directory/order --
    all stored -- so the reconstructed set always reproduces the effective
    version. That is enough to score a cache HIT and materialize the patched
    variant with no re-apply. ``resolved_path`` is needed only on the (uncached)
    MISS path, where the patch file must be re-read; we locate it best-effort
    among *base_dirs*, falling back to the recorded source so a missing file
    fails loudly at apply time rather than silently producing an unpatched tree.
    """
    from .patch import PatchSpec
    raw = entry.get("patches")
    if not raw:
        return []
    specs = []
    for p in raw:
        source = p.get("source") or p.get("name") or ""
        resolved = source
        if source:
            if os.path.isabs(source):
                if os.path.isfile(source):
                    resolved = source
            else:
                for base in base_dirs:
                    cand = os.path.normpath(os.path.join(base, source))
                    if os.path.isfile(cand):
                        resolved = cand
                        break
        specs.append(PatchSpec(
            name=p.get("name") or os.path.basename(source),
            source=source, resolved_path=resolved, md5=p.get("md5"),
            strip=p.get("strip", 1), directory=p.get("directory"),
            tool=p.get("tool")))
    return specs


def _patch_spec_matches(pkg, lock_entry: dict) -> bool:
    """True if pkg's patch set matches the lock entry's recorded patch identity.

    A change in any patch MD5, the patch order, or set membership changes the
    patchset_id, so one comparison captures all three. An unpatched pkg matches
    only an entry with no patchset_id (and vice-versa)."""
    ps = getattr(pkg, "patchset", None)
    live_id = None if (ps is None or ps.is_empty) else ps.patchset_id
    return live_id == lock_entry.get("patchset_id")


def _entry_name(key: str, entry: dict) -> str:
    """The package name for a lock entry.

    v2 records ``name`` explicitly, because the key is a scope path. v1 has no
    ``name`` field and the key *is* the name -- so this one line is the whole
    backward-compatibility rule.
    """
    return entry.get("name", key)


def _entry_scope(key: str, entry: dict) -> str:
    """The scope path an entry belongs to ("" for the root scope)."""
    scope = entry.get("scope")
    if scope is not None:
        return scope
    # v1, or a v2 root entry: the key is a bare name.
    return key.rsplit("/", 1)[0] if "/" in key else ""


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

    # Patch identity is cross-cutting -- record it before the type branches so
    # both the extension-entry path and the built-in path include it.
    _add_patch_fields(entry, pkg)

    # Let extension packages contribute their own lock-entry fields.
    # If get_lock_entry() returns a dict, merge it and skip the
    # built-in type-specific branches.
    _ext_entry = pkg.get_lock_entry()
    if _ext_entry is not None:
        entry.update(_ext_entry)
        return entry

    # Built-in type-specific serialization.
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

    elif src == "module":
        entry["module"] = getattr(pkg, "module", None)
        entry["modulefile"] = getattr(pkg, "modulefile_path", None)
        entry["root"] = getattr(pkg, "module_root", None)
        entry["reproducible"] = False

    # Record provenance if the package came from a deps-source.
    if getattr(pkg, "from_deps_source", None):
        entry["from_deps_source"] = pkg.from_deps_source

    # Record provenance if the package was contributed by a `src: ivpm.yaml`
    # dep-set factory.
    if getattr(pkg, "from_ivpm_source", None):
        entry["from_ivpm_source"] = pkg.from_ivpm_source

    return entry


def _spec_matches_lock(pkg, lock_entry: dict) -> bool:
    """Return True if the user-specified fields of *pkg* match *lock_entry*."""
    src = getattr(pkg, "src_type", None) or ""

    # A patch-set change (different MD5/order/membership) is a spec change for
    # every source type -- check it before the type-specific / extension paths.
    if not _patch_spec_matches(pkg, lock_entry):
        return False

    # Let extension packages handle their own comparison first.
    _ext_result = pkg.spec_matches_lock(lock_entry)
    if _ext_result is not None:
        return _ext_result

    # Built-in type-specific comparison.
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

    elif src == "module":
        return getattr(pkg, "module", None) == lock_entry.get("module")

    # An unrecognized source type: we have no basis for saying the spec
    # changed. Reporting "matches" is the conservative answer now that a
    # drifted package is re-materialized -- claiming a change we cannot
    # substantiate would discard the tree on every single update. Same
    # reasoning as the exception path in LoadPlanner._spec_matches: guessing
    # must never fall on the destructive side.
    _logger.debug("no spec comparison for src '%s' (%s); assuming unchanged",
                  src, getattr(pkg, "name", "?"))
    return True


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def write_lock(
    deps_dir: str,
    all_pkgs,
    handler_contributions: Optional[dict] = None,
    source_manifest: Optional[dict] = None,
    install_record: Optional[dict] = None,
    dep_sets: Optional[List[str]] = None,
) -> None:
    """Write ``<deps_dir>/package-lock.json`` atomically.

    *all_pkgs* is the ``PackagesInfo`` / dict of ``Package`` objects returned
    by ``PackageUpdater.update()``.

    *handler_contributions* is an optional dict of extra top-level keys
    contributed by post-processing handlers (e.g. ``{"python_packages": {...}}``).

    *source_manifest* is an optional record written when the workspace was
    driven by ``ivpm update --from`` (an external manifest), so it can be
    re-resolved without a local ivpm.yaml. It carries ``from`` plus the
    installed dep-set(s): ``dep_set`` for a single set, or ``dep_sets`` (a list)
    when several were installed at once.

    *install_record* is the multi-source counterpart, written by ``ivpm
    install``.  It carries ``install_mode``, an ordered ``sources`` list (each
    with ``from``/``as``/``dep_sets``/``definitions``), and any
    ``collision_resolutions``, so a bare re-run replays the same tool
    directory.  Order is significant: it determines PATH precedence.  These are
    additive top-level keys; a reader that does not know them is unaffected, so
    ``ivpm_lock_version`` is not bumped.

    *dep_sets* is the ordered list of dep-set(s) the workspace was resolved
    with.  It is recorded at top level so a later bare ``ivpm update`` in this
    directory re-uses the same selection instead of silently falling back to
    the manifest default.  The lock is the workspace's record of what is
    installed, so it -- not the (regenerable) ``ivpm.json`` -- is the durable
    home for this.  Additive key; ``ivpm_lock_version`` is not bumped.
    """
    packages = {}
    ivpm_sources = {}
    # PackagesInfo exposes .packages dict; plain dicts are also accepted.
    # Keys are scope paths: a bare name in the root scope (so a flat workspace
    # is unchanged), else "<owner>/<deps-dir>/.../<name>".
    pkg_dict = getattr(all_pkgs, "packages", all_pkgs)
    for key, pkg in pkg_dict.items():
        if pkg is None:
            continue
        # Virtual nodes (e.g. `src: ivpm.yaml` factories) have no packages-dir
        # representation. Record them under a separate top-level map keyed by
        # url, not in the normal ``packages`` map.
        if getattr(pkg, "virtual", False):
            ent = pkg.get_lock_entry() or {}
            vkey = ent.get("url") or key
            ivpm_sources[vkey] = {k: v for k, v in ent.items() if k != "url"}
            continue
        entry = _entry_from_pkg(pkg)
        # The key is a scope path, so the name must be recorded separately.
        entry["name"] = pkg.name
        scope = key[:-(len(pkg.name) + 1)] if key != pkg.name else ""
        if scope:
            entry["scope"] = scope
        # Record the *effective* mode on a boundary -- a package that actually
        # opened a nested scope -- not merely what its dep entry declared.
        if getattr(pkg, "boundary_deps_dir", None):
            entry["deps_mode"] = "nested"
            # The deps-dir it hosts, so readers can walk into its scope without
            # re-reading its manifest.
            entry["deps_dir"] = pkg.boundary_deps_dir
        if getattr(pkg, "cycle_elided", None) is not None:
            entry["cycle_elided"] = pkg.cycle_elided
        packages[key] = entry

    lock = {
        "ivpm_lock_version": LOCK_VERSION,
        "generated": datetime.now(timezone.utc).isoformat(),
        "packages": packages,
    }

    # Carry forward the root-project record (written by `ivpm clone` via
    # stamp_root_record) so ordinary `ivpm update` re-writes preserve it.
    lock_path = os.path.join(deps_dir, "package-lock.json")
    existing_lock = {}
    if os.path.isfile(lock_path):
        try:
            existing_lock = read_lock(lock_path)
        except Exception:
            existing_lock = {}
        existing_root = existing_lock.get("root")
        if existing_root:
            lock["root"] = existing_root

    # Record the dep-set selection. When the caller doesn't supply one (a code
    # path that isn't dep-set-driven), keep whatever the previous run recorded
    # rather than dropping the workspace back to "default" on the next update.
    if dep_sets:
        lock["dep_sets"] = list(dep_sets)
    elif existing_lock.get("dep_sets"):
        lock["dep_sets"] = list(existing_lock["dep_sets"])

    if source_manifest:
        lock["source_manifest"] = source_manifest

    if install_record:
        lock.update(install_record)

    if ivpm_sources:
        lock["ivpm_sources"] = ivpm_sources

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


def stamp_root_record(
    deps_dir: str,
    provider: str,
    src: Optional[str] = None,
    resolved_revision: Optional[str] = None,
    root_config: Optional[dict] = None,
) -> None:
    """Insert/replace the top-level ``root`` block in an existing lock file.

    Records which clone provider produced the root workspace so ``ivpm status``
    can describe the root project.  Called by ``CmdClone`` after the post-clone
    ``ivpm update`` has written the lock.

    *root_config* (optional) is a JSON-serializable dict carrying the provider's
    forwarded configuration (``default_package`` / ``handler_overlay`` from
    ``CloneRootConfig``).  When present it is stored under ``root["config"]`` so a
    later ``ivpm update`` in a *bare* workspace (no ``ivpm.yaml``) can reproduce
    the driving config from the lock alone.

    No-op (logged at debug) when the lock does not exist -- e.g. the cloned tree
    has no ``ivpm.yaml`` and the provider forwarded no config, so no update/lock
    ran; root status then falls back to the probe.  Re-uses
    :func:`_write_lock_dict` so the integrity stamp and timestamp are refreshed
    in one place.
    """
    lock_path = os.path.join(deps_dir, "package-lock.json")
    if not os.path.isfile(lock_path):
        _logger.debug(
            "stamp_root_record: no lock at %s; skipping root record", lock_path)
        return
    try:
        lock = read_lock(lock_path)
    except Exception as e:
        _logger.warning("Could not read package-lock.json to stamp root: %s", e)
        return

    root = {"provider": provider}
    if src:
        root["src"] = src
    if resolved_revision:
        root["resolved_revision"] = resolved_revision
    if root_config:
        root["config"] = root_config
    lock["root"] = root

    _write_lock_dict(lock_path, lock)
    _logger.info("Recorded root clone provider '%s' in package-lock.json", provider)


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
    if version not in SUPPORTED_LOCK_VERSIONS:
        raise ValueError(
            "package-lock.json version %d is not supported (expected one of: "
            "%s). Please regenerate the lock file with this version of ivpm."
            % (version, ", ".join(str(v) for v in sorted(SUPPORTED_LOCK_VERSIONS)))
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


# Conventional deps-dir names, preferred (in order) when disambiguating a bare
# workspace. "packages" is the IVPM default; "import"/"deps" are also common.
_CONVENTIONAL_DEPS_DIRS = ("import", "packages", "deps")


def is_ivpm_deps_dir(path: str) -> bool:
    """True when *path* itself is a deps-dir.

    That is: it directly contains a ``package-lock.json`` that parses and
    carries a compatible ``ivpm_lock_version``.  This is what lets ``status``
    and ``sync`` work when the user is standing *inside* a deps-dir -- the
    normal case for a shared tool directory, where the deps-dir is the root.
    """
    if not os.path.isdir(path):
        return False
    lock_path = os.path.join(path, "package-lock.json")
    if not os.path.isfile(lock_path):
        return False
    try:
        read_lock(lock_path)
    except Exception:
        return False
    return True


def find_ivpm_deps_dir(root_dir: str) -> Optional[str]:
    """Discover the deps-dir at or under *root_dir* that holds our
    package-lock.json.

    Used for "bare" workspaces (no root ``ivpm.yaml`` -- those created by an
    ``ivpm clone`` provider from a source without one) so ``status``/``sync``
    can still operate off the lock.  *root_dir* is tested first: when it is
    itself a deps-dir that wins outright, which is both the tool-directory case
    and the disambiguation for a deps-dir whose nested scopes carry their own
    locks.  Otherwise immediate children are scanned (design §6.1 / decision
    §5): a child is a candidate when it contains a ``package-lock.json`` that
    parses and carries our ``ivpm_lock_version``.

    Returns the absolute path to the single valid deps-dir, or ``None`` when
    there is none.  When several children qualify, a conventional name
    (``import`` > ``packages`` > ``deps``) breaks the tie; if the ambiguity
    remains, this ``fatal``\\ s listing the candidates rather than guessing.
    """
    if not os.path.isdir(root_dir):
        return None

    # Self-match precedes the child scan: with nested deps, a deps-dir whose
    # nested scopes carry their own locks would otherwise resolve to a nested
    # scope (or trip the ambiguity fatal below).
    if is_ivpm_deps_dir(root_dir):
        return os.path.abspath(root_dir)

    candidates = []
    for name in sorted(os.listdir(root_dir)):
        child = os.path.join(root_dir, name)
        if not os.path.isdir(child):
            continue
        lock_path = os.path.join(child, "package-lock.json")
        if not os.path.isfile(lock_path):
            continue
        try:
            read_lock(lock_path)
        except Exception:
            # Not a valid/compatible IVPM lock -- skip (e.g. a vendored lock).
            continue
        candidates.append(child)

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    # Multiple valid locks: prefer a conventional name.
    by_base = {os.path.basename(c): c for c in candidates}
    for conventional in _CONVENTIONAL_DEPS_DIRS:
        if conventional in by_base:
            return by_base[conventional]

    from .utils import fatal
    fatal("Ambiguous IVPM workspace: multiple deps directories contain a "
          "package-lock.json (%s). Add an ivpm.yaml with a 'deps-dir' to "
          "disambiguate." % ", ".join(sorted(os.path.basename(c) for c in candidates)))


def check_lock_changes(deps_dir: str, all_pkgs) -> Dict[str, dict]:
    """Compare *all_pkgs* against an existing lock file in *deps_dir*.

    Returns a dict mapping package name → {"current": entry, "locked": entry}
    for packages whose user-specified fields differ from the lock.  An empty
    dict means everything is up to date.

    .. deprecated::
        No longer used by ``ivpm update``.  It ran *before* dependency
        resolution and against the root dep-set only, so drift in a transitive
        dependency was invisible to it.  ``LoadPlanner`` (``load_plan.py``) now
        classifies every package in every scope as it is decided, and
        ``LoadPlanner.drifted()`` is what the update reports.  Retained for
        callers outside the update path; new code should use the planner.
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

    def build_packages_info(self, include_nested: bool = False):
        """Return a PackagesInfo built from the lock file's complete closure.

        Only *root-scope* entries are returned by default. For a flat lock
        (v1, or v2 over a flat workspace) that is every entry, so reproduction
        is unchanged. Entries belonging to a nested scope describe a sub-tree
        that the boundary package's own manifest reconstructs; they are applied
        as version pins instead -- see :meth:`scope_pins`.
        """
        from .packages_info import PackagesInfo
        from .pkg_types.package_git import PackageGit
        from .pkg_types.package_gh_rls import PackageGhRls
        from .pkg_types.package_http import PackageHttp
        from .pkg_types.package_pypi import PackagePyPi
        from .pkg_types.package_url import PackageURL

        packages = self._data.get("packages", {})
        pkgs_info = PackagesInfo("lock")

        # Patch files are written relative to the declaring ivpm.yaml. In
        # reproduction mode there is no ivpm.yaml, so locate them best-effort
        # near the lock file: its directory (typically the deps dir), the parent
        # (typically the project root), and the cwd. Identity does not depend on
        # finding them (see _patches_from_entry); this only helps an uncached MISS.
        _lock_dir = os.path.dirname(os.path.abspath(self.lock_path))
        base_dirs = [os.path.dirname(_lock_dir), _lock_dir, os.getcwd()]

        for key, entry in packages.items():
            name = _entry_name(key, entry)
            if not include_nested and _entry_scope(key, entry):
                continue
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

            elif src == "module":
                from .pkg_types.package_module import PackageModule
                p = PackageModule(name)
                p.module = entry.get("module")
                p.modulefile_path = entry.get("modulefile")
                p.module_root = entry.get("root")
                p.path = entry.get("root")
                p.src_type = "module"
                pkg = p

            else:
                _logger.warning("Unknown src type %r for package %s — skipping", src, name)
                continue

            pkg.resolved_by = entry.get("resolved_by", "root")
            pkg.dep_set = entry.get("dep_set")
            # Reconstruct the patch set so a patched dependency reproduces its
            # patched variant (not a silent pristine tree). Generic across all
            # source types, mirroring _add_patch_fields on the write side.
            pkg.patches = _patches_from_entry(entry, base_dirs)
            pkgs_info[key if include_nested else name] = pkg

        return pkgs_info

    def scope_pins(self) -> dict:
        """Resolved-identity pins for packages that live in a nested scope.

        Maps scope path -> the recorded entry. The resolver applies these as it
        queues each dependency, so a reproduced nested workspace lands on the
        same commits/versions as the locked one while the tree *shape* comes
        from the manifests (which the lock also records, so the two agree).

        Empty for any flat workspace, including every v1 lock.
        """
        pins = {}
        for key, entry in (self._data.get("packages") or {}).items():
            if _entry_scope(key, entry):
                pins[key] = entry
        return pins

    @property
    def python_packages(self) -> dict:
        """Return the locked pip package versions, or empty dict."""
        return self._data.get("python_packages", {})

#****************************************************************************
#* load_plan.py
#*
#* Copyright 2025 Matthew Ballance and Contributors
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
"""Deciding whether a package needs to be loaded.

Source providers used to answer this by asking whether the package directory
exists.  That conflates four different questions, gives four slightly different
answers (``exists`` vs ``isdir``, with and without ``islink``), and reads an
*empty* directory as a fully-loaded package -- so anything that creates the
target directory before the fetch would make IVPM skip the fetch and exit 0
with an empty tree.

``LoadPlanner`` answers the question once, per package, in every dependency
scope:

* **Residency comes from the filesystem.**  It is the only authority on whether
  content is present.  A hand-deleted package directory is ``ABSENT`` no matter
  what the lock says.
* **Identity comes from the lock file.**  It records what IVPM believes is
  there, and can only ever *add* information to a disk observation -- never
  assert presence on its own.

See pkg-prepare-design.md §3.
"""
import contextlib
import dataclasses as dc
import enum
import logging
import os
import stat
import threading
from typing import Dict, Optional

_logger = logging.getLogger("ivpm.load_plan")


class LoadAction(enum.Enum):
    """What the source provider should do with this package."""

    FETCH = enum.auto()      # populate it
    REUSE = enum.auto()      # content is present and acceptable; leave it alone
    RECONCILE = enum.auto()  # present or not, the source must re-examine it


class LoadState(enum.Enum):
    """Why the action was chosen.  Carried for diagnostics and ``ivpm show``."""

    ABSENT = enum.auto()              # nothing at the path
    PREPARED_EMPTY = enum.auto()      # directory exists and is empty
    RESIDENT_LINK = enum.auto()       # symlink (cache hit / deps-source hit)
    RESIDENT_MATCHING = enum.auto()   # populated; lock entry matches the spec
    RESIDENT_DRIFTED = enum.auto()    # populated; lock entry differs
    RESIDENT_UNTRACKED = enum.auto()  # populated; no lock entry
    PATCHED = enum.auto()             # patch manifest on disk, or a patched spec


@dc.dataclass(frozen=True)
class LoadDecision:
    action: LoadAction
    state: LoadState
    reason: str
    lock_entry: Optional[dict] = None
    # {"current": <entry>, "locked": <entry>} when state is RESIDENT_DRIFTED
    drift: Optional[dict] = None

    @property
    def should_fetch(self) -> bool:
        return self.action is LoadAction.FETCH

    @property
    def is_resident(self) -> bool:
        """True when content is already present and will be kept as-is."""
        return self.action is LoadAction.REUSE


@contextlib.contextmanager
def preserve_dir_mode(path: str):
    """Keep *path*'s permission bits across an operation that rewrites them.

    ``shutil.copytree(dirs_exist_ok=True)`` copies the *source* directory's mode
    onto the destination root (via ``copystat``), silently clearing whatever a
    pre-populate step configured there -- the setgid bit in particular, which is
    the whole mechanism for group inheritance. Files created during the copy do
    still inherit correctly, because they are created inside the directory while
    it is set up; it is the directory's own bits that are lost, and those govern
    everything written into it afterwards.

    A no-op when *path* is not an existing directory.
    """
    try:
        mode = os.stat(path).st_mode if (
            os.path.isdir(path) and not os.path.islink(path)) else None
    except OSError:
        mode = None
    try:
        yield
    finally:
        if mode is not None:
            try:
                os.chmod(path, stat.S_IMODE(mode))
            except OSError as e:
                _logger.debug("Could not restore mode on %s: %s", path, e)


def clear_prepared_dir(path: str) -> bool:
    """Remove *path* when it is an empty directory, so content can be written.

    A pre-populate step creates the target directory before the fetch (to set
    its group/mode); the materializers that cannot write over an existing entry
    -- ``os.symlink``, ``shutil.copytree`` -- need it gone again. Removing it is
    safe precisely because it is empty.

    Returns True if a directory was removed. A populated directory, a symlink,
    a plain file or a missing path is left untouched and returns False: callers
    that cannot proceed over one must still raise their own error.
    """
    try:
        if (os.path.isdir(path) and not os.path.islink(path)
                and not os.listdir(path)):
            os.rmdir(path)
            return True
    except OSError as e:
        _logger.debug("Could not clear prepared dir %s: %s", path, e)
    return False


class LoadPlanner(object):
    """Decides, once per package, whether it needs loading.

    One planner serves a whole update, including nested scopes: lock keys *are*
    scope paths, and ``Package.scope_key`` is the matching key (a bare name in a
    flat workspace).  ``decide()`` is called from the fetch worker threads and
    memoizes, so a provider re-asking later in ``Package.update()`` observes the
    same answer it was dispatched with.
    """

    def __init__(self, deps_dir: str, lock_data: Optional[dict] = None):
        self.deps_dir = deps_dir
        self._entries: Dict[str, dict] = dict(
            (lock_data or {}).get("packages", {}) or {})
        self._memo: Dict[str, LoadDecision] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Query                                                               #
    # ------------------------------------------------------------------ #

    def decide(self, pkg) -> LoadDecision:
        """The decision for *pkg*.  Stable for the life of the planner.

        Memoization is what makes the decision immune to its own consequences:
        a prepare step creating the target directory between the dispatch call
        and a provider's later call must not flip ``ABSENT`` to
        ``PREPARED_EMPTY`` -- both mean FETCH, but only one answer may be
        observed.
        """
        key = self._key(pkg)

        with self._lock:
            cached = self._memo.get(key)
        if cached is not None:
            return cached

        # Classified outside the lock: it does filesystem I/O, and a package is
        # only ever decided on its own worker thread.  setdefault settles the
        # (theoretical) tie deterministically -- first writer wins.
        decision = self._classify(pkg)
        with self._lock:
            return self._memo.setdefault(key, decision)

    def drifted(self) -> Dict[str, dict]:
        """Packages decided so far whose spec differs from the lock.

        Maps scope key -> ``{"current": ..., "locked": ...}``.  Unlike the
        pre-resolution pass this replaces, it covers transitive dependencies and
        every nested scope, because it is populated as each package is decided.
        """
        with self._lock:
            return dict(
                (key, d.drift) for key, d in self._memo.items()
                if d.state is LoadState.RESIDENT_DRIFTED and d.drift is not None)

    def decisions(self) -> Dict[str, LoadDecision]:
        """Every decision made so far, keyed by scope key."""
        with self._lock:
            return dict(self._memo)

    # ------------------------------------------------------------------ #
    # Classification                                                      #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _key(pkg) -> str:
        # scope_key is assigned before any provider or handler sees the package
        # (package_updater._update_pkg).  The fallback keeps the planner usable
        # from tests and from callers that build a Package by hand.
        return getattr(pkg, "scope_key", None) or pkg.name

    def _classify(self, pkg) -> LoadDecision:
        path = getattr(pkg, "path", None)

        # (1) Patched trees first, before any residency test.  A patched
        # package is reconciled whether or not it is already on disk, so that a
        # changed patch set is picked up rather than skipped.  Ordering this
        # ahead of everything else is the specification, not an optimization.
        patch_reason = self._patch_reason(pkg, path)
        if patch_reason is not None:
            return LoadDecision(LoadAction.RECONCILE, LoadState.PATCHED,
                                patch_reason)

        # (2) Nothing there.
        if path is None or not os.path.lexists(path):
            return LoadDecision(LoadAction.FETCH, LoadState.ABSENT,
                                "no content at %s" % (path or "<unset path>"))

        # (3) A symlink is a cache or deps-source hit.  Tested before any
        # emptiness probe: the target is shared, read-only and potentially
        # large, and must never be traversed just to answer this question.
        if os.path.islink(path):
            return LoadDecision(LoadAction.REUSE, LoadState.RESIDENT_LINK,
                                "linked from shared content")

        # (4) An empty directory is not a loaded package.  This is the state a
        # pre-populate step leaves behind, and the one the old existence checks
        # mistook for "already loaded".
        if os.path.isdir(path) and not os.listdir(path):
            return LoadDecision(LoadAction.FETCH, LoadState.PREPARED_EMPTY,
                                "directory is present but empty")

        # (5) Populated.  The lock decides what we believe it to be.
        entry = self._entries.get(self._key(pkg))
        if entry is None:
            # No lock entry: a manual checkout, or a workspace whose lock was
            # deleted.  Never treated as license to overwrite -- disk wins.
            return LoadDecision(LoadAction.REUSE, LoadState.RESIDENT_UNTRACKED,
                                "present, not recorded in the lock file")

        if self._spec_matches(pkg, entry):
            return LoadDecision(LoadAction.REUSE, LoadState.RESIDENT_MATCHING,
                                "present and matches the lock file",
                                lock_entry=entry)

        from .package_lock import _entry_from_pkg
        return LoadDecision(
            LoadAction.REUSE, LoadState.RESIDENT_DRIFTED,
            "present, but its specification has changed since it was locked",
            lock_entry=entry,
            drift={"current": _entry_from_pkg(pkg), "locked": entry})

    @staticmethod
    def _patch_reason(pkg, path) -> Optional[str]:
        """Why this package must be reconciled rather than skipped, or None."""
        patchset = getattr(pkg, "patchset", None)
        if patchset is not None and not patchset.is_empty:
            return "a patch set is declared for this package"
        if path and os.path.isfile(
                os.path.join(path, ".ivpm", "patch-manifest.json")):
            return "a patch manifest is present on disk"
        return None

    @staticmethod
    def _spec_matches(pkg, entry) -> bool:
        """Identity comparison, delegated to the lock module.

        ``_spec_matches_lock`` already honors the per-source
        ``Package.spec_matches_lock`` override, so extension package types
        compare themselves.  Deliberately not reimplemented here -- two
        comparators would drift.
        """
        from .package_lock import _spec_matches_lock
        try:
            return _spec_matches_lock(pkg, entry)
        except Exception as e:
            # A comparison that blows up must not decide to re-fetch (that is
            # the destructive direction).  Treat it as a match and say so.
            _logger.debug("spec comparison failed for %s: %s", pkg.name, e)
            return True

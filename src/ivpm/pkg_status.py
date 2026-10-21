#****************************************************************************
#* pkg_status.py
#*
#* Copyright 2024 Matthew Ballance and Contributors
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
import dataclasses as dc
from typing import List, Optional


@dc.dataclass
class SubmoduleStatus:
    """One submodule of a git package, as ``ivpm status`` reports it.

    *state* is ``ok``, ``uninitialized``, ``moved`` (checked out at a commit
    other than the gitlink), ``conflict`` (unmerged gitlink), ``failed`` (the
    lock recorded a failed fetch, and it is still not initialized) or
    ``not-requested`` (not initialized, and the spec does not ask for it)."""
    path: str
    state: str
    commit: Optional[str] = None     # gitlink (what the superproject pins)
    head: Optional[str] = None       # checked-out commit, when initialized
    dirty: bool = False              # the submodule's own tree has changes
    in_scope: bool = False


@dc.dataclass
class PkgVcsStatus:
    """Per-package VCS status returned by Package.status()."""
    name: str
    src_type: str           # "git", "dir", "pypi", …
    path: str               # absolute path in deps_dir
    vcs: str                # "git" | "none"
    branch: Optional[str] = None    # current branch; None if detached HEAD
    tag: Optional[str] = None       # exact tag at HEAD, else None
    commit: str = ""                # short HEAD hash (7 chars) or ""
    is_dirty: bool = False
    modified: List[str] = dc.field(default_factory=list)  # porcelain lines for tracked modifications
    untracked: List[str] = dc.field(default_factory=list)  # porcelain lines for untracked files (??)
    ahead: Optional[int] = None     # commits ahead of upstream; None = unknown
    behind: Optional[int] = None    # commits behind upstream; None = unknown
    error: Optional[str] = None     # set if status could not be determined
    from_deps_source: Optional[str] = None  # parent deps-dir if materialized via --deps-source
    deps_source_auto: bool = False  # from_deps_source resolves into the parent git worktree
    is_root: bool = False           # True for the root-project entry (not a package)
    provider: Optional[str] = None  # clone-provider name that described a root entry
    # The commit this dependency is *declared* to sit at, when the manifest
    # pins one. Distinguishes "IVPM put it here and will keep it here" from a
    # detached HEAD the user produced by hand -- the two look identical on
    # disk, and only the former means sync will leave it alone.
    pinned_commit: Optional[str] = None
    # Every declared submodule, recursively; empty for a repo without any.
    submodules: List[SubmoduleStatus] = dc.field(default_factory=list)
    # The spec in force, in SubmoduleSpec.describe() form ("off", "legacy",
    # "recursive,depth:1", ...), or None when nothing is declared.
    submodules_spec: Optional[str] = None

    @property
    def submodule_problems(self) -> List[SubmoduleStatus]:
        """In-scope submodules that are not cleanly at their gitlink."""
        return [m for m in self.submodules
                if m.state not in ("ok", "not-requested") or m.dirty]

    def submodule_summary(self) -> str:
        """``submodules 3/5 (2 uninitialized: ext/a, ext/b)``, or ``""``."""
        if not self.submodules:
            return ""
        scope = [m for m in self.submodules if m.in_scope]
        if not scope:
            initialized = [m for m in self.submodules if m.head]
            n = len(self.submodules)
            if initialized:
                return "submodules %d/%d initialized (not requested)" % (
                    len(initialized), n)
            return "submodules: %d not initialized (not requested)" % n
        ok = [m for m in scope if m.state == "ok"]
        out = "submodules %d/%d" % (len(ok), len(scope))
        groups = {}
        for m in self.submodules:
            if m.state not in ("ok", "not-requested"):
                groups.setdefault(m.state, []).append(m.path)
            if m.dirty:
                groups.setdefault("dirty", []).append(m.path)
        if groups:
            out += " (" + "; ".join(
                "%d %s: %s" % (len(v), k, ", ".join(v[:3]) +
                               (", ..." if len(v) > 3 else ""))
                for k, v in sorted(groups.items())) + ")"
        return out


def submodule_statuses(path: str, spec=None, locked_modules=None):
    """``[SubmoduleStatus]`` for the git tree at *path* (local only).

    *spec* (a SubmoduleSpec) decides what is in scope; without one nothing
    is.  *locked_modules* is the lock entry's ``modules`` list: a submodule
    the lock recorded as failed, and still not initialized, reports
    ``failed`` rather than plain ``uninitialized``."""
    from . import git_submodules as gsm
    if not gsm.has_gitmodules(path):
        return []
    records = gsm.walk(path, spec if spec is not None else gsm.OFF)
    failed = set(m.get("path") for m in (locked_modules or [])
                 if m.get("status") == "failed")
    out = []
    for r in records:
        if r.update_none:
            continue
        state = r.state
        if state == "uninitialized":
            if not r.in_scope:
                state = "not-requested"
            elif r.path in failed:
                state = "failed"
        if state == "missing":
            continue
        dirty = False
        if r.state in ("ok", "moved"):
            dirty = bool(gsm.submodule_dirty(path, r))
        out.append(SubmoduleStatus(path=r.path, state=state, commit=r.commit,
                                   head=r.head, dirty=dirty,
                                   in_scope=r.in_scope))
    return out


def git_working_tree_status(path: str, name: str, spec=None,
                            locked_modules=None) -> Optional["PkgVcsStatus"]:
    """Return the local git status of the working tree at *path*, or ``None``
    if *path* is not a git repository.

    Single source of truth for "what git status looks like": used both by
    per-dependency status (``PackageGit.status()``) and by root-project status
    (``GitCloneProvider.root_status()``).  Only local queries -- no network.
    A ``.git`` directory (normal clone) or file (git-worktree checkout) both
    count as a repository.
    """
    import os
    import subprocess

    git_dir = os.path.join(path, ".git")
    if not (os.path.isdir(git_dir) or os.path.isfile(git_dir)):
        return None

    def _git(gargs):
        r = subprocess.run(
            ["git"] + gargs,
            capture_output=True, text=True, cwd=path, timeout=10,
        )
        return r.returncode, r.stdout.strip()

    # Branch (None on detached HEAD)
    _, branch_raw = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    branch = None if branch_raw == "HEAD" else branch_raw

    # Tag (only when exactly on a tag)
    rc_tag, tag_raw = _git(["describe", "--tags", "--exact-match", "HEAD"])
    tag = tag_raw if rc_tag == 0 else None

    # Short commit hash
    _, commit = _git(["rev-parse", "--short", "HEAD"])

    # Dirty / modified files
    _, porcelain = _git(["status", "--porcelain"])
    all_lines = [line for line in porcelain.splitlines() if line.strip()]
    untracked = [line for line in all_lines if line.startswith("??")]
    modified = [line for line in all_lines if not line.startswith("??")]

    # Submodules are reported per submodule (moved / dirty / ...), so their
    # one-line " M <path>" entries come out of 'modified'. They still make the
    # package dirty: a moved or modified submodule is work in this tree.
    submodules = submodule_statuses(path, spec, locked_modules)
    if submodules:
        # Parse the path from "XY <path>" without trusting the column: the
        # output was stripped, which eats a leading blank status column.
        sm_paths = set(m.path for m in submodules)
        modified = [line for line in modified
                    if line.strip().split(" ", 1)[-1].strip() not in sm_paths]
    is_dirty = len(modified) > 0 or any(
        m.dirty or m.state in ("moved", "conflict") for m in submodules)

    # Ahead / behind upstream (silenced if no upstream)
    ahead: Optional[int] = None
    behind: Optional[int] = None
    rc_ab, ab_raw = _git(["rev-list", "--left-right", "--count", "@{u}...HEAD"])
    if rc_ab == 0 and ab_raw:
        parts = ab_raw.split()
        if len(parts) == 2:
            try:
                behind = int(parts[0])
                ahead = int(parts[1])
            except ValueError:
                pass

    return PkgVcsStatus(
        name=name,
        src_type="git",
        path=path,
        vcs="git",
        branch=branch,
        tag=tag,
        commit=commit,
        is_dirty=is_dirty,
        modified=modified,
        untracked=untracked,
        ahead=ahead,
        behind=behind,
        submodules=submodules,
        submodules_spec=(spec.describe() if spec is not None and submodules
                         else None),
    )

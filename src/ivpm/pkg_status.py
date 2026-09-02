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


def git_working_tree_status(path: str, name: str) -> Optional["PkgVcsStatus"]:
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
    is_dirty = len(modified) > 0

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
    )

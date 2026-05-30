#****************************************************************************
#* git_worktree.py
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
"""Detect whether ivpm is running inside a linked git worktree, and if so
locate the main worktree.

All functions degrade to ``None`` outside git -- ivpm must keep working in
non-git directories and on systems where git is not installed.  Detection is
purely opportunistic: it shells out to whatever ``git`` is on ``PATH`` (exactly
as the ``git:`` package handler does) and never raises or writes to the
terminal.

Requires git >= 2.31 for ``rev-parse --path-format=absolute``.  On older git
the ``rev-parse`` simply fails, which collapses to the same silent ``None`` as
a non-git directory -- no explicit version check is needed.
"""
import logging
import os
import subprocess
from typing import Optional

_logger = logging.getLogger("ivpm.git_worktree")


def _git(root_dir: str, *args: str) -> Optional[str]:
    """Run ``git -C <root_dir> <args...>`` and return stripped stdout, or
    ``None`` on any failure (git absent, not a repo, non-zero exit).  Never
    raises and never writes to the terminal (stderr is discarded).
    """
    try:
        out = subprocess.run(
            ["git", "-C", root_dir, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False)
    except (FileNotFoundError, OSError):
        return None    # git binary not installed / not executable
    if out.returncode != 0:
        return None    # not a git repo, or some other git error
    line = out.stdout.strip()
    return line or None


def _first_worktree(root_dir: str) -> Optional[str]:
    """Return the path of the first worktree reported by
    ``git worktree list --porcelain`` (always the main worktree, or a bare
    repo), or ``None`` on failure.
    """
    out = _git(root_dir, "worktree", "list", "--porcelain")
    if out is None:
        return None
    for ln in out.splitlines():
        if ln.startswith("worktree "):
            return ln[len("worktree "):].strip()
    return None


def detect_main_worktree(root_dir: str) -> Optional[str]:
    """Return the absolute path of the main worktree iff ``root_dir`` is a
    *linked* worktree backed by a non-bare main working tree; else ``None``.

    Returns ``None`` for every non-worktree situation: not a git repo, git not
    installed, ``root_dir`` is itself the main worktree, a bare main repo, or
    any git error.
    """
    git_dir = _git(root_dir, "rev-parse", "--path-format=absolute", "--git-dir")
    common_dir = _git(root_dir, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if git_dir is None or common_dir is None:
        return None    # not a git repo / git absent / error
    if os.path.realpath(git_dir) == os.path.realpath(common_dir):
        return None    # we ARE the main worktree (git-dir == common-dir)

    main = _first_worktree(root_dir)
    if main is None:
        return None
    if os.path.realpath(main) == os.path.realpath(root_dir):
        return None    # degenerate: first worktree is us (e.g. bare main repo)
    return main

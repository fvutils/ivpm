#****************************************************************************
#* pkg_sync.py
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
"""
pkg_sync.py — result types for `ivpm sync`.

Mirrors pkg_status.py; returned by Package.sync() implementations.
"""
import dataclasses as dc
import enum
from typing import List, Optional


class SyncOutcome(str, enum.Enum):
    UP_TO_DATE          = "up-to-date"      # already at latest
    SYNCED              = "synced"          # fast-forward merge succeeded
    CONFLICT            = "conflict"        # merge conflict — user must resolve
    DIRTY               = "dirty"           # uncommitted changes block merge
    AHEAD               = "ahead"           # local commits not on origin
    ERROR               = "error"           # network / git command failure
    SKIPPED             = "skipped"         # read-only / non-git / tag-pinned

    # dry-run variants (--dry-run / -n)
    DRY_WOULD_SYNC      = "dry:sync"        # would fast-forward cleanly
    DRY_WOULD_CONFLICT  = "dry:conflict"    # diverged — would produce conflicts
    DRY_DIRTY           = "dry:dirty"       # dirty tree would block merge


class SyncProgressListener:
    """Callback interface for live sync progress.

    Implement and pass as ``ProjectSyncInfo.progress`` to receive
    per-package notifications during a (potentially parallel) sync.
    """
    def on_pkg_start(self, name: str) -> None:
        """Called just before a package's sync work begins."""

    def on_pkg_result(self, result: 'PkgSyncResult') -> None:
        """Called immediately after a package's sync work finishes."""


@dc.dataclass
class PkgSyncResult:
    """Per-package sync result returned by Package.sync()."""
    name: str
    src_type: str
    path: str
    outcome: SyncOutcome
    branch: Optional[str] = None
    old_commit: Optional[str] = None       # short commit before merge
    new_commit: Optional[str] = None       # short commit after merge (or would-be)
    commits_behind: Optional[int] = None   # upstream commits pulled / to be pulled
    commits_ahead: Optional[int] = None    # local commits not on origin
    conflict_files: List[str] = dc.field(default_factory=list)
    dirty_files: List[str] = dc.field(default_factory=list)
    next_steps: List[str] = dc.field(default_factory=list)  # shell commands to show
    error: Optional[str] = None
    skipped_reason: Optional[str] = None

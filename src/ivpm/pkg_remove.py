#****************************************************************************
#* pkg_remove.py
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
pkg_remove.py — verdict/result types for `ivpm destroy`.

Mirrors pkg_sync.py. Two halves:

  * The *gate* verdict — ``RemovalSafety`` (level + structured ``SafetyReason``s)
    returned by ``Package.removal_safety()``. Sources return DATA, not prose;
    the front-end (CmdDestroy / TUI) maps each reason ``kind`` to a label and
    renders it at the chosen verbosity.
  * The *teardown* result — ``PkgRemoveResult`` returned by ``Package.remove()``.

``DestroyReport`` bundles the gate verdicts and per-package results so the
front-end can render both the blocking report and the final summary.
"""
import dataclasses as dc
import enum
from typing import Dict, List, Optional


class SafetyLevel(enum.Enum):
    """How safe it is to remove a package, as judged by the package's own
    source provider. ``destroy`` only aggregates these; it never derives them."""
    SAFE = "safe"                  # nothing unrecoverable; remove freely
    UNVERIFIABLE = "unverifiable"  # cannot prove clean (no VCS / no base); listed, not blocked
    BLOCKED = "blocked"            # holds unrecoverable local work


class RemoveOutcome(enum.Enum):
    """Outcome of a single package teardown."""
    REMOVED = "removed"  # the tree/link was removed
    SKIPPED = "skipped"  # nothing to do (missing), or dry-run preview
    BLOCKED = "blocked"  # the gate refused this package
    MANUAL = "manual"    # partial teardown; user must finish (see next_steps)


@dc.dataclass
class SafetyReason:
    """Structured justification for one gate finding. Carries DATA, not prose —
    the front-end formats it for display at the chosen verbosity."""
    kind: str
    # stable machine token the front-end maps to a label:
    #   "modified" | "untracked" | "unpushed" | "stash" | "local-branch"
    #   | "patch-drift" | "unverifiable" | "symlink" | ...
    items: List[str] = dc.field(default_factory=list)
    # the evidence: actual paths / commit subjects / stash names
    count: Optional[int] = None     # magnitude when items may be elided/sampled
    data: dict = dc.field(default_factory=dict)
    # extra structured context the front-end may use,
    # e.g. {"upstream": "origin/feature-x", "ahead": 3}
    label: Optional[str] = None
    # OPTIONAL fallback label for an extension source whose 'kind' the
    # front-end doesn't recognize.


@dc.dataclass
class RemovalSafety:
    """The gate verdict for one package. A source may attach reasons at ANY
    level — a SAFE verdict can still carry an informational reason, and
    UNVERIFIABLE carries why it couldn't verify."""
    level: SafetyLevel
    reasons: List[SafetyReason] = dc.field(default_factory=list)


@dc.dataclass
class PkgRemoveResult:
    """Per-package teardown result returned by Package.remove()."""
    name: str
    src_type: str
    path: str
    removal: str                    # "unlink" | "rmtree" | "provider" | "noop"
    outcome: RemoveOutcome
    blocked_reason: Optional[str] = None   # why the gate refused (shown to user)
    next_steps: List[str] = dc.field(default_factory=list)  # manual follow-up
    error: Optional[str] = None     # teardown error (best-effort-continue)
    removed_paths: List[str] = dc.field(default_factory=list)  # for dry-run/summary


@dc.dataclass
class DestroyReport:
    """The aggregate result of a destroy, returned by ProjectOps.destroy_plan()
    (gate only, no mutation) and ProjectOps.destroy_apply() (after teardown).

    The front-end renders the blocking report from ``gate`` when ``blocked`` is
    True, and the summary from ``results`` after teardown."""
    mode: str                       # "full" | "deps-only"
    target: str                     # resolved wsdir or project_dir
    deps_dir: str
    gate: Dict[str, RemovalSafety] = dc.field(default_factory=dict)
    # name -> verdict; the root project is keyed "<root>" in full mode.
    blocked: bool = False           # any BLOCKED and not --force
    results: List[PkgRemoveResult] = dc.field(default_factory=list)
    dry_run: bool = False
    forced: bool = False

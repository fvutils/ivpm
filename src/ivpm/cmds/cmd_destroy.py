#****************************************************************************
#* cmd_destroy.py
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
import os
import sys

from ..project_ops import ProjectOps
from ..pkg_remove import RemoveOutcome
from ..utils import fatal
from .. import destroy_tui


class CmdDestroy(object):
    """Front-end for `ivpm destroy`. Owns ALL presentation, the confirm
    prompt, and the exit code. ProjectOps returns structured data only."""

    def __init__(self):
        pass

    def __call__(self, args):
        deps_only = getattr(args, "deps_only", False)
        verbose = getattr(args, "verbose", 0)

        # Resolve the target.
        if deps_only:
            target = getattr(args, "project_dir", None) or os.getcwd()
        else:
            target = getattr(args, "wsdir", None)
            if target is None:
                fatal("'ivpm destroy' requires a workspace directory "
                      "(e.g. 'ivpm destroy mydir'), or use --deps-only to "
                      "remove just the imports of the current workspace.")

        ops = ProjectOps(target)

        # Build the progress TUI and wire it as the live listener for both the
        # (parallel) gate and teardown phases. The TUI owns presentation.
        tui = destroy_tui.create_destroy_tui(args)
        args._destroy_progress = tui

        # 1. Plan (read-only): resolve, validate, gate. fatal() refusals
        #    (not-a-workspace, cwd/ancestor) propagate to the top-level handler.
        tui.gate_begin()
        try:
            report = ops.destroy_plan(args=args)
        finally:
            tui.gate_end()

        # 2. Blocked by the gate -> render and stop.
        if report.blocked:
            tui.show_blocking(report, verbose=verbose)
            sys.exit(1)

        # 3. Dry-run -> render the plan and stop.
        if getattr(args, "dry_run", False):
            tui.show_dry_run(report, verbose=verbose)
            return

        # 4. Confirm, unless --yes/--force. (No live display is active here.)
        if not (getattr(args, "yes", False) or getattr(args, "force", False)):
            if not sys.stdin.isatty():
                fatal("refusing to destroy without confirmation in a "
                      "non-interactive context; pass --yes to proceed.")
            n = len(report.gate) - (1 if "<root>" in report.gate else 0)
            scope = ("workspace %s (root + %d import(s))" % (report.target, n)
                     if report.mode == "full"
                     else "%d import(s) under %s" % (n, report.deps_dir))
            resp = input("Remove %s? [y/N] " % scope).strip().lower()
            if resp not in ("y", "yes"):
                print("Aborted; nothing was removed.")
                return

        # 5. Apply (mutating), with the teardown live display.
        tui.teardown_begin()
        try:
            final = ops.destroy_apply(args=args)
        finally:
            tui.teardown_end()
        tui.show_summary(final)

        # 6. Exit non-zero on any teardown error / manual follow-up.
        if any(r.outcome == RemoveOutcome.MANUAL for r in final.results):
            sys.exit(1)

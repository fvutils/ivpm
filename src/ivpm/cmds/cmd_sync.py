'''
Created on Jun 8, 2021

@author: mballance
'''
import os
import sys

from ..project_ops import ProjectOps
from ..pkg_sync import SyncOutcome
from ..sync_tui import create_sync_tui


class CmdSync(object):

    def __init__(self):
        pass

    def __call__(self, args):
        if args.project_dir is None:
            args.project_dir = os.getcwd()

        dry_run = getattr(args, "dry_run", False)
        tui = create_sync_tui(args)

        # Wire the TUI as the live progress listener so it receives
        # per-package notifications during the parallel sync.
        args._sync_progress = tui

        tui.start()
        try:
            results = ProjectOps(args.project_dir).sync(args=args)
        finally:
            tui.stop()

        tui.render(results, dry_run=dry_run)

        # Only exit non-zero on true fatal errors (network failure, git crash, etc.).
        # CONFLICT, DIRTY, and AHEAD are informational — the tool completed successfully.
        if any(r.outcome == SyncOutcome.ERROR for r in results):
            sys.exit(1)


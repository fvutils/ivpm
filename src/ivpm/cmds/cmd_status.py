import os
from ..project_ops import ProjectOps
from ..status_tui import create_status_tui


class CmdStatus(object):

    def __init__(self):
        pass

    def __call__(self, args):
        if args.project_dir is None:
            args.project_dir = os.getcwd()

        results = ProjectOps(args.project_dir).status(args=args)
        verbose = getattr(args, "verbose", 0)
        tui = create_status_tui(args)
        tui.render(results, verbose=verbose)


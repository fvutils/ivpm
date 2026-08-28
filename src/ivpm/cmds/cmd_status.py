import os
from ..project_ops import ProjectOps
from ..status_tui import create_status_tui


class CmdStatus(object):

    def __init__(self):
        pass

    def __call__(self, args):
        # An explicit -p is an assertion: never resolve it to an ancestor.
        # Only a cwd-defaulted start walks up looking for an enclosing scope.
        explicit = args.project_dir is not None
        if not explicit:
            args.project_dir = os.getcwd()

        root_status, results = ProjectOps(args.project_dir).status(
            args=args, walk=not explicit)
        verbose = getattr(args, "verbose", 0)
        tui = create_status_tui(args)
        tui.render(results, verbose=verbose, root_status=root_status)


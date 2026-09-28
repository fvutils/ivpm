import os
from ..install_spec import flatten_dep_sets
from ..project_ops import ProjectOps

class CmdBuild(object):

    def __init__(self):
        self.debug = False
        pass

    def __call__(self, args):
        if args.project_dir is None:
            # If a default is not provided, use the current directory
            print("Note: project_dir not specified ; using working directory")
            args.project_dir = os.getcwd()

        # Deprecated: build uses the installed dep-sets; -d is only validated.
        ds_names = flatten_dep_sets(getattr(args, "dep_set", None))

        print("--> build")
        ProjectOps(args.project_dir).build(
            dep_set=ds_names,
            args=args,
            debug=args.debug)
        print("<-- build")

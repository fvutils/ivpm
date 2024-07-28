import os
import sys
import dataclasses as dc
import subprocess
from ivpm.utils import fatal

@dc.dataclass
class CmdActivate(object):

    def __call__(self, args):
        if args.project_dir is None:
            # If a default is not provided, use the current directory
            print("Note: project_dir not specified ; using working directory")
            args.project_dir = os.getcwd()

        packages_dir = os.path.join(args.project_dir, "packages")
        if not os.path.isdir(packages_dir):
            fatal("No packages directory ; must run ivpm update first")

        python_dir = os.path.join(packages_dir, "python")
        if not os.path.isdir(python_dir):
            fatal("No packages/python directory ; must run ivpm update first")

        activate = os.path.join(python_dir, "bin/activate")

        # TODO: consider non-bash shells and non-Linux platforms
        cmd = ["bash", "-rcfile",  activate]

        result = subprocess.run(cmd)
        sys.exit(result.returncode)


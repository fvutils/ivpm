import os
import sys
import dataclasses as dc
import subprocess
from ivpm.project_info_reader import ProjectInfoReader
from ivpm.utils import fatal

@dc.dataclass
class CmdActivate(object):

    def __call__(self, args):
        if args.project_dir is None:
            # If a default is not provided, use the current directory
#            print("Note: project_dir not specified ; using working directory")
            args.project_dir = os.getcwd()
            
        proj_info = ProjectInfoReader(args.project_dir).read()

        if proj_info is None:
            fatal("Failed to locate IVPM meta-data (eg ivpm.yaml)")

        packages_dir = os.path.join(args.project_dir, "packages")
        if not os.path.isdir(packages_dir):
            fatal("No packages directory ; must run ivpm update first")

        python_dir = os.path.join(packages_dir, "python")
        if not os.path.isdir(python_dir):
            fatal("No packages/python directory ; must run ivpm update first")

        activate = os.path.join(python_dir, "bin/activate")

        # TODO: consider non-bash shells and non-Linux platforms
        shell = getattr(os.environ, "SHELL", "bash")
        cmd = None
        if shell.find("bash") != -1:
            cmd = [shell, "-rcfile",  activate]

            if args.c is not None:
                cmd.extend(["-c", args.c])

        cmd.extend(args.args)

        env = os.environ.copy()
        env["PROJECT_ROOT"] = args.project_dir

        for es in proj_info.env_settings:
            es.apply(env)

        result = subprocess.run(
            cmd,
            env=env)
        sys.exit(result.returncode)


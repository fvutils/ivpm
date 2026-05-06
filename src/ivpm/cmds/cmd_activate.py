import os
import sys
import dataclasses as dc
import platform
import subprocess
from ..proj_info import ProjInfo
from ..utils import fatal, get_venv_bindir

@dc.dataclass
class CmdActivate(object):

    def __call__(self, args):
        if args.project_dir is None:
            # If a default is not provided, use the current directory
#            print("Note: project_dir not specified ; using working directory")
            args.project_dir = os.getcwd()
            
        proj_info = ProjInfo.mkFromProj(args.project_dir)

        if proj_info is None:
            fatal("Failed to locate IVPM meta-data (eg ivpm.yaml)")

        packages_dir = os.path.join(args.project_dir, "packages")
        if not os.path.isdir(packages_dir):
            fatal("No packages directory ; must run ivpm update first")

        python_dir = os.path.join(packages_dir, "python")
        if not os.path.isdir(python_dir):
            fatal("No packages/python directory ; must run ivpm update first")

        bindir = get_venv_bindir(python_dir)

        if platform.system() == "Windows":
            # On Windows, prefer PowerShell; fall back to cmd.exe
            comspec = os.environ.get("COMSPEC", "cmd.exe")
            ps_activate = os.path.join(bindir, "Activate.ps1")
            bat_activate = os.path.join(bindir, "activate.bat")

            if os.path.isfile(ps_activate) and "powershell" not in comspec.lower():
                # Launch PowerShell with the activation script
                cmd = ["powershell", "-NoExit", "-Command",
                       ". '%s'" % ps_activate]
                if args.c is not None:
                    cmd = ["powershell", "-Command",
                           ". '%s'; %s" % (ps_activate, args.c)]
            elif os.path.isfile(bat_activate):
                # Launch cmd.exe with the activation batch file
                cmd = [comspec, "/K", bat_activate]
                if args.c is not None:
                    cmd = [comspec, "/C", "%s && %s" % (bat_activate, args.c)]
            else:
                fatal("Cannot find activation script in %s" % bindir)
        else:
            # Unix shells
            activate = os.path.join(bindir, "activate")
            shell = os.environ.get("SHELL", "bash")
            cmd = None
            if "bash" in shell:
                cmd = [shell, "-rcfile", activate]
                if args.c is not None:
                    cmd.extend(["-c", args.c])
            elif "csh" in shell or "ksh" in shell:
                cmd = [shell, "-s", activate + ".csh"]
                if args.c is not None:
                    cmd.extend(["-i", args.c])
            else:
                # Generic fallback: source activate in a subshell
                cmd = [shell, "-c", ". '%s' && exec %s" % (activate, shell)]
                if args.c is not None:
                    cmd = [shell, "-c", ". '%s' && %s" % (activate, args.c)]

        cmd.extend(args.args)

        env = os.environ.copy()
        env["IVPM_PROJECT"] = args.project_dir
        env["IVPM_PACKAGES"] = os.path.join(args.project_dir, "packages")
        # env["VIRTUAL_ENV_DISABLE_PROMPT"] = "1"

        # PS1 = getattr(env, "PS1", None)
        # print("PS1: %s" % str(PS1))
        # if PS1 is not None:
        #     PS1 = "(ivpm) %s" % PS1
        # else:
        #     PS1 = "\\[\\](ivpm) "
        # env["PS1"] = PS1

        for es in proj_info.env_settings:
            es.apply(env)

        result = subprocess.run(
            cmd,
            env=env)
        sys.exit(result.returncode)

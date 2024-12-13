'''
Created on Jan 19, 2020

@author: ballance
'''

import argparse
import os
import sys

from ivpm.packages_info import PackagesInfo
from ivpm.proj_info import ProjInfo
from .cmds.cmd_activate import CmdActivate
from .cmds.cmd_build import CmdBuild
from .cmds.cmd_init import CmdInit
from .cmds.cmd_update import CmdUpdate
from .cmds.cmd_git_status import CmdGitStatus
from .cmds.cmd_git_update import CmdGitUpdate
from .cmds.cmd_share import CmdShare
from .cmds.cmd_snapshot import CmdSnapshot
from .cmds.cmd_pkg_info import CmdPkgInfo


def get_parser(parser_ext = None):
    """Create the argument parser"""
    parser = argparse.ArgumentParser(prog="ivpm")
    
    subparser = parser.add_subparsers()
    subparser.required = True
    subparser.dest = 'command'

    activate_cmd = subparser.add_parser("activate",
        help="Starts a new shell that contains the activated python virtual environment")
    activate_cmd.add_argument("-c",
        help="When specified, executes the specified string")
    activate_cmd.add_argument("-p", "--project-dir", dest="project_dir",
        help="Specifies the project directory to use (default: cwd)")
    activate_cmd.add_argument("args", nargs='*')
    activate_cmd.set_defaults(func=CmdActivate())

    build_cmd = subparser.add_parser("build",
        help="Build all sub-projects with an IVPM-supported build infrastructure (Python)")
    build_cmd.add_argument("-d", "--dep-set", dest="dep_set", 
        help="Uses dependencies from specified dep-set instead of 'default-dev'")
    build_cmd.add_argument("-g", "--debug", 
        action="store_true",
        help="Enables debug for native extensions")
    build_cmd.set_defaults(func=CmdBuild())

    pkginfo_cmd = subparser.add_parser("pkg-info",
        help="Collect paths/files for a listed set of packages")
    pkginfo_cmd.add_argument("type", 
            choices=("paths", "libdirs", "libs", "flags"),
            help="Specifies what info to query")
    pkginfo_cmd.add_argument("-k", "--kind",
            help="Specifies qualifiers on the type of info to query")
    pkginfo_cmd.add_argument("pkgs", nargs="+")
    pkginfo_cmd.set_defaults(func=CmdPkgInfo())

    share_cmd = subparser.add_parser("share",
        help="Returns the 'share' directory, which includes cmake files, etc")
    share_cmd.add_argument("path", nargs=argparse.REMAINDER)
    share_cmd.set_defaults(func=CmdShare())

    update_cmd = subparser.add_parser("update",
        help="Fetches packages specified in ivpm.yaml that have not already been loaded")
    update_cmd.set_defaults(func=CmdUpdate())
    update_cmd.add_argument("-p", "--project-dir", dest="project_dir",
        help="Specifies the project directory to use (default: cwd)")
    update_cmd.add_argument("-d", "--dep-set", dest="dep_set", 
        help="Uses dependencies from specified dep-set instead of 'default-dev'")
    update_cmd.add_argument("-a", "--anonymous-git", dest="anonymous", 
        action="store_true",
        help="Clones git repositories in 'anonymous' mode")
#    update_cmd.add_argument("-r", "--requirements", dest="requirements")
    
    init_cmd = subparser.add_parser("init",
        help="Creates an initial ivpm.yaml file")
    init_cmd.set_defaults(func=CmdInit())
    init_cmd.add_argument("-v", "--version", default="0.0.1")
    init_cmd.add_argument("-f", "--force", default=False, action='store_const', const=True)
    init_cmd.add_argument("name")
    
    git_status_cmd = subparser.add_parser("git-status",
        help="Runs git status on any git packages")
    git_status_cmd.set_defaults(func=CmdGitStatus())
    git_status_cmd.add_argument("-p", "-project-dir", dest="project_dir")
    
    git_update_cmd = subparser.add_parser("git-update")
    git_update_cmd.set_defaults(func=CmdGitUpdate())
    git_update_cmd.add_argument("-p", "-project-dir", dest="project_dir")
    
    snapshot_cmd = subparser.add_parser("snapshot",
        help="Creates a snapshot of required packages")
    snapshot_cmd.set_defaults(func=CmdSnapshot())
    snapshot_cmd.add_argument("-p", "-project-dir", dest="project_dir")
    snapshot_cmd.add_argument("-r", "--rls-deps", dest="rls", action="store_true",
        help="Uses release deps from project root instead of dev deps")
    snapshot_cmd.add_argument("snapshot_dir", 
            help="Specifies the directory where the snapshot will be created")

    if parser_ext is not None:
        for ext in parser_ext:
            ext(subparser)

    return parser

def main(project_dir=None):
    from .pkg_types.pkg_type_rgy import PkgTypeRgy

    # First things first: load any extensions
    import sys
    if sys.version_info < (3, 10):
        from importlib_metadata import entry_points
    else:
        from importlib.metadata import entry_points

    discovered_plugins = entry_points(group='ivpm.ext')
    parser_ext = []
    for p in discovered_plugins:
        try:
            mod = p.load()
            if hasattr(mod, "ivpm_subcommand"):
                parser_ext.append(getattr(mod, "ivpm_subcommand"))
            elif hasattr(mod, "ivpm_pkgtype"):
                pkg_types = []
                getattr(mod, "ivpm_pkgtype")(pkg_types)
                for pt in pkg_types:
                    PkgTypeRgy.inst().register(pt[0], pt[1], pt[2] if len(pt) > 2 else "")
        except Exception as e:
            print("Error: caught exception while loading IVPM extension %s (%s)" %(
                p.name,
                str(e)))
            raise e

    parser = get_parser(parser_ext)
    
    args = parser.parse_args()

    # If the user hasn't specified the project directory,
    # set the default
    if not hasattr(args, "project_dir") or getattr(args, "project_dir") is None:
        args.project_dir = project_dir

    args.func(args)

if __name__ == "__main__":
    main()
    

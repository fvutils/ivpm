'''
Created on Jan 19, 2020

@author: ballance
'''

import argparse
import os
import sys

from typing import Dict, List, Tuple

from ivpm.packages_info import PackagesInfo
from ivpm.proj_info import ProjInfo
from ivpm.msg import setup_logging
from .cmds.cmd_activate import CmdActivate
from .cmds.cmd_build import CmdBuild
from .cmds.cmd_cache import CmdCache
from .cmds.cmd_init import CmdInit
from .cmds.cmd_update import CmdUpdate
from .cmds.cmd_clone import CmdClone
from .cmds.cmd_git_status import CmdGitStatus
from .cmds.cmd_git_update import CmdGitUpdate
from .cmds.cmd_pkg_info import CmdPkgInfo
from .cmds.cmd_share import CmdShare
from .cmds.cmd_snapshot import CmdSnapshot
from .cmds.cmd_status import CmdStatus
from .cmds.cmd_sync import CmdSync


def get_parser(parser_ext : List = None, options_ext : List = None):
    """Create the argument parser"""
    subcommands : Dict[str, object] = {}
    parser = argparse.ArgumentParser(prog="ivpm")

    # Global options that apply to all sub-commands
    parser.add_argument("--log-level", dest="log_level",
        choices=["INFO", "DEBUG", "WARN", "NONE"],
        default="NONE",
        help="Set the logging level (default: NONE)")
    
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
    subcommands["activate"] = activate_cmd

    build_cmd = subparser.add_parser("build",
        help="Build all sub-projects with an IVPM-supported build infrastructure (Python)")
    build_cmd.add_argument("-d", "--dep-set", dest="dep_set", 
        help="Uses dependencies from specified dep-set instead of 'default-dev'")
    build_cmd.add_argument("-g", "--debug", 
        action="store_true",
        help="Enables debug for native extensions")
    build_cmd.set_defaults(func=CmdBuild())
    subcommands["build"] = build_cmd

    # Cache management commands
    cache_cmd = subparser.add_parser("cache",
        help="Manage the IVPM package cache")
    cache_subparser = cache_cmd.add_subparsers(dest="cache_cmd")
    cache_subparser.required = True

    cache_init_cmd = cache_subparser.add_parser("init",
        help="Initialize a new cache directory")
    cache_init_cmd.add_argument("cache_dir",
        help="Path to the cache directory to initialize")
    cache_init_cmd.add_argument("-s", "--shared", dest="shared", action="store_true",
        help="Set group inheritance (chmod g+s) for shared cache usage")
    cache_init_cmd.add_argument("-f", "--force", dest="force", action="store_true",
        help="Force reinitialization of existing directory")

    cache_info_cmd = cache_subparser.add_parser("info",
        help="Show cache information (packages, versions, sizes)")
    cache_info_cmd.add_argument("-c", "--cache-dir", dest="cache_dir",
        help="Cache directory (default: $IVPM_CACHE)")
    cache_info_cmd.add_argument("-v", "--verbose", dest="verbose", action="store_true",
        help="Show detailed version information")

    cache_clean_cmd = cache_subparser.add_parser("clean",
        help="Remove old cache entries")
    cache_clean_cmd.add_argument("-c", "--cache-dir", dest="cache_dir",
        help="Cache directory (default: $IVPM_CACHE)")
    cache_clean_cmd.add_argument("-d", "--days", dest="days", type=int, default=7,
        help="Remove entries older than this many days (default: 7)")

    cache_cmd.set_defaults(func=CmdCache())
    subcommands["cache"] = cache_cmd

    pkginfo_cmd = subparser.add_parser("pkg-info",
        help="Collect paths/files for a listed set of packages")
    pkginfo_cmd.add_argument("type", 
            choices=("incdirs", "paths", "libdirs", "libs", "flags"),
            help="Specifies what info to query")
    pkginfo_cmd.add_argument("-k", "--kind",
            help="Specifies qualifiers on the type of info to query")
    pkginfo_cmd.add_argument("pkgs", nargs="+")
    pkginfo_cmd.set_defaults(func=CmdPkgInfo())
    subcommands["pkginfo"] = pkginfo_cmd

    share_cmd = subparser.add_parser("share",
        help="Returns the 'share' directory, which includes cmake files, etc")
    share_cmd.add_argument("path", nargs=argparse.REMAINDER)
    share_cmd.set_defaults(func=CmdShare())
    subcommands["share"] = share_cmd

    clone_cmd = subparser.add_parser("clone",
        help="Create a new workspace from a Git URL or path")
    clone_cmd.add_argument("src", help="Source URL or path to clone")
    clone_cmd.add_argument("-a", "--anonymous", dest="anonymous", action="store_true",
        help="Clone anonymously (HTTPS); default converts to SSH when applicable")
    clone_cmd.add_argument("-b", "--branch", dest="branch",
        help="Target branch; checks out existing or creates new")
    clone_cmd.add_argument("workspace_dir", nargs="?",
        help="Target workspace directory; defaults to basename of src")
    clone_cmd.add_argument("-d", "--dep-set", dest="dep_set",
        help="Dependency set to use for ivpm update")
    clone_cmd.add_argument("--py-uv", dest="py_uv", action="store_true",
        help="Use 'uv' to manage virtual environment")
    clone_cmd.add_argument("--py-pip", dest="py_pip", action="store_true",
        help="Use 'pip' to manage virtual environment")
    clone_cmd.set_defaults(func=CmdClone())
    subcommands["clone"] = clone_cmd

    update_cmd = subparser.add_parser("update",
        help="Fetches packages specified in ivpm.yaml that have not already been loaded")
    update_cmd.set_defaults(func=CmdUpdate())
    update_cmd.add_argument("-p", "--project-dir", dest="project_dir",
        help="Specifies the project directory to use (default: cwd)")
    update_cmd.add_argument("-d", "--dep-set", dest="dep_set", 
        help="Uses dependencies from specified dep-set instead of 'default-dev'")
    update_cmd.add_argument("-j", "--jobs", dest="jobs", type=int, default=None,
        help="Maximum number of parallel package fetches (default: number of CPU cores)")
    update_cmd.add_argument("-a", "--anonymous-git", dest="anonymous", 
        action="store_true",
        help="Clones git repositories in 'anonymous' mode")
    update_cmd.add_argument("--skip-py-install", "--py-skip-install",
        help="Skip installation of Python packages",
        action="store_true")
    update_cmd.add_argument("--force-py-install", "--py-force-install",
        help="Forces a re-install of Python packages",
        action="store_true")
    update_cmd.add_argument("--py-prerls-packages",
        help="Enable installation of pre-release packages",
        action="store_true")
    update_cmd.add_argument("--py-uv",
        help="Use 'uv' to manage virtual environment",
        action="store_true")
    update_cmd.add_argument("--py-pip",
        help="Use 'uv' to manage virtual environment",
        action="store_true")
    subcommands["update"] = update_cmd
#    update_cmd.add_argument("-r", "--requirements", dest="requirements")
    
    init_cmd = subparser.add_parser("init",
        help="Creates an initial ivpm.yaml file")
    init_cmd.set_defaults(func=CmdInit())
    init_cmd.add_argument("-v", "--version", default="0.0.1")
    init_cmd.add_argument("-f", "--force", default=False, action='store_const', const=True)
    init_cmd.add_argument("name")
    subcommands["init"] = init_cmd
    
    git_status_cmd = subparser.add_parser("git-status",
        help="Runs git status on any git packages (Note: deprecated. use 'status' instead)")
    git_status_cmd.set_defaults(func=CmdGitStatus())
    git_status_cmd.add_argument("-p", "-project-dir", dest="project_dir")
    
    git_update_cmd = subparser.add_parser("git-update",
        help="Updates any git packages (Note: deprecated. use 'sync' instead)")
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
    subcommands["snapshot"] = snapshot_cmd

    sync_cmd = subparser.add_parser("sync",
        help="Synchronizes dependent packages with an upstream source (if available)")
    sync_cmd.set_defaults(func=CmdSync())

    status_cmd = subparser.add_parser("status",
        help="Checks the status of sub-dependencies such as git repositories")
    status_cmd.set_defaults(func=CmdStatus())

    if parser_ext is not None:
        for ext in parser_ext:
            ext(subparser)

    if options_ext is not None:
        for ext in options_ext:
            ext(subcommands)

    return parser

def main(project_dir=None):
    from .pkg_types.pkg_type_rgy import PkgTypeRgy
    import logging

    # First things first: load any extensions
    import sys
    if sys.version_info < (3, 10):
        from importlib_metadata import entry_points
    else:
        from importlib.metadata import entry_points

    discovered_plugins = entry_points(group='ivpm.ext')
    parser_ext = []
    options_ext = []
    for p in discovered_plugins:
        try:
            mod = p.load()
            if hasattr(mod, "ivpm_subcommand"):
                parser_ext.append(getattr(mod, "ivpm_subcommand"))
            elif hasattr(mod, "ivpm_options"):
                options_ext.append(mod)
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

    parser = get_parser(parser_ext, options_ext)
    
    # Custom parsing to allow trailing workspace dir after options for 'clone'
    args, extras = parser.parse_known_args()
    if getattr(args, 'command', None) == 'clone' and getattr(args, 'workspace_dir', None) is None:
        if len(extras) == 1:
            args.workspace_dir = extras[0]
            extras = []
    if len(extras) != 0:
        print('ivpm: error: unrecognized arguments: ' + ' '.join(extras))
        sys.exit(2)

    # Setup logging based on --log-level option
    log_level = getattr(args, 'log_level', 'NONE')
    setup_logging(log_level)

    # If the user hasn't specified the project directory,
    # set the default
    if not hasattr(args, "project_dir") or getattr(args, "project_dir") is None:
        args.project_dir = project_dir

    args.func(args)

if __name__ == "__main__":
    main()
    

'''
Created on Jan 19, 2020

@author: ballance
'''

import argparse
import os
import sys
import tarfile
import urllib.request
from zipfile import ZipFile

from ivpm.packages_info import PackagesInfo
from ivpm.proj_info import ProjInfo
from .cmds.cmd_init import CmdInit
from .cmds.cmd_update import CmdUpdate
from .cmds.cmd_git_status import CmdGitStatus
from .cmds.cmd_git_update import CmdGitUpdate
from .cmds.cmd_share import CmdShare
from .cmds.cmd_snapshot import CmdSnapshot
from .cmds.cmd_c_flags import CmdCFlags


#********************************************************************
# write_packages
#********************************************************************
def write_packages(packages_mf, packages):
    
    with open(packages_mf, "w") as fh:
        for package in packages.keys():
            fh.write(package + "@" + packages[package] + "\n")
  
#********************************************************************
# write_packages_mk
#********************************************************************
def write_packages_mk(
        packages_mk, 
        project,
        package_deps):
    packages_dir = os.path.dirname(packages_mk)
    
    print("write_packages_mk: " + packages_dir)
  
    fh = open(packages_mk, "w")
    fh.write("#********************************************************************\n");
    fh.write("# packages.mk for " + project + "\n");
    fh.write("#********************************************************************\n");
    fh.write("\n");
    fh.write("ifneq (1,$(RULES))\n");
    fh.write("  ifeq (,$(IVPM_PYTHON))\n")
    if os.path.isdir(os.path.join(packages_dir, "python", "Scripts")):
        fh.write("    IVPM_PYTHON := $(PACKAGES_DIR)/python/Scripts/python\n")
    else:
        fh.write("    IVPM_PYTHON := $(PACKAGES_DIR)/python/bin/python\n")
    fh.write("  endif\n")
    fh.write("  PYTHON_BIN ?= $(IVPM_PYTHON)\n")
    fh.write("  IVPM_PYTHON_BINDIR := $(dir $(IVPM_PYTHON))\n")
# Remove this until we can figure out what's going on
    fh.write("  PATH := $(IVPM_PYTHON_BINDIR):$(PATH)\n")
    fh.write("  export PATH\n")
    fh.write("package_deps = " + project + "\n")
  
    for p in package_deps.keys():
        print("package_dep: " + str(p))
        info = package_deps[p]
        fh.write(p + "_deps=")
        for d in info.deps():
            if d != project and os.path.exists(os.path.join(packages_dir, d, "etc/packages.mf")):
                fh.write(d + " ")
        fh.write("\n")
        fh.write(p + "_clean_deps=")
        for d in info.deps():
            if d != project and os.path.exists(os.path.join(packages_dir, d, "etc/packages.mf")):
                fh.write("clean_" + d + " ")
        fh.write("\n")
      
        if os.path.isfile(packages_dir + "/" + p + "/mkfiles/" + p + ".mk"):
            fh.write("include $(PACKAGES_DIR)/" + p + "/mkfiles/" + p + ".mk\n")

    fh.write("else # Rules\n");
    fh.write("ifneq (1,$(PACKAGES_MK_RULES_INCLUDED))\n");
    fh.write("PACKAGES_MK_RULES_INCLUDED := 1\n")
    for p in package_deps.keys():
        info = package_deps[p]
        fh.write(p + " : $(" + p + "_deps)\n");
     
        if info.is_src:
            fh.write("\t$(Q)$(MAKE) PACKAGES_DIR=$(PACKAGES_DIR) PHASE2=true -C $(PACKAGES_DIR)/" + p + "/scripts -f ivpm.mk build\n")
        fh.write("\n");
        fh.write("clean_" + p + " : $(" + p + "_clean_deps)\n");
     
        if info.is_src:
            fh.write("\t$(Q)$(MAKE) PACKAGES_DIR=$(PACKAGES_DIR) PHASE2=true -C $(PACKAGES_DIR)/" + p + "/scripts -f ivpm.mk clean\n")
        fh.write("\n");
      
        if os.path.isfile(packages_dir + "/" + p + "/mkfiles/" + p + ".mk"):
            fh.write("include $(PACKAGES_DIR)/" + p + "/mkfiles/" + p + ".mk\n")

    fh.write("\n")
    fh.write("endif # PACKAGES_MK_RULES_INCLUDED\n")
    fh.write("endif\n");
    fh.write("\n")
  
    fh.close()
  
#********************************************************************
# write_sve_f
#********************************************************************
def write_sve_f(
        sve_f, 
        project,
        package_deps):
    packages_dir = os.path.dirname(sve_f)
  
    with open(sve_f, "w") as fh:
        fh.write("//********************************************************************\n");
        fh.write("//* sve.F for " + project + "\n");
        fh.write("//********************************************************************\n");
        fh.write("\n");

        for p in os.listdir(packages_dir):
            if os.path.isfile(os.path.join(packages_dir, p, "sve.F")):
                fh.write("-F ./" + p + "/sve.F\n")
                

#********************************************************************
# write_packages_env
#********************************************************************
def write_packages_env(
        env_f,
        is_csh,
        project,
        package_deps):
  
    with open(env_f, "w") as fh:
        fh.write("#********************************************************************\n");
        fh.write("#* environment setup file for " + project + "\n");
        fh.write("#********************************************************************\n");
        fh.write("\n");

        for p in package_deps.keys():
            info = package_deps[p]
            ivpm = info.ivpm_info

            if "rootvar" in ivpm.keys():
                if is_csh:
                    fh.write("setenv " + ivpm["rootvar"] + " $PACKAGES_DIR/" + ivpm["name"] + "\n")
                else:
                    fh.write("export " + ivpm["rootvar"] + "=$PACKAGES_DIR/" + ivpm["name"] + "\n")
      


def fetch_file(
        url,
        dest):
    urllib.request.urlretrieve(url, dest)
    pass
    
        

     

def get_parser():
    """Create the argument parser"""
    parser = argparse.ArgumentParser(prog="ivpm")
    
    subparser = parser.add_subparsers()
    subparser.required = True
    subparser.dest = 'command'

    cflags_cmd = subparser.add_parser("pkg-flags",
        help="Collect cflags for a listed set of packages")
    cflags_cmd.add_argument("pkgs", nargs="+")
    cflags_cmd.set_defaults(func=CmdCFlags())

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

    return parser

def main(project_dir=None):
    parser = get_parser()
    
    args = parser.parse_args()

    # If the user hasn't specified the project directory,
    # set the default
    if not hasattr(args, "project_dir") or getattr(args, "project_dir") is None:
        args.project_dir = project_dir

    args.func(args)
    pass

if __name__ == "__main__":
    main()
    

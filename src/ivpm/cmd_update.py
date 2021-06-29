'''
Created on Jun 8, 2021

@author: mballance
'''
import os
from subprocess import check_output

from ivpm.packages_info import PackagesInfo
from ivpm.project_info_reader import ProjectInfoReader
from ivpm.utils import get_venv_python, setup_venv
from ivpm.msg import note, fatal
from ivpm.sve_filelist_writer import SveFilelistWriter
from ivpm.out_wrapper import OutWrapper
from ivpm.package_updater import PackageUpdater
from _struct import pack


class CmdUpdate(object):
    
    def __init__(self):
        pass
    
    def __call__(self, args):
        if args.project_dir is None:
            # If a default is not provided, use the current directory
            print("Note: project_dir not specified ; using working directory")
            args.project_dir = os.getcwd()
            
        proj_info = ProjectInfoReader(args.project_dir).read()
            
        packages_dir = os.path.join(args.project_dir, "packages")
 
        # Ensure that we have a python virtual environment setup
        if not os.path.isdir(os.path.join(packages_dir, "python")):
            ivpm_python = setup_venv(os.path.join(packages_dir, "python"))
        else:
            note("python virtual environment already exists")
            ivpm_python = get_venv_python(os.path.join(packages_dir, "python"))
            
        if not args.rls:
            if proj_info.dev_deps is None:
                fatal("development mode selected, but no dev-deps specified")
        else:
            if proj_info.deps is None:
                fatal("release mode selected, but no deps specified")

        print("********************************************************************")
        print("* Processing root package %s" % proj_info.name)
        print("********************************************************************")
        pkgs_info = PackageUpdater(packages_dir, args.anonymous).update(
            proj_info.dev_deps if not args.rls else proj_info.deps
            )
            
        with open(os.path.join(packages_dir, "sve.F"), "w") as fp:
            SveFilelistWriter(OutWrapper(fp)).write(pkgs_info)


    
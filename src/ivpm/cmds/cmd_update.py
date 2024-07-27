'''
Created on Jun 8, 2021

@author: mballance
'''
import os
from subprocess import check_output

from ivpm.packages_info import PackagesInfo
from ivpm.project_info_reader import ProjectInfoReader
from ivpm.project_update import ProjectUpdate
from ivpm.utils import get_venv_python, setup_venv
from ivpm.msg import note, fatal, warning
from ivpm.sve_filelist_writer import SveFilelistWriter
from ivpm.out_wrapper import OutWrapper
from ivpm.package_updater import PackageUpdater
from ivpm.package import Package, PackageType, SourceType
import subprocess
from toposort import toposort
from typing import List


class CmdUpdate(object):
    
    def __init__(self):
        self.debug = False
        pass
    
    def __call__(self, args):
        if args.project_dir is None:
            # If a default is not provided, use the current directory
            print("Note: project_dir not specified ; using working directory")
            args.project_dir = os.getcwd()

        ds_name = "default-dev"

        if hasattr(args, "dep_set") and args.dep_set is not None:
            ds_name = args.dep_set

        ProjectUpdate(
            args.project_dir,
            dep_set=ds_name,
            anonymous=args.anonymous
        )            


#        with open(os.path.join(packages_dir, "sve.F"), "w") as fp:
#            SveFilelistWriter(OutWrapper(fp)).write(pkgs_info)


    # def _write_requirements_txt(self, 
    #                             packages_dir,
    #                             python_pkgs : List[Package],
    #                             file):
    #     with open(file, "w") as fp:
    #         for pkg in python_pkgs:
                
    #             if pkg.url is not None:
    #                 # Editable package
    #                 # fp.write("-e file://%s/%s#egg=%s\n" % (
    #                 #     packages_dir.replace("\\","/"), 
    #                 #     pkg.name, 
    #                 #     pkg.name))
    #                 fp.write("-e %s/%s\n" % (
    #                     packages_dir.replace("\\","/"), 
    #                     pkg.name))
    #             else:
    #                 # PyPi package
    #                 if pkg.version is not None:
    #                     if pkg.version[0] in ['<','>','=']:
    #                         fp.write("%s%s\n" % (pkg.name, pkg.version))
    #                     else:
    #                         fp.write("%s==%s\n" % (pkg.name, pkg.version))
    #                 else:
    #                     fp.write("%s\n" % pkg.name)
   
    

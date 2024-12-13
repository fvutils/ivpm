'''
Created on Jun 8, 2021

@author: mballance
'''
import os
from subprocess import check_output

from ivpm.packages_info import PackagesInfo
from ivpm.project_update import ProjectUpdate
from ivpm.utils import get_venv_python, setup_venv
from ivpm.msg import note, fatal, warning
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
        ).update()



   
    

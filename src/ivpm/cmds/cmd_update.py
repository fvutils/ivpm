'''
Created on Jun 8, 2021

@author: mballance
'''
import os

from ivpm.packages_info import PackagesInfo
from ivpm.project_ops import ProjectOps
from ivpm.out_wrapper import OutWrapper
from ivpm.package_updater import PackageUpdater
from ivpm.package import Package, PackageType, SourceType
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

        ProjectOps(args.project_dir).update(
            dep_set=ds_name,
            anonymous=args.anonymous)



   
    

'''
Created on Jun 8, 2021

@author: mballance
'''
import logging
import os

from ivpm.project_ops import ProjectOps

_logger = logging.getLogger("ivpm.cmd_update")


class CmdUpdate(object):
    
    def __init__(self):
        self.debug = False
        pass
    
    def __call__(self, args):
        if args.project_dir is None:
            # If a default is not provided, use the current directory
            _logger.info("project_dir not specified; using working directory")
            args.project_dir = os.getcwd()

        ds_name = None
        if hasattr(args, "dep_set") and args.dep_set is not None:
            ds_name = args.dep_set

        ProjectOps(args.project_dir).update(
            dep_set=ds_name,
            force_py_install=args.force_py_install,
            args=args)



   
    

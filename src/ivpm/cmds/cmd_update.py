'''
Created on Jun 8, 2021

@author: mballance
'''
import logging
import os

from ivpm.project_ops import ProjectOps
from ivpm.variables import parse_definitions

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

        # --dep-set is repeatable (action="append") and each value may itself
        # be a comma-separated list, so flatten into an ordered, de-duplicated
        # list of dep-set names. None means "use the catalog/manifest default".
        ds_name = None
        if getattr(args, "dep_set", None):
            ds_name = []
            for val in args.dep_set:
                for name in val.split(","):
                    name = name.strip()
                    if name and name not in ds_name:
                        ds_name.append(name)

        cli_overrides = parse_definitions(getattr(args, 'definitions', []))

        ProjectOps(args.project_dir).update(
            dep_set=ds_name,
            force_py_install=args.force_py_install,
            args=args,
            lock_file=getattr(args, "lock_file", None),
            refresh_all=getattr(args, "refresh_all", False),
            force=getattr(args, "force", False),
            cli_overrides=cli_overrides,
            from_manifest=getattr(args, "from_manifest", None),
            deps_dir_override=getattr(args, "deps_dir_override", None))



   
    

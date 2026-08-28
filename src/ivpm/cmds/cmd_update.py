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
        # be a comma-separated list. None means "use the catalog/manifest
        # default". Shared with 'ivpm install', which applies the same rule
        # per source.
        from ivpm.install_spec import flatten_dep_sets
        ds_name = flatten_dep_sets(getattr(args, "dep_set", None))

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
            deps_dir_override=getattr(args, "deps_dir_override", None),
            timing=getattr(args, "timing", False))



   
    

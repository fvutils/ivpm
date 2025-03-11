'''
Created on Jun 8, 2021

@author: mballance
'''
import os
import sys
from ivpm.arg_utils import ensure_have_project_dir
from ..proj_info import ProjInfo
from ..utils import fatal

class CmdStatus(object):
    
    def __init__(self):
        pass
    
    def __call__(self, args):
        
        if args.project_dir is None:
            args.project_dir = os.getcwd()

        proj_info = ProjInfo.mkFromProj(args.project_dir)

        if proj_info is None:
            fatal("Failed to locate IVPM meta-data (eg ivpm.yaml)")
            
        deps_dir = os.path.join(args.project_dir, proj_info.deps_dir)

        # After that check, go ahead and just check directories
        for dir in os.listdir(deps_dir):
            if os.path.isdir(os.path.join(deps_dir, dir, ".git")):
                print("Package: " + dir)
                cwd = os.getcwd()
                os.chdir(deps_dir + "/" + dir)
                status = os.system("git status -s")
                os.chdir(cwd)
            elif dir != "python" and os.path.isdir(os.path.join(deps_dir, dir)):
                print("Note: skipping non-Git package \"" + dir + "\"")
                sys.stdout.flush()

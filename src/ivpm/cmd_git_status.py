'''
Created on Jun 8, 2021

@author: mballance
'''
import os
import sys
from ivpm.arg_utils import ensure_have_project_dir
from ivpm.project_info_reader import ProjectInfoReader

class CmdGitStatus(object):
    
    def __init__(self):
        pass
    
    def __call__(self, args):
        
        if args.project_dir is None:
            args.project_dir = os.getcwd()

        packages_dir = os.path.join(args.project_dir, "packages")

        # After that check, go ahead and just check directories
        for dir in os.listdir(packages_dir):
            if os.path.isdir(os.path.join(packages_dir, dir, ".git")):
                print("Package: " + dir)
                cwd = os.getcwd()
                os.chdir(packages_dir + "/" + dir)
                status = os.system("git status -s")
                os.chdir(cwd)
            elif dir != "python" and os.path.isdir(os.path.join(packages_dir, dir)):
                print("Note: skipping non-Git package \"" + dir + "\"")
                sys.stdout.flush()        
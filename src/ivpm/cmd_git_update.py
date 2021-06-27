'''
Created on Jun 8, 2021

@author: mballance
'''
import os
import subprocess
import sys

from ivpm.arg_utils import ensure_have_project_dir


class CmdGitUpdate(object):
    
    def __init__(self):
        pass
    
    def __call__(self, args):
        ensure_have_project_dir(args)
    
        packages_dir = os.path.join(args.project_dir, "packages")
    
        # After that check, go ahead and just check directories
        for dir in os.listdir(packages_dir):
            if os.path.isdir(os.path.join(packages_dir, dir, ".git")):
                print("Package: " + dir)
                cwd = os.getcwd()
                os.chdir(packages_dir + "/" + dir)
                try:
                    branch = subprocess.check_output(["git", "branch"])
                except Exception as e:
                    print("Note: Failed to get branch of package \"" + dir + "\"")
                    continue

                branch = branch.strip()
                if len(branch) == 0:
                    raise Exception("Error: branch is empty")

                branch = branch.decode()
                if branch[0] == "*":
                    branch = branch[1:].strip()

                status = os.system("git fetch")
                status = os.system("git merge origin/" + branch)
                os.chdir(cwd)
            elif os.path.isdir(packages_dir + "/" + dir):
                print("Note: skipping non-Git package \"" + dir + "\"")
                sys.stdout.flush()        
        pass
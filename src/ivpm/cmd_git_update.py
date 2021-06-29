'''
Created on Jun 8, 2021

@author: mballance
'''
import os
import subprocess
import sys

from ivpm.arg_utils import ensure_have_project_dir
from ivpm.msg import fatal


class CmdGitUpdate(object):
    
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

                status = subprocess.run(["git", "fetch"])
                if status.returncode != 0:
                    fatal("Failed to run git fetch on package %s" % dir)
                status = subprocess.run(["git", "merge", "origin/" + branch])
                if status.returncode != 0:
                    fatal("Failed to run git merge origin/%s on package %s" % (branch, dir))
                os.chdir(cwd)
            elif os.path.isdir(packages_dir + "/" + dir):
                print("Note: skipping non-Git package \"" + dir + "\"")
                sys.stdout.flush()        
        pass
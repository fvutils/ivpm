'''
Created on Jun 8, 2021

@author: mballance
'''
import os
import subprocess
import sys
import stat

from ivpm.arg_utils import ensure_have_project_dir
from ivpm.msg import fatal, note
from ..project_ops import ProjectOps
from ..proj_info import ProjInfo



class CmdSync(object):
    
    def __init__(self):
        pass
    
    def __call__(self, args):
        if args.project_dir is None:
            args.project_dir = os.getcwd()
    
        proj_info = ProjInfo.mkFromProj(args.project_dir)

        if proj_info is None:
            fatal("Failed to locate IVPM meta-data (eg ivpm.yaml)")
            
        packages_dir = os.path.join(args.project_dir, proj_info.deps_dir)
    
        # After that check, go ahead and just check directories
        for dir in os.listdir(packages_dir):
            pkg_path = os.path.join(packages_dir, dir)
            git_dir = os.path.join(pkg_path, ".git")
            
            if os.path.isdir(git_dir):
                # Check if the package is editable by testing if it's writable
                # Cached (non-editable) packages are made read-only
                try:
                    mode = os.stat(pkg_path).st_mode
                    is_writable = bool(mode & stat.S_IWUSR)
                except Exception:
                    is_writable = False
                
                if not is_writable:
                    print("Note: skipping cached (read-only) package \"%s\"" % dir)
                    continue
                
                print("Package: " + dir)
                cwd = os.getcwd()
                os.chdir(pkg_path)
                try:
                    branch = subprocess.check_output(["git", "branch"])
                except Exception as e:
                    print("Note: Failed to get branch of package \"" + dir + "\"")
                    continue

                branch = branch.strip()
                if len(branch) == 0:
                    raise Exception("Error: branch is empty")

                branch_lines = branch.decode().splitlines()
                branch = None
                for bl in branch_lines:
                    if bl[0] == "*":
                        branch = bl[1:].strip()
                        break
                if branch is None:
                    raise Exception("Failed to identify branch")

                status = subprocess.run(["git", "fetch"])
                if status.returncode != 0:
                    fatal("Failed to run git fetch on package %s" % dir)
                status = subprocess.run(["git", "merge", "origin/" + branch])
                if status.returncode != 0:
                    fatal("Failed to run git merge origin/%s on package %s" % (branch, dir))
                os.chdir(cwd)
            elif os.path.isdir(pkg_path):
                print("Note: skipping non-Git package \"" + dir + "\"")
                sys.stdout.flush()        
        pass

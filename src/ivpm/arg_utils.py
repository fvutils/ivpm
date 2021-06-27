'''
Created on Jun 8, 2021

@author: mballance
'''
import os
import sys


def find_project_dir():
    """Attempt to find the real project directory"""
    cwd = os.getcwd()
        
    if os.path.isdir(os.path.join(cwd, "packages")):
        return cwd
    else:
        # Go up the path
        parent = os.path.dirname(cwd)
        while parent != "" and parent != "/":
            if os.path.isdir(os.path.join(parent, "packages")):
                return parent
            parent = os.path.dirname(parent)
            
    return None

def ensure_have_project_dir(args):
    if args.project_dir is None:
        print("Note: Attempting to discover project_dir")
        sys.stdout.flush()
        args.project_dir = find_project_dir()
        
        if args.project_dir is None:
            raise Exception("Failed to find project_dir ; specify with --project-dir")
        else:
            print("Note: project_dir is " + args.project_dir) 
            sys.stdout.flush()

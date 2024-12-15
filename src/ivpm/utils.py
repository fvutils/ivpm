'''
Created on Jun 22, 2021

@author: mballance
'''
import os
import sys
from subprocess import check_output
from typing import List
from ivpm.msg import note, fatal, warning

def find_project_root(path):
    pt = path
    while pt != "/" and pt != "":
        if os.path.isfile(os.path.join(pt, "ivpm.yaml")) and os.path.isdir(os.path.join(pt, "packages")):
            break
        else:
            pt = os.path.dirname(pt)

    if pt == "/":
        return None
    else:
        return pt

def load_project_package_info(project_dir) -> List['ProjInfo']:
    from .ivpm_yaml_reader import IvpmYamlReader
    ret = []

    if not os.path.isfile(os.path.join(project_dir, "ivpm.yaml")):
        raise Exception("Invalid project format: no ivpm.yaml file in %s" % project_dir)

    with open(os.path.join(project_dir, "ivpm.yaml"), "r") as fp:
        ret.append(IvpmYamlReader().read(fp, os.path.join(project_dir, "ivpm.yaml")))

    if os.path.isdir(os.path.join(project_dir, "packages")):
        pkgs_dir = os.path.join(project_dir, "packages")
        for f in os.listdir(pkgs_dir):
            if f != "." and f != ".." and os.path.isdir(os.path.join(pkgs_dir, f)):
                if os.path.isfile(os.path.join(pkgs_dir, f, "ivpm.yaml")):
                    with open(os.path.join(pkgs_dir, f, "ivpm.yaml"), "r") as fp:
                        ret.append(IvpmYamlReader().read(fp, os.path.join(pkgs_dir, f, "ivpm.yaml")))

    return ret


def get_sys_python():
    # Ensure that we have a python virtual environment setup
    if 'IVPM_PYTHON' in os.environ.keys() and os.environ["IVPM_PYTHON"] != "":
        # Trust what we've been told
        note("Using user-specified Python %s" % os.environ["IVPM_PYTHON"])
        python = os.environ["IVPM_PYTHON"]
    else:
        # Default to the executing python
        python = sys.executable
        out = check_output([python, "--version"])
        out_s = out.decode().split()

        print("Note: using Python version: %s" % out_s)
            
    return python
    
def get_venv_python(python_dir):
    # Windows venv
    if os.path.isdir(os.path.join(python_dir, "Scripts")):
        ivpm_python = os.path.join(python_dir, "Scripts", "python")
    elif os.path.isfile(os.path.join(python_dir, "bin", "python")):
        ivpm_python = os.path.join(python_dir, "bin", "python")
    else:
        fatal("Unknown python virtual-environment structure in python_dir (%s)" % python_dir)
        
    return ivpm_python

def setup_venv(python_dir):
    note("creating Python virtual environment")
    
    python = get_sys_python()
    
    os.system(python + " -m venv --system-site-packages " + python_dir)
    note("upgrading pip")
    
    ivpm_python = get_venv_python(python_dir)
            
    os.system(ivpm_python + " -m pip install --upgrade pip")
    os.system(ivpm_python + " -m pip install --upgrade ivpm setuptools wheel")
    
    return ivpm_python
    
    
    
def which(exe : str):
    for p in os.environ['PATH'].split(os.pathsep):
        exe_file = os.path.join(p, exe)
        exe_file_e = os.path.join(p, exe + ".exe")
        if os.path.isfile(exe_file) and os.access(exe_file, os.X_OK):
            return exe_file
        if os.path.isfile(exe_file_e) and os.access(exe_file_e, os.X_OK):
            return exe_file_e
    return None  
      
def getlocstr(e):
    if hasattr(e, "srcinfo"):
        return "%s:%d:%d" % (
            e.srcinfo.filename, 
            e.srcinfo.lineno,
            e.srcinfo.linepos)
    else:
        return "<no-srcinfo>"
    pass
    

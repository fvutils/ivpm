'''
Created on Jun 22, 2021

@author: mballance
'''
import os
from subprocess import check_output
from ivpm.msg import note, fatal


def get_sys_python():
    # Ensure that we have a python virtual environment setup
    if 'IVPM_PYTHON' in os.environ.keys() and os.environ["IVPM_PYTHON"] != "":
        # Trust what we've been told
        note("Using user-specified Python %s" % os.environ["IVPM_PYTHON"])
        python = os.environ["IVPM_PYTHON"]
    else:
        # First, find a Python to use
        python = None
        for p in ["python", "python3"]:
            if which(p) is None:
                continue
            out = check_output([p, "--version"])

            out_s = out.decode().split()

            if len(out_s) == 2 and out_s[1][0] == "3":
                python = p
                break
            
    if python is None:
        fatal("failed to find Python3")
            
    return python
    
def get_venv_python(python_dir):
    # Windows venv
    if os.path.isdir(os.path.join(python_dir, "Scripts")):
        ivpm_python = os.path.join(python_dir, "Scripts", "python")
    elif os.path.isfile(os.path.join(python_dir, "bin", "python")):
        ivpm_python = os.path.join(python_dir, "bin", "python")
    else:
        fatal("Unknown python virtual-environment structure")
        
    return ivpm_python

def setup_venv(python_dir):
    note("creating Python virtual environment")
    
    python = get_sys_python()
    
    os.system(python + " -m venv " + python_dir)
    note("upgrading pip")
    
    ivpm_python = get_venv_python(python_dir)
            
    os.system(ivpm_python + " -m pip install --upgrade pip")
    os.system(ivpm_python + " -m pip install --upgrade setuptools wheel")
    
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
    
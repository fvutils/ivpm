#****************************************************************************
#* init.py for snapshot
#****************************************************************************
import subprocess
import os
import sys

snapshot_dir = os.path.dirname(os.path.abspath(__file__))

print("Note: python executable %s" % sys.executable)

print("Note: Creating Python virtual environment")
cmd = [sys.executable, '-m', 'venv', os.path.join(snapshot_dir, "python")]
status = subprocess.run(cmd)

if status.returncode != 0:
    print("Python venv-creation command failed: %s" % str(cmd))
    sys.exit(1)
    
if os.path.isfile(os.path.join(snapshot_dir, "python_pkgs.txt")):
    print("Note: installing required Python packages")
    python_dir = os.path.join(snapshot_dir, "python")
    
    # Windows venv
    if os.path.isdir(os.path.join(python_dir, "Scripts")):
        venv_python = os.path.join(python_dir, "Scripts", "python")
    elif os.path.isfile(os.path.join(python_dir, "bin", "python")):
        venv_python = os.path.join(python_dir, "bin", "python")
    else:
        print("Error: unknown python virtual-environment structure")
        sys.exit(1)

    cmd = [venv_python, "-m", "pip", "install", "pip", "setuptools", "--upgrade"]        
    status = subprocess.run(cmd)
    
    if status.returncode != 0:
        print("Error: failed to upgrade pip")
        sys.exit(1)

    cwd = os.getcwd()        
    os.chdir(snapshot_dir)
    cmd = [venv_python, "-m", "pip", "install", "-r", 
           os.path.join(snapshot_dir, "python_pkgs.txt")]
    status = subprocess.run(cmd)
    
    if status.returncode != 0:
        print("Error: failed to install packages from snapshot")
        sys.exit(1)
        
else:
    print("Note: no Python packages to install")
    
print("Done setting up Python virtual environment")

    
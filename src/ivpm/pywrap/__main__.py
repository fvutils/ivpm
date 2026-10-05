import os
import platform
import sys
import subprocess

# IVPM no longer runs installers through this wrapper; it is kept for anything
# outside IVPM that still invokes 'python -m ivpm.pywrap <cmd>'.
#
# The wrapped command runs as the venv's interpreter would on its own. A
# PYTHONPATH inherited from IVPM would make packages outside the venv look
# installed to pip, so it is not passed on.
ps = ";" if platform.system() == "Windows" else ":"
env = os.environ.copy()
env["IVPM_PYTHONPATH"] = ps.join(sys.path)
for var in ("PYTHONPATH", "PYTHONHOME", "__PYVENV_LAUNCHER__"):
    env.pop(var, None)
exec_dir = os.path.dirname(sys.executable)

if platform.system() == "Windows":
    # User scripts are in 'Scripts'
    env["PATH"] = os.path.join(exec_dir, "Scripts") + ps + env["PATH"]
else:
    # User scripts are alongside the executable
    env["PATH"] = exec_dir + ps + env["PATH"]

def main():
    cmd = sys.argv[1:]

    status = subprocess.run(cmd, env=env)

    return status.returncode

if __name__ == "__main__":
    sys.exit(main())

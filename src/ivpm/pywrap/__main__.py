import os
import platform
import sys
import subprocess

ps = ";" if platform.system() == "Windows" else ":"
env = os.environ.copy()
env["IVPM_PYTHONPATH"] = ps.join(sys.path)
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
    main()

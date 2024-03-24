import os
import platform
import sys
import subprocess

ps = ";" if platform.system() == "Windows" else ":"
env = os.environ.copy()
env["IVPM_PYTHONPATH"] = ps.join(sys.path)

def main():
    cmd = sys.argv[1:]

    status = subprocess.run(cmd, env=env)

    return status.returncode

if __name__ == "__main__":
    main()

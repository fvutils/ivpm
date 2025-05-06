import os
import shutil
import subprocess
import sys
from .test_base import TestBase

class TestSetup(TestBase):

    def test_pypkg_build(self):
        shutil.copytree(
            os.path.join(self.data_dir, "setup", "py_project"),
            os.path.join(self.testdir, "py_project"))

        env = os.environ.copy()
        cmd = [
            sys.executable,
            os.path.join(self.testdir, "py_project", "setup.py"),
            "build_ext",
            "--inplace"
        ]

        output = subprocess.check_call(
            cmd,
            cwd=os.path.join(self.testdir, "py_project"),
            env=env)
        
        print("output: %s" % str(output))

    def test_pypkg_bdist_wheel(self):
        shutil.copytree(
            os.path.join(self.data_dir, "setup", "py_project"),
            os.path.join(self.testdir, "py_project"))

        env = os.environ.copy()
        cmd = [
            sys.executable,
            os.path.join(self.testdir, "py_project", "setup.py"),
            "bdist_wheel"
        ]

        output = subprocess.check_call(
            cmd,
            cwd=os.path.join(self.testdir, "py_project"),
            env=env)
        
        print("output: %s" % str(output))
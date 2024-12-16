import os
import shutil
import sys
import subprocess
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "src"));

from ivpm.project_ops import ProjectOps

class TestBase(unittest.TestCase):

    def setUp(self) -> None:
        unit_dir = os.path.dirname(os.path.abspath(__file__))
        self.data_dir = os.path.join(unit_dir, "data")
        os.environ["DATA_DIR"] = self.data_dir
        test_dir = os.path.dirname(unit_dir)

        rundir = os.path.join(test_dir, "rundir")
        self.testdir = os.path.join(rundir, "test")

        if os.path.isdir(self.testdir):
            shutil.rmtree(self.testdir)
        os.makedirs(self.testdir)

        return super().setUp()
    
    def tearDown(self) -> None:
        return super().tearDown()
    
    def mkFile(self, filename, content):
        fullpath = os.path.join(self.testdir, filename)
        parent = os.path.dirname(fullpath)

        if not os.path.isdir(parent):
            os.makedirs(parent)
        
        with open(fullpath, "w") as fp:
            fp.write(content)

    def ivpm_update(self, dep_set="default-dev", anonymous=False, skip_venv=False):
        
        ProjectOps(self.testdir).update(
                dep_set=dep_set, 
                anonymous=anonymous,
                skip_venv=skip_venv
            )
    
    def ivpm_sync(self, dep_set=None):
        ProjectOps(self.testdir).sync(dep_set=dep_set)
        
    def exec(self, cmd, cwd=None):
        return subprocess.check_output(cmd, cwd=cwd).decode("utf-8")



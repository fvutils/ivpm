'''
Created on Jun 8, 2021

@author: mballance
'''
import os
from subprocess import check_output

from ivpm.packages_info import PackagesInfo
from ivpm.project_info_reader import ProjectInfoReader
from ivpm.utils import get_venv_python, setup_venv
from ivpm.msg import note, fatal, warning
from ivpm.sve_filelist_writer import SveFilelistWriter
from ivpm.out_wrapper import OutWrapper
from ivpm.package_updater import PackageUpdater
from ivpm.package import Package, PackageType
import subprocess
from typing import List


class CmdUpdate(object):
    
    def __init__(self):
        pass
    
    def __call__(self, args):
        if args.project_dir is None:
            # If a default is not provided, use the current directory
            print("Note: project_dir not specified ; using working directory")
            args.project_dir = os.getcwd()
            
        proj_info = ProjectInfoReader(args.project_dir).read()
            
        packages_dir = os.path.join(args.project_dir, "packages")
 
        # Ensure that we have a python virtual environment setup
        if not os.path.isdir(os.path.join(packages_dir, "python")):
            ivpm_python = setup_venv(os.path.join(packages_dir, "python"))
        else:
            note("python virtual environment already exists")
            ivpm_python = get_venv_python(os.path.join(packages_dir, "python"))
            
        if not args.rls:
            if proj_info.dev_deps is None:
                fatal("development mode selected, but no dev-deps specified")
        else:
            if proj_info.deps is None:
                fatal("release mode selected, but no deps specified")

        print("********************************************************************")
        print("* Processing root package %s" % proj_info.name)
        print("********************************************************************")
        pkgs_info = PackageUpdater(packages_dir, args.anonymous).update(
            proj_info.dev_deps if not args.rls else proj_info.deps
            )
        
        python_pkgs = []
        for key in pkgs_info.keys():
            pkg : Package = pkgs_info[key]
            
            if pkg.pkg_type == PackageType.Python:
                if pkg.path is None:
                    python_pkgs.append(pkg)
                elif os.path.isfile(os.path.join(pkg.path, "setup.py")):
                    python_pkgs.append(pkg)
                else:
                    warning("Package %s (%s) is marked as Python, but is missing setup.py" % (
                        pkg.name, pkg.path))
        
        if len(python_pkgs):
            note("Installing Python dependencies")
            self._write_requirements_txt(
                packages_dir,
                python_pkgs, 
                os.path.join(packages_dir, "python_pkgs.txt"))
            cwd = os.getcwd()
            os.chdir(os.path.join(packages_dir))
            cmd = [
                get_venv_python(os.path.join(packages_dir, "python")),
                "-m",
                "pip",
                "install",
                "-r",
                "python_pkgs.txt"]
            
            status = subprocess.run(cmd)
            
            if status.returncode != 0:
                fatal("failed to install Python packages")
        
            os.chdir(cwd)        
            
        with open(os.path.join(packages_dir, "sve.F"), "w") as fp:
            SveFilelistWriter(OutWrapper(fp)).write(pkgs_info)


    def _write_requirements_txt(self, 
                                packages_dir,
                                python_pkgs : List[Package],
                                file):
        with open(file, "w") as fp:
            for pkg in python_pkgs:
                
                if pkg.url is not None:
                    # Editable package
                    fp.write("-e file://%s/%s#egg=%s\n" % (
                        packages_dir.replace("\\","/"), 
                        pkg.name, 
                        pkg.name))
                else:
                    # PyPi package
                    fp.write("%s\n" % pkg.name)
   
    

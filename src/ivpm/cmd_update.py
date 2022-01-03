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
from toposort import toposort
from typing import List


class CmdUpdate(object):
    
    def __init__(self):
        self.debug = False
        pass
    
    def __call__(self, args):
        if args.project_dir is None:
            # If a default is not provided, use the current directory
            print("Note: project_dir not specified ; using working directory")
            args.project_dir = os.getcwd()
            
        proj_info = ProjectInfoReader(args.project_dir).read()

        if proj_info is None:
            fatal("Failed to locate IVPM meta-data (eg ivpm.yaml)")
            
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
        updater = PackageUpdater(packages_dir, args.anonymous)
        # Prevent an attempt to load the top-level project as a depedency
        updater.all_pkgs[proj_info.name] = None
        pkgs_info = updater.update(
            proj_info.dev_deps if not args.rls else proj_info.deps
            )

        # Remove the root package before processing what's left
        pkgs_info.pop(proj_info.name)

        # Build up a dependency map for Python package installation        
        python_deps_m = {}
        python_pkgs_s = set()

        # Collect the full set of packages
        for pid in pkgs_info.keys():
            p = pkgs_info[pid]
            if p.pkg_type == PackageType.Python:
                python_pkgs_s.add(pid)

        setup_deps_s = set()
        
        # Collect all packages that have a setup dependency
        for sdp in pkgs_info.setup_deps.keys():
            print("Package %s has a setup dependency" % sdp)
            if sdp not in python_deps_m.keys():
                python_deps_m[sdp] = set()
            for sdp_d in pkgs_info.setup_deps[sdp]:
                python_deps_m[sdp].add(sdp_d)
                setup_deps_s.add(sdp_d)

        if self.debug:
            print("python_deps_m: %s" % str(python_deps_m))
        
        pypkg_order = list(toposort(python_deps_m))
        
        # Remove the original packages that asserted
        # setup dependencies
        for sdp in pkgs_info.setup_deps.keys():
            if sdp not in setup_deps_s:
                for pypkg_s in pypkg_order:
                    pypkg_s.discard(sdp)
        
        if self.debug:            
            print("ordered: %s" % str(pypkg_order))

        python_requirements_paths = []
        
        # Now, build out the set of requirements files
        for pydep_s in pypkg_order:
            python_pkgs = []
            for key in pydep_s:
                
                # A future iteration does not need to install this
                python_pkgs_s.discard(key)
                
                # Note: for completeness, should collect Python 
                # packages known to be required by this pre-dep
                
                pkg : Package = pkgs_info[key]
                
                if pkg.pkg_type == PackageType.Python:
                    python_pkgs.append(pkg)
                elif os.path.isfile(os.path.join(pkg.path, "setup.py")):
                    python_pkgs.append(pkg)
                else:
                    warning("Package %s (%s) is marked as Python, but is missing setup.py" % (
                        pkg.name, pkg.path))

            if len(python_pkgs):
                requirements_path = os.path.join(
                    packages_dir, "python_pkgs_%d.txt" % (len(python_requirements_paths)+1))
                self._write_requirements_txt(
                    packages_dir,
                    python_pkgs, 
                    requirements_path)
                python_requirements_paths.append(requirements_path)
            
        python_pkgs = []
        for key in python_pkgs_s:
            pkg : Package = pkgs_info[key]
                
            if pkg.pkg_type == PackageType.Python:
                python_pkgs.append(pkg)
            elif os.path.isfile(os.path.join(pkg.path, "setup.py")):
                python_pkgs.append(pkg)
            else:
                warning("Package %s (%s) is marked as Python, but is missing setup.py" % (
                    pkg.name, pkg.path))

        if len(python_pkgs):
            requirements_path = os.path.join(
                packages_dir, "python_pkgs_%d.txt" % (len(python_requirements_paths)+1))
            self._write_requirements_txt(
                packages_dir,
                python_pkgs, 
                requirements_path)
                
            python_requirements_paths.append(requirements_path)
                
        if len(python_requirements_paths):
            note("Installing Python dependencies in %d phases" % len(python_requirements_paths))
            for reqfile in python_requirements_paths:
                cwd = os.getcwd()
                os.chdir(os.path.join(packages_dir))
                cmd = [
                    get_venv_python(os.path.join(packages_dir, "python")),
                    "-m",
                    "pip",
                    "install",
                    "-r",
                    reqfile]
            
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
                    if pkg.version is not None:
                        if pkg.version[0] in ['<','>','=']:
                            fp.write("%s%s\n" % (pkg.name, pkg.version))
                        else:
                            fp.write("%s==%s\n" % (pkg.name, pkg.version))
                    else:
                        fp.write("%s\n" % pkg.name)
   
    

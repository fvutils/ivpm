#****************************************************************************
#* package_handler_python.py
#*
#* Copyright 2023 Matthew Ballance and Contributors
#*
#* Licensed under the Apache License, Version 2.0 (the "License"); you may 
#* not use this file except in compliance with the License.  
#* You may obtain a copy of the License at:
#*
#*   http://www.apache.org/licenses/LICENSE-2.0
#*
#* Unless required by applicable law or agreed to in writing, software 
#* distributed under the License is distributed on an "AS IS" BASIS, 
#* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.  
#* See the License for the specific language governing permissions and 
#* limitations under the License.
#*
#* Created on:
#*     Author: 
#*
#****************************************************************************
import dataclasses as dc
import toposort
import os
from typing import List
from .update_info import UpdateInfo
from .utils import note, fatal

from .package import Package
from .package_handler import PackageHandler

@dc.dataclass
class PackageHandlerPython(PackageHandler):
    name="python"
    packages : List[Package] = dc.field(default_factory=list)

    def process_pkg(self, pkg: Package):
        print("process_pkg: %s" % pkg.name)
        if pkg.pkg_type is not None and pkg.pkg_type == PackageHandlerPython.name:
            self.packages.append(pkg)
        elif pkg.pkg_type is None:
            # Check if there are known Python files
            for py in ("setup.py", "setup.cfg", "pyproject.toml"):
                if os.path.isfile(os.path.join(pkg.pkg_path, py)):
                    pkg.pkg_type = PackageHandlerPython.name
                    self.packages.append(pkg)
                    break            
    
    def update(self, update_info : UpdateInfo):
        # TODO:
        pkgs_info = {}

        # Remove the root package before processing what's left
        pkgs_info.pop(proj_info.name)

        # Build up a dependency map for Python package installation        
        python_deps_m = {}
        python_pkgs_s = set()

        # Collect the full set of packages
        for pkg in self.packages:
            python_pkgs_s.add(pkg.name)

        pypi_pkg_s = set() # List of packages on PyPi to install
        # Map between package name and a set of python
        # packages it depends on
        py_pkg_m = {}
        for pyp in python_pkgs_s:
            p = pkgs_info[pyp]
            if p.src_type == SourceType.PyPi:
                pypi_pkg_s.add(p.name)
            else:
                # Read the package-info
                if pyp not in python_deps_m.keys():
                    python_deps_m[pyp] = set()

                proj_info = ProjectInfoReader(p.path).read()

                if proj_info is None:
                    continue

                # TODO: see if the package specifies the package set
                if proj_info.has_dep_set(proj_info.target_dep_set):
                    for dp in proj_info.get_dep_set(proj_info.target_dep_set).keys():
                        if dp in python_pkgs_s:
                            dp_p = pkgs_info[dp]
                            if dp_p.src_type != SourceType.PyPi:
                                python_deps_m[pyp].add(dp)
                else:
                    print("Warning: project %s does not contain its target dependency set (%s)" % (
                        proj_info.name,
                        proj_info.target_dep_set))
                    for d in proj_info.dep_set_m.keys():
                        print("Dep-Set: %s" % d)

        # Order the source packages based on their dependencies 
        pysrc_pkg_order = list(toposort(python_deps_m))
        if self.debug:
            print("python_deps_m: %s" % str(python_deps_m))
            print("pysrc_pkg_order: %s" % str(pysrc_pkg_order))

        setup_deps_s = set()
        python_deps_m = {}
        
#        # Collect all packages that have a setup dependency
#        for sdp in pkgs_info.setup_deps.keys():
#            print("Package %s has a setup dependency" % sdp)
#            if sdp not in python_deps_m.keys():
#                python_deps_m[sdp] = set()
#            for sdp_d in pkgs_info.setup_deps[sdp]:
#                python_deps_m[sdp].add(sdp_d)
#                setup_deps_s.add(sdp_d)
#
#        if self.debug:
#            print("python_deps_m: %s" % str(python_deps_m))
#        
#        pypkg_order = list(toposort(python_deps_m))
#        print("pypkg_order: %s" % str(pypkg_order))
#        
#        # Remove the original packages that asserted
#        # setup dependencies
#        for sdp in pkgs_info.setup_deps.keys():
#            if sdp not in setup_deps_s:
#                for pypkg_s in pypkg_order:
#                    pypkg_s.discard(sdp)
#        
#        if self.debug:           
#            print("ordered: %s" % str(pypkg_order))
#
        python_requirements_paths = []

        # First, collect the setup-deps
        setup_deps_s = set()
        for proj,deps in pkgs_info.setup_deps.items():
            for dep in deps:
                if dep not in setup_deps_s:
                    setup_deps_s.add(dep)
                    if dep in pypi_pkg_s:
                        pypi_pkg_s.remove(dep)
        print("setup_deps_s: %s" % str(setup_deps_s))

        if len(setup_deps_s) > 0:
            setup_deps_pkgs = []
            for dep in setup_deps_s:
                setup_deps_pkgs.append(pkgs_info[dep])

            requirements_path = os.path.join(
                update_info.deps_dir, "python_pkgs_%d.txt" % (
                len(python_requirements_paths)+1))
            self._write_requirements_txt(
                update_info.deps_dir,
                setup_deps_pkgs, 
                requirements_path)
            python_requirements_paths.append(requirements_path)

        # Next, create a requirements file for all
        # non-setup-dep PyPi packages
        python_pkgs = []
        print("pypi_pkg_s: %s" % str(pypi_pkg_s))
        for pypi_p in pypi_pkg_s:
            python_pkgs.append(pkgs_info[pypi_p])

        if len(python_pkgs) > 0:
            requirements_path = os.path.join(
                update_info.deps_dir, "python_pkgs_%d.txt" % (
                len(python_requirements_paths)+1))

            self._write_requirements_txt(
                update_info.deps_dir,
                python_pkgs, 
                requirements_path)
            python_requirements_paths.append(requirements_path)

        # Now, add requirement files for any source packages
        for pydep_s in pysrc_pkg_order:
            python_pkgs = []
            for key in pydep_s:
                
                # A future iteration does not need to install this
                python_pkgs_s.discard(key)
                
                # Note: for completeness, should collect Python 
                # packages known to be required by this pre-dep
                
                if key not in pkgs_info.keys():
                    raise Exception("Package %s not found in packages-info %s" % (key, pkgs_info.name))
                
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
                    deps_dir, "python_pkgs_%d.txt" % (len(python_requirements_paths)+1))
                self._write_requirements_txt(
                    deps_dir,
                    python_pkgs, 
                    requirements_path)
                python_requirements_paths.append(requirements_path)
            
        if len(python_requirements_paths):
            import sys
            import platform
            ps = ";" if platform.system() == "Windows" else ":"
            env = os.environ.copy()
            env["PYTHONPATH"] = ps.join(sys.path)

            note("Installing Python dependencies in %d phases" % len(python_requirements_paths))
            for reqfile in python_requirements_paths:
                cwd = os.getcwd()
                os.chdir(os.path.join(deps_dir))
                cmd = [
                    get_venv_python(os.path.join(deps_dir, "python")),
                    "-m",
                    "ivpm.pywrap",
                    get_venv_python(os.path.join(deps_dir, "python")),
                    "-m",
                    "pip",
                    "install",
                    "-r",
                    reqfile]
            
                status = subprocess.run(cmd, env=env)
            
                if status.returncode != 0:
                    fatal("failed to install Python packages")
                os.chdir(cwd)        
        return super().update()



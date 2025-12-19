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
import logging
import subprocess
import toposort
import os
import shutil
import sys
from typing import Dict, List, Set
from ..project_ops_info import ProjectUpdateInfo, ProjectBuildInfo
from ..utils import note, fatal, get_venv_python

from ..package import Package
from .package_handler import PackageHandler

_logger = logging.getLogger("ivpm.handlers.package_handler_python")

@dc.dataclass
class PackageHandlerPython(PackageHandler):
    name="python"
    pkgs_info  : Dict[str,Package] = dc.field(default_factory=dict)
    src_pkg_s  : Set[str] = dc.field(default_factory=set)
    pypi_pkg_s : Set[str] = dc.field(default_factory=set)
    use_uv : bool = False
    debug : bool = True

    def process_pkg(self, pkg: Package):
        add = False
        if pkg.src_type == "pypi":
            self.pypi_pkg_s.add(pkg.name)
            add = True
        elif pkg.pkg_type is not None and pkg.pkg_type == PackageHandlerPython.name:
            self.src_pkg_s.add(pkg.name)
            add = True
        elif pkg.pkg_type is None and hasattr(pkg, "path"):
            # Check if there are known Python files
            for py in ("setup.py", "setup.cfg", "pyproject.toml"):
                if os.path.isfile(os.path.join(pkg.path, py)):
                    add = True
                    self.src_pkg_s.add(pkg.name)
                    break            
        if add:
            pkg.pkg_type = PackageHandlerPython.name
            self.pkgs_info[pkg.name] = pkg
    
    def update(self, update_info : ProjectUpdateInfo):

        if getattr(update_info.args, "py_uv", False):
            self.use_uv = True
        elif getattr(update_info.args, "py_pip", False):
            self.use_uv = False
        else:
            # Auto-probe
            if shutil.which("uv") is not None:
                self.use_uv = True

        # First, check to see if we've already installed 
        # Python packages, and whether we should repeat
        if getattr(update_info.args, "py_skip_install", False):
            note("Skipping Python package installation")
            return
        elif update_info.skip_venv:
            note("Skipping Python package installation (no venv)")
            return
        elif os.path.isfile(os.path.join(update_info.deps_dir, "python_pkgs_1.txt")):
            if update_info.force_py_install:
                note("Forcing re-install of Python packages")
            else:
                note("Python packages already installed. Use --force-py-install to force re-install")
                return
        else:
            note("Installing Python packages")


        # Build up a dependency map for Python package installation        
        python_deps_m = {}
#        python_pkgs_s = set()

        # Collect the full set of packages
#        for pkg in self.packages:
#            python_pkgs_s.add(pkg.name)

        # Map between package name and a set of python
        # packages it depends on
        py_pkg_m = {}
        _logger.debug("src_pkg_s: %s", str(self.src_pkg_s))
        for pyp in self.src_pkg_s:
            _logger.debug("pyp: %s", pyp) 
            p = self.pkgs_info[pyp]
            if pyp not in python_deps_m.keys():
                python_deps_m[pyp] = set()

            if p.proj_info is not None:
                _logger.debug("non-none proj_info")
                # TODO: see if the package specifies the package set
                if p.proj_info.has_dep_set(p.proj_info.target_dep_set):
                    for dp in p.proj_info.get_dep_set(p.proj_info.target_dep_set).keys():
                        if dp in self.pkgs_info.keys():
                            dp_p = self.pkgs_info[dp]
                            if dp_p.src_type != "pypi":
                                python_deps_m[pyp].add(dp)
                        # if dp in python_pkgs_s:
                        #     dp_p = pkgs_info[dp]
                        #     if dp_p.src_type != SourceType.PyPi:
                        #         python_deps_m[pyp].add(dp)
                else:
                    _logger.warning("Project %s does not contain its target dependency set (%s)",
                        p.proj_info.name,
                        p.proj_info.target_dep_set)
                    for d in p.proj_info.dep_set_m.keys():
                        _logger.debug("Dep-Set: %s", d)

        # Order the source packages based on their dependencies 
        it = toposort.toposort(python_deps_m)
        pysrc_pkg_order = list(it)
        if self.debug:
            _logger.debug("python_deps_m: %s", str(python_deps_m))
            _logger.debug("pysrc_pkg_order: %s", str(pysrc_pkg_order))

        python_deps_m = {}
        
        python_requirements_paths = []

        # Setup deps are a special category. We need to 
        # install them first -- possibly even before
        # installing other pypi packages
        setup_deps_s = set()
        # for pkg,deps in update_info.setup_deps.items():
        #     for dep in deps:
        #         if dep not in setup_deps_s:
        #             setup_deps_s.add(dep)
        #             if dep in self.pypi_pkg_s:
        #                 self.pypi_pkg_s.remove(dep)

        # for proj,deps in self.pkgs_info.setup_deps.items():
        #     for dep in deps:
        #         if dep not in setup_deps_s:
        #             setup_deps_s.add(dep)
        #             if dep in self.pypi_pkg_s:
        #                 self.pypi_pkg_s.remove(dep)
        _logger.debug("setup_deps_s: %s", str(setup_deps_s))

        if len(setup_deps_s) > 0:
            setup_deps_pkgs = []
            for dep in setup_deps_s:
                setup_deps_pkgs.append(self.pkgs_info[dep])

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
        _logger.debug("pypi_pkg_s: %s", str(self.pypi_pkg_s))
        for pypi_p in self.pypi_pkg_s:
            python_pkgs.append(self.pkgs_info[pypi_p])

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
                self.pypi_pkg_s.discard(key)
                self.src_pkg_s.discard(key)
                
                # Note: for completeness, should collect Python 
                # packages known to be required by this pre-dep
                
                if key not in self.pkgs_info.keys():
                    raise Exception("Package %s not found in packages-info" % key)
                
                pkg : Package = self.pkgs_info[key]
                python_pkgs.append(pkg)
                
            if len(python_pkgs):
                requirements_path = os.path.join(
                    update_info.deps_dir, "python_pkgs_%d.txt" % (len(python_requirements_paths)+1))
                self._write_requirements_txt(
                    update_info.deps_dir,
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
            suppress_output = getattr(update_info, 'suppress_output', False)
            for reqfile in python_requirements_paths:
                self._install_requirements(
                    os.path.join(update_info.deps_dir, "python"),
                    reqfile,
                    getattr(update_info.args, "py_prerls_packages", False),
                    self.use_uv,
                    suppress_output=suppress_output)

    def build(self, build_info : ProjectBuildInfo):
        python_deps_m = {}
        py_pkg_m = {}
        _logger.debug("src_pkg_s: %s", str(self.src_pkg_s))
        for pyp in self.src_pkg_s:
            _logger.debug("pyp: %s", pyp) 
            p = self.pkgs_info[pyp]
            if pyp not in python_deps_m.keys():
                python_deps_m[pyp] = set()

            if p.proj_info is not None:
                _logger.debug("non-none proj_info")
                # TODO: see if the package specifies the package set
                if p.proj_info.has_dep_set(p.proj_info.target_dep_set):
                    for dp in p.proj_info.get_dep_set(p.proj_info.target_dep_set).keys():
                        if dp in self.pkgs_info.keys():
                            dp_p = self.pkgs_info[dp]
                            if dp_p.src_type != "pypi":
                                python_deps_m[pyp].add(dp)
                        # if dp in python_pkgs_s:
                        #     dp_p = pkgs_info[dp]
                        #     if dp_p.src_type != SourceType.PyPi:
                        #         python_deps_m[pyp].add(dp)
                else:
                    _logger.warning("Project %s does not contain its target dependency set (%s)",
                        p.proj_info.name,
                        p.proj_info.target_dep_set)
                    for d in p.proj_info.dep_set_m.keys():
                        _logger.debug("Dep-Set: %s", d)

        # Order the source packages based on their dependencies 
        it = toposort.toposort(python_deps_m)
        pysrc_pkg_order = list(it)
        if self.debug:
            _logger.debug("python_deps_m: %s", str(python_deps_m))
            _logger.debug("pysrc_pkg_order: %s", str(pysrc_pkg_order))

        env = os.environ.copy()
        env["DEBUG"] = "1" if build_info.debug else "0"
        for pkg_s in pysrc_pkg_order:
            for pkg in pkg_s:
                p = self.pkgs_info[pkg]
                if p.pkg_type == PackageHandlerPython.name:
                    if os.path.isfile(os.path.join(build_info.deps_dir, pkg, "setup.py")):
                        cmd = [
                            sys.executable,
                            'setup.py',
                            'build_ext',
                            '--inplace'
                        ]
                        result = subprocess.run(
                            cmd,
                            env=env,
                            cwd=os.path.join(build_info.deps_dir, pkg))

                        if result.returncode != 0:
                            raise Exception("Failed to build package %s" % pkg)
                        
    def _install_requirements(self,
                              python_dir,
                              requirements_file,
                              use_pre,
                              use_uv,
                              suppress_output=False):
        """Installs the requirements specified in a file"""

        # Setup output redirection
        if suppress_output:
            stdout_arg = subprocess.DEVNULL
            stderr_arg = subprocess.DEVNULL
        else:
            stdout_arg = None
            stderr_arg = None

        if use_uv:
            env = os.environ.copy()
            env["VIRTUAL_ENV"] = python_dir

            cmd = [
                shutil.which("uv"),
                "pip",
                "install",
                # Ensure user-specified packages are used during build
                # An isolated build installs its own release packages
                "--no-build-isolation", 
                "-r",
                requirements_file
            ]

            if use_pre:
                cmd.append("--pre")

            result = subprocess.run(cmd, env=env, stdout=stdout_arg, stderr=stderr_arg)

            if result.returncode != 0:
                raise Exception("Failed to install Python packages")
        else: # Use pip
            import sys
            import platform

            ps = ";" if platform.system() == "Windows" else ":"
            env = os.environ.copy()
            env["PYTHONPATH"] = ps.join(sys.path)

            cwd = os.getcwd()
            os.chdir(os.path.join(python_dir))
            cmd = [
                get_venv_python(python_dir),
                "-m",
                "ivpm.pywrap",
                get_venv_python(python_dir),
                "-m",
                "pip",
                "install",
                "-r",
                requirements_file]

            if use_pre:
                cmd.append("--pre")

            status = subprocess.run(cmd, env=env, stdout=stdout_arg, stderr=stderr_arg)
    
            if status.returncode != 0:
                fatal("failed to install Python packages")
            os.chdir(cwd)


    def _write_requirements_txt(self, 
                                packages_dir,
                                python_pkgs : List[Package],
                                file):
        """Writes a requirements file for pip to use in installing packages"""
        with open(file, "w") as fp:
            for pkg in python_pkgs:
                
                if hasattr(pkg, "url"):
                    # Editable package
                    # fp.write("-e file://%s/%s#egg=%s\n" % (
                    #     packages_dir.replace("\\","/"), 
                    #     pkg.name, 
                    #     pkg.name))
                    fp.write("-e %s/%s\n" % (
                        packages_dir.replace("\\","/"), 
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



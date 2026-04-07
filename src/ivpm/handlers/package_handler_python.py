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
from typing import ClassVar, Dict, List, Optional, Set
from ..project_ops_info import ProjectUpdateInfo, ProjectBuildInfo
from ..utils import note, fatal, get_venv_python, setup_venv
from ..pkg_content_type import PythonTypeData
from ..package import get_type_data

from ..package import Package, SourceType
from .package_handler import PackageHandler
from .handler_conditions import HasType

_logger = logging.getLogger("ivpm.handlers.package_handler_python")

@dc.dataclass
class PackageHandlerPython(PackageHandler):
    name:               ClassVar[str]            = "python"
    description:        ClassVar[str]            = "Installs Python packages into the managed virtual environment"
    leaf_when:          ClassVar[Optional[List]] = None               # always inspect every package
    root_when:          ClassVar[Optional[List]] = [HasType("python")] # only run root when Python pkgs present
    phase:              ClassVar[int]            = 0
    conditions_summary: ClassVar[str]            = "leaf: all packages; root: only when at least one Python package is present"

    pkgs_info  : Dict[str,Package] = dc.field(default_factory=dict)
    src_pkg_s  : Set[str] = dc.field(default_factory=set)
    pypi_pkg_s : Set[str] = dc.field(default_factory=set)
    use_uv : bool = False
    debug : bool = True

    def reset(self):
        self.pkgs_info  = {}
        self.src_pkg_s  = set()
        self.pypi_pkg_s = set()

    @classmethod
    def handler_info(cls):
        from ..show.info_types import HandlerInfo, ParamInfo
        return HandlerInfo(
            name=cls.name,
            description=cls.description,
            phase=cls.phase,
            conditions=cls.conditions_summary,
            params=[
                ParamInfo("type: python", "Mark a package for Python installation (used in the 'type:' field of an ivpm.yaml dep entry)"),
            ],
            cli_options=[
                "update: --py-uv         Use 'uv' instead of pip to manage the virtual environment",
                "update: --py-pip         Force use of pip (overrides uv detection)",
                "update: --skip-py-install  Skip Python package installation",
                "update: --force-py-install  Force re-install of all Python packages",
                "update: --py-prerls-packages  Allow pre-release packages",
                "update: --py-system-site-packages  Inherit system site-packages in the venv",
                "clone:  --py-uv / --py-pip / --py-system-site-packages  (same as update)",
            ],
        )

    def on_leaf_post_load(self, pkg: Package, update_info):
        add = False
        if pkg.src_type == "pypi":
            with self._lock:
                self.pypi_pkg_s.add(pkg.name)
            add = True
        elif get_type_data(pkg, PythonTypeData) is not None:
            # Explicit type: python
            with self._lock:
                self.src_pkg_s.add(pkg.name)
            add = True
        elif pkg.pkg_type is not None and pkg.pkg_type == PackageHandlerPython.name:
            with self._lock:
                self.src_pkg_s.add(pkg.name)
            add = True
        elif pkg.pkg_type is None and hasattr(pkg, "path"):
            # Check if there are known Python files
            for py in ("setup.py", "setup.cfg", "pyproject.toml"):
                if os.path.isfile(os.path.join(pkg.path, py)):
                    add = True
                    with self._lock:
                        self.src_pkg_s.add(pkg.name)
                    break
        if add:
            pkg.pkg_type = PackageHandlerPython.name
            with self._lock:
                self.pkgs_info[pkg.name] = pkg

    def _resolve_venv_mode(self, update_info: ProjectUpdateInfo):
        """Determine effective VenvMode from CLI flags, skip_venv, and yaml config.

        Priority (highest first):
          1. CLI --skip-py-install (py_skip_install)     → SKIP
          2. update_info.skip_venv                       → SKIP
          3. yaml with.python.venv == false              → SKIP (cannot be overridden by tool flags)
          4. CLI --py-uv                                 → UV
          5. CLI --py-pip                                → PIP
          6. yaml with.python.venv (uv / pip / true)    → as specified
          7. Default                                     → AUTO
        """
        from ..proj_info import VenvMode

        if getattr(update_info.args, "py_skip_install", False):
            return VenvMode.SKIP
        if update_info.skip_venv:
            return VenvMode.SKIP
        # YAML SKIP wins over tool-selection CLI flags
        if (update_info.python_config is not None and
                update_info.python_config.venv == VenvMode.SKIP):
            return VenvMode.SKIP
        # Tool-selection CLI flags override yaml tool preference
        if getattr(update_info.args, "py_uv", False):
            return VenvMode.UV
        if getattr(update_info.args, "py_pip", False):
            return VenvMode.PIP
        if update_info.python_config is not None:
            return update_info.python_config.venv
        return VenvMode.AUTO

    def on_root_post_load(self, update_info: ProjectUpdateInfo):
        from ..proj_info import VenvMode

        # --- Inject ivpm unconditionally (unless explicitly specified) ---
        if "ivpm" not in self.pypi_pkg_s:
            _logger.info("Will install IVPM from PyPi")
            from ..pkg_types.package_pypi import PackagePyPi
            ivpm_pkg = PackagePyPi("ivpm")
            ivpm_pkg.src_type = SourceType.PyPi
            self.pypi_pkg_s.add("ivpm")
            self.pkgs_info["ivpm"] = ivpm_pkg

        venv_mode = self._resolve_venv_mode(update_info)

        if venv_mode == VenvMode.SKIP:
            note("Skipping Python package installation")
            return

        python_dir = os.path.join(update_info.deps_dir, "python")

        # --- Create venv if it doesn't exist yet ---
        if not os.path.isdir(python_dir):
            system_site_packages = False
            if update_info.python_config is not None:
                system_site_packages = update_info.python_config.system_site_packages
            # CLI flag overrides yaml
            if getattr(update_info.args, "py_system_site_packages", False):
                system_site_packages = True

            # Map VenvMode → uv_pip argument for setup_venv
            if venv_mode == VenvMode.UV:
                uv_pip = "uv"
            elif venv_mode == VenvMode.PIP:
                uv_pip = "pip"
            else:
                uv_pip = "auto"

            suppress_output = getattr(update_info, 'suppress_output', False)
            with self.task_context(update_info, "venv-create", "Creating Python virtual environment") as task:
                try:
                    setup_venv(
                        python_dir,
                        uv_pip=uv_pip,
                        suppress_output=suppress_output,
                        system_site_packages=system_site_packages,
                    )
                except Exception as e:
                    raise
        else:
            note("python virtual environment already exists")

        if getattr(update_info.args, "py_uv", False):
            self.use_uv = True
        elif getattr(update_info.args, "py_pip", False):
            self.use_uv = False
        else:
            if shutil.which("uv") is not None:
                self.use_uv = True

        # Check whether packages were already installed
        if os.path.isfile(os.path.join(update_info.deps_dir, "python_pkgs_1.txt")):
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
                                # Only add a dependency edge if dp was resolved
                                # by this package (pyp). If dp was resolved at a
                                # higher level (e.g., root or another package),
                                # there's no dependency edge from pyp to dp.
                                # This prevents circular dependencies when upper-level
                                # imports override lower-level imports.
                                if dp_p.resolved_by == pyp:
                                    python_deps_m[pyp].add(dp)
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

            n = len(python_requirements_paths)
            note("Installing Python dependencies in %d phases" % n)
            suppress_output = getattr(update_info, 'suppress_output', False)
            with self.task_context(update_info, "python-install", "Installing Python packages") as task:
                for i, reqfile in enumerate(python_requirements_paths, 1):
                    task.progress(
                        f"Installing package set {i}/{n}",
                        step=i, total=n,
                    )
                    self._install_requirements(
                        os.path.join(update_info.deps_dir, "python"),
                        reqfile,
                        getattr(update_info.args, "py_prerls_packages", False),
                        self.use_uv,
                        suppress_output=suppress_output,
                        task=task)

    def get_lock_entries(self, deps_dir: str) -> dict:
        """Return pip-resolved package versions from the managed venv.

        Queries the venv's Python interpreter with ``pip list --format=json``.
        This works regardless of whether ``pip`` or ``uv`` was used to install
        packages — both write to the same venv site-packages directory.
        """
        python_dir = os.path.join(deps_dir, "python")
        if not os.path.isdir(python_dir):
            return {}

        venv_python = get_venv_python(python_dir)
        if not os.path.isfile(venv_python):
            return {}

        try:
            result = subprocess.run(
                [venv_python, "-m", "pip", "list", "--format=json"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                _logger.warning("pip list failed: %s", result.stderr.strip())
                return {}

            import json
            raw = json.loads(result.stdout)
            versions = {item["name"]: item["version"] for item in raw}
            return {"python_packages": versions}
        except Exception as e:
            _logger.warning("Failed to query pip versions for lock file: %s", e)
            return {}

    def build(self, build_info : ProjectBuildInfo):
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
                                # Only add a dependency edge if dp was resolved
                                # by this package (pyp). If dp was resolved at a
                                # higher level, there's no dependency edge.
                                if dp_p.resolved_by == pyp:
                                    python_deps_m[pyp].add(dp)
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
                              suppress_output=False,
                              task=None):
        """Installs the requirements specified in a file.

        If *task* is provided, stdout/stderr are captured and parsed for
        progress messages that are emitted via task.progress().
        """

        # When we have a task handle, always capture output for progress parsing.
        # Otherwise fall back to suppress or inherit.
        if task is not None:
            stdout_arg = subprocess.PIPE
            stderr_arg = subprocess.STDOUT
        elif suppress_output:
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
                "--verbose",
                # Ensure user-specified packages are used during build
                # An isolated build installs its own release packages
                "--no-build-isolation", 
                "-r",
                requirements_file
            ]

            if use_pre:
                cmd.append("--pre")

            returncode, captured_lines = self._run_with_progress(cmd, env=env,
                                                  stdout_arg=stdout_arg,
                                                  stderr_arg=stderr_arg,
                                                  use_uv=True, task=task)
            if returncode != 0:
                detail = _format_installer_error(captured_lines)
                raise Exception("Failed to install Python packages" + detail)
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

            returncode, captured_lines = self._run_with_progress(cmd, env=env,
                                                  stdout_arg=stdout_arg,
                                                  stderr_arg=stderr_arg,
                                                  use_uv=False, task=task)

            if returncode != 0:
                detail = _format_installer_error(captured_lines)
                fatal("failed to install Python packages" + detail)
            os.chdir(cwd)

    def _run_with_progress(self, cmd, env, stdout_arg, stderr_arg, use_uv, task):
        """Run cmd and return (exit_code, captured_lines).

        When stdout_arg is PIPE (i.e. task is not None), stream output
        line by line and emit progress events via task.progress().
        All captured lines are returned so the caller can surface them on error.
        Otherwise call subprocess.run() directly and return an empty line list.
        """
        if stdout_arg != subprocess.PIPE:
            result = subprocess.run(cmd, env=env, stdout=stdout_arg, stderr=stderr_arg)
            return result.returncode, []

        captured_lines = []
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                errors="replace")
        for raw_line in proc.stdout:
            line = raw_line.rstrip()
            captured_lines.append(line)
            msg = self._parse_installer_line(line, use_uv)
            if msg and task is not None:
                task.progress(msg)
        proc.wait()
        return proc.returncode, captured_lines

    @staticmethod
    def _parse_installer_line(line: str, use_uv: bool):
        """Return a short human-readable status string from one line of uv/pip output, or None."""
        stripped = line.strip()
        if not stripped:
            return None

        if use_uv:
            # "   Building pkgname @ file://..."  or  "      Built pkgname @ file://..."
            for prefix in ("Building ", "Built "):
                if stripped.startswith(prefix):
                    rest = stripped[len(prefix):]
                    name = rest.split(" @ ")[0].strip()
                    return f"{prefix.strip()} {name}"
            # "Resolved N packages in Xms", "Installed N packages in Xms",
            # "Prepared N packages in Xs", "Uninstalled N package..."
            for prefix in ("Resolved ", "Installed ", "Prepared ", "Uninstalled "):
                if stripped.startswith(prefix):
                    return stripped
            # "Using Python X environment at: ..."
            if stripped.startswith("Using Python"):
                return stripped
            # "DEBUG Selecting: pkg==version [compatible] (wheel.whl)"
            # → "Selecting pkg==version"
            if stripped.startswith("DEBUG Selecting: "):
                rest = stripped[len("DEBUG Selecting: "):]
                # "pkg==version [compatible] (...)" → "pkg==version"
                pkg_ver = rest.split(" [")[0].split(" (")[0].strip()
                return f"Selecting {pkg_ver}"
        else:
            # pip output
            if stripped.startswith("Collecting "):
                # "Collecting requests>=2.0" → "Collecting requests"
                pkg = stripped[len("Collecting "):].split(" ")[0]
                return f"Collecting {pkg}"
            if stripped.startswith("Downloading "):
                pkg = stripped[len("Downloading "):].split(" ")[0]
                return f"Downloading {pkg}"
            if stripped.startswith("Building wheel for "):
                pkg = stripped[len("Building wheel for "):].split(" ")[0]
                return f"Building {pkg}"
            if stripped.startswith("Successfully installed "):
                return stripped[:80]  # may be long; cap it

        return None


    def _write_requirements_txt(self, 
                                packages_dir,
                                python_pkgs : List[Package],
                                file):
        """Writes a requirements file for pip to use in installing packages"""
        with open(file, "w") as fp:
            for pkg in python_pkgs:
                
                if hasattr(pkg, "url"):
                    # Source package (git, dir, http, etc.)
                    # Determine editability: type_data takes priority, then default True
                    editable = True
                    td = get_type_data(pkg, PythonTypeData)
                    if td is not None and td.editable is not None:
                        editable = td.editable

                    # Extras from type_data
                    extras = None
                    if td is not None:
                        extras = td.extras
                    extras_str = "[%s]" % ",".join(extras) if extras else ""

                    pkg_path = "%s/%s" % (packages_dir.replace("\\","/"), pkg.name)
                    if editable:
                        fp.write("-e %s%s\n" % (pkg_path, extras_str))
                    else:
                        fp.write("%s%s\n" % (pkg_path, extras_str))
                else:
                    # PyPi package — build PEP 508 specifier: name[extras]version
                    # Extras: prefer type_data if present, fall back to pkg.extras (PackagePyPi)
                    td = get_type_data(pkg, PythonTypeData)
                    if td is not None and td.extras is not None:
                        extras = td.extras
                    else:
                        extras = getattr(pkg, "extras", None)
                    extras_str = "[%s]" % ",".join(extras) if extras else ""
                    if pkg.version is not None:
                        if pkg.version[0] in ['<','>','=']:
                            fp.write("%s%s%s\n" % (pkg.name, extras_str, pkg.version))
                        else:
                            fp.write("%s%s==%s\n" % (pkg.name, extras_str, pkg.version))
                    else:
                        fp.write("%s%s\n" % (pkg.name, extras_str))


def _format_installer_error(captured_lines: list, tail: int = 20) -> str:
    """Return a newline-prefixed string with the last *tail* lines of installer
    output, or an empty string when nothing was captured (non-PIPE mode)."""
    if not captured_lines:
        return ""
    relevant = [l for l in captured_lines if l.strip()]
    snippet = relevant[-tail:] if len(relevant) > tail else relevant
    return "\n" + "\n".join(snippet)



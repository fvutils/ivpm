#****************************************************************************
#* setup.py
#*
#* Copyright 2022 Matthew Ballance and Contributors
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
import importlib
import os
import sys
from enum import Enum, auto
from setuptools import setup as _setup
from .build_ext import BuildExt
from .install_lib import InstallLib
import inspect
import platform
from ivpm.pkg_info.pkg_info_rgy import PkgInfoRgy
import ivpm.setup.ivpm_data as idata


def setup(*args, **kwargs):
    global _ivpm_extra_data, _ivpm_extdep_data, _ivpm_hooks, _ivpm_ext_name_m

    print("IVPM setup: %s" % kwargs["name"])

    stack = inspect.stack()
    caller = stack[1][0]

    if "-DDEBUG" in sys.argv:
        debug = True
        sys.argv.remove("-DDEBUG")
    else:
        debug = False

    if "ivpm_extra_data" in kwargs.keys():
        idata._ivpm_extra_data = kwargs["ivpm_extra_data"]
        kwargs.pop("ivpm_extra_data")

    if "ivpm_extdep_data" in kwargs.keys():
        idata._ivpm_extdep_data = kwargs["ivpm_extdep_data"]
        kwargs.pop("ivpm_extdep_data")

    if "ivpm_hooks" in kwargs.keys():
        idata._ivpm_hooks = kwargs["ivpm_hooks"]
        kwargs.pop("ivpm_hooks")

    if "ivpm_ext_name_m" in kwargs.keys():
        idata._ivpm_ext_name_m = kwargs["ivpm_ext_name_m"]
        kwargs.pop("ivpm_ext_name_m") 

    # project_dir = os.path.dirname(os.path.abspath(
    #     inspect.getmodule(caller).__file__))

    # if os.path.isdir(os.path.join(project_dir, 'packages')):
    #     packages_dir = os.path.join(project_dir, 'packages')
    # else:
    #     packages_dir = os.path.dirname(os.path.join(project_dir))

    #     if not os.path.isdir(packages_dir):
    #         raise Exception("Failed to locate packages directory: project_dir=%s ; packages_dir=%s" % (
    #             project_dir, packages_dir
    #         ))

    # Update extension flags based on common requirements
    if "ext_modules" in kwargs.keys():
        for m in kwargs["ext_modules"]:
            if hasattr(m, "language"):
                if m.language == "c++":
                    print("C++ extension")
                    if platform.system() == "Darwin":
                        if not hasattr(m, "extra_compile_args"):
                            setattr(m, "extra_compile_args", [])
                        m.extra_compile_args.append("-std=c++17")
                elif m.language == "c":
                    pass
                else:
                    print("No 'language': %s" % getattr(m, "language", "<notpresent>"))

    if "ivpm_extdep_pkgs" in kwargs.keys():
        include_dirs = []
        library_dirs = []
        libraries = []
        paths = []
        for dep in kwargs["ivpm_extdep_pkgs"]:
            processed = set()
            _collect_extdeps(
                dep,
                processed,
                include_dirs,
                library_dirs,
                libraries,
                paths)
        kwargs.pop("ivpm_extdep_pkgs")

        print("paths: %s" % str(paths), flush=True)
        print("include_dirs: %s" % str(include_dirs), flush=True)
#        sys.path.extend(paths)

        if "ext_modules" in kwargs.keys():
            for m in kwargs["ext_modules"]:
                # Configure include paths, libraries, etc
                print("Applying extension updates to: %s" % m.name)
                _apply_extdeps(
                    m,
                    include_dirs,
                    library_dirs,
                    libraries)
                print("Final settings for %s:" % m.name)
                print("   incdirs: %s" % str(m.include_dirs))
        else:
            print("Note: no extension libraries")

#    if "BUILD_NUM" in os.environ.keys() and "version" in kwargs:
#        kwargs["version"] += ".%s" % os.environ["BUILD_NUM"]
    
    if "cmdclass" in kwargs:
        cmdclass = kwargs["cmdclass"]
    else:
        cmdclass = {}
        kwargs["cmdclass"] = cmdclass
    
    if "build_ext" in cmdclass.keys():
        print("Warning: build_ext is overridden")
    else:
        cmdclass["build_ext"] = BuildExt
    
    if "install_lib" in cmdclass.keys():
        print("Warning: install_lib is overridden")
    else:
        cmdclass["install_lib"] = InstallLib
    
    print("ivpm.build.setup")
    if "ext_modules" in kwargs:
        print("ext_modules")
        for ext in kwargs["ext_modules"]:
            if hasattr(ext, "package_deps"):
                print("package_deps")
   
    for hook in idata.get_hooks(idata.Phase_SetupPre):
        hook(None)

    _setup(*args, **kwargs)

    for hook in idata.get_hooks(idata.Phase_SetupPost):
        hook(None)

def _collect_extdeps(
    dep,
    processed,
    include_dirs,
    library_dirs,
    libraries,
    paths):
    from ivpm.pkg_info.pkg_info_rgy import PkgInfoRgy
    if dep in processed:
        return
    else:
        processed.add(dep)
    
    rgy = PkgInfoRgy.inst()

    for pkg in rgy.getPkgs():
        print("Package: %s" % pkg, flush=True)

    if rgy.hasPkg(dep):
        print("Package %s is an IVPM package" % dep)
        pkg = rgy.getPkg(dep)

        path = pkg.getPath()
        if path is not None and path not in paths:
            paths.append(path)

        for incdir in pkg.getIncDirs():
            if incdir not in include_dirs:
                include_dirs.append(incdir)

        for libdir in pkg.getLibDirs():
            if libdir not in library_dirs:
                library_dirs.append(libdir)

        for lib in pkg.getLibs():
            if lib not in libraries:
                libraries.append(lib)

        for sub_dep in pkg.getDeps():
            _collect_extdeps(
                sub_dep, 
                processed,
                include_dirs,
                library_dirs,
                libraries,
                paths)
    else:
        # Dep is not an IVPM package
        print("TODO: %s is not an IVPM package" % str(dep))
        try:
            mod = importlib.import_module(dep)
            pkg_path = mod.__file__

            if pkg_path is not None:
                if os.path.isfile(pkg_path):
                    pkg_dir = os.path.dirname(pkg_path)
                else:
                    pkg_dir = pkg_path
                print("pkg_path: %s ; pkg_dir: %s" % (pkg_path, pkg_dir))
                if pkg_dir not in include_dirs:
                    include_dirs.append(pkg_dir)
            else:
                print("Package %s does not have a non-null module path" % dep)
        except ImportError as e:
            print("Failed to import dependency %s (%s)" % (dep, str(e)))


def _apply_extdeps(
    m,
    include_dirs,
    library_dirs,
    libraries):
    for incdir in include_dirs:
        if incdir not in m.include_dirs:
            print("Add incdir %s" % incdir)
            m.include_dirs.append(incdir)
    for libdir in library_dirs:
        if libdir not in m.library_dirs:
            m.library_dirs.append(libdir)
#    for lib in libraries:
#        if lib not in m.libraries:
#            m.libraries.append(lib)

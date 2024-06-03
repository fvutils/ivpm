
import os
import sys
import subprocess
from ivpm.packages_info import PackagesInfo
from ivpm.project_info_reader import ProjectInfoReader
from ivpm.utils import get_venv_python
from ivpm.msg import note, fatal, warning
from ivpm.package_updater import PackageUpdater
from ivpm.package import Package, PackageType, SourceType
from toposort import toposort
from typing import List

class CmdBuild(object):

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
        if os.path.isdir(os.path.join(packages_dir, "python")):
            ivpm_python = get_venv_python(os.path.join(packages_dir, "python"))
        else:
            raise Exception("packages/python does not exist ; ivpm update must be run before ivpm build")
            
        print("********************************************************************")
        print("* Processing root package %s" % proj_info.name)
        print("********************************************************************")

        if self.debug:
            for ds_name in proj_info.dep_set_m.keys():
                print("DepSet: %s" % ds_name)
                for d in proj_info.dep_set_m[ds_name].packages.keys():
                    print("  Package: %s" % d)
                    
        ds_name = "default-dev"
        
        if hasattr(args, "dep_set") and args.dep_set is not None:
            ds_name = args.dep_set
            
        if ds_name not in proj_info.dep_set_m.keys():
            raise Exception("Dep-set %s is not present" % ds_name)
        else:
            ds = proj_info.dep_set_m[ds_name]

        # TODO: need a way to step through packages without updating
        updater = PackageUpdater(packages_dir, load=False)
        # Prevent an attempt to load the top-level project as a depedency
        updater.all_pkgs[proj_info.name] = None
        pkgs_info : PackagesInfo = updater.update(ds)

        # Remove the root package before processing what's left
        pkgs_info.pop(proj_info.name)

        # Build up a dependency map for Python package installation        
        python_deps_m = {}
        python_pkgs_s = set()

        # Collect the full set of packages
        for pid in pkgs_info.keys():
            p = pkgs_info[pid]
            if p.pkg_type == PackageType.Python and p.src_type != SourceType.PyPi:
                python_pkgs_s.add(pid)

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

        env = os.environ.copy()
        env["DEBUG"] = "1" if args.debug else "0"
        for pkgs in pysrc_pkg_order:
            for pkg in pkgs:
                if os.path.isfile(os.path.join(packages_dir, pkg, "setup.py")):
                    cmd = [
                        sys.executable,
                        'setup.py',
                        'build_ext',
                        '--inplace'
                    ]
                    result = subprocess.run(
                        cmd,
                        env=env,
                        cwd=os.path.join(packages_dir, pkg))

                    if result.returncode != 0:
                        raise Exception("Failed to build package %s" % pkg)
                else:
                    note("Skipping Python project without setup.py")




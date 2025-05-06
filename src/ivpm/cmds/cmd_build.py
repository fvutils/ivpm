
import json
import os
import sys
import subprocess
from ivpm.packages_info import PackagesInfo
from ivpm.proj_info import ProjInfo
from ivpm.utils import get_venv_python
from ivpm.msg import note, fatal, warning
from ivpm.package_updater import PackageUpdater
from ivpm.package import Package, PackageType, SourceType
from ..handlers.package_handler_rgy import PackageHandlerRgy
from ..project_ops import ProjectOps
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

        ds_name = None
        if hasattr(args, "dep_set") and args.dep_set is not None:
            ds_name = args.dep_set

        print("--> build")
        ProjectOps(args.project_dir).build(
            dep_set=ds_name,
            args=args,
            debug=args.debug)
        print("<-- build")
        
        return
            
        proj_info = ProjInfo.mkFromProj(args.project_dir)

        if proj_info is None:
            fatal("Failed to locate IVPM meta-data (eg ivpm.yaml)")
            
        deps_dir = os.path.join(args.project_dir, proj_info.deps_dir)
 
        # Ensure that we have a python virtual environment setup
        if os.path.isdir(os.path.join(deps_dir, "python")):
            ivpm_python = get_venv_python(os.path.join(deps_dir, "python"))
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
                    
        ds_name = None
        if hasattr(args, "dep_set") and args.dep_set is not None:
            ds_name = args.dep_set

        ivpm_json = {}

        if os.path.isfile(os.path.join(deps_dir, "ivpm.json")):
            with open(os.path.join(deps_dir, "ivpm.json"), "r") as fp:
                try:
                    ivpm_json = json.load(fp)
                except Exception as e:
                    warning("failed to read ivpm.json: %s" % str(e))

        if "dep-set" in ivpm_json.keys():
            if ds_name is None:
                ds_name = ivpm_json["dep-set"]
            elif ds_name != ivpm_json["dep-set"]:
                fatal("Attempting to update with a different dep-set than previously used")
        
        if ds_name not in proj_info.dep_set_m.keys():
            raise Exception("Dep-set %s is not present" % ds_name)
        else:
            ds = proj_info.dep_set_m[ds_name]

        # TODO: need a way to step through packages without updating
        pkg_handler = PackageHandlerRgy.inst().mkHandler()
        updater = PackageUpdater(deps_dir, pkg_handler, args=args, load=False)
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

                proj_info = ProjInfo.mkFromProj(p.path)

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
                if os.path.isfile(os.path.join(deps_dir, pkg, "setup.py")):
                    cmd = [
                        sys.executable,
                        'setup.py',
                        'build_ext',
                        '--inplace'
                    ]
                    result = subprocess.run(
                        cmd,
                        env=env,
                        cwd=os.path.join(deps_dir, pkg))

                    if result.returncode != 0:
                        raise Exception("Failed to build package %s" % pkg)
                else:
                    note("Skipping Python project without setup.py")




#****************************************************************************
#* project_ops.py
#*
#* Copyright 2018-2024 Matthew Ballance and Contributors
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
import os
import json
import dataclasses as dc
from .package import Package, SourceType
from .package_updater import PackageUpdater
from .handlers.package_handler_rgy import PackageHandlerRgy
from .project_ops_info import ProjectUpdateInfo
from .utils import fatal, note, get_venv_python, setup_venv, warning

@dc.dataclass
class ProjectOps(object):
    root_dir : str
    debug : bool = False

    def update(self,
               dep_set : str = None,
               force_py_install : bool = False,
               anonymous : bool = False,
               skip_venv : bool = False,
               args = None):
        from .proj_info import ProjInfo

        proj_info = ProjInfo.mkFromProj(self.root_dir)

        if proj_info is None:
            fatal("Failed to locate IVPM meta-data (eg ivpm.yaml)")
            
        deps_dir = os.path.join(self.root_dir, proj_info.deps_dir)

        ivpm_json = {}

        if os.path.isfile(os.path.join(deps_dir, "ivpm.json")):
            with open(os.path.join(deps_dir, "ivpm.json"), "r") as fp:
                try:
                    ivpm_json = json.load(fp)
                except Exception as e:
                    warning("failed to read ivpm.json: %s" % str(e))

        if "dep-set" in ivpm_json.keys():
            if dep_set is None:
                dep_set = ivpm_json["dep-set"]
            elif dep_set != ivpm_json["dep-set"]:
                fatal("Attempting to update with a different dep-set than previously used")
 
        # Ensure that we have a python virtual environment setup
        if not skip_venv:
            if not os.path.isdir(os.path.join(deps_dir, "python")):
                ivpm_python = setup_venv(os.path.join(deps_dir, "python"))
            else:
                note("python virtual environment already exists")
                ivpm_python = get_venv_python(os.path.join(deps_dir, "python"))
            
        print("********************************************************************")
        print("* Processing root package %s" % proj_info.name)
        print("********************************************************************")

        if self.debug:
            for self.dep_set in proj_info.dep_set_m.keys():
                print("DepSet: %s" % self.dep_set)
                for d in proj_info.dep_set_m[self.dep_set].packages.keys():
                    print("  Package: %s" % d)

        if dep_set is None:
            if "default-dev" in proj_info.dep_set_m.keys():
                dep_set = "default-dev"
            elif len(proj_info.dep_set_m.keys()) == 1:
                dep_set = list(proj_info.dep_set_m.keys())[0]
            else:
                fatal("No default-dev dep-set and multiple dep-sets present")

        if dep_set not in proj_info.dep_set_m.keys():
            raise Exception("Dep-set %s is not present" % dep_set)
        else:
            ds = proj_info.dep_set_m[dep_set]

        # If the root dependency set doesn't specify a source
        # for IVPM, auto-load it from PyPi
        if "ivpm" not in ds.packages.keys():
            print("Note: will install IVPM from PyPi")
            ivpm = Package("ivpm")
            ivpm.src_type = SourceType.PyPi
            ds.packages["ivpm"] = ivpm

        pkg_handler = PackageHandlerRgy.inst().mkHandler()
        updater = PackageUpdater(
            deps_dir, 
            pkg_handler,
            anonymous_git=anonymous)

        # Prevent an attempt to load the top-level project as a depedency
        updater.all_pkgs[proj_info.name] = None
        pkgs_info = updater.update(ds)

        print("Setup-deps: %s" % str(pkgs_info.setup_deps))

        if args is None:
            args = object()

        # Call the handlers to take care of project-level setup work
        update_info = ProjectUpdateInfo(args, deps_dir, force_py_install=force_py_install)
        pkg_handler.update(update_info)

        # Finally, write out some meta-data
        ivpm_json["dep-set"] = dep_set
        with open(os.path.join(deps_dir, "ivpm.json"), "w") as fp:
            json.dump(ivpm_json, fp)

    def status(self, dep_set : str = None):
        pass

    def sync(self, dep_set : str = None):
        pass



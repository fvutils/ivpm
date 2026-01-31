#****************************************************************************
#* project_sync.py
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
import os
import dataclasses as dc
from .package import Package, SourceType
from .package_updater import PackageUpdater
from .handlers.package_handler_rgy import PackageHandlerRgy
from .project_ops_info import UpdateInfo
from .utils import fatal, note, get_venv_python, setup_venv

@dc.dataclass
class ProjectSync(object):
    root_dir : str
    dep_set : str = None
    anonymous : bool = False
    skip_venv : bool = False
    debug : bool = False

    def sync(self):
        from .proj_info import ProjInfo

        proj_info = ProjInfo.mkFromProj(self.root_dir)

        if proj_info is None:
            fatal("Failed to locate IVPM meta-data (eg ivpm.yaml)")
            
        deps_dir = os.path.join(self.root_dir, proj_info.deps_dir)
 
        # Ensure that we have a python virtual environment setup
        if not self.skip_venv:
            if not os.path.isdir(os.path.join(deps_dir, "python")):
                ivpm_python = setup_venv(os.path.join(deps_dir, "python"))
            else:
                note("python virtual environment already exists")
                ivpm_python = get_venv_python(os.path.join(deps_dir, "python"))
            
        print("********************************************************************")
        print("* Processing root package %s" % proj_info.name)
        print("********************************************************************")

        if self.debug:
            for ds_name in proj_info.dep_set_m.keys():
                print("DepSet: %s" % ds_name)
                for d in proj_info.dep_set_m[ds_name].packages.keys():
                    print("  Package: %s" % d)
                    
        # Determine which dep-set to use
        if self.dep_set is None:
            # Priority: 1) default-dep-set setting, 2) first dep-set in file
            if proj_info.default_dep_set is not None:
                self.dep_set = proj_info.default_dep_set
            elif len(proj_info.dep_set_m.keys()) > 0:
                self.dep_set = list(proj_info.dep_set_m.keys())[0]
            else:
                fatal("No dependency sets defined in project")

        if self.dep_set not in proj_info.dep_set_m.keys():
            raise Exception("Dep-set %s is not present" % self.dep_set)
        else:
            ds = proj_info.dep_set_m[self.dep_set]

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
            anonymous_git=self.anonymous)

        # Prevent an attempt to load the top-level project as a depedency
        updater.all_pkgs[proj_info.name] = None
        pkgs_info = updater.update(ds)

        print("Setup-deps: %s" % str(pkgs_info.setup_deps))

        # Finally, call the handlers to take care of project-level setup work
        update_info = UpdateInfo(deps_dir)
        pkg_handler.update(update_info)

               


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
import dataclasses as dc
from .package import Package, SourceType
from .package_updater import PackageUpdater
from .handlers.package_handler_rgy import PackageHandlerRgy
from .project_ops_info import ProjectUpdateInfo
from .utils import fatal, note, get_venv_python, setup_venv

@dc.dataclass
class ProjectOps(object):
    root_dir : str
    debug : bool = False

    def update(self,
               dep_set : str = "default-dev",
               anonymous : bool = False,
               skip_venv : bool = False):
        from .proj_info import ProjInfo

        proj_info = ProjInfo.mkFromProj(self.root_dir)

        if proj_info is None:
            fatal("Failed to locate IVPM meta-data (eg ivpm.yaml)")
            
        deps_dir = os.path.join(self.root_dir, proj_info.deps_dir)
 
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

        # Finally, call the handlers to take care of project-level setup work
        update_info = ProjectUpdateInfo(deps_dir)
        pkg_handler.update(update_info)

    def status(self, dep_set : str = None):
        pass

    def sync(self, dep_set : str = None):
        pass



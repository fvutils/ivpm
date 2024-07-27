#****************************************************************************
#* project_update.py
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
from .project_info_reader import ProjectInfoReader
from .package_updater import PackageUpdater
from .utils import fatal, note, get_venv_python, setup_venv

@dc.dataclass
class ProjectUpdate(object):
    root_dir : str
    dep_set : str = "default-dev"
    anonymous : bool = False
    debug : bool = False

    def update(self):
        proj_info = ProjectInfoReader(self.root_dir).read()

        if proj_info is None:
            fatal("Failed to locate IVPM meta-data (eg ivpm.yaml)")
            
        packages_dir = os.path.join(self.root_dir, "packages")
 
        # Ensure that we have a python virtual environment setup
        if not os.path.isdir(os.path.join(packages_dir, "python")):
            ivpm_python = setup_venv(os.path.join(packages_dir, "python"))
        else:
            note("python virtual environment already exists")
            ivpm_python = get_venv_python(os.path.join(packages_dir, "python"))
            
        print("********************************************************************")
        print("* Processing root package %s" % proj_info.name)
        print("********************************************************************")

        if self.debug:
            for self.dep_set in proj_info.dep_set_m.keys():
                print("DepSet: %s" % self.dep_set)
                for d in proj_info.dep_set_m[self.dep_set].packages.keys():
                    print("  Package: %s" % d)
                    
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

        updater = PackageUpdater(packages_dir, self.anonymous)
        # Prevent an attempt to load the top-level project as a depedency
        updater.all_pkgs[proj_info.name] = None
        pkgs_info = updater.update(ds)

        print("Setup-deps: %s" % str(pkgs_info.setup_deps))
        pass
               


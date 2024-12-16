#****************************************************************************
#* project_updater.py
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
#* Created on: Jun 22, 2021
#*     Author: mballance
#*
#****************************************************************************
import os
import shutil
import subprocess
import sys
import tarfile
import urllib
from zipfile import ZipFile

from ivpm.msg import note, fatal, warning
from ivpm.package import Package, SourceType, SourceType2Ext, PackageType
from ivpm.packages_info import PackagesInfo
from ivpm.proj_info import ProjInfo
from typing import Dict
from ivpm.utils import get_venv_python
from .project_ops_info import ProjectUpdateInfo


class PackageUpdater(object):
    
    def __init__(self, 
                 deps_dir, 
                 pkg_handler,
                 anonymous_git=False,
                 load=True):
        self.debug = False
        self.deps_dir = deps_dir
        self.pkg_handler = pkg_handler
        self.all_pkgs = PackagesInfo("root")
        self.new_deps = []
        self.anonymous_git = anonymous_git
        self.load = load
        pass
    
    def update(self, pkgs : PackagesInfo) -> PackagesInfo:
        """
        Updates the specified packages, handling dependencies
        The 'pkgs' parameter holds the dependency information
        from the root project
        """
        count = 1

        pkg_q = []
        
        if len(pkgs.keys()) == 0:
            print("No packages")
        
        for key in pkgs.keys():
            print("Package: %s" % key)
            pkg_q.append(pkgs[key])

        if not os.path.isdir(self.deps_dir):
            os.makedirs(self.deps_dir)
            
        while True:        
            pkg_deps = {}
            
            # Process this batch of packages
            while len(pkg_q) > 0:
                pkg : Package = pkg_q.pop(0)
                
                self.all_pkgs[pkg.name] = pkg
                
                proj_info : ProjInfo = self._update_pkg(pkg)

                # proj_info contains info on any setup-deps that
                # might be required
                if proj_info is not None:
                    for sd in proj_info.setup_deps:
                        print("Add setup-dep %s to package %s" % (sd, pkg.name))
                        if pkg.name not in self.all_pkgs.setup_deps.keys():
                            self.all_pkgs.setup_deps[pkg.name] = set()
                        self.all_pkgs.setup_deps[pkg.name].add(sd)

                    if proj_info.process_deps:
                        if not proj_info.has_dep_set(pkg.dep_set):
                            warning("package %s does not contain specified dep-set %s ; skipping" % (proj_info.name, pkg.dep_set))
                            continue
                        else:
                            note("Loading package %s dependencies from dep-set %s" % (proj_info.name, pkg.dep_set))

                        note("Processing dep-set %s of project %s" % (
                            pkg.dep_set,
                            pkg.name))                        

                        ds : PackagesInfo = proj_info.get_dep_set(pkg.dep_set)
                        for d in ds.packages.keys():
                            dep = ds.packages[d]
                    
                            if dep.name not in pkg_deps.keys():
                                pkg_deps[dep.name] = dep
                            else:
                                # TODO: warn about possible version conflict?
                                pass
            
            # Collect new dependencies and add to queue
            for key in pkg_deps.keys():
                if not key in self.all_pkgs.keys():
                    # New package
                    pkg_q.append(pkg_deps[key])
            note("%d new dependencies from iteration %d" % (len(pkg_q), count))
                    
            if len(pkg_q) == 0:
                # We're done
                break
            
            count += 1
            
        return self.all_pkgs
    
    def _update_pkg(self, pkg : Package) -> ProjInfo:
        """Loads a single package. Returns any dependencies"""
        must_update=False

        update_info = ProjectUpdateInfo(self.deps_dir)
  
        print("********************************************************************")
        print("* Processing package %s" % pkg.name)
        print("********************************************************************")


        pkg_dir = os.path.join(self.deps_dir, pkg.name)
        pkg.path = pkg_dir.replace("\\", "/")

        pkg.proj_info = pkg.update(update_info)

        # Notify the package handlers after the source is 
        # loaded so they can take further action if required 
        self.pkg_handler.process_pkg(pkg)
        
        # Ensure that we use the requested dep-set
        if pkg.proj_info is not None:
            pkg.proj_info.target_dep_set = pkg.dep_set
            pkg.proj_info.process_deps = pkg.process_deps
        
        return pkg.proj_info

    

    


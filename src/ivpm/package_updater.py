'''
Created on Jun 22, 2021

@author: mballance
'''
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
from ivpm.project_info_reader import ProjectInfoReader
from typing import Dict
from ivpm.utils import get_venv_python
from .update_info import UpdateInfo


class PackageUpdater(object):
    
    def __init__(self, 
                 packages_dir, 
                 anonymous_git=False,
                 load=True):
        self.debug = False
        self.deps_dir = packages_dir
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
            
        while True:        
            pkg_deps = {}
            
            # Process this batch of packages
            while len(pkg_q) > 0:
                pkg : Package = pkg_q.pop(0)
                
                self.all_pkgs[pkg.name] = pkg
                
                if pkg.src_type != SourceType.PyPi:
                    proj_info : ProjInfo = self._update_pkg(pkg)
                    
                    # proj_info contains info on any setup-deps that
                    # might be required
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

        update_info = UpdateInfo(self.deps_dir)
  
        print("********************************************************************")
        print("* Processing package %s" % pkg.name)
        print("********************************************************************")


        pkg_dir = os.path.join(self.deps_dir, pkg.name)
        pkg.path = pkg_dir.replace("\\", "/")

        info = pkg.update(update_info)
        
        # if os.path.exists(pkg_dir):
        #     note("package %s is already loaded" % pkg.name)
        # elif self.load:
        #     note("loading package %s" % pkg.name)

        #     # Package isn't currently present in dependencies
        #     scheme_idx = pkg.url.find("://")
        #     scheme = pkg.url[0:scheme_idx+3]
            
        #     if pkg.src_type == SourceType.Git:
        #         self._clone_git(pkg)
        #     else:
        #         remove_pkg_src = False
        #         pkg_path = None
        #         print("Must add package " + pkg.name + " scheme=" + scheme)
                
        #         if scheme == "file://":
        #             pkg_path = pkg.url[scheme_idx+3:-1]
        #         elif scheme in ("http://", "https://", "ssh://"):

                    
        #         pkg.path = os.path.join(self.packages_dir, pkg.name)
        #         pkg.path = pkg.path.replace("\\", "/")

        #         if self.debug:
        #             print("package %s: type=%s" % (pkg.path, str(pkg.src_type)))
        #         if pkg.src_type in (SourceType.Jar,SourceType.Zip):
        #             self._install_zip(pkg, pkg_path)
        #         elif pkg.src_type == SourceType.Tgz or pkg.src_type == SourceType.Txz:
        #             self._install_tgz(pkg, pkg_path)
                    

        #         if remove_pkg_src:
        #             os.unlink(os.path.join(download_dir, filename))
        # else:
        #     # Package doesn't exist, and we won't load it
        #     raise Exception("Package %s is not present" % pkg.name)
                    

        # After loading the package, or finding it already loaded,
        # check what we have
        if pkg.pkg_type == PackageType.Unknown:
            for py in ("setup.py", "pyproject.toml"):
                if os.path.isfile(os.path.join(self.deps_dir, pkg.name, py)):
                    pkg.pkg_type = PackageType.Python
                    break
        
        if info is None:
            info = ProjInfo(False)
            info.name = pkg.name

        # Ensure that we use the requested dep-set
        info.target_dep_set = pkg.dep_set
        info.process_deps = pkg.process_deps
        
        return info
    

    

    


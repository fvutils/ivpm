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
import asyncio
import logging
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
from typing import Dict, List, Tuple
from ivpm.utils import get_venv_python
from .project_ops_info import ProjectUpdateInfo

_logger = logging.getLogger("ivpm.package_updater")


class PackageUpdater(object):
    
    def __init__(self, 
                 deps_dir, 
                 pkg_handler,
                 load=True,
                 args=None):
        self.debug = False
        self.deps_dir = deps_dir
        self.pkg_handler = pkg_handler
        self.all_pkgs = PackagesInfo("root")
        self.new_deps = []
        self.args = object() if args is None else args
        self.load = load
        self.update_info = ProjectUpdateInfo(self.args, deps_dir)
        
        # Get max parallelism from args, default to CPU count
        if hasattr(args, 'jobs') and args.jobs is not None:
            self.max_parallel = args.jobs
        else:
            import multiprocessing
            self.max_parallel = multiprocessing.cpu_count()
        self.update_info.max_parallel = self.max_parallel
        pass
    
    def update(self, pkgs : PackagesInfo) -> PackagesInfo:
        """
        Updates the specified packages, handling dependencies.
        Uses async parallel fetching for efficiency.
        """
        return asyncio.run(self._update_async(pkgs))
    
    async def _update_async(self, pkgs: PackagesInfo) -> PackagesInfo:
        """
        Async implementation of update that processes packages in parallel.
        The 'pkgs' parameter holds the dependency information
        from the root project.
        """
        count = 1

        pkg_q = []
        
        if len(pkgs.keys()) == 0:
            _logger.info("No packages")

        for key in pkgs.keys():
            _logger.debug("Package: %s", key)
            pkg_q.append(pkgs[key])

        if not os.path.isdir(self.deps_dir):
            os.makedirs(self.deps_dir)

        # Create semaphore to limit parallelism
        semaphore = asyncio.Semaphore(self.max_parallel)
        
        while True:        
            pkg_deps = {}
            
            # Process this batch of packages in parallel
            if len(pkg_q) > 0:
                results = await self._process_batch_parallel(pkg_q, semaphore)
                
                # Collect dependencies from results
                for pkg, proj_info in results:
                    self.all_pkgs[pkg.name] = pkg
                    
                    # proj_info contains info on any setup-deps that
                    # might be required
                    if proj_info is not None:
                        for sd in proj_info.setup_deps:
                            _logger.debug("Add setup-dep %s to package %s", sd, pkg.name)
                            if pkg.name not in self.all_pkgs.setup_deps.keys():
                                self.all_pkgs.setup_deps[pkg.name] = set()
                            self.all_pkgs.setup_deps[pkg.name].add(sd)

                        if proj_info.process_deps:
                            if not proj_info.has_dep_set(pkg.dep_set):
                                fatal("package %s in %s does not contain specified dep-set %s" % (
                                    proj_info.name, 
                                    pkg.name,
                                    pkg.dep_set))
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
            pkg_q = []
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
    
    async def _process_batch_parallel(self, pkg_q: List[Package], semaphore: asyncio.Semaphore) -> List[Tuple[Package, ProjInfo]]:
        """Process a batch of packages in parallel."""
        tasks = []
        for pkg in pkg_q:
            tasks.append(self._update_pkg_async(pkg, semaphore))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results, handling any exceptions
        processed = []
        for i, result in enumerate(results):
            pkg = pkg_q[i]
            if isinstance(result, Exception):
                # Notify listeners of failure
                self.update_info.package_error(pkg.name, str(result))
                fatal("Failed to update package %s: %s" % (pkg.name, str(result)))
            else:
                processed.append(result)
        
        return processed
    
    async def _update_pkg_async(self, pkg: Package, semaphore: asyncio.Semaphore) -> Tuple[Package, ProjInfo]:
        """Async wrapper for updating a single package with semaphore limiting."""
        async with semaphore:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._update_pkg, pkg
            )
    
    def _update_pkg(self, pkg : Package) -> Tuple[Package, ProjInfo]:
        """Loads a single package. Returns the package and any dependencies."""
        must_update=False

        _logger.info("Processing package %s (dep-set %s)", pkg.name, pkg.dep_set)
        
        # Get package source info for event - normalize the type name
        pkg_type = getattr(pkg, 'src_type', None)
        if pkg_type is not None:
            # Convert SourceType enum to string spec if needed
            if hasattr(pkg_type, 'name'):
                # It's an enum - use lowercase name
                pkg_type = pkg_type.name.lower()
            else:
                # It's already a string
                pkg_type = str(pkg_type).lower()
        pkg_src = getattr(pkg, 'url', None) or getattr(pkg, 'path', None) or ""

        # Signal package start
        self.update_info.package_start(pkg.name, pkg_type, pkg_src)

        pkg_dir = os.path.join(self.deps_dir, pkg.name)
        pkg.path = pkg_dir.replace("\\", "/")

        try:
            pkg.proj_info = pkg.update(self.update_info)

            # Notify the package handlers after the source is 
            # loaded so they can take further action if required 
            self.pkg_handler.process_pkg(pkg)
            
            # Ensure that we use the requested dep-set
            if pkg.proj_info is not None:
                pkg.proj_info.target_dep_set = pkg.dep_set
                pkg.proj_info.process_deps = pkg.process_deps
            
            # Signal package complete
            self.update_info.package_complete(pkg.name)
            
            return (pkg, pkg.proj_info)
        except Exception as e:
            # Signal package error
            self.update_info.package_error(pkg.name, str(e))
            raise

    

    


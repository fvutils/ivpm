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


def _origin_suffix(pkg) -> str:
    """A short parenthetical describing *where* a failing dependency came from.

    Names the source type and URL (the actual specification that failed) and,
    for a transitive dependency, the package that pulled it in. The file:line
    location is added separately by the diagnostic reporter (it reads
    ``pkg.srcinfo``), so this only carries the spec details that help the user
    recognise which entry to fix."""
    parts = []
    src = getattr(pkg, "src_type", None)
    if src is not None:
        src = src.name.lower() if hasattr(src, "name") else str(src)
        parts.append("src: %s" % src)
    url = getattr(pkg, "url", None)
    if url:
        parts.append("url: %s" % url)
    resolved_by = getattr(pkg, "resolved_by", None)
    if resolved_by:
        parts.append("required by: %s" % resolved_by)
    return (" [%s]" % ", ".join(parts)) if parts else ""


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
                                    # Track which package caused this dependency to be resolved
                                    dep.resolved_by = pkg.name
                                    pkg_deps[dep.name] = dep
                                else:
                                    # Already resolved by a higher-level package - don't override
                                    pass
            
            # Collect new dependencies and add to queue
            pkg_q = []
            for key in pkg_deps.keys():
                dep = pkg_deps[key]
                existing = self.all_pkgs.packages.get(key)
                if existing is None:
                    # New package
                    pkg_q.append(dep)
                elif getattr(existing, "virtual", False) and not getattr(dep, "virtual", False):
                    # A real package whose name collides with a virtual redirect
                    # (`src: ivpm.yaml` factory) of the same name. The redirect
                    # installs nothing itself, so it must not shadow the real
                    # package -- otherwise the real one is silently never fetched
                    # (e.g. a `gcc-riscv` alias pointing at a dep-set that also
                    # contains a `gcc-riscv` package). Queue the real package so
                    # it is fetched; it will overwrite the virtual node below.
                    pkg_q.append(dep)
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
                # The per-package PACKAGE_ERROR event was already dispatched by
                # _update_pkg (closest to the failure, with source location), so
                # we don't re-report it here -- that would duplicate the
                # '<< <pkg> ERROR:' line. Raise a fatal that points at the exact
                # dependency specification (file:line:col, plus the source/url
                # that failed) so the user knows which entry to fix and where.
                fatal(
                    "Failed to update package %s: %s%s" % (
                        pkg.name, str(result), _origin_suffix(pkg)),
                    pkg)
            else:
                processed.append(result)

        return processed
    
    async def _update_pkg_async(self, pkg: Package, semaphore: asyncio.Semaphore) -> Tuple[Package, ProjInfo]:
        """Async wrapper for updating a single package with semaphore limiting."""
        # Measure the real "wait" — time queued for a worker slot (there is no
        # cache lock to wait on). This span opens/closes on the event-loop
        # thread and parents onto the enclosing fetch phase; the per-package
        # fetch.pkg span (opened in the executor by package_start) is a sibling.
        perf = self.update_info.perf
        qspan = None
        if perf is not None:
            qspan = perf.open_span(
                "pkg.queue_wait", package=pkg.name,
                parent_id=self.update_info._fetch_parent_id)
        async with semaphore:
            if qspan is not None:
                perf.close_span(qspan)
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
            # Notify handler before the package is fetched
            self.pkg_handler.on_leaf_pre_load(pkg, self.update_info)

            pkg.proj_info = pkg.update(self.update_info)

            # Merge self-declared types from the dep's own ivpm.yaml into pkg.type_data.
            # Caller-specified types take priority; self-declared ones are appended only
            # if their type name is not already present.
            if pkg.proj_info is not None and pkg.proj_info.self_types:
                from .pkg_content_type_rgy import PkgContentTypeRgy
                ct_rgy = PkgContentTypeRgy.inst()
                caller_names = {td.type_name for td in pkg.type_data}
                for type_name, opts in pkg.proj_info.self_types:
                    if type_name not in caller_names and ct_rgy.has(type_name):
                        pkg.type_data.append(ct_rgy.get(type_name).create_data(opts, None))

            # Notify the package handlers after the source is loaded
            from .handlers.package_handler import HandlerFatalError
            try:
                self.pkg_handler.on_leaf_post_load(pkg, self.update_info)
            except HandlerFatalError:
                raise
            except Exception as leaf_exc:
                _logger.warning("Handler error for package %s: %s", pkg.name, leaf_exc)

            # Ensure that we use the requested dep-set
            if pkg.proj_info is not None:
                pkg.proj_info.target_dep_set = pkg.dep_set
                pkg.proj_info.process_deps = pkg.process_deps
            
            # Signal package complete, passing resolved version if available (e.g., gh-rls)
            resolved_version = getattr(pkg, 'resolved_version', None)
            self.update_info.package_complete(pkg.name, version=resolved_version)
            
            return (pkg, pkg.proj_info)
        except Exception as e:
            # Signal package error, carrying the dependency's source location so
            # the TUI can show file:line:col alongside the message.
            self.update_info.package_error(
                pkg.name, str(e), loc=getattr(pkg, "srcinfo", None))
            raise

    

    


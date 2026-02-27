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
import logging
import os
import json
import dataclasses as dc
from typing import Tuple, List, Optional
from .package import Package, SourceType
from .package_updater import PackageUpdater
from .handlers.package_handler_rgy import PackageHandlerRgy
from .project_ops_info import ProjectUpdateInfo, ProjectBuildInfo
from .update_event import UpdateEventDispatcher
from .update_tui import create_update_tui, RichUpdateTUI
from .utils import fatal, note, get_venv_python, setup_venv, warning
from .package_lock import write_lock, check_lock_changes

_logger = logging.getLogger("ivpm.project_ops")


@dc.dataclass
class ProjectOps(object):
    root_dir : str
    debug : bool = False

    def update(self,
               dep_set : str = None,
               force_py_install : bool = False,
               skip_venv : bool = False,
               args = None,
               lock_file : str = None,
               refresh_all : bool = False,
               force : bool = False):
        from .update_event import UpdateEvent, UpdateEventType
        import time
        
        proj_info, deps_dir, dep_set = self._init(dep_set)

        # Get log level from args for TUI selection
        log_level = getattr(args, 'log_level', 'NONE')
        
        # Create event dispatcher and TUI
        event_dispatcher = UpdateEventDispatcher()
        tui = create_update_tui(log_level)
        event_dispatcher.add_listener(tui)
        
        # Determine if we should suppress output (Rich TUI mode)
        suppress_output = isinstance(tui, RichUpdateTUI)
        
        # Start the TUI if it's Rich-based
        if isinstance(tui, RichUpdateTUI):
            tui.start()
 
        try:
            # Ensure that we have a python virtual environment setup
            if not skip_venv:
                if not os.path.isdir(os.path.join(deps_dir, "python")):
                    uv_pip = "auto"
                    if hasattr(args, "py_uv") and args.py_uv:
                        uv_pip = "uv"
                    elif hasattr(args, "py_pip") and args.py_pip:
                        uv_pip = "pip"
                    system_site_packages = (
                        hasattr(args, "py_system_site_packages") and
                        bool(args.py_system_site_packages)
                    )
                    
                    # Signal venv creation start
                    venv_start_time = time.time()
                    event_dispatcher.dispatch(UpdateEvent(
                        event_type=UpdateEventType.VENV_START
                    ))
                    
                    try:
                        ivpm_python = setup_venv(
                            os.path.join(deps_dir, "python"), 
                            uv_pip=uv_pip,
                            suppress_output=suppress_output,
                            system_site_packages=system_site_packages
                        )
                        # Signal venv creation complete
                        event_dispatcher.dispatch(UpdateEvent(
                            event_type=UpdateEventType.VENV_COMPLETE,
                            duration=time.time() - venv_start_time
                        ))
                    except Exception as e:
                        # Signal venv creation error
                        event_dispatcher.dispatch(UpdateEvent(
                            event_type=UpdateEventType.VENV_ERROR,
                            error_message=str(e)
                        ))
                        raise
                else:
                    note("python virtual environment already exists")
                    ivpm_python = get_venv_python(os.path.join(deps_dir, "python"))
                
            _logger.info("Processing root package %s", proj_info.name)

            if self.debug:
                for self.dep_set in proj_info.dep_set_m.keys():
                    _logger.debug("DepSet: %s", self.dep_set)
                    for d in proj_info.dep_set_m[self.dep_set].packages.keys():
                        _logger.debug("  Package: %s", d)

            if lock_file:
                # Reproduction mode: use lock file as the sole package source
                from .package_lock import IvpmLockReader
                note("Reproducing workspace from lock file: %s" % lock_file)
                lock_reader = IvpmLockReader(lock_file)
                ds = lock_reader.build_packages_info()
            else:
                ds = self._getDepSet(proj_info, dep_set)

                # If the root dependency set doesn't specify a source
                # for IVPM, auto-load it from PyPi
                if "ivpm" not in ds.packages.keys():
                    _logger.info("Will install IVPM from PyPi")
                    ivpm = Package("ivpm")
                    ivpm.src_type = SourceType.PyPi
                    ds.packages["ivpm"] = ivpm

                # Change detection: compare current specs against existing lock
                if not refresh_all and not force:
                    diffs = check_lock_changes(deps_dir, ds.packages)
                    if diffs:
                        note("The following packages have changed specs vs package-lock.json:")
                        for name, diff in diffs.items():
                            note("  %s: run with --refresh-all to re-fetch" % name)
                        note("No packages re-fetched. Use --refresh-all to update.")

            pkg_handler = PackageHandlerRgy.inst().mkHandler()
            updater = PackageUpdater(deps_dir, pkg_handler, args=args)
            
            # Configure event dispatcher on update_info
            updater.update_info.event_dispatcher = event_dispatcher
            
            # Suppress subprocess output when using Rich TUI
            updater.update_info.suppress_output = suppress_output

            # Prevent an attempt to load the top-level project as a depedency
            updater.all_pkgs[proj_info.name] = None
            pkgs_info = updater.update(ds)

            _logger.debug("Setup-deps: %s", str(pkgs_info.setup_deps))

            # Call the handlers to take care of project-level setup work
            update_info = ProjectUpdateInfo(
                args, deps_dir,
                project_name=proj_info.name,
                force_py_install=force_py_install, 
                skip_venv=skip_venv,
                suppress_output=suppress_output
            )
            pkg_handler.update(update_info)

            # Signal update complete
            updater.update_info.update_complete()

            # Write package-lock.json with resolved package versions
            handler_contributions = pkg_handler.get_lock_entries(deps_dir)
            write_lock(deps_dir, updater.all_pkgs, handler_contributions)

            # Finally, write out some meta-data
            ivpm_json = {}
            ivpm_json["dep-set"] = dep_set
            with open(os.path.join(deps_dir, "ivpm.json"), "w") as fp:
                json.dump(ivpm_json, fp)
        finally:
            # Ensure TUI is stopped on exception
            if isinstance(tui, RichUpdateTUI):
                tui.stop()

    def build(self, dep_set : str = None, args = None, debug : bool = False):
        proj_info, deps_dir, dep_set = self._init(dep_set)

        ds = self._getDepSet(proj_info, dep_set)

        pkg_handler = PackageHandlerRgy.inst().mkHandler()
        updater = PackageUpdater(deps_dir, pkg_handler, args=args, load=False)

        # Prevent an attempt to load the top-level project as a depedency
        updater.all_pkgs[proj_info.name] = None
        pkgs_info = updater.update(ds)

        # Now, run the actual build operation
        build_info = ProjectBuildInfo(args, deps_dir, debug=debug)

        pkg_handler.build(build_info)

        pass

    def status(self, dep_set: str = None, args=None):
        from .proj_info import ProjInfo
        from .pkg_status import PkgVcsStatus
        from .project_ops_info import ProjectStatusInfo
        from .pkg_types.pkg_type_rgy import PkgTypeRgy
        from .package_lock import read_lock

        proj_info = ProjInfo.mkFromProj(self.root_dir)
        if proj_info is None:
            fatal("Failed to locate IVPM meta-data (eg ivpm.yaml)")

        deps_dir = os.path.join(self.root_dir, proj_info.deps_dir)
        lock_path = os.path.join(deps_dir, "package-lock.json")

        if not os.path.isfile(lock_path):
            fatal("package-lock.json not found in %s — run 'ivpm update' first" % deps_dir)

        lock = read_lock(lock_path)
        packages = lock.get("packages", {})

        status_info = ProjectStatusInfo(args=args, deps_dir=deps_dir)
        rgy = PkgTypeRgy.inst()

        results = []
        for name, entry in packages.items():
            src = entry.get("src", "")
            if not rgy.hasPkgType(src):
                results.append(PkgVcsStatus(
                    name=name,
                    src_type=src,
                    path=os.path.join(deps_dir, name),
                    vcs="none",
                ))
                continue

            pkg = rgy.mkPackage(src, name, entry, None)
            pkg.path = os.path.join(deps_dir, name)
            result = pkg.status(status_info)
            if result is None:
                result = PkgVcsStatus(
                    name=name,
                    src_type=src,
                    path=pkg.path,
                    vcs="none",
                )
            results.append(result)

        return sorted(results, key=lambda r: r.name)

    def sync(self, dep_set: str = None, args=None):
        import asyncio
        import multiprocessing
        from .pkg_sync import PkgSyncResult, SyncOutcome
        from .project_ops_info import ProjectSyncInfo
        from .pkg_types.pkg_type_rgy import PkgTypeRgy
        from .package_lock import read_lock, patch_lock_after_sync
        from .proj_info import ProjInfo

        proj_info = ProjInfo.mkFromProj(self.root_dir)
        if proj_info is None:
            fatal("Failed to locate IVPM meta-data (eg ivpm.yaml)")

        deps_dir = os.path.join(self.root_dir, proj_info.deps_dir)
        lock_path = os.path.join(deps_dir, "package-lock.json")

        if not os.path.isfile(lock_path):
            fatal("package-lock.json not found in %s — run 'ivpm update' first" % deps_dir)

        lock = read_lock(lock_path)
        packages = lock.get("packages", {})

        dry_run          = getattr(args, "dry_run",          False) if args is not None else False
        packages_filter  = getattr(args, "packages_filter",  None)  if args is not None else None
        max_parallel     = getattr(args, "jobs",             0)     if args is not None else 0
        progress         = getattr(args, "_sync_progress",   None)  if args is not None else None

        sync_info = ProjectSyncInfo(
            args=args,
            deps_dir=deps_dir,
            dry_run=dry_run,
            packages_filter=packages_filter,
            max_parallel=max_parallel or 0,
            progress=progress,
        )
        rgy = PkgTypeRgy.inst()

        # Filter the package list once.
        pkg_items = [
            (name, entry) for name, entry in packages.items()
            if not packages_filter or name in packages_filter
        ]

        if not pkg_items:
            return []

        # ── Parallel execution ─────────────────────────────────────────────
        n_workers = sync_info.max_parallel or multiprocessing.cpu_count()

        async def _run_all():
            semaphore = asyncio.Semaphore(n_workers)
            loop = asyncio.get_event_loop()

            async def _run_one(name, entry):
                async with semaphore:
                    if progress:
                        progress.on_pkg_start(name)
                    src = entry.get("src", "")
                    if not rgy.hasPkgType(src):
                        result = PkgSyncResult(
                            name=name, src_type=src,
                            path=os.path.join(deps_dir, name),
                            outcome=SyncOutcome.SKIPPED,
                            skipped_reason="unknown package type",
                        )
                    else:
                        pkg = rgy.mkPackage(src, name, entry, None)
                        pkg.path = os.path.join(deps_dir, name)
                        result = await loop.run_in_executor(
                            None, pkg.sync, sync_info
                        )
                    if progress:
                        progress.on_pkg_result(result)
                    return result

            return await asyncio.gather(
                *[_run_one(n, e) for n, e in pkg_items],
                return_exceptions=True,
            )

        raw = asyncio.run(_run_all())

        results = []
        for i, r in enumerate(raw):
            if isinstance(r, Exception):
                name = pkg_items[i][0]
                entry = pkg_items[i][1]
                err_result = PkgSyncResult(
                    name=name,
                    src_type=entry.get("src", ""),
                    path=os.path.join(deps_dir, name),
                    outcome=SyncOutcome.ERROR,
                    error=str(r),
                )
                if progress:
                    progress.on_pkg_result(err_result)
                results.append(err_result)
            else:
                results.append(r)

        if not dry_run:
            patch_lock_after_sync(lock_path, results)

        return sorted(results, key=lambda r: r.name)

    def _init(self, dep_set : str = None) -> Tuple['ProjInfo', str, str]:
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

        return (proj_info, deps_dir, dep_set)
    
    def _getDepSet(self, proj_info, dep_set):
        if dep_set is None:
            # Priority: 1) default-dep-set setting, 2) first dep-set in file
            if proj_info.default_dep_set is not None:
                dep_set = proj_info.default_dep_set
            elif len(proj_info.dep_set_m.keys()) > 0:
                dep_set = list(proj_info.dep_set_m.keys())[0]
            else:
                fatal("No dependency sets defined in project")

        if dep_set not in proj_info.dep_set_m.keys():
            raise Exception("Dep-set %s is not present" % dep_set)
        else:
            ds = proj_info.dep_set_m[dep_set]
        
        return ds



#****************************************************************************
#* project_opts_info.py
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
import dataclasses as dc
import enum
import logging
import time
from typing import List, Optional, Tuple

from .update_event import UpdateEvent, UpdateEventType, UpdateEventDispatcher

_logger = logging.getLogger("ivpm.project_ops_info")


class FileStatus(enum.Enum):
    Unknown = "U"
    Modified = "M"
    Added = "A"
    Deleted = "D"

@dc.dataclass
class ProjectOpsInfo(object):
    args : object
    deps_dir : str

@dc.dataclass
class ProjectBuildInfo(ProjectOpsInfo):
    debug : bool = False
    event_dispatcher: Optional['UpdateEventDispatcher'] = None

@dc.dataclass
class ProjectSyncInfo(ProjectOpsInfo):
    dry_run: bool = False
    packages_filter: Optional[List[str]] = None  # if set, only sync named packages
    max_parallel: int = 0                         # 0 = cpu_count()
    progress: Optional[object] = None             # SyncProgressListener instance

@dc.dataclass
class ProjectStatusInfo(ProjectOpsInfo):
    dep_set: Optional[str] = None

@dc.dataclass
class ProjectStatusResult(object):
    package : str
    path : str
    status : FileStatus

@dc.dataclass
class ProjectUpdateInfo(ProjectOpsInfo):
    project_name : Optional[str] = None
    force_py_install : bool = False
    skip_venv : bool = False
    cache: Optional['Cache'] = None
    cache_hits: int = 0
    cache_misses: int = 0
    total_packages: int = 0
    cacheable_packages: int = 0
    cache_unconfigured_packages: int = 0  # cache=True but IVPM_CACHE not set
    editable_packages: int = 0
    deps_source: Optional['DepsSource'] = None
    deps_source_mode: str = "link"  # "link" or "copy"
    deps_source_auto: bool = False  # deps_source was auto-detected (git worktree)
    deps_source_hits: int = 0
    deps_source_misses: int = 0
    max_parallel: int = 0  # 0 means use available cores
    event_dispatcher: Optional[UpdateEventDispatcher] = None
    suppress_output: bool = False  # When True, suppress subprocess output (Rich TUI mode)
    python_config: Optional[object] = None  # PythonConfig from root ivpm.yaml
    node_config: Optional[object] = None    # NodeConfig from root ivpm.yaml
    handler_configs: dict = dc.field(default_factory=dict)  # Extra with: keys for plugin handlers
    project_dir: Optional[str] = None   # Project root (one level above deps_dir)
    handler_state: dict = dc.field(default_factory=dict)  # Loaded from ivpm.json["handlers"]
    lock_data: Optional[dict] = None  # Parsed package-lock.json for change detection
    pending_skill_dirs: List[Tuple[str, str]] = dc.field(default_factory=list)  # (name, skill_dir) pushed by handlers
    modules_interface: Optional['ModulesInterface'] = None  # lazily populated by PackageModule.update()
    _tui_ref: Optional[object] = None  # Reference to the TUI for prompt callbacks
    _current_package_start: Optional[float] = None
    _current_package_name: Optional[str] = None
    _current_cache_hit: Optional[bool] = None
    
    def get_prompt_callback(self):
        """Return a prompt callback appropriate for the current TUI.
    
        Returns None if no TUI is configured or if running non-interactively.
        """
        if self._tui_ref is not None and hasattr(self._tui_ref, "make_prompt_callback"):
            return self._tui_ref.make_prompt_callback()
        return None

    def report_cache_unconfigured(self):
        """Record that a package had cache=True but IVPM_CACHE was not set."""
        self.cache_unconfigured_packages += 1

    def report_deps_source_hit(self):
        self.deps_source_hits += 1

    def report_deps_source_miss(self):
        self.deps_source_misses += 1

    def try_deps_source(self, pkg) -> bool:
        """If a deps-source is configured and satisfies ``pkg``, materialize
        ``deps/<pkg.name>`` from the parent and return True; otherwise return
        False without modifying anything.
        """
        if self.deps_source is None:
            return False
        hit = self.deps_source.lookup(pkg)
        if hit is None:
            self.report_deps_source_miss()
            return False
        self._materialize_from_deps_source(pkg, hit)
        self.report_deps_source_hit()
        return True

    def _materialize_from_deps_source(self, pkg, source_path: str):
        import os
        import shutil
        target = os.path.join(self.deps_dir, pkg.name)
        if os.path.lexists(target):
            raise RuntimeError(
                "Cannot materialize %s from deps-source %s: %s already exists"
                % (pkg.name, source_path, target))
        os.makedirs(self.deps_dir, exist_ok=True)
        if self.deps_source_mode == "copy":
            shutil.copytree(source_path, target, symlinks=True)
        else:
            os.symlink(source_path, target)
        pkg.from_deps_source = source_path
        pkg.path = target.replace("\\", "/")

    def report_cache_hit(self):
        self.cache_hits += 1
        self._current_cache_hit = True
    
    def report_cache_miss(self):
        self.cache_misses += 1
        self._current_cache_hit = False
    
    def report_package(self, cacheable: bool = False, editable: bool = False):
        """Report a package for statistics.
        
        Args:
            cacheable: True if package has cache=True set
            editable: True if package could be cached but isn't (e.g., git repos, .tar.gz without cache=True)
        """
        self.total_packages += 1
        if cacheable:
            self.cacheable_packages += 1
        if editable:
            self.editable_packages += 1
    
    def package_start(self, name: str, pkg_type: str = None, pkg_src: str = None):
        """Signal that loading of a package has started."""
        self._current_package_start = time.time()
        self._current_package_name = name
        self._current_cache_hit = None
        
        if self.event_dispatcher:
            event = UpdateEvent(
                event_type=UpdateEventType.PACKAGE_START,
                package_name=name,
                package_type=pkg_type,
                package_src=pkg_src
            )
            self.event_dispatcher.dispatch(event)
        _logger.debug("Package start: %s", name)
    
    def package_complete(self, name: str, version: str = None):
        """Signal that loading of a package has completed."""
        duration = None
        if self._current_package_start and self._current_package_name == name:
            duration = time.time() - self._current_package_start
        
        if self.event_dispatcher:
            event = UpdateEvent(
                event_type=UpdateEventType.PACKAGE_COMPLETE,
                package_name=name,
                duration=duration,
                cache_hit=self._current_cache_hit,
                version=version
            )
            self.event_dispatcher.dispatch(event)
        _logger.debug("Package complete: %s (%.2fs)", name, duration or 0)
        
        self._current_package_start = None
        self._current_package_name = None
        self._current_cache_hit = None
    
    def package_error(self, name: str, error_message: str):
        """Signal that loading of a package has failed."""
        if self.event_dispatcher:
            event = UpdateEvent(
                event_type=UpdateEventType.PACKAGE_ERROR,
                package_name=name,
                error_message=error_message
            )
            self.event_dispatcher.dispatch(event)
        _logger.error("Package error: %s - %s", name, error_message)
    
    def update_complete(self):
        """Signal that the update operation is complete."""
        if self.event_dispatcher:
            event = UpdateEvent(
                event_type=UpdateEventType.UPDATE_COMPLETE,
                total_packages=self.total_packages,
                cache_hits=self.cache_hits,
                cache_misses=self.cache_misses,
                cacheable_packages=self.cacheable_packages,
                editable_packages=self.editable_packages,
                cache_unconfigured_packages=self.cache_unconfigured_packages,
                deps_source_hits=self.deps_source_hits,
                deps_source_misses=self.deps_source_misses,
            )
            self.event_dispatcher.dispatch(event)
        _logger.debug("Update complete: %d packages", self.total_packages)
    
    def print_cache_summary(self):
        """Print a summary of cache statistics (legacy support)."""
        # Now handled via TUI events - this is kept for backward compatibility
        # when running without TUI
        if self.event_dispatcher is None:
            _logger.info("")
            _logger.info("Sub-Package Update Summary:")
            _logger.info("  Total packages: %d", self.total_packages)
            _logger.info("  Cacheable packages: %d", self.cacheable_packages)
            _logger.info("  Editable packages: %d", self.editable_packages)
            if self.cacheable_packages > 0:
                _logger.info("  Cache hits: %d", self.cache_hits)
                _logger.info("  Cache misses: %d", self.cache_misses)
                hit_rate = (self.cache_hits / self.cacheable_packages * 100) if self.cacheable_packages > 0 else 0
                _logger.info("  Hit rate: %.1f%%", hit_rate)


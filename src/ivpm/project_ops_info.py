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
from typing import Optional

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
    pass

@dc.dataclass
class ProjectSyncInfo(ProjectOpsInfo):
    pass

@dc.dataclass
class ProjectStatusInfo(ProjectOpsInfo):
    pass

@dc.dataclass
class ProjectStatusResult(object):
    package : str
    path : str
    status : FileStatus

@dc.dataclass
class ProjectUpdateInfo(ProjectOpsInfo):
    force_py_install : bool = False
    skip_venv : bool = False
    cache: Optional['Cache'] = None
    cache_hits: int = 0
    cache_misses: int = 0
    total_packages: int = 0
    cacheable_packages: int = 0
    editable_packages: int = 0
    max_parallel: int = 0  # 0 means use available cores
    event_dispatcher: Optional[UpdateEventDispatcher] = None
    suppress_output: bool = False  # When True, suppress subprocess output (Rich TUI mode)
    _current_package_start: Optional[float] = None
    _current_package_name: Optional[str] = None
    _current_cache_hit: Optional[bool] = None
    
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
    
    def package_complete(self, name: str):
        """Signal that loading of a package has completed."""
        duration = None
        if self._current_package_start and self._current_package_name == name:
            duration = time.time() - self._current_package_start
        
        if self.event_dispatcher:
            event = UpdateEvent(
                event_type=UpdateEventType.PACKAGE_COMPLETE,
                package_name=name,
                duration=duration,
                cache_hit=self._current_cache_hit
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
                editable_packages=self.editable_packages
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



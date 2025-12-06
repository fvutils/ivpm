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
from typing import Optional

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
    
    def report_cache_hit(self):
        self.cache_hits += 1
    
    def report_cache_miss(self):
        self.cache_misses += 1
    
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
    
    def print_cache_summary(self):
        """Print a summary of cache statistics."""
        print("")
        print("Sub-Package Update Summary:")
        print("  Total packages: %d" % self.total_packages)
        print("  Cacheable packages: %d" % self.cacheable_packages)
        print("  Editable packages: %d" % self.editable_packages)
        if self.cacheable_packages > 0:
            print("  Cache hits: %d" % self.cache_hits)
            print("  Cache misses: %d" % self.cache_misses)
            hit_rate = (self.cache_hits / self.cacheable_packages * 100) if self.cacheable_packages > 0 else 0
            print("  Hit rate: %.1f%%" % hit_rate)



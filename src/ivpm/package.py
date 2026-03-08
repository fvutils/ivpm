#****************************************************************************
#* package.py
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
#* Created on: Jun 8, 2021
#*     Author: mballance
#*
#****************************************************************************

import logging
import os
import dataclasses as dc
from enum import Enum, auto
from typing import Dict, List, Set, Optional, Tuple
from .project_ops_info import ProjectUpdateInfo
from .utils import fatal, getlocstr

_logger = logging.getLogger("ivpm.package")

class PackageType(Enum):
    Raw = auto()
    Python = auto()
    Unknown = auto()
    
PackageType2Spec = {
    PackageType.Raw     : "raw",
    PackageType.Python  : "python",
    PackageType.Unknown : "unknown"
    }

Spec2PackageType = {
    "raw"     : PackageType.Raw,
    "python"  : PackageType.Python,
    "unknown" : PackageType.Unknown
    }
    
class SourceType(Enum):
    Git = auto()
    Jar = auto()
    Tgz = auto()
    Txz = auto()
    Zip = auto()
    PyPi = auto()

Ext2SourceType = {
        ".git" : SourceType.Git,
        ".jar" : SourceType.Jar,
        ".tar.gz" : SourceType.Tgz,
        ".tar.xz" : SourceType.Txz,
        ".tgz" : SourceType.Tgz,
        ".zip" : SourceType.Zip
        }

SourceType2Ext = {
        SourceType.Git : ".git",
        SourceType.Jar : ".jar",
        SourceType.Tgz : ".tar.gz",
        SourceType.Txz : ".tar.xz",
        SourceType.Zip : ".zip"
    }

SourceType2Spec = {
        SourceType.Git  : "git",
        SourceType.Jar  : "jar",
        SourceType.Tgz  : "tgz",
        SourceType.Txz  : "txz",
        SourceType.Zip  : "zip",
        SourceType.PyPi : "pypi",
    }
Spec2SourceType = {
        "git"  : SourceType.Git,
        "jar"  : SourceType.Jar,
        "tgz"  : SourceType.Tgz,
        "txz"  : SourceType.Txz,
        "zip"  : SourceType.Zip,
        "pypi" : SourceType.PyPi
    }

@dc.dataclass
class Package(object):
    """Contains leaf-level information about a single package"""
    name : str
    srcinfo : object = None
    path : str = None
    pkg_type : PackageType = None
    src_type : str = None
    # type_data holds the list of validated TypeData objects produced from the 'type:' field.
    # Set by IvpmYamlReader; empty list means no explicit type (auto-detection may still apply).
    type_data : List['TypeData'] = dc.field(default_factory=list)
    # self_types holds the raw (type_name, opts) pairs read from the package's own ivpm.yaml.
    # Populated during dep resolution by package_updater; empty until then.
    self_types : List[Tuple[str, dict]] = dc.field(default_factory=list)

    process_deps : bool = True
    setup_deps : Set[str] = dc.field(default_factory=set)
    dep_set : str = None
    proj_info : 'ProjInfo'= None
    # Track which package caused this dependency to be resolved.
    # None means it was resolved at the root level.
    resolved_by : str = None

    def build(self, pkgs_info):
        pass

    def status(self, pkgs_info):
        pass

    def sync(self, sync_info):
        from .pkg_sync import PkgSyncResult, SyncOutcome
        src = str(getattr(self, "src_type", "") or "non-vcs")
        return PkgSyncResult(
            name=self.name,
            src_type=src,
            path=os.path.join(sync_info.deps_dir, self.name),
            outcome=SyncOutcome.SKIPPED,
            skipped_reason=src,
        )

    def update(self, update_info : ProjectUpdateInfo) -> 'ProjInfo':
        from .proj_info import ProjInfo

        # Report this package for cache statistics (base packages are not cacheable)
        update_info.report_package(cacheable=False)

        info = ProjInfo.mkFromProj(
            os.path.join(update_info.deps_dir, self.name))
        
        return info
    
    def process_options(self, opts, si):
        self.srcinfo = si

        if "dep-set" in opts.keys():
            _logger.debug("Using dep-set %s for package %s",
                opts["dep-set"], self.name)
            self.dep_set = opts["dep-set"]

        if "deps" in opts.keys():
            if opts["deps"] == "skip":
                self.process_deps = False
            else:
                fatal("Unknown value for 'deps': %s" % opts["deps"])

        # Set pkg_type from 'type:' for backward compatibility.
        # The authoritative typed data is stored in type_data by IvpmYamlReader.
        if "type" in opts.keys():
            from .pkg_content_type import parse_type_field
            pairs = parse_type_field(opts["type"])
            if pairs:
                first_name = pairs[0][0]
                if first_name in Spec2PackageType.keys():
                    self.pkg_type = Spec2PackageType[first_name]
    
    @staticmethod
    def mk(name, opts, si) -> 'Package':
        raise NotImplementedError()


def get_type_data(pkg: 'Package', cls):
    """Return the first TypeData entry in pkg.type_data that is an instance of cls, or None."""
    for td in pkg.type_data:
        if isinstance(td, cls):
            return td
    return None


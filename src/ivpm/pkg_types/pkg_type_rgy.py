#****************************************************************************
#* package_factory_rgy.py
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
import dataclasses as dc
from typing import Dict, List, Tuple, Callable, Union
from ..package import Package
from .package_dir import PackageDir
from .package_file import PackageFile
from .package_http import PackageHttp
from .package_gh_rls import PackageGhRls
from .package_git import PackageGit
from .package_pypi import PackagePyPi
from .package_url import PackageURL

@dc.dataclass
class PkgTypeRgy(object):
    _inst = None

    src2fact_m : Dict[str,Tuple[str, Callable]] = dc.field(default_factory=dict)

    def hasPkgType(self, src) -> bool:
        return src in self.src2fact_m.keys()
    
    def mkPackage(self, src, name, opts, si) -> Package:
        return self.src2fact_m[src][0](name, opts, si)
    
    def getSrcTypes(self):
        return self.src2fact_m.keys()

    def register(self, src: str, f: Callable, info: Union[str, 'PkgSourceInfo'] = "", origin: str = "built-in"):
        """Register a package source factory.

        ``info`` may be a bare description string (backward-compatible) or a
        ``PkgSourceInfo`` instance.  When a string is given it is auto-wrapped into a
        minimal ``PkgSourceInfo`` with no parameter documentation.  ``origin`` is
        recorded in the info object and shown by ``ivpm show source`` to indicate
        whether the source is built-in or from a plugin entry point.
        """
        from ..show.info_types import PkgSourceInfo
        if src in self.src2fact_m.keys():
            raise Exception("Duplicate registration of src %s ; this type=%s ; original type=%s" % (
                            src,
                            str(f),
                            str(self.src2fact_m[src])))
        if isinstance(info, str):
            info = PkgSourceInfo(name=src, description=info, origin=origin)
        else:
            info.origin = origin
        self.src2fact_m[src] = (f, info)

    def getSourceInfo(self, src: str) -> 'PkgSourceInfo':
        """Return the PkgSourceInfo for a registered source type."""
        return self.src2fact_m[src][1]

    def getAllSourceInfo(self) -> List['PkgSourceInfo']:
        """Return PkgSourceInfo for all registered source types, in registration order."""
        return [v[1] for v in self.src2fact_m.values()]

    def _load(self):
        self.register("dir",    PackageDir.create,   PackageDir.source_info())
        self.register("file",   PackageFile.create,  PackageFile.source_info())
        self.register("http",   PackageHttp.create,  PackageHttp.source_info())
        self.register("git",    PackageGit.create,   PackageGit.source_info())
        self.register("pypi",   PackagePyPi.create,  PackagePyPi.source_info())
        self.register("url",    PackageURL.create,   PackageURL.source_info())
        self.register("gh-rls", PackageGhRls.create, PackageGhRls.source_info())

    @classmethod
    def inst(cls):
        if cls._inst is None:
            cls._inst = PkgTypeRgy()
            cls._inst._load()
        return cls._inst


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
from typing import Dict, Tuple, Callable
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
        print("name: %s" % name)
        return self.src2fact_m[src][0](name, opts, si)
    
    def getSrcTypes(self):
        return self.src2fact_m.keys()

    def register(self, src, f, description=""):
        if src in self.src2fact_m.keys():
            raise Exception("Duplicate registration of src %s ; this type=%s ; original type=%s" % (
                            src,
                            str(f),
                            str(self.src2fact_m[src])))
        self.src2fact_m[src] = (f, description)

    def _load(self):
        self.register("dir", PackageDir.create, "Directory")
        self.register("file", PackageFile.create, "File")
        self.register("http", PackageHttp.create, "Http")
        self.register("git", PackageGit.create, "Git")
        self.register("pypi", PackagePyPi.create, "PyPi")
        self.register("url", PackageURL.create, "URL")
        self.register("gh-rls", PackageGhRls.create, "Github Release")

    @classmethod
    def inst(cls):
        if cls._inst is None:
            cls._inst = PkgTypeRgy()
            cls._inst._load()
        return cls._inst


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
from typing import Dict
from .package_factory import PackageFactory
from .package_factory_dir import PackageFactoryDir
from .package_factory_file import PackageFactoryFile
from .package_factory_http import PackageFactoryHttp
from .package_factory_git import PackageFactoryGit
from .package_factory_pypi import PackageFactoryPyPi
from .package_factory_url import PackageFactoryURL

@dc.dataclass
class PackageFactoryRgy(object):
    _inst = None

    src2fact_m : Dict[str,PackageFactory] = dc.field(default_factory=dict)

    def hasFactory(self, src) -> bool:
        return src in self.src2fact_m.keys()
    
    def getFactory(self, src) -> PackageFactory:
        return self.src2fact_m[src]

    def register(self, f : PackageFactory):
        if f.src in self.src2fact_m.keys():
            raise Exception("Duplicate registration of src %s ; this type=%s ; original type=%s" % (
                            f.src,
                            str(f),
                            str(self.src2fact_m[f.src])))
        self.src2fact_m[f.src] = f

    def _load(self):
        self.register(PackageFactoryDir)
        self.register(PackageFactoryFile)
        self.register(PackageFactoryHttp)
        self.register(PackageFactoryGit)
        self.register(PackageFactoryPyPi)
        self.register(PackageFactoryURL)

    @classmethod
    def inst(cls):
        if cls._inst is None:
            cls._inst = PackageFactoryRgy()
            cls._inst._load()
        return cls._inst


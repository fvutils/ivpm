#****************************************************************************
#* package_py_pi.py
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
from ..package import Package

@dc.dataclass
class PackagePyPi(Package):
    version : str = None

    def process_options(self, opts, si):
        super().process_options(opts, si)

        self.src_type = "pypi"
        
        if "version" in opts.keys():
            self.version = opts["version"]

    @staticmethod
    def create(name, opts, si) -> 'Package':
        pkg = PackagePyPi(name)
        pkg.process_options(opts, si)
        return pkg


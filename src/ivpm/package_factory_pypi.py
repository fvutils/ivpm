#****************************************************************************
#* package_factory_py_pi.py
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
from typing import Dict
from ivpm.package import Package
from .package_factory import PackageFactory
from .package_pypi import PackagePyPi

class PackageFactoryPyPi(PackageFactory):
    src = "pypi"
    description = "Fetch packages from the PyPi Python package repository"

    def process_options(self, p: Package, d: Dict, si):
        super().process_options(p, d, si)

        if "version" in d.keys():
            p = d["version"]

    def create(self, name, opts, si) -> PackagePyPi:
        p = PackagePyPi(name)
        self.process_options(p, opts, si)
        return p



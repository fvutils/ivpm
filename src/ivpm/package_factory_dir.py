#****************************************************************************
#* package_factory_dir.py
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
from .package import Package
from .package_dir import PackageDir
from .package_url import PackageURL
from .package_factory_url import PackageFactoryURL
from .package_file import PackageFile

class PackageFactoryDir(PackageFactoryURL):
    src = "dir"
    description = "Copies a directory"

    def process_options(self, p: PackageURL, d: Dict, si):
        super().process_options(p, d, si)
        if "link" in d.keys():
            p.link = bool(d["link"])

    def create(self, name, opts, si) -> Package:
        p = PackageDir(name)
        self.process_options(p, opts, si)
        return p

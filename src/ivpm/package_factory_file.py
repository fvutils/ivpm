#****************************************************************************
#* package_factory_file.py
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
import os
from typing import Dict
from .package_factory_url import PackageFactoryURL
from .package_file import PackageFile

class PackageFactoryFile(PackageFactoryURL):
    src = "file"
    description = "Core fetch fetcher"

    def process_options(self, p: PackageFile, d: Dict, si):
        super().process_options(p, d, si)


        if "src" in d.keys():
            p.src_type = d["src"]
        else:
            ext = os.path.splitext(p.url)
            if ext == ".tgz":
                p.src_type = ".tar.gz"
            else:
                p.src_type = os.path.splitext(p.url)[1]
                if p.src_type in [".gz", ".xz", ".bz2"]:
                    pdot = p.url.rfind('.')
                    pdot = p.url.rfind('.', 0, pdot-1)
                    p.src_type = p.url[pdot:]

        if "unpack" in d.keys():
            p.unpack = d["unpack"]
        else:
            if p.src_type in [".jar"]:
                p.unpack = False
            else:
                p.unpack = True

    def create(self, name, opts, si) -> PackageFile:
        p = PackageFile(name)
        self.process_options(p, opts, si)
        return p

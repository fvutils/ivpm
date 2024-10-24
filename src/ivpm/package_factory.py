#****************************************************************************
#* package_factory.py
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
from .package import Package, Spec2PackageType, PackageType, SourceType
from .utils import fatal, note, getlocstr

class PackageFactory(object):
    src : str = None
    description : str = None

    def process_options(self, p : Package, d : Dict, si):
        p.srcinfo = si

        if "dep-set" in d.keys():
            p.dep_set = d["dep-set"]

        if "deps" in d.keys():
            if d["deps"] == "skip":
                p.process_deps = False
            else:
                fatal("Unknown value for 'deps': %s" % d["deps"])

        # Determine the package type (eg Python, Raw)
        if "type" in d.keys():
            type_s = d["type"]
            if not type_s in Spec2PackageType.keys():
                fatal("unknown package type %s @ %s ; Supported types 'raw', 'python'" % (
                    type_s, getlocstr(d["type"])))
                    
            p.pkg_type = Spec2PackageType[type_s]

    def create(self, name, opts, si) -> Package:
        p = Package(name)
        self.process_options(p, opts, si)
        return p


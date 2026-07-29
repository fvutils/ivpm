#****************************************************************************
#* packages_info.py
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
#* Created on: Jan 19, 2020
#*     Author: mballance
#*
#****************************************************************************
from typing import Dict, List, Optional, Set
from ivpm.package import Package

class PackagesInfo():
    """
    Holds information about a set of packages. Holds data
    from one dep-set in an IVPM file.
    """
    
    def __init__(self, name):
        self.name = name
        # Optional one-line summary from the dep-set's 'description'
        self.description : Optional[str] = None
        # Optional explicit classification ("package" | "collection") from the
        # dep-set's 'kind'. None -> inferred from dep count / name / 'uses'.
        self.kind : Optional[str] = None
        # Names of dep-set(s) in the same file to inherit packages from.
        # Populated during parsing (always a list, even for a single base);
        # resolved (merged) before use.
        self.uses : Optional[List[str]] = None
        self.packages : Dict[str,Package] = {}
        # Raw 'with:' dict declared on this dep-set (None if absent). Merged with
        # the package-level 'with:' when this dep-set is the selected install
        # target. Kept raw so the effective config is computed (merged + parsed)
        # once the install target is known.
        self.with_raw : Optional[dict] = None

        # Map of package name to set of packages
        # required for setup. This is Python-specific
        self.setup_deps : Dict[str, Set[str]] = {}
        self.options = {}

    def keys(self):
        return self.packages.keys()
    
    def add_package(self, pkg : Package):
        self.packages[pkg.name] = pkg

    def get_options(self, package):
        if package in self.options.keys():
            return self.options[package]
        else:
            return {}

    def set_options(self, package, options):
        self.options[package] = options

    def pop(self, key):
        self.packages.pop(key)

    def __getitem__(self, key):
        return self.packages[key]

    def __setitem__(self, key, value):
        self.packages[key] = value
        
    def copy(self) -> 'PackagesInfo':
        ret = PackagesInfo(self.name)
        ret.uses     = self.uses
        ret.packages = self.packages.copy()
        ret.options  = self.options.copy()
        ret.with_raw = self.with_raw

        return ret

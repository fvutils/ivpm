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
from typing import Dict, List, Set
from ivpm.package import Package

class PackagesInfo():
    """
    Holds information about a set of packages. Holds data
    from one dep-set in an IVPM file.
    """
    
    def __init__(self, name):
        self.name = name
        self.packages : Dict[str,Package] = {}
        
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
        ret.packages = self.packages.copy()
        ret.options  = self.options.copy()
        
        return ret

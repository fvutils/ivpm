'''
Created on Jan 19, 2020

@author: ballance
'''
from typing import Dict, List, Set
from ivpm.package import Package

class PackagesInfo():
    def __init__(self):
        self.packages : Dict[str,Package] = {}
        # Map of package name to set of packages
        # required for setup. This is Python-specific
        self.setup_deps : Dict[str, Set[str]] = {}
        self.options = {}

    def keys(self):
        return self.packages.keys()
    
    def add_package(self, pkg):
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
        ret = PackagesInfo()
        ret.packages = self.packages.copy()
        ret.options  = self.options.copy()
        
        return ret

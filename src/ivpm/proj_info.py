'''
Created on Jan 19, 2020

@author: ballance
'''
from enum import Enum, auto
from ivpm.packages_info import PackagesInfo
from typing import Dict, List

class ProjInfo():
    def __init__(self, is_src):
        self.is_src = is_src
        self.dependencies = []
        self.is_legacy = False

        # Dep-set to use when loading sub-dependencies
        self.target_dep_set = "default"
        self.dep_set_m : Dict[str,PackagesInfo] = {}
        self.setup_deps = set()
        
        self.ivpm_info = {}
        self.requirements_txt = None
        self.name = None
        self.version = None
        self.process_deps = True
        self.paths : Dict[str, Dict[str, List[str]]] = {}

    def has_dep_set(self, name):
        return name in self.dep_set_m.keys()
            
    def get_dep_set(self, name):
        return self.dep_set_m[name]
    
    def get_target_dep_set(self):
        if self.target_dep_set is None:
            raise Exception("target_dep_set is not specified")
        if self.target_dep_set not in self.dep_set_m.keys():
            raise Exception("Dep-set %s is not present in project %s" % (
                self.target_dep_set, self.name))
        return self.dep_set_m[self.target_dep_set]
    
    def set_dep_set(self, name, ds):
        self.dep_set_m[name] = ds

    def add_dependency(self, dep):
        self.dependencies.append(dep)

#    @property        
#    def deps(self):
#        return self.dependencies
'''
Created on Jan 19, 2020

@author: ballance
'''
from enum import Enum, auto
from ivpm.packages_info import PackagesInfo
from typing import Dict

class ProjInfo():
    def __init__(self, is_src):
        self.is_src = is_src
        self.dependencies = []
        self.is_legacy = False

        self.dep_set_m : Dict[str,PackagesInfo] = {}
        self.setup_deps = set()
        
        self.ivpm_info = {}
        self.requirements_txt = None
        self.name = None
        self.version = None
        self.process_deps = True

    def has_dep_set(self, name):
        return name in self.dep_set_m.keys()
            
    def get_dep_set(self, name):
        return self.dep_set_m[name]
    
    def set_dep_set(self, name, ds):
        self.dep_set_m[name] = ds

    def add_dependency(self, dep):
        self.dependencies.append(dep)

#    @property        
#    def deps(self):
#        return self.dependencies
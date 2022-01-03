'''
Created on Jan 19, 2020

@author: ballance
'''
from enum import Enum, auto
from ivpm.packages_info import PackagesInfo

class ProjInfo():
    def __init__(self, is_src):
        self.is_src = is_src
        self.dependencies = []
        self.is_legacy = False
        
        self.setup_deps = set()
        self.deps = PackagesInfo()
        self.dev_deps = PackagesInfo()
        
        self.ivpm_info = {}
        self.requirements_txt = None
        self.name = None
        self.version = None
        self.process_deps = True

    def add_dependency(self, dep):
        self.dependencies.append(dep)
        
    def deps(self):
        return self.dependencies
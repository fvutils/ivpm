'''
Created on Jan 19, 2020

@author: ballance
'''

class ProjInfo():
    def __init__(self, is_src):
        self.is_src = is_src
        self.dependencies = []
        self.ivpm_info = {}

    def add_dependency(self, dep):
        self.dependencies.append(dep)
        
    def deps(self):
        return self.dependencies
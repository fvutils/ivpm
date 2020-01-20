'''
Created on Jan 19, 2020

@author: ballance
'''

class PackagesInfo():
    def __init__(self):
        self.packages = {}
        self.options = {}

    def keys(self):
        return self.packages.keys()

    def get_options(self, package):
        if package in self.options.keys():
          return self.options[package]
        else:
          return {}

    def set_options(self, package, options):
        self.options[package] = options

    def __getitem__(self, key):
        return self.packages[key]

    def __setitem__(self, key, value):
        self.packages[key] = value
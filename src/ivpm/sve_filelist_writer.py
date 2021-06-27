'''
Created on Jun 27, 2021

@author: mballance
'''
from ivpm.packages_info import PackagesInfo
from ivpm.out_wrapper import OutWrapper

class SveFilelistWriter(object):
    
    def __init__(self, out : OutWrapper):
        self.out = out
        pass
    
    def write(self, pkgs_info : PackagesInfo):
        pass
'''
Created on Jun 27, 2021

@author: mballance
'''
import os

from ivpm.out_wrapper import OutWrapper
from ivpm.package import Package
from ivpm.packages_info import PackagesInfo


class SveFilelistWriter(object):
    
    def __init__(self, out : OutWrapper):
        self.out = out
        pass
    
    def write(self, pkgs_info : PackagesInfo):
        
        for key in pkgs_info.packages.keys():
            pkg : Package = pkgs_info.packages[key]
            
            if pkg.path is not None and os.path.isfile(os.path.join(pkg.path, "sve.F")):
                self.out.println("-F ./%s/sve.F", os.path.basename(pkg.path))
                
'''
Created on Jun 27, 2021

@author: mballance
'''
from ivpm.out_wrapper import OutWrapper
from ivpm.proj_info import ProjInfo
from ivpm.package import PackageType, PackageType2Spec, SourceType,\
    SourceType2Spec

class IvpmYamlWriter(object):
    
    def __init__(self, out : OutWrapper):
        self.out = out
        
    def write(self, info : ProjInfo):
        self.out.println("package:")
        self.out.inc_indent()
        self.out.println("name: %s", info.name)
        self.out.println("version: %s", info.version)
        
        self.out.println("deps:")
        self.out.inc_indent()
        for key in info.deps.keys():
            self.write_dep(info.deps[key])
        self.out.dec_indent()
        
        self.out.println("dev-deps:")
        self.out.inc_indent()
        for key in info.dev_deps.keys():
            self.write_dep(info.dev_deps[key])
        self.out.dec_indent()

    def write_dep(self, pkg):
        self.out.println("- name: %s", pkg.name)
        if pkg.url is not None:
            self.out.println("  url: %s", pkg.url)
        if pkg.pkg_type != PackageType.Unknown:
            self.out.println("  type: %s", PackageType2Spec[pkg.pkg_type])
        if pkg.src_type in (SourceType.PyPi,):
            self.out.println("  src: %s", SourceType2Spec[pkg.src_type])
        if pkg.version is not None:
            self.out.println("  version: %s", pkg.version)
            
        pass
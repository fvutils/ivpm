
import os
import sys
from setuptools import setup as _setup
from .build_ext import BuildExt
from .install_lib import InstallLib

def setup(*args, **kwargs):
    
    if "BUILD_NUM" in os.environ.keys() and "version" in kwargs:
        kwargs["version"] += ".%s" % os.environ["BUILD_NUM"]
    
    if "cmdclass" in kwargs:
        cmdclass = kwargs["cmdclass"]
    else:
        cmdclass = {}
        kwargs["cmdclass"] = cmdclass
    
    if "build_ext" in cmdclass.keys():
        print("Warning: build_ext is overridden")
    else:
        cmdclass["build_ext"] = BuildExt
    
    if "install_lib" in cmdclass.keys():
        print("Warning: install_lib is overridden")
    else:
        cmdclass["install_lib"] = InstallLib
    
    print("ivpm.build.setup")
    if "ext_modules" in kwargs:
        print("ext_modules")
        for ext in kwargs["ext_modules"]:
            if hasattr(ext, "package_deps"):
                print("package_deps")
    _setup(*args, **kwargs)


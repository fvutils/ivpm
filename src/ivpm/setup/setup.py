
import os
import sys
from setuptools import setup as _setup
from .build_ext import BuildExt
from .install_lib import InstallLib
import inspect

_ivpm_extra_data = {}

def get_ivpm_extra_data():
    global _ivpm_extra_data
    return _ivpm_extra_data

_package_dir = {}
def get_package_dir():
    return _package_dir


def setup(*args, **kwargs):
    global _ivpm_extra_data

    stack = inspect.stack()
    caller = stack[1][0]

    if "-DDEBUG" in sys.argv:
        debug = True
        sys.argv.remove("-DDEBUG")
    else:
        debug = False

    if "ivpm_extra_data" in kwargs.keys():
        _ivpm_extra_data = kwargs["ivpm_extra_data"]
        kwargs.pop("ivpm_extra_data")

    project_dir = os.path.dirname(os.path.abspath(
        inspect.getmodule(caller).__file__))

    if os.path.isdir(os.path.join(project_dir, 'packages')):
        packages_dir = os.path.join(project_dir, 'packages')
    else:
        packages_dir = os.path.join(project_dir, '../packages')

        if not os.path.isdir(packages_dir):
            raise Exception("Failed to locate packages directory")

    
#    if "BUILD_NUM" in os.environ.keys() and "version" in kwargs:
#        kwargs["version"] += ".%s" % os.environ["BUILD_NUM"]
    
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


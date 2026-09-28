#****************************************************************************
#* IVPM __init__.py
#*
#* Copyright 2018-2023 Matthew Ballance and Contributors
#*
#* Licensed under the Apache License, Version 2.0 (the "License"); you may 
#* not use this file except in compliance with the License.  
#* You may obtain a copy of the License at:
#*
#*   http://www.apache.org/licenses/LICENSE-2.0
#*
#* Unless required by applicable law or agreed to in writing, software 
#* distributed under the License is distributed on an "AS IS" BASIS, 
#* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.  
#* See the License for the specific language governing permissions and 
#* limitations under the License.
#*
#* Created on:
#*     Author: 
#*
#****************************************************************************
import importlib
import os

# Note: ivpm.setup is intentionally *not* imported here. It depends on
# setuptools (a build-time dependency), which is not required to run the
# ivpm CLI and is not part of requirements.txt. Importing it eagerly breaks
# `python -m ivpm` in a minimal venv (e.g. bootstrap.sh). Projects that need
# the setup wrapper import it directly via `from ivpm.setup import setup`.

# The public names below are imported on first use (PEP 562), not here, for
# the same reason: importing 'ivpm' -- which every 'ivpm.<submodule>' import
# does first -- must stay cheap and dependency-free. The 'agent.skills' entry
# point loads ivpm.skills in whatever environment is being queried; pulling in
# PyYAML and the package model there would make IVPM's own skill vanish from
# any environment that lacks them.
_LAZY = {
    "PkgCompileFlags": ".pkg_info.pkg_compile_flags",
    "PkgInfo": ".pkg_info.pkg_info",
    "PkgInfoRgy": ".pkg_info.pkg_info_rgy",
    "load_project_package_info": ".utils",
    "Package": ".package",
    "ProjectUpdateInfo": ".project_ops_info",
    "UpdateListener": ".update_listener",
    "DefaultUpdateListener": ".update_listener",
    "PackageUpdateEvent": ".update_listener",
}

__all__ = sorted(_LAZY) + ["get_pkg_info", "get_pkg_version"]


def __getattr__(name):
    modname = _LAZY.get(name)
    if modname is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    value = getattr(importlib.import_module(modname, __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY))


def get_pkg_version(setup_py_path):
    """Returns the package version based on the etc/ivpm.info file"""
    rootdir = os.path.dirname(os.path.realpath(setup_py_path))

    version=None
    with open(os.path.join(rootdir, "etc", "ivpm.info"), "r") as fp:
        while True:
            l = fp.readline()
            if l == "":
                break
            if l.find("version=") != -1:
                version=l[l.find("=")+1:].strip()
                break

    if version is None:
        raise Exception("Failed to find version in ivpm.info")

    if "BUILD_NUM" in os.environ.keys():
        version += "." + os.environ["BUILD_NUM"]

    return version

def get_pkg_info(name):
    from .pkg_info.pkg_info_loader import PkgInfoLoader
    if isinstance(name, list):
        return PkgInfoLoader().load_pkgs(name)
    else:
        return PkgInfoLoader().load(name)


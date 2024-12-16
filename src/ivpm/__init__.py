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
import os
from .ivpm_subprocess import ivpm_popen
from .pkg_info.pkg_compile_flags import PkgCompileFlags
from .pkg_info.pkg_info import PkgInfo
from .pkg_info.pkg_info_rgy import PkgInfoRgy
from .utils import load_project_package_info
import ivpm.setup

from .package import Package
from .project_ops_info import ProjectUpdateInfo

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


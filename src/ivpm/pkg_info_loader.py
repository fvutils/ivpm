#****************************************************************************
#* pkg_info_loader.py
#*
#* Copyright 2022 Matthew Ballance and Contributors
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
import typing
from .pkg_info import PkgInfo

class PkgInfoLoader(object):

    def __init__(self):
        self._pkg_info_m = {}
        pass

    def load(self, root_pkg) -> PkgInfo:
        self._pkg_info_m.clear()

        pi = self._load_pkg(root_pkg)

        return pi
    
    def load_pkgs(self, root_pkgs) -> typing.List[PkgInfo]:
        ret = []
        self._pkg_info_m.clear()

        for p in root_pkgs:
            if p in self._pkg_info_m.keys():
                ret.append(self._pkg_info_m[p])
            else:
                ret.append(self._load_pkg(p))

        return ret


    def _load_pkg(self, name):
        pkg = importlib.import_module(name)

        if hasattr(pkg, "pkginfo"):
            # Load the dedicated class
            pkginfo_m = getattr(pkg, "pkginfo")
            if not hasattr(pkginfo_m, "PkgInfo"):
                raise Exception("Failed to find pkginfo:PkgInfo in %s" % name)
            
            pkg_info = getattr(pkginfo_m, "PkgInfo")()

            for dep in pkg_info.getDeps():
                pass
        else:
            pkg_info = PkgInfo(name)

            deps = []
            if hasattr(pkg, "get_deps"):
                deps.extend(pkg.get_deps())

            if hasattr(pkg, "get_libs"):
                pkg_info._libs.extend(pkg.get_libs())

            if hasattr(pkg, "get_libdirs"):
                pkg_info._libdirs.extend(pkg.get_libdirs())

            if hasattr(pkg, "get_incdirs"):
                pkg_info._incdirs.extend(pkg.get_incdirs())

            self._pkg_info_m[name] = pkg_info

            for d in deps:
                if d not in self._pkg_info_m.keys():
                    dep = self._load_pkg(d)
                    self._pkg_info_m[d] = dep
                    pkg_info._deps.append(dep)
                else:
                    pkg_info._deps.append(self._pkg_info_m[d])

        return pkg_info




#****************************************************************************
#* pkg_info_rgy.py
#*
#* Copyright 2023 Matthew Ballance and Contributors
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
import sys
import importlib
from typing import Callable

class PkgInfoRgy(object):

    _inst = None

    def __init__(self):
        self.info_m = {}
        pass

    def load(self):
        if sys.version_info < (3,10):
            from importlib_metadata import entry_points
        else:
            from importlib.metadata import entry_points

        plugins = entry_points(group='ivpm.pkginfo')

        for p in plugins:
            try:
                ext_t = p.load()
                ext = ext_t()
                if ext.name not in self.info_m.keys():
                    self.info_m[ext.name] = ext
                else:
                    raise Exception("Duplicate package %s" % ext.name)
            except Exception as e:
                print("IVPM: failed to load plugin (%s)" % str(e))
            
        # Finally, iterate through the path looking for leftovers (?)
        for path in sys.path:
            if os.path.isdir(path):
                for pkg in os.listdir(path):
                    if os.path.isfile(os.path.join(path, pkg, "pkginfo.py")):
                        try:
                            pkginfo_m = importlib.import_module("%s.pkginfo" % pkg)
                        except ModuleNotFoundError as e:
                            sys.stderr.write("Note: package @ %s has pkginfo.py, but cannot load %s.pkginfo (%s)\n" % (path, pkg, str(e)))
                            continue
                        if not hasattr(pkginfo_m, "PkgInfo"):
                            sys.stderr.write("Note: pkginfo (%s) module exists, but is missing PkgInfo\n", os.path.join(path, pkg))
                            continue
                        pkginfo = getattr(pkginfo_m, "PkgInfo")()
                        if pkginfo.name not in self.info_m.keys():
                            self.info_m[pkginfo.name] = pkginfo

    def hasPkg(self, pkg):
        return pkg in self.info_m.keys()
    
    def getPkgs(self):
        return self.info_m.keys()

    def getPkg(self, pkg):
        if pkg in self.info_m.keys():
            return self.info_m[pkg]
        else:
            raise Exception("Package %s is not present" % pkg)

    def getIncDirs(self, kind=None, filter : Callable=None):
        incdirs = []

        for pkg_id in self.info_m.keys():
            pkg = self.info_m[pkg_id]
            if filter is not None and not filter(pkg.name):
                continue
            pkg_incdirs = pkg.getIncDirs()

            if pkg_incdirs is not None:
                for ld in pkg_incdirs:
                    if ld not in incdirs:
                        incdirs.append(ld)

        return incdirs
        
    def getLibDirs(self, kind=None, filter : Callable=None):
        libdirs = []

        for pkg_id in self.info_m.keys():
            pkg = self.info_m[pkg_id]
            if filter is not None and not filter(pkg.name):
                continue
            pkg_libdirs = pkg.getLibDirs(kind)

            if pkg_libdirs is not None:
                for ld in pkg_libdirs:
                    if ld not in libdirs:
                        libdirs.append(ld)

        return libdirs

    def getLibs(self, kind=None, filter : Callable=None):
        libs = []

        for pkg_id in self.info_m.keys():
            pkg = self.info_m[pkg_id]
            if filter is not None and not filter(pkg.name):
                continue
            pkg_libs = pkg.getLibs(kind)

            if pkg_libs is not None:
                for lib in pkg_libs:
                    if lib not in libs:
                        libs.append(lib)

        return libs

    def getPaths(self, kind):
        ret = []
        for name,pkg in self.info_m.items():
            ret.extend(pkg.getPaths(kind))
        return ret

    @classmethod
    def inst(cls):
        if cls._inst is None:
            cls._inst = PkgInfoRgy()
            cls._inst.load()
        return cls._inst



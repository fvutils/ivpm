#****************************************************************************
#* pkg_compile_flags.py
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
from .pkg_info import PkgInfo

class PkgCompileFlags(object):

    def __init__(self):
        self._processed_pkgs = set()
        pass

    def cflags(self, pkg : PkgInfo):
        ret = []
        self._processed_pkgs.clear()
        for p in pkg:
            self._collect_cflags(ret, p)

        return ret

    def lflags(self, pkg : PkgInfo):
        ret = []
        self._processed_pkgs.clear()
        for p in pkg:
            self._collect_lflags(ret, p)

        return ret

    def flags(self, pkg):
        ret = []
        self._processed_pkgs.clear()
        for p in pkg:
            self._collect_cflags(ret, p)

        self._processed_pkgs.clear()
        for p in pkg:
            self._collect_lflags(ret, p)

        return ret

    def libs(self, pkg, kind):
        ret = []
        self._processed_pkgs.clear()
        for p in pkg:
            self._collect_libs(ret, p, kind)

        return ret

    def paths(self, pkg, kind):
        ret = []
        self._processed_pkgs.clear()
        for p in pkg:
            self._collect_paths(ret, p, kind)

        return ret
    
    def ldirs(self, pkg : PkgInfo, kind):
        ret = []
        self._processed_pkgs.clear()
        self._collect_ldirs(ret, pkg, kind)
        return ret

    def _collect_cflags(self, ret, pkg : PkgInfo):
        for i in pkg._incdirs:
            inc = "-I" + i
            if inc not in ret:
                ret.append(inc)

        for d in pkg._deps:
            if d._name not in self._processed_pkgs:
                self._processed_pkgs.add(d._name)
                self._collect_cflags(ret, d)

    def _collect_paths(self, ret, pkg : PkgInfo, kind):
        for path in pkg.getPaths(kind):
            if path not in ret:
                ret.append(path)

        for d in pkg._deps:
            if d._name not in self._processed_pkgs:
                self._processed_pkgs.add(d._name)
                self._collect_paths(ret, d, kind)

    def _collect_libs(self, ret, pkg : PkgInfo, kind):
        libs = pkg.getLibs(kind)
        for path in [] if libs is None else libs:
            if path not in ret:
                ret.append(path)

        for d in pkg._deps:
            if d._name not in self._processed_pkgs:
                self._processed_pkgs.add(d._name)
                self._collect_libs(ret, d, kind)

    def _collect_ldirs(self, ret, pkg : PkgInfo, kind):
        for d in pkg.getLibDirs(kind):
            if d not in ret:
                ret.append(d)

        for d in pkg._deps:
            if d._name not in self._processed_pkgs:
                self._processed_pkgs.add(d._name)
                self._collect_ldirs(ret, d, kind)

    def _collect_lflags(self, ret, pkg : PkgInfo):
        for d in pkg._libdirs:
            lpath = "-L" + d
            if lpath not in ret:
                ret.append(lpath)

        for l in pkg._libs:
            lpath = "-l" + l
            if lpath not in ret:
                ret.append(lpath)

        for d in pkg._deps:
            if d._name not in self._processed_pkgs:
                self._processed_pkgs.add(d._name)
                self._collect_lflags(ret, d)




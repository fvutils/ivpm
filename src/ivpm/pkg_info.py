#****************************************************************************
#* pkg_info.py
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
import platform

class PkgInfo(object):

    KIND_DPI = "dpi"
    KIND_VPI = "vpi"

    def __init__(self, name, path=None):
        self._name = name
        self._path = path
        self._deps = []
        self._incdirs = []
        self._libdirs = []
        self._libs = []
        self._libpref = "lib" if platform.system() != "Windows" else ""
        if platform.system() == "Windows":
            self._dllext = ".dll"
        elif platform.system() == "Darwin":
            self._dllext = ".dylib"
        else:
            self._dllext = ".so"

    @property
    def libpref(self):
        return self._libpref
    
    @property
    def dllext(self):
        return self._dllext

    @property
    def name(self):
        return self._name

    def getDeps(self):
        return self._deps

    def getPath(self):
        return self._path
    
    def getIncDirs(self):
        return self._incdirs
    
    def getLibDirs(self, kind=None):
        return self._libdirs
    
    def getLibs(self, kind=None):
        return self._libs
    
    def getPaths(self, kind):
        return []




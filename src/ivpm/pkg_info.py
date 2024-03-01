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

class PkgInfo(object):

    def __init__(self, name, path=None):
        self._name = name
        self._path = path
        self._deps = []
        self._incdirs = []
        self._libdirs = []
        self._libs = []

    @property
    def name(self):
        return self._name

    def getDeps(self):
        return self._deps

    def getPath(self):
        return self._path
    
    def getIncDirs(self):
        return self._incdirs
    
    def getLibDirs(self):
        return self._libdirs
    
    def getLibs(self):
        return self._libs
    
    def getPaths(self, kind):
        return []




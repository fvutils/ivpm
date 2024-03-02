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
import sys

class PkgInfoRgy(object):

    _inst = None

    def __init__(self):
        self.info_m = {}
        pass

    def load(self):
        if sys.version_info < (3, 10):
            from importlib_metadata import entry_points
        else:
            from importlib.metadata import entry_points

        plugins = entry_points(group='ivpm.pkginfo')

        print("plugins: %s" % str(plugins))

        for p in plugins:
            print("Plugin: %s" % str(p))
            ext_t = p.load()
            ext = ext_t()
            if ext.name not in self.info_m.keys():
                self.info_m[ext.name] = ext
            else:
                raise Exception("Duplicate package %s" % ext.name)

    def hasPkg(self, pkg):
        return pkg in self.info_m.keys()
    
    def getPkgs(self):
        return self.info_m.keys()

    def getPkg(self, pkg):
        if pkg in self.info_m.keys():
            return self.info_m[pkg]
        else:
            raise Exception("Package %s is not present" % pkg)

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



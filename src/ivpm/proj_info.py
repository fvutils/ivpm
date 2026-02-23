#****************************************************************************
#* proj_info.py
#*
#* Copyright 2018-2024 Matthew Ballance and Contributors
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
#* Created on: Jan 19, 2020
#*     Author: mballance
#*
#****************************************************************************
import os
import dataclasses as dc
from enum import Enum, auto
from ivpm.packages_info import PackagesInfo
from .ivpm_yaml_reader import IvpmYamlReader
from .msg import error, fatal, note
from typing import Dict, List, Optional
from .env_spec import EnvSpec


@dc.dataclass
class CacheConfig:
    """Settings parsed from the ``cache:`` block in ivpm.yaml."""
    backend: Optional[str] = None           # auto|filesystem|gha|none
    local_dir: Optional[str] = None         # override for local cache directory
    key_prefix: Optional[str] = None        # prefix for GHA cache keys
    include_python_venv: bool = True
    include_pip_cache: bool = True
    max_age_days: int = 30

class ProjInfo():
    """Holds information read about a project from its IVPM file"""
    def __init__(self, is_src):
        self.is_src = is_src
        self.dependencies = []
        self.is_legacy = False

        # Dep-set to use when loading sub-dependencies
        self.target_dep_set = "default"
        self.dep_set_m : Dict[str,PackagesInfo] = {}
        self.setup_deps = set()
        
        self.ivpm_info = {}
        self.requirements_txt = None

        self.name = None
        self.version = None
        # This should be set to the dep-set specified by the 'dep' 
        # statement or on the command-line
        self.default_dep_set = None
        self.deps_dir = "packages"

        self.process_deps = True
        self.paths : Dict[str, Dict[str, List[str]]] = {}
        self.env_settings : List[EnvSpec] = []
        self.cache_config : Optional[CacheConfig] = None

    def has_dep_set(self, name):
        return name in self.dep_set_m.keys()
            
    def get_dep_set(self, name):
        return self.dep_set_m[name]
    
    def get_target_dep_set(self):
        if self.target_dep_set is None:
            raise Exception("target_dep_set is not specified")
        if self.target_dep_set not in self.dep_set_m.keys():
            raise Exception("Dep-set %s is not present in project %s" % (
                self.target_dep_set, self.name))
        return self.dep_set_m[self.target_dep_set]
    
    def set_dep_set(self, name, ds):
        self.dep_set_m[name] = ds

    def add_dependency(self, dep):
        self.dependencies.append(dep)

    @staticmethod
    def mkFromProj(proj_dir : str) -> 'ProjInfo':
        ret : ProjInfo = None
        
        # First, see if this is a new-style project
        if os.path.isfile(os.path.join(proj_dir, "ivpm.yaml")):
            note("Reading ivpm.yaml from project %s" % proj_dir)
            path = os.path.join(proj_dir, "ivpm.yaml");
            with open(path, "r") as fp:
                ret = IvpmYamlReader().read(fp, path)
        else:
            # This doesn't appear to be an IVPM project
            # No IVPM-specific data to rely on here
            pass
        return ret

#    @property        
#    def deps(self):
#        return self.dependencies
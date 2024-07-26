#****************************************************************************
#* package_factory_git.py
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
from typing import Dict
from package import Package
from package_git import PackageGit
from .package_factory_url import PackageFactoryURL

class PackageFactoryGit(PackageFactoryURL):
    src = "git"
    description = "Package is hosted in a Git repository"

    def process_options(self, p: PackageGit, d: Dict, si):
        super().process_options(p, d, si)

        if "anonymous" in d.keys():
            p.anonymous = d["anonymous"]
                
        if "depth" in d.keys():
            p.depth = d["depth"]
                
        if "dep-set" in d.keys():
            p.dep_set = d["dep-set"]
               
        if "branch" in d.keys():
            p.branch = d["branch"]
                
        if "commit" in d.keys():
            p.commit = d["commit"]
               
        if "tag" in d.keys():
            p.tag = d["tag"]
    
    def create(self, name, opts, si) -> Package:
        p = PackageGit(name)
        self.process_options(p, opts, si)
        return p


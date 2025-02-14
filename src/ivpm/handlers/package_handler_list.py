#****************************************************************************
#* package_handler_list.py
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
import dataclasses as dc
from typing import List

from ivpm.package import Package
from .package_handler import PackageHandler

@dc.dataclass
class PackageHandlerList(PackageHandler):
    handlers : List[PackageHandler] = dc.field(default_factory=list)

    def addHandler(self, h):
        self.handlers.append(h)

    def process_pkg(self, pkg: Package):
        for h in self.handlers:
            h.process_pkg(pkg) 

    def build(self, build_info):
        for h in self.handlers:
            h.build(build_info) 

    def update(self, update_info):
        for h in self.handlers:
            h.update(update_info) 


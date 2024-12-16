#****************************************************************************
#* package_handler.py
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
from ..package import Package
from ..project_ops_info import ProjectUpdateInfo

@dc.dataclass
class PackageHandler(object):
    name : str = None
    description : str = None


    def process_pkg(self, pkg : Package):
        """Called each time a package description is added to the active set"""
        pass

    def update(self, update_info : ProjectUpdateInfo):
        """Called after an 'update' action completes"""
        pass


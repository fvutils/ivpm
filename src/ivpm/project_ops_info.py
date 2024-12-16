#****************************************************************************
#* project_opts_info.py
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
#* Created on:
#*     Author: 
#*
#****************************************************************************
import dataclasses as dc
import enum

class FileStatus(enum.Enum):
    Unknown = "U"
    Modified = "M"
    Added = "A"
    Deleted = "D"

@dc.dataclass
class ProjectOpsInfo(object):
    deps_dir : str

@dc.dataclass
class ProjectSyncInfo(ProjectOpsInfo):
    pass

@dc.dataclass
class ProjectStatusInfo(ProjectOpsInfo):
    pass

@dc.dataclass
class ProjectStatusResult(object):
    package : str
    path : str
    status : FileStatus

@dc.dataclass
class ProjectUpdateInfo(ProjectOpsInfo):
    anonymous_git : bool = False



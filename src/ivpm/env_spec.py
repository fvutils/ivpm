#****************************************************************************
#* env_spec.py
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
import enum
from typing import Any, Dict

class EnvSpec(object):

    class Act(enum.Enum):
        Set = enum.auto()
        Path = enum.auto()
        PathAppend = enum.auto()
        PathPrepend = enum.auto()

    def __init__(self,
                 var : str,
                 val : Any,
                 act : 'EnvSpec.Act'):
        self.var = var
        self.val = val
        self.act = act

    def apply(self, env : Dict[str,str]):
        if isinstance(self.val, list):
            for i,v in enumerate(self.val):
                self.val[i] = self.expand(v, env)
        else:
            self.val = self.expand(self.val, env)
                
        if self.act == EnvSpec.Act.Set:
            val = self.val
            if isinstance(val, list):
                val = " ".join(val)
            env[self.var] = val
        elif self.act == EnvSpec.Act.Path:
            val = self.val
            if isinstance(val, list):
                val = ":".join(val)
            env[self.var] = val
        elif self.act == EnvSpec.Act.PathAppend:
            val = self.val
            if isinstance(val, list):
                val = ":".join(val)
            if self.var in env.keys():
                env[self.var] = env[self.var] + ":" + val
            else:
                env[self.var] = val
        elif self.act == EnvSpec.Act.PathPrepend:
            val = self.val
            if isinstance(val, list):
                val = ":".join(val)
            if self.var in env.keys():
                env[self.var] = val + ":" + env[self.var]
            else:
                env[self.var] = val
        else:
            raise Exception("Unknown action: %s" % str(self.act))
        
    def expand(self, var, env):
        idx = 0
        while idx < len(var):
            idx1 = var.find('${', idx)

            if idx1 == -1:
                break
            idx2 = var.find('}', idx1)
            if idx2 == -1:
                idx = idx1+2
            else:
                key = var[idx1+2:idx2]
                if key in env.keys():
                    var = var[:idx1] + env[key] + var[idx2+1:]
                else:
                    idx = idx2+1
        return var


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
import enum
from typing import Any

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

    def as_direnv(self) -> str:
        """Render this spec as a single ``direnv``/bash directive for
        ``packages.envrc``.

        IVPM no longer applies environment variables itself; it emits
        ``direnv`` directives and lets ``direnv`` (via bash) apply them.
        ``${VAR}`` references are emitted verbatim and expanded by bash at
        ``direnv``-eval time -- there is no Python-side expansion.

        The mapping mirrors the four ``env:`` actions:

        ==============  ================================================
        Action          Emitted line
        ==============  ================================================
        ``value``       ``export VAR="val"``
        ``path``        ``export VAR="a:b:c"``
        ``path-prepend``  ``path_add VAR "a" "b"`` (direnv stdlib)
        ``path-append``   ``export VAR="${VAR:+$VAR:}a:b"``
        ==============  ================================================
        """
        if self.act == EnvSpec.Act.Set:
            val = " ".join(self.val) if isinstance(self.val, list) else self.val
            return 'export %s="%s"' % (self.var, val)
        elif self.act == EnvSpec.Act.Path:
            val = ":".join(self.val) if isinstance(self.val, list) else self.val
            return 'export %s="%s"' % (self.var, val)
        elif self.act == EnvSpec.Act.PathPrepend:
            # direnv stdlib path_add prepends its args (in order) to the
            # colon-list variable and de-duplicates on re-source.
            vals = self.val if isinstance(self.val, list) else [self.val]
            quoted = " ".join('"%s"' % v for v in vals)
            return "path_add %s %s" % (self.var, quoted)
        elif self.act == EnvSpec.Act.PathAppend:
            # No direnv stdlib append helper; emit an explicit bash form that
            # appends only a leading ':' separator when the var is already set.
            val = ":".join(self.val) if isinstance(self.val, list) else self.val
            return 'export %s="${%s:+$%s:}%s"' % (self.var, self.var, self.var, val)
        else:
            raise Exception("Unknown action: %s" % str(self.act))


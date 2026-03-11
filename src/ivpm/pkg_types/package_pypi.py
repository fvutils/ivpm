#****************************************************************************
#* package_py_pi.py
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

@dc.dataclass
class PackagePyPi(Package):
    version : str = None
    resolved_version : str = None  # actual installed version after pip install
    extras : list = None  # PEP 508 extras, e.g. ["litellm"] -> package[litellm]

    def process_options(self, opts, si):
        super().process_options(opts, si)

        self.src_type = "pypi"
        
        if "version" in opts.keys():
            self.version = opts["version"]

        if "extras" in opts.keys():
            raw = opts["extras"]
            if isinstance(raw, list):
                self.extras = [str(e) for e in raw]
            else:
                self.extras = [str(raw)]

    @staticmethod
    def create(name, opts, si) -> 'Package':
        pkg = PackagePyPi(name)
        pkg.process_options(opts, si)
        return pkg

    @classmethod
    def source_info(cls):
        from ..show.info_types import PkgSourceInfo, ParamInfo
        return PkgSourceInfo(
            name="pypi",
            description="Python Package Index (PyPI) — installed into the managed virtual environment",
            params=[
                ParamInfo("version", "PEP 440 version specifier (e.g. '>=1.20,<2')", default="(latest)"),
                ParamInfo("extras", "PEP 508 extras to install (e.g. [litellm, openai])", type_hint="str"),
            ],
            notes="The package name is taken from the dep entry 'name:' field.  PyPI packages "
                  "are not placed in packages/; they are installed directly into packages/python.",
        )


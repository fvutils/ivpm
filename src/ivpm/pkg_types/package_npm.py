#****************************************************************************
#* package_npm.py
#*
#* Copyright 2024 Matthew Ballance and Contributors
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
#****************************************************************************
import dataclasses as dc
from ..package import Package


@dc.dataclass
class PackageNpm(Package):
    """An npm registry package.

    Installed into ``packages/node/node_modules/`` by the node handler.
    The package does NOT land in ``packages/<name>/`` as a directory.
    """
    version: str = "*"
    dev: bool = False
    optional: bool = False

    def process_options(self, opts, si):
        super().process_options(opts, si)
        self.src_type = "npm"

        if "version" in opts:
            self.version = str(opts["version"])
        if "dev" in opts:
            self.dev = bool(opts["dev"])
        if "optional" in opts:
            self.optional = bool(opts["optional"])

    @staticmethod
    def create(name, opts, si) -> 'Package':
        pkg = PackageNpm(name)
        pkg.process_options(opts, si)
        return pkg

    @classmethod
    def source_info(cls):
        from ..show.info_types import PkgSourceInfo, ParamInfo
        return PkgSourceInfo(
            name="npm",
            description="npm registry package — installed into the managed node environment",
            params=[
                ParamInfo("version", "npm semver range or tag (e.g. '^5.4.0')", default="*"),
                ParamInfo("dev", "Install as devDependency", type_hint="bool", default="false"),
                ParamInfo("optional", "Install as optionalDependency", type_hint="bool", default="false"),
            ],
            notes="The package name is taken from the dep entry 'name:' field.  npm packages "
                  "are not placed in packages/; they are installed directly into packages/node.",
        )

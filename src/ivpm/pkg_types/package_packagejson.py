#****************************************************************************
#* package_packagejson.py
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
class PackagePackageJson(Package):
    """Virtual package source type that reads an existing ``package.json`` file.

    IVPM reads ``url`` (a ``file://`` path or resolved filesystem path to a
    ``package.json``) and synthesizes npm dep entries from its
    ``dependencies`` and ``devDependencies`` maps.  No directory is created
    under ``packages/``; the entries are injected into the current dep-set.

    Explicit IVPM entries always win on name collision.
    """
    url: str = None
    json_path: str = None

    def process_options(self, opts, si):
        super().process_options(opts, si)
        self.src_type = "package.json"

        if "url" in opts:
            self.url = str(opts["url"])
        if "path" in opts:
            self.json_path = str(opts["path"])

    @staticmethod
    def create(name, opts, si) -> 'Package':
        pkg = PackagePackageJson(name)
        pkg.process_options(opts, si)
        return pkg

    @classmethod
    def source_info(cls):
        from ..show.info_types import PkgSourceInfo, ParamInfo
        return PkgSourceInfo(
            name="package.json",
            description="Import npm dependencies from an existing package.json file",
            params=[
                ParamInfo("url", "file:// path to the package.json (supports ${PROJ_ROOT} variables)"),
                ParamInfo("path", "relative or absolute filesystem path to the package.json"),
            ],
            notes="IVPM reads 'dependencies' → non-dev and 'devDependencies' → dev npm packages. "
                  "Explicit IVPM dep entries always win on name collision.",
        )

#****************************************************************************
#* package_pyproject_toml.py
#*
#* Copyright 2025 Matthew Ballance and Contributors
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
class PackagePyprojectToml(Package):
    """Virtual source type that imports Python deps from a ``pyproject.toml``.

    No directory is created under ``packages/``; the harvested
    ``PackagePyPi`` entries are injected into the Python handler's
    install queue exactly as if they had been written inline.

    Explicit ``src: pypi`` entries always win on name collision.

    Supported ``include`` section names:

    * ``dependencies``                  — ``[project].dependencies``
    * ``optional-dependencies.<extra>`` — ``[project.optional-dependencies].<extra>``
    * ``dependency-groups.<group>``     — ``[dependency-groups].<group>`` (PEP 735)
    * ``all``                           — everything: runtime + all extras + all groups
    """
    url: str = None
    toml_path: str = None
    include: list = dc.field(default_factory=lambda: ["dependencies"])

    def process_options(self, opts, si):
        super().process_options(opts, si)
        self.src_type = "pyproject.toml"
        if "url" in opts:
            self.url = str(opts["url"])
        if "path" in opts:
            self.toml_path = str(opts["path"])
        if "include" in opts:
            raw = opts["include"]
            self.include = [raw] if isinstance(raw, str) else list(raw)

    @staticmethod
    def create(name, opts, si) -> 'Package':
        pkg = PackagePyprojectToml(name)
        pkg.process_options(opts, si)
        return pkg

    @classmethod
    def source_info(cls):
        from ..show.info_types import PkgSourceInfo, ParamInfo
        return PkgSourceInfo(
            name="pyproject.toml",
            description="Import Python dependencies from a pyproject.toml file",
            params=[
                ParamInfo(
                    "url",
                    "file:// path to the pyproject.toml "
                    "(supports ${PROJ_ROOT} and other IVPM variables)",
                    required=False,
                    type_hint="url",
                ),
                ParamInfo(
                    "path",
                    "relative or absolute filesystem path to the pyproject.toml",
                ),
                ParamInfo(
                    "include",
                    "Sections to import: 'dependencies', "
                    "'optional-dependencies.<extra>', "
                    "'dependency-groups.<group>' (PEP 735), or 'all'",
                    default="[dependencies]",
                ),
            ],
            notes=(
                "Harvested entries are injected as if written inline.  "
                "Explicit src: pypi entries always win on name collision.  "
                "Uses tomllib (Python >= 3.11) or tomli (older Python)."
            ),
        )

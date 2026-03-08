#****************************************************************************
#* pkg_content_type_rgy.py
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
#*
#****************************************************************************
import logging
from .pkg_content_type import PkgContentType, PythonContentType, RawContentType

_logger = logging.getLogger("ivpm.pkg_content_type_rgy")


class PkgContentTypeRgy:
    """Registry for package content types (what IVPM does with a package after fetching).

    Maps type names (e.g. 'python', 'raw') to PkgContentType instances.
    Third-party packages can extend this registry by calling register()
    before the first package YAML is read.

    Future: will also scan entry_points(group='ivpm.content_types').
    """

    _inst = None

    def __init__(self):
        self._types = {}

    def register(self, content_type: PkgContentType):
        name = content_type.name
        if name in self._types:
            raise Exception("Duplicate registration of content type '%s'" % name)
        _logger.debug("Registering content type: %s", name)
        self._types[name] = content_type

    def has(self, name: str) -> bool:
        return name in self._types

    def get(self, name: str) -> PkgContentType:
        return self._types[name]

    def names(self) -> list:
        return sorted(self._types.keys())

    def _load(self):
        self.register(PythonContentType())
        self.register(RawContentType())

        # Future: scan entry_points(group='ivpm.content_types') here
        try:
            from importlib.metadata import entry_points
            eps = entry_points(group="ivpm.content_types")
            for ep in eps:
                try:
                    ct = ep.load()
                    self.register(ct())
                    _logger.debug("Loaded content type from entry_point: %s", ep.name)
                except Exception as e:
                    _logger.warning("Failed to load content type entry_point '%s': %s", ep.name, e)
        except Exception:
            pass

    @classmethod
    def inst(cls) -> 'PkgContentTypeRgy':
        if cls._inst is None:
            cls._inst = PkgContentTypeRgy()
            cls._inst._load()
        return cls._inst

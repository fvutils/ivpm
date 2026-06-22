#****************************************************************************
#* package_handler_rgy.py
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
import logging
from .package_handler_list import PackageHandlerList

_logger = logging.getLogger("ivpm.handlers.package_handler_rgy")

class PackageHandlerRgy(object):

    _inst = None

    def __init__(self):
        self.handlers = []
        # handler class -> (origin, version, provider) recorded at registration time
        self._meta = {}

    def addHandler(self, h, origin: str = "built-in", version: str = None, provider: str = None):
        _logger.debug("Handler: %s", h.name)
        self.handlers.append(h)
        self._meta[h] = (origin, version, provider)

    def handler_info(self, cls) -> 'HandlerInfo':
        """Return cls.handler_info() with provenance and ordering info applied."""
        from .handler_order import normalize_phase
        info = cls.handler_info()
        # Normalize phase to a named phase (maps legacy ints) and surface the
        # relative ordering constraints regardless of what the handler's own
        # handler_info() populated.
        try:
            info.phase = normalize_phase(cls.phase)
        except Exception:
            info.phase = str(cls.phase)
        info.run_after = list(getattr(cls, "run_after", None) or [])
        info.run_before = list(getattr(cls, "run_before", None) or [])
        origin, version, provider = self._meta.get(cls, ("built-in", None, None))
        info.origin = origin
        if version is not None:
            info.version = version
        if provider is not None and info.provider is None:
            info.provider = provider
        return info

    def resolved_order(self) -> list:
        """Return registered handler classes in resolved root-phase order.

        This is the *static* order (no root_when filtering, since there is no
        run context). Used by 'ivpm show handler --order'.
        """
        from .handler_order import resolve_order
        instances = [h_t() for h_t in self.handlers]
        return [type(h) for h in resolve_order(instances)]

    def handler_infos(self) -> list:
        """Return HandlerInfo for all registered handlers, provenance applied."""
        return [self.handler_info(h) for h in self.handlers]

    def _load(self):
        # Discover handlers via entry points (built-ins registered via pyproject.toml)
        if sys.version_info < (3, 10):
            from importlib_metadata import entry_points
        else:
            from importlib.metadata import entry_points
        from ..show.info_types import ep_registration_kwargs

        seen = set()
        for ep in entry_points(group="ivpm.handlers"):
            if ep.value in seen:
                continue
            seen.add(ep.value)
            try:
                cls = ep.load()
                _logger.debug("Loaded handler '%s' from entry point", ep.name)
                self.addHandler(cls, **ep_registration_kwargs(ep))
            except Exception as e:
                _logger.warning("Failed to load handler '%s': %s", ep.name, e)

    def add_handler_options(self, subcommands: dict):
        """Call add_options() on each registered handler type. Pass as an options_ext item."""
        for h_t in self.handlers:
            try:
                h_t().add_options(subcommands)
            except Exception as e:
                _logger.warning("Handler '%s' raised error in add_options: %s", h_t.name, e)

    def mkHandler(self):
        h = PackageHandlerList()
        for h_t in self.handlers:
            h.addHandler(h_t())
        return h

    @classmethod
    def inst(cls) -> 'PackageHandlerRgy':
        if cls._inst is None:
            cls._inst = PackageHandlerRgy()
            cls._inst._load()
        return cls._inst


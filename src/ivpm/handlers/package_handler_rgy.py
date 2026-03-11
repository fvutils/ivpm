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

    def addHandler(self, h):
        _logger.debug("Handler: %s", h.name)
        self.handlers.append(h)

    def _load(self):
        # Discover handlers via entry points (built-ins registered via pyproject.toml)
        if sys.version_info < (3, 10):
            from importlib_metadata import entry_points
        else:
            from importlib.metadata import entry_points

        for ep in entry_points(group="ivpm.handlers"):
            try:
                cls = ep.load()
                _logger.debug("Loaded handler '%s' from entry point", ep.name)
                self.addHandler(cls)
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


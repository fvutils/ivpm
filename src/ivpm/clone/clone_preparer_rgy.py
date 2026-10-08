#****************************************************************************
#* clone_preparer_rgy.py
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
#*
#****************************************************************************
"""Registry of clone preparers.

Mirrors ``PackagePreparerRgy``: no built-ins, and a plugin that fails to load
is fatal rather than a warning -- a clone preparer that silently does not run
is a policy step skipped while the clone writes content anyway.
"""
import logging

from .clone_preparer_list import ClonePreparerList

_logger = logging.getLogger("ivpm.clone.clone_preparer_rgy")

CLONE_PREPARER_GROUP = "ivpm.clone_preparers"


class ClonePreparerLoadError(Exception):
    """A registered clone-preparer entry point could not be loaded."""


class ClonePreparerRgy(object):

    _inst = None

    def __init__(self):
        self.preparers = []
        # preparer class -> (origin, version, provider), recorded at registration
        self._meta = {}

    def addPreparer(self, cls, origin: str = "built-in", version: str = None,
                    provider: str = None):
        _logger.debug("Clone preparer: %s", getattr(cls, "name", cls.__name__))
        self.preparers.append(cls)
        self._meta[cls] = (origin, version, provider)

    def names(self):
        return [getattr(c, "name", None) or c.__name__ for c in self.preparers]

    def preparer_infos(self) -> list:
        """ClonePreparerInfo for every registered preparer, provenance applied."""
        infos = []
        for cls in self.preparers:
            info = cls.preparer_info()
            origin, version, provider = self._meta.get(
                cls, ("built-in", None, None))
            info.origin = origin
            if version is not None:
                info.version = version
            if provider is not None and info.provider is None:
                info.provider = provider
            infos.append(info)
        return infos

    def mkPreparerList(self) -> ClonePreparerList:
        """One instance per preparer, for one clone."""
        return ClonePreparerList([cls() for cls in self.preparers])

    # ------------------------------------------------------------------ #
    # Discovery                                                           #
    # ------------------------------------------------------------------ #

    def _load(self):
        # No built-ins by design -- see the module docstring.
        self._load_plugins()

    def _load_plugins(self):
        from .._compat import entry_points
        from ..show.info_types import ep_registration_kwargs

        seen = set()
        for ep in entry_points(group=CLONE_PREPARER_GROUP):
            if ep.value in seen:
                continue
            seen.add(ep.value)
            try:
                cls = ep.load()
            except Exception as e:
                raise ClonePreparerLoadError(
                    "Failed to load clone preparer '%s' (%s): %s\n"
                    "  A clone preparer checks or prepares where a workspace "
                    "is created, so IVPM will not clone without it. Fix or "
                    "uninstall the providing distribution."
                    % (ep.name, ep.value, e)) from e
            if cls in self._meta:
                _logger.debug("clone preparer '%s' already registered; "
                              "skipping", ep.name)
                continue
            _logger.debug("Loaded clone preparer '%s' from entry point", ep.name)
            self.addPreparer(cls, **ep_registration_kwargs(ep))

    @classmethod
    def inst(cls) -> 'ClonePreparerRgy':
        if cls._inst is None:
            rgy = ClonePreparerRgy()
            rgy._load()          # raises before caching: a retry reloads
            cls._inst = rgy
        return cls._inst

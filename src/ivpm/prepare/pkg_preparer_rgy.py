#****************************************************************************
#* pkg_preparer_rgy.py
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
"""Registry of package preparers.

Mirrors ``PackageHandlerRgy``, with two deliberate differences:

* **No built-ins.**  IVPM ships no preparers, so the registry is empty by
  default and dispatch is a no-op.
* **A plugin that fails to load is fatal, not a warning.**  A handler that
  cannot be loaded costs an artifact; a *preparer* that cannot be loaded is a
  permission or policy step that silently does not run, while the update
  proceeds to write content anyway.
"""
import logging
import sys

from .preparer_list import PreparerList

_logger = logging.getLogger("ivpm.prepare.pkg_preparer_rgy")

PREPARER_GROUP = "ivpm.pkg_preparers"


class PreparerLoadError(Exception):
    """A registered preparer entry point could not be loaded."""


class PackagePreparerRgy(object):

    _inst = None

    def __init__(self):
        self.preparers = []
        # preparer class -> (origin, version, provider), recorded at registration
        self._meta = {}

    def addPreparer(self, cls, origin: str = "built-in", version: str = None,
                    provider: str = None):
        _logger.debug("Preparer: %s", getattr(cls, "name", cls.__name__))
        self.preparers.append(cls)
        self._meta[cls] = (origin, version, provider)

    def names(self):
        return [getattr(c, "name", None) or c.__name__ for c in self.preparers]

    def preparer_infos(self) -> list:
        """PreparerInfo for every registered preparer, provenance applied."""
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

    def mkPreparerList(self) -> PreparerList:
        """One instance per preparer, for one update."""
        return PreparerList([cls() for cls in self.preparers])

    # ------------------------------------------------------------------ #
    # Discovery                                                           #
    # ------------------------------------------------------------------ #

    def _load(self):
        # No built-ins by design -- see the module docstring.
        self._load_plugins()

    def _load_plugins(self):
        if sys.version_info < (3, 10):
            from importlib_metadata import entry_points
        else:
            from importlib.metadata import entry_points
        from ..show.info_types import ep_registration_kwargs

        seen = set()
        for ep in entry_points(group=PREPARER_GROUP):
            if ep.value in seen:
                continue
            seen.add(ep.value)
            try:
                cls = ep.load()
            except Exception as e:
                # Deliberately fatal: a preparer that fails to load is a policy
                # step that does not run, and the update would otherwise carry
                # on and write content regardless.
                raise PreparerLoadError(
                    "Failed to load package preparer '%s' (%s): %s\n"
                    "  A preparer configures or validates a package's location "
                    "before content is written, so IVPM will not proceed "
                    "without it. Fix or uninstall the providing distribution."
                    % (ep.name, ep.value, e)) from e
            if cls in self._meta:
                _logger.debug("preparer '%s' already registered; skipping",
                              ep.name)
                continue
            _logger.debug("Loaded preparer '%s' from entry point", ep.name)
            self.addPreparer(cls, **ep_registration_kwargs(ep))

    @classmethod
    def inst(cls) -> 'PackagePreparerRgy':
        if cls._inst is None:
            cls._inst = PackagePreparerRgy()
            cls._inst._load()
        return cls._inst

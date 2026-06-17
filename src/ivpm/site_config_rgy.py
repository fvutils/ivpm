#****************************************************************************
#* site_config_rgy.py
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
"""Registry for site configurations.

IVPM resolves a single *active* :class:`~ivpm.site_config.SiteConfig` from all
those registered here.  Configs are discovered from three places, registered in
this order:

1. ``DefaultSiteConfig`` — always registered first, under the name ``default``.
2. A legacy ``ivpm_site_config`` Python module exposing ``get_config()`` (the
   historical override mechanism), registered under the name ``ivpm_site_config``.
3. Entry points in the ``ivpm.site_config`` group — the preferred way for a
   site-policy *extension* to ship enforced defaults::

       [project.entry-points."ivpm.site_config"]
       acme = "acme_ivpm.site_config:AcmeSiteConfig"

   The target may be a ``SiteConfig`` subclass or a zero-arg callable returning
   a ``SiteConfig`` instance.

**Selecting the active config.**  By default the *last-registered* config wins,
so any extension or legacy module overrides ``default``.  Contributing a site
config is expected to be rare (typically a single corporate extension), so this
is unambiguous in practice.  When more than one is registered, the choice can be
pinned deterministically by name via either:

* the ``IVPM_SITE_CONFIG_NAME`` environment variable, or
* a top-level ``site-config: <name>`` key in an IVPM config file (user file
  first, then the site file).

The environment variable wins over the config files.  An override naming an
unknown config is ignored (with a warning) and resolution falls back to
last-registered.
"""
import logging
import os
from typing import Callable, List, Optional, Union

from .site_config import SiteConfig, DefaultSiteConfig

_logger = logging.getLogger("ivpm.site_config_rgy")

# Entry-point group third-party extensions use to contribute a site config.
SITE_CONFIG_GROUP = "ivpm.site_config"


class SiteConfigRgy:
    """Registry of available site configurations (see module docstring)."""

    _inst = None

    def __init__(self):
        # name -> factory (a SiteConfig subclass or zero-arg callable)
        self._factories = {}
        # name -> (origin, version, provider) recorded at registration time
        self._meta = {}
        # registration order of names; "active by default" == the last entry
        self._order: List[str] = []
        # name -> instantiated SiteConfig (lazily created, cached)
        self._instances = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self,
                 name: str,
                 factory: Union[type, Callable[[], SiteConfig]],
                 origin: str = "built-in",
                 version: str = None,
                 provider: str = None):
        """Register a site config under *name*.

        *factory* is either a ``SiteConfig`` subclass or a zero-arg callable
        returning a ``SiteConfig`` instance.  Re-registering an existing name
        replaces it and moves it to the end of the registration order (so it
        becomes the new last-registered default).

        ``origin`` distinguishes built-ins from plugins; ``provider`` (the
        distribution supplying the config) and ``version`` are optional and
        surfaced by ``ivpm show site-config``.
        """
        if name in self._factories:
            _logger.debug("Re-registering site config: %s", name)
            self._order.remove(name)
        else:
            _logger.debug("Registering site config: %s", name)
        self._factories[name] = factory
        self._meta[name] = (origin, version, provider)
        self._order.append(name)
        # Drop any cached instance so a re-registration takes effect
        self._instances.pop(name, None)

    def has(self, name: str) -> bool:
        return name in self._factories

    def names(self) -> List[str]:
        """Registered config names in registration order."""
        return list(self._order)

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def active_name(self) -> Optional[str]:
        """Resolve the name of the active config.

        Priority: ``IVPM_SITE_CONFIG_NAME`` env var, then a ``site-config`` key
        in an IVPM config file, then the last-registered config.  An override
        naming an unknown config is ignored (with a warning).
        """
        env_val = os.environ.get("IVPM_SITE_CONFIG_NAME")
        if env_val is not None and env_val.strip() != "":
            name = env_val.strip()
            if name in self._factories:
                return name
            _logger.warning(
                "IVPM_SITE_CONFIG_NAME=%r names an unknown site config; "
                "known: %s — falling back to last-registered",
                name, ", ".join(self._order))

        from .site_config import _file_site_config_name
        file_name = _file_site_config_name()
        if file_name:
            if file_name in self._factories:
                return file_name
            _logger.warning(
                "config file 'site-config: %s' names an unknown site config; "
                "known: %s — falling back to last-registered",
                file_name, ", ".join(self._order))

        return self._order[-1] if self._order else None

    def _instantiate(self, name: str) -> SiteConfig:
        if name not in self._instances:
            factory = self._factories[name]
            self._instances[name] = factory()
        return self._instances[name]

    def get_active(self) -> SiteConfig:
        """Return the active site config instance (cached per registry)."""
        name = self.active_name()
        if name is None:
            # No config registered at all — should not happen, but degrade safely
            return DefaultSiteConfig()
        return self._instantiate(name)

    # ------------------------------------------------------------------
    # Introspection (used by 'ivpm show site-config')
    # ------------------------------------------------------------------

    def site_config_infos(self) -> list:
        """Return a SiteConfigInfo for each registered config, in registration
        order, with the active one flagged and registration provenance applied."""
        active = self.active_name()
        infos = []
        for name in self._order:
            cfg = self._instantiate(name)
            info = cfg.site_config_info()
            info.name = name
            origin, version, provider = self._meta.get(name, ("built-in", None, None))
            info.origin = origin
            if version is not None:
                info.version = version
            if provider is not None and info.provider is None:
                info.provider = provider
            info.active = (name == active)
            infos.append(info)
        return infos

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _load(self):
        # 1. Default, always first so anything else overrides it
        self.register("default", DefaultSiteConfig)

        # 2. Legacy ``ivpm_site_config`` module (historical override hook)
        try:
            import ivpm_site_config  # type: ignore[import]
            get_config = getattr(ivpm_site_config, "get_config", None)
            if get_config is not None:
                self.register("ivpm_site_config", get_config,
                              origin="ivpm_site_config (module)")
            else:
                _logger.warning(
                    "ivpm_site_config module has no get_config(); ignoring")
        except ImportError:
            pass

        # 3. Plugin entry points
        try:
            from importlib.metadata import entry_points
            from .show.info_types import ep_registration_kwargs
            eps = entry_points(group=SITE_CONFIG_GROUP)
            for ep in eps:
                try:
                    factory = self._as_factory(ep.load())
                    kwargs = ep_registration_kwargs(ep)
                    # entry-point plugins default origin to their distribution
                    kwargs.setdefault("origin", ep.name)
                    self.register(ep.name, factory, **kwargs)
                    _logger.debug("Loaded site config from entry_point: %s", ep.name)
                except Exception as e:
                    _logger.warning(
                        "Failed to load site config entry_point '%s': %s",
                        ep.name, e)
        except Exception:
            pass

    @staticmethod
    def _as_factory(loaded) -> Callable[[], SiteConfig]:
        """Normalize an entry-point target to a zero-arg factory.

        Accepts a SiteConfig subclass (instantiated on demand) or an existing
        zero-arg callable returning a SiteConfig.
        """
        if isinstance(loaded, type):
            return loaded
        if callable(loaded):
            return loaded
        raise TypeError(
            "site config entry point must resolve to a class or callable, got %r"
            % type(loaded))

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------

    @classmethod
    def inst(cls) -> 'SiteConfigRgy':
        if cls._inst is None:
            cls._inst = SiteConfigRgy()
            cls._inst._load()
        return cls._inst

    @classmethod
    def _reset(cls):
        """Drop the singleton so the next inst() re-discovers configs (tests)."""
        cls._inst = None

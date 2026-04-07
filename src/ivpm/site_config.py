#****************************************************************************
#* site_config.py
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
"""Site-level configuration for IVPM.

Site administrators can override IVPM's defaults by installing a separate
``ivpm_site_config`` Python package that provides a ``SiteConfig`` subclass
and exposes it via a module-level ``get_config()`` function::

    # ivpm_site_config/__init__.py
    from ivpm.site_config import SiteConfig

    class MySiteConfig(SiteConfig):
        def get_default_cache_dir(self) -> str:
            return ""  # disable caching

        def get_ivpm_install_args(self) -> list:
            return ["/opt/site/ivpm-custom.whl"]

    def get_config() -> SiteConfig:
        return MySiteConfig()

If no ``ivpm_site_config`` module is found, ``DefaultSiteConfig`` is used.
"""
import os
from typing import List, Optional


class SiteConfig:
    """Base class defining the site-configuration interface.

    Subclass this and install as ``ivpm_site_config`` to override defaults.
    """

    def get_default_cache_dir(self) -> str:
        """Return the default IVPM cache directory path.

        Return an empty string ``""`` to disable caching by default.
        The ``IVPM_CACHE`` environment variable and any explicit ``cache_dir``
        argument still take priority over this value.
        """
        raise NotImplementedError

    def get_ivpm_install_args(self) -> List[str]:
        """Return the pip install argument(s) used to install IVPM into a new venv.

        The returned list is spliced directly into the ``pip install`` /
        ``uv pip install`` command, e.g. ``["ivpm"]`` or
        ``["/opt/site/ivpm-1.0.whl"]``.
        """
        raise NotImplementedError


class DefaultSiteConfig(SiteConfig):
    """Default site configuration shipped with IVPM.

    * Cache directory: ``$XDG_CACHE_HOME/ivpm`` if ``XDG_CACHE_HOME`` is set,
      otherwise ``~/.cache/ivpm``.
    * IVPM install source: PyPI (``["ivpm"]``).
    """

    def get_default_cache_dir(self) -> str:
        xdg = os.environ.get("XDG_CACHE_HOME", "")
        if xdg:
            return os.path.join(xdg, "ivpm")
        return os.path.join(os.path.expanduser("~"), ".cache", "ivpm")

    def get_ivpm_install_args(self) -> List[str]:
        return ["ivpm"]


# Module-level singleton — populated on first call to get_site_config().
_site_config: Optional[SiteConfig] = None


def get_site_config() -> SiteConfig:
    """Return the active site configuration.

    On the first call this function attempts ``import ivpm_site_config`` and
    calls its ``get_config()`` function.  If that import fails the
    ``DefaultSiteConfig`` is used.  The result is cached so the import is
    only attempted once per process.
    """
    global _site_config
    if _site_config is None:
        try:
            import ivpm_site_config  # type: ignore[import]
            _site_config = ivpm_site_config.get_config()
        except (ImportError, AttributeError):
            _site_config = DefaultSiteConfig()
    return _site_config


def reset_site_config() -> None:
    """Reset the cached site config singleton (intended for use in tests)."""
    global _site_config
    _site_config = None

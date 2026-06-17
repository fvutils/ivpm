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

A site can override IVPM's defaults by registering a ``SiteConfig`` subclass.
There are two mechanisms, both resolved through
:class:`~ivpm.site_config_rgy.SiteConfigRgy`:

**Preferred — an entry-point extension.**  Ship a package that declares an
``ivpm.site_config`` entry point.  This composes cleanly with vanilla IVPM:
install IVPM, then install the extension to enforce site policy::

    # acme_ivpm/site_config.py
    from ivpm.site_config import SiteConfig

    class AcmeSiteConfig(SiteConfig):
        def get_default_cache_dir(self) -> str:
            return "/opt/acme/ivpm-cache"

        def get_ivpm_install_args(self) -> list:
            return ["/opt/acme/ivpm-custom.whl"]

    # pyproject.toml
    # [project.entry-points."ivpm.site_config"]
    # acme = "acme_ivpm.site_config:AcmeSiteConfig"

**Legacy — an ``ivpm_site_config`` module.**  A package providing a module named
``ivpm_site_config`` with a ``get_config()`` function returning a ``SiteConfig``
is still honored.

If neither is present, ``DefaultSiteConfig`` is used.  When more than one config
is registered the last-registered one wins by default; pin a specific one with
the ``IVPM_SITE_CONFIG_NAME`` env var or a ``site-config: <name>`` config-file
key.  Use ``ivpm show site-config`` to see what is registered and which is active.
"""
import fnmatch
import logging
import os
from typing import List, Optional, Tuple, TYPE_CHECKING

import yaml

_logger = logging.getLogger("ivpm.site_config")

if TYPE_CHECKING:
    from .cache_provider import CacheContext, CacheProvider
    from .show.info_types import SiteConfigInfo


# Ordered git auth methods tried for an https URL that has no explicit
# per-package/CLI override.  Recognized tokens:
#   'gh'    -- clone the https URL as-is when 'gh' is authenticated for the
#              host (its credential helper performs auth)
#   'ssh'   -- rewrite the URL to git@host:path (terminal)
#   'https' -- clone the URL exactly as written (terminal)
# The first applicable method wins; 'ssh'/'https' always apply.  The default
# prefers gh when available and otherwise falls back to ssh.
DEFAULT_GIT_AUTH_ORDER = ["gh", "ssh"]


class SiteConfig:
    """Base class defining the site-configuration interface.

    Subclass this and install as ``ivpm_site_config`` to override defaults.
    """

    def get_cache_provider(self, context: 'CacheContext') -> 'CacheProvider':
        """Return the cache provider for one session. Never ``None``.

        Returns a :class:`~ivpm.cache_provider.NullCacheProvider` when
        caching is disabled, otherwise a
        :class:`~ivpm.cache_provider.DirectoryCacheProvider` rooted at the
        resolved cache directory.

        The default implementation is built on :meth:`get_default_cache_dir`,
        so a site config that overrides only ``get_default_cache_dir`` keeps
        working unchanged.  Override this method directly for per-dependency
        routing or an alternate backend.
        """
        from .cache_provider import DirectoryCacheProvider, NullCacheProvider
        cache_dir = self._resolve_cache_dir()
        if not cache_dir:
            return NullCacheProvider(context)
        from .cache import DirectoryCacheStore
        return DirectoryCacheProvider(context, DirectoryCacheStore(cache_dir))

    def _resolve_cache_dir(self) -> Optional[str]:
        """Resolve the cache directory: ``IVPM_CACHE`` wins, then the
        (overridable) site default.  Returns ``None`` when caching is
        disabled."""
        env_val = os.environ.get("IVPM_CACHE")
        if env_val is not None:
            return env_val or None
        default = self.get_default_cache_dir()
        return default or None

    def get_default_cache_dir(self) -> str:
        """Return the default IVPM cache directory path.

        Return an empty string ``""`` to disable caching by default.
        The ``IVPM_CACHE`` environment variable and any explicit ``cache_dir``
        argument still take priority over this value.

        Retained for backward compatibility: the default
        :meth:`get_cache_provider` consults this.
        """
        raise NotImplementedError

    def get_ivpm_install_args(self) -> List[str]:
        """Return the pip install argument(s) used to install IVPM into a new venv.

        The returned list is spliced directly into the ``pip install`` /
        ``uv pip install`` command, e.g. ``["ivpm"]`` or
        ``["/opt/site/ivpm-1.0.whl"]``.
        """
        raise NotImplementedError

    def get_default_git_auth_order(self) -> List[str]:
        """Return the default ordered list of git auth methods for https URLs.

        See :data:`DEFAULT_GIT_AUTH_ORDER` for the recognized tokens.  Applies
        to any host without a matching :meth:`get_git_auth_rules` entry.
        """
        return list(DEFAULT_GIT_AUTH_ORDER)

    def get_git_auth_rules(self) -> List[Tuple[str, List[str]]]:
        """Return host-pattern -> auth-order rules, most specific first.

        Each rule is ``(host_glob, order)`` where *host_glob* is an
        ``fnmatch`` pattern matched against the URL host (e.g. ``"github.com"``,
        ``"*.internal.corp"``) and *order* is a list of auth methods
        (``gh``/``ssh``/``https``).  The first rule whose glob matches the host
        wins.  Empty by default — the flat default order applies to every host.

        Override in an ``ivpm_site_config`` package, e.g.::

            def get_git_auth_rules(self):
                return [
                    ("*.internal.corp", ["ssh"]),
                    ("github.com",      ["gh", "ssh"]),
                ]
        """
        return []

    def site_config_info(self) -> 'SiteConfigInfo':
        """Return this config's self-description for ``ivpm show site-config``.

        The registry overlays the registered ``name`` and provenance
        (``origin``/``provider``/``version``) afterward, so subclasses only
        need to supply a ``description`` (and optionally ``notes``).  The
        default derives the description from the class docstring's first line.
        """
        from .show.info_types import SiteConfigInfo
        doc = (self.__class__.__doc__ or "").strip()
        description = doc.splitlines()[0].strip() if doc else self.__class__.__name__
        return SiteConfigInfo(name=self.__class__.__name__, description=description)


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


def get_site_config() -> SiteConfig:
    """Return the active site configuration.

    The active config is resolved by :class:`~ivpm.site_config_rgy.SiteConfigRgy`
    from everything registered there: ``DefaultSiteConfig``, a legacy
    ``ivpm_site_config`` module, and any ``ivpm.site_config`` entry-point
    plugins.  By default the last-registered config wins; see the registry for
    the ``IVPM_SITE_CONFIG_NAME`` / ``site-config:`` overrides.  The resolved
    instance is cached by the registry for the process lifetime.
    """
    from .site_config_rgy import SiteConfigRgy
    return SiteConfigRgy.inst().get_active()


def parse_git_auth_order(value) -> List[str]:
    """Normalize a git-auth-order spec into a lowercased method list.

    Accepts a comma-separated string (``"gh, ssh"``) or a YAML list
    (``["gh", "ssh"]``).
    """
    if isinstance(value, str):
        items = value.split(",")
    else:
        items = list(value)
    return [str(m).strip().lower() for m in items if str(m).strip()]


# --------------------------------------------------------------------------
# Declarative config files (user + site), checked in order
# --------------------------------------------------------------------------
#
# Both files share the same schema; recognized keys today:
#   site-config: acme                  # pin the active registered site config
#   git-auth-order: [gh, ssh]          # default order for unmatched hosts
#   git-auth:                          # host-glob -> order rules
#     - host: "*.internal.corp"
#       order: [ssh]
#     - host: "github.com"
#       order: [gh, ssh]
#
# Files are consulted user-first, then site: a user rule overrides a site rule,
# and the first git-auth-order found wins.

def user_config_path() -> str:
    """Path to the per-user config file.

    ``IVPM_USER_CONFIG`` overrides; otherwise ``$XDG_CONFIG_HOME/ivpm/config.yaml``
    (falling back to ``~/.config/ivpm/config.yaml``).
    """
    override = os.environ.get("IVPM_USER_CONFIG")
    if override:
        return override
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    base = xdg if xdg else os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "ivpm", "config.yaml")


def site_config_path() -> str:
    """Path to the system-wide config file.

    ``IVPM_SITE_CONFIG`` overrides; otherwise ``/etc/ivpm/config.yaml``.
    """
    override = os.environ.get("IVPM_SITE_CONFIG")
    if override:
        return override
    return os.path.join(os.sep, "etc", "ivpm", "config.yaml")


# Cache of loaded config files: list of (path, data) in priority order (user, site).
_config_files: Optional[List[Tuple[str, dict]]] = None


def _read_config_file(path: str) -> Optional[dict]:
    try:
        with open(path) as fp:
            data = yaml.safe_load(fp)
    except FileNotFoundError:
        return None
    except (OSError, yaml.YAMLError) as e:
        _logger.warning("ignoring unreadable IVPM config %s: %s", path, e)
        return None
    if not isinstance(data, dict):
        if data is not None:
            _logger.warning("ignoring IVPM config %s: top level is not a mapping", path)
        return None
    return data


def _load_config_files() -> List[Tuple[str, dict]]:
    global _config_files
    if _config_files is None:
        _config_files = []
        for path in (user_config_path(), site_config_path()):
            data = _read_config_file(path)
            if data is not None:
                _config_files.append((path, data))
    return _config_files


def loaded_config_paths() -> List[str]:
    """Return the paths of config files that were found and loaded (for diagnostics)."""
    return [path for path, _ in _load_config_files()]


def _file_git_auth_rules() -> List[Tuple[str, List[str]]]:
    """Host-glob rules from the config files, user file first."""
    rules: List[Tuple[str, List[str]]] = []
    for path, data in _load_config_files():
        for rule in (data.get("git-auth") or []):
            if not isinstance(rule, dict):
                continue
            host = rule.get("host")
            order = rule.get("order")
            if host and order:
                rules.append((str(host), parse_git_auth_order(order)))
    return rules


def _file_default_order() -> Optional[List[str]]:
    """First ``git-auth-order`` default found across the config files (user first)."""
    for path, data in _load_config_files():
        order = data.get("git-auth-order")
        if order:
            return parse_git_auth_order(order)
    return None


def _file_site_config_name() -> Optional[str]:
    """First ``site-config`` name found across the config files (user first).

    Used by :class:`~ivpm.site_config_rgy.SiteConfigRgy` to let a user/site
    config file pin which registered site config is active.
    """
    for path, data in _load_config_files():
        name = data.get("site-config")
        if name:
            return str(name).strip()
    return None


def resolve_git_auth_order(host: Optional[str] = None) -> List[str]:
    """Resolve the active git auth order for *host*.

    Host-glob rules are checked first (most specific); they are gathered from
    the user config file, the site config file, then the Python site config
    (in that order — first matching glob wins).  When no rule matches, the
    default order is the first available of: ``IVPM_GIT_AUTH_ORDER``, the user
    config file, the site config file, then the Python site-config default.

    (The ``--git-auth-order`` CLI option, when given, is applied by the caller
    and bypasses this resolution entirely.)
    """
    cfg = get_site_config()
    if host:
        for glob, order in _file_git_auth_rules() + list(cfg.get_git_auth_rules()):
            if fnmatch.fnmatch(host, glob):
                return parse_git_auth_order(order)

    env_val = os.environ.get("IVPM_GIT_AUTH_ORDER")
    if env_val is not None and env_val.strip() != "":
        return parse_git_auth_order(env_val)

    file_default = _file_default_order()
    if file_default:
        return file_default

    return parse_git_auth_order(cfg.get_default_git_auth_order())


def reset_site_config() -> None:
    """Reset the site-config registry and config-file cache (for tests)."""
    global _config_files
    _config_files = None
    from .site_config_rgy import SiteConfigRgy
    SiteConfigRgy._reset()

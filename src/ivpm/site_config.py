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
import re
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

# How much checking a cache HIT is subjected to before its bytes are handed to
# a consumer.  See :func:`resolve_cache_verify_level`.
#   'off'     -- trust the entry as found
#   'shape'   -- one metadata-only walk; counts vs. the entry's sealed manifest
#   'content' -- 'shape' plus a per-file hash compared to the sealed merkle root
CACHE_VERIFY_LEVELS = ("off", "shape", "content")
DEFAULT_CACHE_VERIFY_LEVEL = "shape"


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

    def get_default_cache_verify_level(self) -> str:
        """How thoroughly a cache HIT is checked before it is trusted.

        One of :data:`CACHE_VERIFY_LEVELS`.  ``shape`` (the default) is one
        metadata-only walk of the entry -- ``O(inodes)``, no reads -- and
        catches every corruption a truncated copy or an interrupted GC can
        produce.  ``content`` additionally hashes every byte.  ``off`` trusts
        whatever is on disk.

        ``IVPM_CACHE_VERIFY`` and the ``cache-verify:`` config-file key take
        priority over this value.
        """
        return DEFAULT_CACHE_VERIFY_LEVEL

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
#   cache-verify: shape                # off | shape | content
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


def _config_file_kind(path: str) -> str:
    """``user-config`` or ``site-config-file`` for a loaded config path.

    Used only for provenance reporting (``ivpm diagnose git`` and the DEBUG
    auth trace): "a rule from a config file" is not actionable, "a rule from
    *this* file" is.
    """
    return "user-config" if path == user_config_path() else "site-config-file"


def _file_git_auth_rules_ex() -> List[Tuple[str, List[str], str]]:
    """Host-glob rules from the config files with their source path."""
    rules: List[Tuple[str, List[str], str]] = []
    for path, data in _load_config_files():
        for rule in (data.get("git-auth") or []):
            if not isinstance(rule, dict):
                continue
            host = rule.get("host")
            order = rule.get("order")
            if host and order:
                rules.append((str(host), parse_git_auth_order(order), path))
    return rules


def _file_git_auth_rules() -> List[Tuple[str, List[str]]]:
    """Host-glob rules from the config files, user file first."""
    return [(host, order) for host, order, _ in _file_git_auth_rules_ex()]


def _file_default_order_ex() -> Optional[Tuple[List[str], str]]:
    """First ``git-auth-order`` default found, with the file it came from."""
    for path, data in _load_config_files():
        order = data.get("git-auth-order")
        if order:
            return parse_git_auth_order(order), path
    return None


def _file_default_order() -> Optional[List[str]]:
    """First ``git-auth-order`` default found across the config files (user first)."""
    found = _file_default_order_ex()
    return found[0] if found is not None else None


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


def parse_cache_verify_level(value, origin: str = "") -> Optional[str]:
    """Normalize a cache-verify level, or None when it is not recognized.

    An unrecognized value is *ignored with a warning* rather than raising: a
    typo in a site-wide config file must not make IVPM unusable for everyone
    who reads it, and the fallback (the default level) is safe.
    """
    if value is None:
        return None
    level = str(value).strip().lower()
    if level in CACHE_VERIFY_LEVELS:
        return level
    if level:
        _logger.warning(
            "ignoring unrecognized cache verify level %r%s (expected one of %s)",
            value, (" from %s" % origin) if origin else "",
            ", ".join(CACHE_VERIFY_LEVELS))
    return None


def _file_cache_verify_level() -> Optional[str]:
    """First ``cache-verify`` level found across the config files (user first)."""
    for path, data in _load_config_files():
        level = parse_cache_verify_level(data.get("cache-verify"), path)
        if level:
            return level
    return None


def resolve_cache_verify_level(override=None) -> str:
    """The active cache verification level.

    Priority: an explicit *override* (the ``--verify`` CLI option), then
    ``IVPM_CACHE_VERIFY``, then the ``cache-verify:`` key of the user and site
    config files, then the Python site config's
    :meth:`SiteConfig.get_default_cache_verify_level`.
    """
    explicit = parse_cache_verify_level(override, "--verify")
    if explicit:
        return explicit

    env_level = parse_cache_verify_level(
        os.environ.get("IVPM_CACHE_VERIFY"), "IVPM_CACHE_VERIFY")
    if env_level:
        return env_level

    file_level = _file_cache_verify_level()
    if file_level:
        return file_level

    return (parse_cache_verify_level(
        get_site_config().get_default_cache_verify_level(), "site config")
        or DEFAULT_CACHE_VERIFY_LEVEL)


def resolve_git_auth_order_ex(host: Optional[str] = None) -> Tuple[List[str], str]:
    """``(order, source)`` -- the active git auth order for *host* and where
    it came from.

    Resolution is genuinely layered (host rules from the user file, the site
    file, then the Python site config; then ``IVPM_GIT_AUTH_ORDER``, the file
    default, and the site-config default), and nothing in the logs used to be
    able to answer "why https?".  *source* names the exact layer, using the
    vocabulary::

        env:IVPM_GIT_AUTH_ORDER
        host-rule:<glob> (user-config:<path>)
        host-rule:<glob> (site-config-file:<path>)
        host-rule:<glob> (site-config:<ClassName>)
        user-config:<path>
        site-config-file:<path>
        site-config-default:<ClassName>

    (The ``--git-auth-order`` CLI option, when given, is applied by the caller
    and bypasses this resolution entirely.)
    """
    cfg = get_site_config()
    if host:
        rules = [(glob, order, "%s:%s" % (_config_file_kind(path), path))
                 for glob, order, path in _file_git_auth_rules_ex()]
        rules += [(glob, order, "site-config:%s" % type(cfg).__name__)
                  for glob, order in cfg.get_git_auth_rules()]
        for glob, order, origin in rules:
            if fnmatch.fnmatch(host, glob):
                return (parse_git_auth_order(order),
                        "host-rule:%s (%s)" % (glob, origin))

    env_val = os.environ.get("IVPM_GIT_AUTH_ORDER")
    if env_val is not None and env_val.strip() != "":
        return parse_git_auth_order(env_val), "env:IVPM_GIT_AUTH_ORDER"

    file_default = _file_default_order_ex()
    if file_default is not None:
        order, path = file_default
        return order, "%s:%s" % (_config_file_kind(path), path)

    return (parse_git_auth_order(cfg.get_default_git_auth_order()),
            "site-config-default:%s" % type(cfg).__name__)


def resolve_git_auth_order(host: Optional[str] = None) -> List[str]:
    """Resolve the active git auth order for *host*.

    Host-glob rules are checked first (most specific); they are gathered from
    the user config file, the site config file, then the Python site config
    (in that order — first matching glob wins).  When no rule matches, the
    default order is the first available of: ``IVPM_GIT_AUTH_ORDER``, the user
    config file, the site config file, then the Python site-config default.

    See :func:`resolve_git_auth_order_ex` for the same answer plus its source.
    """
    return resolve_git_auth_order_ex(host)[0]


# --------------------------------------------------------------------------
# git-url-map: on-the-fly rewrite of git URLs before cloning
# --------------------------------------------------------------------------
#
# Config-file key (user + site), a list of prefix-pattern rewrite rules:
#
#   git-url-map:
#     - from: "https://github.com/"          # broad: everything under github
#       to:   "file:///repos/"
#     - from: "https://github.com/fvutils/"  # more specific -> wins for fvutils
#       to:   "https://github.com/fvutils/"
#
# `from` is anchored at the start of the URL and matched by path element.
# Wildcards: `*` matches within one path segment (no `/`), `**` spans segments.
# Captures from `*`/`**` are available in `to` as \1, \2, ... (use single-quoted
# YAML so the backslash is preserved).  The unmatched tail of the URL is appended
# automatically.  When several rules match, the *most specific* wins: most path
# segments constrained, then most literal characters, then fewest wildcards, then
# declaration order (env before user before site).  IVPM_GIT_URL_MAP contributes
# additional rules (highest tie-break priority); its format is a semicolon-
# separated list of ``from=to`` pairs.


def _path_seg_count(frm: str) -> int:
    """Number of non-empty path segments the pattern constrains (after host)."""
    d = frm.find("://")
    rest = frm[d + 3:] if d >= 0 else frm
    slash = rest.find("/")
    if slash < 0:
        return 0
    return len([s for s in rest[slash + 1:].split("/") if s])


def _compile_git_url_pattern(frm: str) -> Tuple["re.Pattern", int, int, int]:
    """Compile *frm* into (anchored regex, path-seg count, literal count, wildcard count)."""
    parts: List[str] = []
    i = lit = wc = 0
    while i < len(frm):
        if frm.startswith("**", i):
            parts.append("(.*)"); wc += 1; i += 2
        elif frm[i] == "*":
            parts.append("([^/]*)"); wc += 1; i += 1
        else:
            parts.append(re.escape(frm[i])); lit += 1; i += 1
    tail = frm[-1:]
    # A rule that does not end in "/" or "*" must align on a path boundary, so
    # "…/ORG" does not match "…/ORGANIZATION".  ".git" counts as a boundary:
    # a repo-scoped rule written as "…/owner/repo" is expected to match the
    # "…/owner/repo.git" spelling that manifests actually use.  Without this,
    # such a rule silently fails to match and the URL falls through to
    # upstream -- no error, just an unexpected remote.
    boundary = "" if (tail == "/" or tail == "*") else r"(?=/|\.git(?:/|$)|$)"
    return (re.compile("^" + "".join(parts) + boundary),
            _path_seg_count(frm), lit, wc)


def _env_git_url_map() -> List[Tuple[str, str]]:
    """(from, to) rules from ``IVPM_GIT_URL_MAP`` (``from=to;from=to``)."""
    spec = os.environ.get("IVPM_GIT_URL_MAP")
    if not spec:
        return []
    rules: List[Tuple[str, str]] = []
    for entry in spec.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        frm, sep, to = entry.partition("=")
        frm, to = frm.strip(), to.strip()
        if sep and frm and to:
            rules.append((frm, to))
        else:
            _logger.warning("ignoring malformed IVPM_GIT_URL_MAP entry: %r", entry)
    return rules


def _file_git_url_map() -> List[Tuple[str, str]]:
    """(from, to) rewrite rules from the config files, user first."""
    rules: List[Tuple[str, str]] = []
    for _path, data in _load_config_files():
        for rule in (data.get("git-url-map") or []):
            if not isinstance(rule, dict):
                continue
            frm, to = rule.get("from"), rule.get("to")
            if frm and to:
                rules.append((str(frm), str(to)))
            else:
                _logger.warning("ignoring git-url-map rule missing from/to: %r", rule)
    return rules


def apply_git_url_map_ex(url: str) -> Tuple[str, Optional[str]]:
    """``(result, matched_rule)`` -- the rewrite plus which rule produced it.

    *matched_rule* is the winning rule's ``from`` pattern (and its ``to``),
    or ``None`` when no rule matched.  The plain
    :func:`apply_git_url_map` logs the rewrite but not the rule, which is the
    piece a user needs when an unexpected remote shows up.
    """
    best = None  # (specificity key, match end, capture groups, replacement, from)
    for idx, (frm, to) in enumerate(_env_git_url_map() + _file_git_url_map()):
        rx, segs, lit, wc = _compile_git_url_pattern(frm)
        m = rx.match(url)
        if not m:
            continue
        key = (segs, lit, -wc, -idx)   # depth, literal, wildcards, declaration order
        if best is None or key > best[0]:
            best = (key, m.end(), m.groups(), to, frm)
    if best is None:
        return url, None
    _, end, groups, to, frm = best
    for n, g in enumerate(groups, start=1):
        to = to.replace("\\%d" % n, g or "")
    result = to + url[end:]
    if result != url:
        _logger.debug("git-url-map: %s -> %s", url, result)
    return result, "%s -> %s" % (frm, to)


def apply_git_url_map(url: str) -> str:
    """Rewrite *url* by the most-specific matching git-url-map rule.

    Returns *url* unchanged when no rule matches.  Applied by
    :func:`ivpm.utils.resolve_clone_url` before any ssh/auth handling, so a
    rule that redirects an https URL to a ``file://`` mirror or an internal
    host is honored and then re-evaluated for auth against the new host.
    """
    return apply_git_url_map_ex(url)[0]


def reset_site_config() -> None:
    """Reset the site-config registry and config-file cache (for tests)."""
    global _config_files
    _config_files = None
    from .site_config_rgy import SiteConfigRgy
    SiteConfigRgy._reset()

#****************************************************************************
#* template_rgy.py
#*
#* Copyright 2026 Matthew Ballance and Contributors
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
"""Registry of templates available to ``ivpm init``.

Templates are discovered from six sources, highest precedence first:

1. ``IVPM_INIT_TEMPLATE_PATH`` -- ``os.pathsep``-separated directories
   (template or collection) and YAML index files.  Untrusted.
2. The user config file's ``init-templates:`` key.
3. The site config file's ``init-templates:`` key.  Config-file entries are
   trusted only with ``trust: true``.
4. The active ``SiteConfig``'s ``get_init_templates()``.  Trusted.
5. Entry points in the ``ivpm.init_templates`` group.  Trusted.
6. IVPM's built-in templates (``ivpm/templates/init/*``).  Trusted.

The first template registered under a name wins; later ones with the same
name are kept on the winner's ``shadowed`` list so ``ivpm show
init-templates`` can show the override.  A source that fails is reported in
:meth:`InitTemplateRgy.warnings` and skipped, so a broken template pack never
breaks ``ivpm init`` for everyone else.

The registry is built only by the commands that need it (``init`` and ``show
init-templates``); nothing here runs at IVPM startup.
"""
import dataclasses as dc
import difflib
import logging
import os
from typing import Any, Iterable, List, Optional

import yaml

from .template_info import InitTemplate, resolve_src
from . import template_dir

_logger = logging.getLogger("ivpm.init_template")

# Entry-point group template packs use to contribute templates.
INIT_TEMPLATES_GROUP = "ivpm.init_templates"

ENV_TEMPLATE_PATH = "IVPM_INIT_TEMPLATE_PATH"
ENV_DEFAULT_TEMPLATE = "IVPM_INIT_TEMPLATE"

BUILTIN_TEMPLATE = "default"


def builtin_templates_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "templates", "init")


class InitTemplateRgy(object):
    """Registry of init templates (see module docstring)."""

    _inst = None

    def __init__(self):
        self._templates = {}          # name -> InitTemplate (the winner)
        self._order: List[str] = []   # registration order of winners
        self._warnings: List[str] = []

    # ------------------------------------------------------------------
    # Registration and lookup
    # ------------------------------------------------------------------

    def register(self, t: InitTemplate):
        """Register *t*; the first registration of a name wins."""
        winner = self._templates.get(t.name)
        if winner is not None:
            _logger.debug("init template '%s' from %s shadowed by %s",
                          t.name, t.origin, winner.origin)
            winner.shadowed.append(t)
            return
        self._templates[t.name] = t
        self._order.append(t.name)

    def get(self, name: str) -> Optional[InitTemplate]:
        return self._templates.get(name)

    def names(self, include_hidden: bool = True) -> List[str]:
        return [t.name for t in self.templates(include_hidden)]

    def templates(self, include_hidden: bool = False) -> List[InitTemplate]:
        """Winning templates in precedence order (hidden ones only on request)."""
        return [self._templates[n] for n in self._order
                if include_hidden or not self._templates[n].hidden]

    def suggest(self, name: str) -> List[str]:
        """Registered names close to *name*, for 'unknown template' errors."""
        return difflib.get_close_matches(name, self._order, n=3, cutoff=0.5)

    def warnings(self) -> List[str]:
        return list(self._warnings)

    def warn(self, msg: str):
        _logger.debug("init templates: %s", msg)
        self._warnings.append(msg)

    def default_name(self) -> str:
        """Template used when ``-t`` is not given.

        ``IVPM_INIT_TEMPLATE``, then a ``default-init-template:`` config-file
        key (user file first), then ``SiteConfig.get_default_init_template()``,
        then ``default``.
        """
        env_val = os.environ.get(ENV_DEFAULT_TEMPLATE, "").strip()
        if env_val:
            return env_val
        from ..site_config import _file_default_init_template, get_site_config
        name = _file_default_init_template()
        if name:
            return name
        try:
            name = get_site_config().get_default_init_template()
        except Exception as e:
            self.warn("site config get_default_init_template() failed: %s" % e)
            name = None
        if name:
            return str(name)
        return BUILTIN_TEMPLATE

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _load(self):
        for label, source in (
                (ENV_TEMPLATE_PATH, self._from_env),
                ("config files", self._from_config_files),
                ("site config", self._from_site_config),
                ("entry points", self._from_entry_points),
                ("built-in templates", self._from_builtin)):
            try:
                found = source()
            except Exception as e:
                _logger.warning("init templates: %s failed: %s", label, e)
                self.warn("%s: failed: %s" % (label, e))
                continue
            for t in found:
                self.register(t)

    def _from_env(self) -> List[InitTemplate]:
        value = os.environ.get(ENV_TEMPLATE_PATH, "")
        ret = []
        for entry in value.split(os.pathsep):
            entry = entry.strip()
            if not entry:
                continue
            path = os.path.abspath(os.path.expanduser(entry))
            if os.path.isdir(path):
                ret.extend(template_dir.scan(path, "env", self._warnings))
            elif os.path.isfile(path):
                ret.extend(self._from_index_file(path))
            else:
                self.warn("%s entry '%s' does not exist" % (ENV_TEMPLATE_PATH, entry))
        for t in ret:
            # Env vars are easy to inherit unknowingly; never trust them.
            if t.trusted:
                self.warn("template '%s': 'trust: true' is ignored for templates from %s"
                          % (t.name, ENV_TEMPLATE_PATH))
            t.trusted = False
        return ret

    def _from_index_file(self, path: str) -> List[InitTemplate]:
        try:
            with open(path) as fp:
                data = yaml.safe_load(fp)
        except (OSError, yaml.YAMLError) as e:
            self.warn("%s: cannot read template index: %s" % (path, e))
            return []
        if isinstance(data, dict) and "init-templates" in data:
            data = data["init-templates"]
        return self._parse_entries(data, "env", os.path.dirname(path), path)

    def _parse_entries(self, data: Any, origin: str, base_dir: str,
                       where: str) -> List[InitTemplate]:
        """Parse a list of config/index entries (``path:`` or ``name``/``src``)."""
        if data is None:
            return []
        if not isinstance(data, list):
            self.warn("%s: 'init-templates' must be a list; ignored" % where)
            return []
        ret = []
        for i, ent in enumerate(data):
            if isinstance(ent, dict) and "path" in ent:
                trust = ent.get("trust")
                extra = set(ent.keys()) - {"path", "trust"}
                if extra:
                    self.warn("%s: init-templates[%d]: keys %s are ignored with 'path'"
                              % (where, i, ", ".join(sorted(extra))))
                if trust is not None and not isinstance(trust, bool):
                    self.warn("%s: init-templates[%d]: 'trust' must be true or false"
                              % (where, i))
                    continue
                if not isinstance(ent["path"], str):
                    self.warn("%s: init-templates[%d]: 'path' must be a string" % (where, i))
                    continue
                found = template_dir.scan(resolve_src(ent["path"], base_dir),
                                          origin, self._warnings)
                for t in found:
                    t.trusted = trust
                ret.extend(found)
                continue
            try:
                t = InitTemplate.from_mapping(ent, origin=origin, base_dir=base_dir,
                                              warnings=self._warnings)
            except ValueError as e:
                self.warn("%s: init-templates[%d]: %s; skipped" % (where, i, e))
                continue
            self._fill_from_dir(t)
            ret.append(t)
        return ret

    def _fill_from_dir(self, t: InitTemplate):
        """Merge ``_ivpm`` metadata into a descriptor whose src is a local dir."""
        if t.is_remote:
            return
        if template_dir.is_template_dir(t.src):
            meta, w = template_dir.read_template_meta(t.src)
            self._warnings.extend(w)
            template_dir.apply_meta(t, meta)
        else:
            self.warn("template '%s': %s is not a copier template directory"
                      % (t.name, t.src))

    def _from_config_files(self) -> List[InitTemplate]:
        from ..site_config import _file_init_templates_ex, _config_file_kind
        ret = []
        for data, path in _file_init_templates_ex():
            found = self._parse_entries(data, _config_file_kind(path),
                                        os.path.dirname(os.path.abspath(path)), path)
            for t in found:
                # A config file's owner opts in explicitly; nothing else trusts.
                t.trusted = bool(t.trusted)
            ret.extend(found)
        return ret

    def _from_provided(self, items: Iterable, origin: str, where: str,
                       provider: Optional[str] = None,
                       version: Optional[str] = None) -> List[InitTemplate]:
        """Normalize items from a SiteConfig hook or an entry point.

        Items are paths or ``InitTemplate`` instances.  Both kinds of provider
        are installed Python code, so their templates are trusted unless the
        provider explicitly said otherwise.
        """
        if isinstance(items, (str, os.PathLike, InitTemplate)):
            items = [items]
        ret = []
        for item in items:
            if isinstance(item, InitTemplate):
                # Copy: a provider may hand back the same instance every call.
                item = dc.replace(item, defaults=dict(item.defaults), shadowed=[])
                found = [item]
                if not item.is_remote:
                    item.src = resolve_src(item.src)
                    self._fill_from_dir(item)
            elif isinstance(item, (str, os.PathLike)):
                found = template_dir.scan(os.fspath(item), origin, self._warnings)
            else:
                self.warn("%s: ignoring %r (expected a path or InitTemplate)"
                          % (where, item))
                continue
            for t in found:
                t.origin = origin
                t.provider = provider
                t.version = version
                if t.trusted is None:
                    t.trusted = True
            ret.extend(found)
        return ret

    def _from_site_config(self) -> List[InitTemplate]:
        from ..site_config import get_site_config
        cfg = get_site_config()
        try:
            items = cfg.get_init_templates()
        except Exception as e:
            self.warn("site config get_init_templates() failed: %s" % e)
            return []
        return self._from_provided(items or (), "site-config",
                                   "site config %s" % type(cfg).__name__)

    def _from_entry_points(self) -> List[InitTemplate]:
        from .._compat import entry_points
        from ..show.info_types import ep_registration_kwargs
        eps = []
        seen = set()
        for ep in entry_points(group=INIT_TEMPLATES_GROUP):
            # A stale egg-info next to an installed dist can list the same
            # target twice; load it once.
            if ep.value in seen:
                continue
            seen.add(ep.value)
            eps.append(ep)

        def _key(ep):
            dist = getattr(ep, "dist", None)
            return (getattr(dist, "name", None) or "", ep.name)

        ret = []
        for ep in sorted(eps, key=_key):
            try:
                target = ep.load()
                items = target() if callable(target) else target
                kwargs = ep_registration_kwargs(ep)
                ret.extend(self._from_provided(
                    items or (), kwargs.get("origin", ep.name),
                    "entry point '%s'" % ep.name,
                    provider=kwargs.get("provider"),
                    version=kwargs.get("version")))
            except Exception as e:
                _logger.warning("init templates: entry point '%s' (%s) failed: %s",
                                ep.name, ep.value, e)
                self.warn("entry point '%s' (%s) failed: %s" % (ep.name, ep.value, e))
        return ret

    def _from_builtin(self) -> List[InitTemplate]:
        path = builtin_templates_dir()
        if not os.path.isdir(path):
            return []
        found = template_dir.scan(path, "built-in", self._warnings)
        for t in found:
            t.trusted = True
        return found

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------

    @classmethod
    def inst(cls) -> 'InitTemplateRgy':
        if cls._inst is None:
            cls._inst = InitTemplateRgy()
            cls._inst._load()
        return cls._inst

    @classmethod
    def _reset(cls):
        """Drop the singleton so the next inst() re-discovers templates (tests)."""
        cls._inst = None

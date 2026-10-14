#****************************************************************************
#* template_info.py
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
"""The :class:`InitTemplate` descriptor held by the init-template registry.

Providers (``ivpm.init_templates`` entry points and ``SiteConfig`` hooks) may
construct these directly when a bare path is not enough -- a remote template,
injected defaults, or metadata the template itself cannot carry.  The
registry fills in ``origin``/``provider``/``version`` and ``shadowed``.
"""
import dataclasses as dc
import os
import re
from typing import Any, Dict, List, Optional

# A registry name must not be mistaken for a path or URL on the command line
# (see ``classify_template_arg``), so keep it to a conservative alphabet.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Keys accepted in a config-file / index-file template entry.
_ENTRY_KEYS = ("name", "src", "ref", "description", "category", "hidden",
               "trust", "defaults", "next_steps")


def is_remote_src(src: str) -> bool:
    """True when *src* is a URL or copier shorthand rather than a local path."""
    s = str(src)
    return ("://" in s
            or s.startswith(("gh:", "gl:", "git@", "git+"))
            or s.endswith(".git"))


def valid_template_name(name) -> bool:
    return isinstance(name, str) and bool(_NAME_RE.match(name))


@dc.dataclass
class InitTemplate:
    """One template known to the registry (see ``init-templates-design.md`` §2)."""
    name: str
    src: str                                 # local dir or copier-understood URL
    ref: Optional[str] = None                # VCS ref for remote templates
    description: str = ""
    category: str = ""
    hidden: bool = False
    # None means "use the origin's default" (built-in / entry point / SiteConfig
    # are trusted, everything else is not).  See trust.is_trusted().
    trusted: Optional[bool] = None
    defaults: Dict[str, Any] = dc.field(default_factory=dict)
    next_steps: str = ""
    # Filled in by the registry, not by providers:
    origin: str = ""
    provider: Optional[str] = None
    version: Optional[str] = None
    shadowed: List['InitTemplate'] = dc.field(default_factory=list)

    @property
    def is_remote(self) -> bool:
        return is_remote_src(self.src)

    @classmethod
    def from_mapping(cls,
                     d: dict,
                     origin: str = "",
                     base_dir: Optional[str] = None,
                     warnings: Optional[List[str]] = None) -> 'InitTemplate':
        """Build a descriptor from a config-file or index-file entry.

        ``~`` and relative local ``src`` values are resolved against
        *base_dir* (the directory of the file the entry came from).  An unknown
        key is reported through *warnings* rather than rejected, so an older
        IVPM can read a config written for a newer one.  A missing or
        wrongly-typed field raises ``ValueError`` naming the field.
        """
        if not isinstance(d, dict):
            raise ValueError("template entry must be a mapping, got %s" % type(d).__name__)
        for key in d.keys():
            if key not in _ENTRY_KEYS and warnings is not None:
                warnings.append("unknown key '%s' in template entry %r" % (key, d.get("name")))

        name = d.get("name")
        if not valid_template_name(name):
            raise ValueError(
                "template entry 'name' must be a word of letters, digits, '.', '_' "
                "or '-', got %r" % (name,))
        src = d.get("src")
        if not isinstance(src, str) or not src.strip():
            raise ValueError("template '%s': 'src' must be a non-empty string" % name)
        src = resolve_src(src.strip(), base_dir)

        def _str(key):
            v = d.get(key)
            if v is None:
                return ""
            if not isinstance(v, (str, int, float)):
                raise ValueError("template '%s': '%s' must be a string" % (name, key))
            return str(v)

        def _bool(key):
            v = d.get(key)
            if v is None:
                return None
            if not isinstance(v, bool):
                raise ValueError("template '%s': '%s' must be true or false, got %r"
                                 % (name, key, v))
            return v

        defaults = d.get("defaults")
        if defaults is None:
            defaults = {}
        elif not isinstance(defaults, dict):
            raise ValueError("template '%s': 'defaults' must be a mapping" % name)

        ref = d.get("ref")
        return cls(
            name=name,
            src=src,
            ref=None if ref is None else str(ref),
            description=_str("description"),
            category=_str("category"),
            hidden=bool(_bool("hidden")),
            trusted=_bool("trust"),
            defaults=dict(defaults),
            next_steps=_str("next_steps"),
            origin=origin)


def resolve_src(src: str, base_dir: Optional[str] = None) -> str:
    """Expand ``~`` and make a relative local *src* absolute against *base_dir*.

    URLs and copier shorthands are returned unchanged.
    """
    if is_remote_src(src):
        return src
    src = os.path.expanduser(src)
    if not os.path.isabs(src):
        src = os.path.join(base_dir if base_dir else os.getcwd(), src)
    return os.path.normpath(src)

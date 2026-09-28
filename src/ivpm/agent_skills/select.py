#****************************************************************************
#* select.py
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
"""Skills an environment offers, and selectors that pick among them.

A selector is one of (shell-style globs allowed everywhere):

- ``<name>``            the skill's frontmatter name, or its entry-point name
                        (which selects every skill that entry point provides)
- ``<ep>/<skill>``      fully qualified: one skill of one provider
- ``dist:<name>``       every skill from a distribution
"""
import dataclasses as dc
import fnmatch
import os
import re
from typing import Iterable, List, Optional

from . import frontmatter
from .query import QueryResult


def _norm_dist(name: Optional[str]) -> str:
    # PEP 503 normalization, so dist:Foo_Bar matches foo-bar
    return re.sub(r"[-_.]+", "-", name or "").lower()


@dc.dataclass
class Available(object):
    """One skill (or plugin) an environment offers."""
    #: Frontmatter name (skills) or directory name (plugins)
    name: str
    #: Entry-point name, or None for share/agent-skills
    ep_name: Optional[str]
    dist: Optional[str]
    version: Optional[str]
    path: str
    description: str = ""
    #: 'skill' or 'plugin'
    kind: str = "skill"

    @property
    def provider(self) -> str:
        """'ep:<name>' or 'share' -- how the skill was found."""
        return "ep:%s" % self.ep_name if self.ep_name is not None else "share"

    @property
    def qualified(self) -> str:
        return "%s/%s" % (self.ep_name if self.ep_name is not None else "share", self.name)

    @property
    def key(self) -> tuple:
        """Identity used by the state file: (provider, name)."""
        return (self.provider, self.name)


def available_from_query(result: QueryResult) -> List[Available]:
    """Every valid skill and plugin in a query result, deduplicated by path.

    Entry points come first, so a directory offered by an entry point and by
    share/agent-skills is attributed to the entry point.
    """
    out: List[Available] = []
    seen = set()

    def add(av: Available):
        key = os.path.realpath(av.path)
        if key in seen:
            return
        seen.add(key)
        out.append(av)

    for ep in result.entries:
        for path in ep.dirs:
            if ep.kind == "plugins":
                add(Available(name=os.path.basename(os.path.normpath(path)),
                              ep_name=ep.name, dist=ep.dist, version=ep.version,
                              path=path, kind="plugin"))
                continue
            fields = frontmatter.parse(os.path.join(path, "SKILL.md")) or {}
            if not fields.get("name") or not fields.get("description"):
                continue
            add(Available(name=fields["name"], ep_name=ep.name, dist=ep.dist,
                          version=ep.version, path=path,
                          description=fields.get("description", "")))
    for sh in result.share:
        fields = frontmatter.parse(os.path.join(sh.dir, "SKILL.md")) or {}
        if not fields.get("name") or not fields.get("description"):
            continue
        add(Available(name=fields["name"], ep_name=None, dist=sh.dist,
                      version=sh.version, path=sh.dir,
                      description=fields.get("description", "")))
    return out


def matches(selector: str, av: Available, dest_name: Optional[str] = None) -> bool:
    """True when ``selector`` selects ``av``."""
    if selector.startswith("dist:"):
        return bool(av.dist) and fnmatch.fnmatchcase(
            _norm_dist(av.dist), _norm_dist(selector[5:]))
    if "/" in selector:
        prov, _, name = selector.partition("/")
        prov_name = av.ep_name if av.ep_name is not None else "share"
        return fnmatch.fnmatchcase(prov_name, prov) and fnmatch.fnmatchcase(av.name, name)
    for field in (dest_name, av.name, av.ep_name):
        if field is not None and fnmatch.fnmatchcase(field, selector):
            return True
    return False


def select(selectors: Iterable[str], available: List[Available]) -> List[Available]:
    """Everything any selector matches, in ``available`` order."""
    sels = list(selectors)
    return [av for av in available if any(matches(s, av) for s in sels)]


class SelectionError(Exception):
    pass


def resolve(selectors: Iterable[str], available: List[Available]) -> List[Available]:
    """Like ``select``, but strict, for commands that act on the result.

    A selector that matches nothing is an error. So is a bare name that picks
    two *different* skills with the same name from different providers: the
    user must say which, with the qualified ``<ep>/<skill>`` form.
    """
    chosen: List[Available] = []
    for sel in selectors:
        hits = [av for av in available if matches(sel, av)]
        if not hits:
            raise SelectionError("'%s' matches no available skill" % sel)
        is_plain = not sel.startswith("dist:") and "/" not in sel \
            and not any(c in sel for c in "*?[")
        if is_plain:
            by_name = {}
            for av in hits:
                by_name.setdefault(av.name, []).append(av)
            for name, avs in by_name.items():
                # Selecting an entry point by name legitimately yields several
                # skills; the same *skill name* from two providers does not.
                if len(avs) > 1 and name == sel:
                    raise SelectionError(
                        "'%s' is ambiguous; use one of: %s"
                        % (sel, ", ".join(av.qualified for av in avs)))
        for av in hits:
            if av not in chosen:
                chosen.append(av)
    return chosen

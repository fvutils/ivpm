#****************************************************************************
#* state.py
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
"""State of the two managers that write into skill directories.

``ivpm skills`` records what it selected and wrote in
``<dir>/.agents/ivpm-skills.json``, a file meant to be checked in: its
``selections`` (and ``with``) hold no paths, so a teammate can re-create the
same install with ``ivpm skills sync``.

The ``agents`` handler records what ``ivpm update`` wrote under
``<deps_dir>/ivpm.json["handlers"]["agents"]``.

Each manager removes only what its own state says it wrote, and neither
replaces an entry the other owns.
"""
import dataclasses as dc
import json
import os
from typing import Dict, List, Optional, Set

STATE_REL = os.path.join(".agents", "ivpm-skills.json")
STATE_VERSION = 1


@dc.dataclass
class Selection(object):
    #: 'ep:<entry-point name>' or 'share'
    provider: str
    #: Frontmatter name of the skill (directory name for a plugin)
    skill: str
    dist: Optional[str] = None
    #: Installed under this name instead of ``skill``
    as_name: Optional[str] = None

    @property
    def key(self) -> tuple:
        return (self.provider, self.skill)

    @property
    def dest_name(self) -> str:
        return self.as_name or self.skill

    def to_json(self) -> dict:
        d = {"provider": self.provider, "skill": self.skill}
        if self.dist:
            d["dist"] = self.dist
        if self.as_name:
            d["as"] = self.as_name
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Selection":
        return cls(provider=d["provider"], skill=d["skill"],
                   dist=d.get("dist"), as_name=d.get("as"))


@dc.dataclass
class InstalledEntry(object):
    name: str
    mode: str = "link"          # 'link' or 'copy'
    #: Content hash of a copy when it was made (see install.content_hash)
    hash: Optional[str] = None

    def to_json(self) -> dict:
        d = {"name": self.name, "mode": self.mode}
        if self.hash:
            d["hash"] = self.hash
        return d

    @classmethod
    def from_json(cls, d) -> "InstalledEntry":
        if isinstance(d, str):
            return cls(name=d)
        return cls(name=d["name"], mode=d.get("mode", "link"), hash=d.get("hash"))


@dc.dataclass
class SkillsState(object):
    agents: List[str] = dc.field(default_factory=list)
    mode: str = "link"
    #: Package specs 'ivpm skills --with' built the environment from
    with_specs: List[str] = dc.field(default_factory=list)
    selections: List[Selection] = dc.field(default_factory=list)
    #: state key ('agents_skills', 'claude_skills', ...) -> entries written
    installed: Dict[str, List[InstalledEntry]] = dc.field(default_factory=dict)
    #: Informational and machine-specific: interpreter and dist versions
    resolved: dict = dc.field(default_factory=dict)

    def installed_names(self, key: Optional[str] = None) -> Set[str]:
        keys = [key] if key else list(self.installed.keys())
        return {e.name for k in keys for e in self.installed.get(k, [])}

    def installed_map(self) -> Dict[str, List[str]]:
        return {k: [e.name for e in v] for k, v in self.installed.items()}

    def find(self, key: tuple) -> Optional[Selection]:
        for sel in self.selections:
            if sel.key == key:
                return sel
        return None

    def to_json(self) -> dict:
        d = {"version": STATE_VERSION,
             "agents": list(self.agents),
             "mode": self.mode}
        if self.with_specs:
            d["with"] = list(self.with_specs)
        d["selections"] = [s.to_json() for s in self.selections]
        d["installed"] = {k: [e.to_json() for e in v]
                          for k, v in sorted(self.installed.items()) if v}
        if self.resolved:
            d["resolved"] = self.resolved
        return d

    @classmethod
    def from_json(cls, d: dict) -> "SkillsState":
        version = d.get("version", 1)
        if version > STATE_VERSION:
            raise ValueError("state file version %s is newer than this IVPM "
                             "understands (%d); upgrade IVPM" % (version, STATE_VERSION))
        return cls(
            agents=list(d.get("agents", [])),
            mode=d.get("mode", "link"),
            with_specs=list(d.get("with", [])),
            selections=[Selection.from_json(s) for s in d.get("selections", [])],
            installed={k: [InstalledEntry.from_json(e) for e in v]
                       for k, v in (d.get("installed") or {}).items()},
            resolved=dict(d.get("resolved") or {}))


def state_path(root: str) -> str:
    return os.path.join(root, STATE_REL)


def load(root: str) -> Optional[SkillsState]:
    """The ``ivpm skills`` state under ``root``, or None when there is none."""
    path = state_path(root)
    if not os.path.isfile(path):
        return None
    with open(path) as fh:
        return SkillsState.from_json(json.load(fh))


def load_quiet(root: str) -> Optional[SkillsState]:
    """Like ``load``, but a damaged file reads as absent."""
    try:
        return load(root)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save(root: str, state: SkillsState):
    """Write atomically: a crash never leaves a half-written state file."""
    path = state_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state.to_json(), fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def handler_installed(deps_dir: Optional[str]) -> Dict[str, List[str]]:
    """What ``ivpm update``'s agents handler last wrote, by state key."""
    if not deps_dir:
        return {}
    path = os.path.join(deps_dir, "ivpm.json")
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    agents = (data.get("handlers") or {}).get("agents") or {}
    return {k: list(v) for k, v in agents.items() if isinstance(v, list)}

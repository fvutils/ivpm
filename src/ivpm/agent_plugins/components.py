#****************************************************************************
#* components.py
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
"""What a plugin carries beyond skills, and which of it runs code.

Skills and MCP servers are the only components with a meaning outside Claude
Code.  Everything else a plugin directory may hold -- commands, subagents,
hooks, LSP servers, ``bin/`` -- is Claude Code's own, and reaches it only
through the installed plugin (``.claude/skills/<name>/``).

Some of those components *execute*: hooks run shell commands on agent events,
LSP servers and monitors are launched as processes, and ``bin/`` is put on the
agent's PATH.  A plugin arrives through the transitive dependency graph, so
these are held back unless the project opts in, exactly as MCP servers are.

This module only *describes*.  The installer (``agent_skills.install``) and
``ivpm show plugins`` both consult it, so what is reported as left out is what
was actually left out.
"""
import copy
import os
from typing import Dict, List, Mapping, Optional, Tuple

from .manifest import PluginManifest

#: Component kinds, in report order.
MCP = "mcp"
HOOKS = "hooks"
LSP = "lsp"
BIN = "bin"
MONITORS = "monitors"
COMMANDS = "commands"
AGENTS = "agents"
OUTPUT_STYLES = "output-styles"
WORKFLOWS = "workflows"
THEMES = "themes"
SETTINGS = "settings"

#: Kinds gated by ``with.agents.executables``.
EXECUTABLE_KINDS = (HOOKS, LSP, BIN, MONITORS)

#: Kinds only Claude Code understands, installed with the plugin as they are.
CLAUDE_ONLY_KINDS = (COMMANDS, AGENTS, OUTPUT_STYLES, WORKFLOWS, THEMES, SETTINGS)

#: kind -> top-level entries of the plugin root that make it up
_ENTRIES = {
    MCP: ("mcp.json", ".mcp.json"),
    HOOKS: ("hooks",),
    LSP: (".lsp.json",),
    BIN: ("bin",),
    MONITORS: ("monitors",),
    COMMANDS: ("commands",),
    AGENTS: ("agents",),
    OUTPUT_STYLES: ("output-styles",),
    WORKFLOWS: ("workflows",),
    THEMES: ("themes",),
    SETTINGS: ("settings.json",),
}

#: kind -> Claude manifest keys that declare it (dotted for nested keys)
_MANIFEST_KEYS = {
    MCP: ("mcpServers",),
    HOOKS: ("hooks",),
    LSP: ("lspServers",),
    MONITORS: ("experimental.monitors",),
    COMMANDS: ("commands",),
    AGENTS: ("agents",),
    OUTPUT_STYLES: ("outputStyles",),
    WORKFLOWS: ("workflows",),
    THEMES: ("experimental.themes",),
}

_ORDER = (MCP,) + EXECUTABLE_KINDS + CLAUDE_ONLY_KINDS


def find(manifest: PluginManifest) -> Dict[str, Tuple[str, ...]]:
    """Map each component kind present to where it was found.

    Locations are ``<entry>`` for a top-level file or directory of the plugin
    root and ``plugin.json#<key>`` for a declaration in the Claude manifest.
    Kinds that are absent do not appear.
    """
    out: Dict[str, Tuple[str, ...]] = {}
    for kind in _ORDER:
        where: List[str] = []
        for entry in _ENTRIES.get(kind, ()):
            if os.path.lexists(os.path.join(manifest.root_dir, entry)):
                where.append(entry)
        for key in _MANIFEST_KEYS.get(kind, ()):
            if _get(manifest.claude, key) is not None:
                where.append("plugin.json#%s" % key)
        if where:
            out[kind] = tuple(where)
    return out


def executables_default(kind: str) -> bool:
    """Default for ``executables`` when the project has not set it.

    A plugin in the project itself was written by the project, so its hooks
    are trusted; one that arrived as a dependency is not.
    """
    return kind in ("plugin-project", "project")


def omitted(components: Mapping[str, Tuple[str, ...]],
            emit_mcp: bool, emit_exec: bool) -> Dict[str, str]:
    """Kinds held back from the installed plugin, mapped to the key that
    would enable them."""
    out: Dict[str, str] = {}
    for kind in components:
        if kind == MCP and not emit_mcp:
            out[kind] = "mcp"
        elif kind in EXECUTABLE_KINDS and not emit_exec and not _bin_needed(kind, emit_mcp):
            out[kind] = "executables"
    return out


def _bin_needed(kind: str, emit_mcp: bool) -> bool:
    # MCP servers conventionally live in bin/ ('./bin/server'). A project that
    # enabled MCP has already agreed to run this plugin's code, and without
    # bin/ those servers could not start.
    return kind == BIN and emit_mcp


def skipped_entries(omit: Mapping[str, str]) -> Tuple[str, ...]:
    """Top-level plugin entries to leave out of the installed plugin."""
    return tuple(e for kind in omit for e in _ENTRIES.get(kind, ()))


def filter_manifest(data: Optional[Mapping], omit: Mapping[str, str]
                    ) -> Tuple[Optional[dict], bool]:
    """Return ``(manifest, changed)`` with omitted kinds' declarations removed.

    ``changed`` is False when nothing had to be removed, so the caller can
    ship the original file byte for byte.
    """
    if data is None:
        return (None, False)
    out = copy.deepcopy(dict(data))
    changed = False
    for kind in omit:
        for key in _MANIFEST_KEYS.get(kind, ()):
            if _pop(out, key):
                changed = True
    return (out, changed)


def _get(data: Optional[Mapping], dotted: str):
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _pop(data: dict, dotted: str) -> bool:
    parts = dotted.split(".")
    cur = data
    for part in parts[:-1]:
        cur = cur.get(part) if isinstance(cur, dict) else None
        if cur is None:
            return False
    if isinstance(cur, dict) and parts[-1] in cur:
        del cur[parts[-1]]
        return True
    return False

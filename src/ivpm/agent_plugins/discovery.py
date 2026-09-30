#****************************************************************************
#* discovery.py
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
"""Locating Agent Plugins inside an IVPM package.

One implementation, two consumers: ``ivpm show plugins`` (which reports what
*would* be wired up) and the agents handler (which wires it up).  Keeping them
on the same code path is the point -- a report that disagrees with the handler
would be worse than no report.

Discovery is either *explicit* (glob patterns from ``with.agents.plugins`` or a
dep entry) or an *auto-probe* of the conventional locations.  The distinction
controls severity, not logic: pointing a pattern at something that turns out
not to be an Agent Plugins manifest is a mistake worth warning about, whereas
an auto-probe finding an unrelated ``plugin.json`` is entirely normal and stays
quiet.
"""
import dataclasses as dc
import os
from typing import List, Mapping, Optional, Sequence, Tuple

from .._compat import glob_rel
from .manifest import (
    Diagnostic, PluginManifest, iter_skill_dirs, load_claude_data, load_plugin,
    normalize_plugin_path, within,
)
from .mcp import McpConfig, load_mcp
from . import components as _components

#: Conventional locations probed when no explicit patterns are configured:
#: Agent Plugins manifests, then Claude Code's. A plugin carrying both is
#: matched twice and loaded once (the dedup below keys on the plugin root).
PROBE_PATTERNS = ("plugin.json", "plugins/*/plugin.json",
                  ".claude-plugin/plugin.json", "plugins/*/.claude-plugin/plugin.json")


@dc.dataclass(frozen=True)
class DiscoveredPlugin:
    """A plugin found in a package, with its components already resolved."""
    #: IVPM package (or project, or entry-point) that supplied this plugin.
    owner_name: str
    #: "project" | "dependency" | "entry-point"
    kind: str
    manifest: PluginManifest
    skill_dirs: Tuple[str, ...] = ()
    mcp: Optional[McpConfig] = None
    diagnostics: Tuple[Diagnostic, ...] = ()
    #: Components beyond skills (see agent_plugins.components.find)
    components: Mapping[str, Tuple[str, ...]] = dc.field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def root_dir(self) -> str:
        return self.manifest.root_dir


def discover(owner_name: str,
             root_dir: str,
             patterns: Optional[Sequence[str]] = None,
             kind: str = "dependency",
             with_mcp: bool = True,
             overlays: Optional[Mapping[str, Mapping]] = None
             ) -> Tuple[List[DiscoveredPlugin], List[Diagnostic]]:
    """Find plugins provided by the package rooted at ``root_dir``.

    ``patterns`` are globs matching ``plugin.json`` files, relative to
    ``root_dir``; None selects the auto-probe locations.  A pattern that
    matches a *directory* containing a manifest is also accepted, so an
    entry-point or a hand-written config may use either spelling.

    ``overlays`` maps a plugin's path relative to ``root_dir`` (``"."`` for
    the root) to manifest data supplied for it by a marketplace (see
    ``apply_overlay``).  A directory with an overlay is a plugin even without
    a manifest of its own.

    Returns ``(plugins, diagnostics)``.  Diagnostics not attributable to a
    specific plugin (a pattern that matched nothing, a path that escaped the
    package) come back in the second element.
    """
    overlays = overlays or {}
    explicit = patterns is not None
    pattern_list = list(patterns) if explicit else list(PROBE_PATTERNS)

    diags: List[Diagnostic] = []
    found: List[DiscoveredPlugin] = []
    seen = set()

    for pattern in pattern_list:
        matches = sorted(glob_rel(pattern, root_dir, recursive=True))
        if not matches and explicit:
            diags.append(Diagnostic(
                "warning", "plugins.no-match",
                "package %s: plugin pattern '%s' matched nothing"
                % (owner_name, pattern)))
            continue

        for match in matches:
            plugin_root = normalize_plugin_path(os.path.join(root_dir, match))
            if plugin_root is None and _rel_key(root_dir, os.path.join(root_dir, match)) \
                    in overlays and os.path.isdir(os.path.join(root_dir, match)):
                plugin_root = os.path.abspath(os.path.join(root_dir, match))
            if plugin_root is None:
                if explicit:
                    diags.append(Diagnostic(
                        "warning", "plugins.not-a-plugin-path",
                        "package %s: '%s' is neither a plugin manifest nor a "
                        "directory containing one" % (owner_name, match)))
                continue

            # A dep entry must not be able to reach outside the package it
            # describes -- otherwise a manifest could point anywhere on disk.
            if not within(root_dir, plugin_root):
                diags.append(Diagnostic(
                    "warning", "plugins.escapes-package",
                    "package %s: plugin path '%s' resolves outside the package; "
                    "ignored" % (owner_name, match)))
                continue

            key = os.path.realpath(plugin_root)
            if key in seen:
                continue
            seen.add(key)

            plugin, pdiags = load_at(plugin_root, owner_name, kind,
                                     explicit=explicit, with_mcp=with_mcp,
                                     overlay=overlays.get(_rel_key(root_dir, plugin_root)))
            if plugin is None:
                diags.extend(pdiags)
            else:
                found.append(plugin)

    return (found, diags)


def resolve_patterns(pkg_dir: str,
                     dep_agents_config: Optional[dict] = None) -> Optional[List[str]]:
    """Resolve the plugin glob patterns that apply to a package.

    Priority, highest first (mirroring how skill patterns resolve):

    1. the consuming project's dep entry (``agents: {plugins: [...]}``)
    2. the package's own ``ivpm.yaml`` (``with.agents.plugins``)
    3. None -- the caller falls through to the auto-probe locations
    """
    if dep_agents_config and dep_agents_config.get("plugins") is not None:
        return [str(p) for p in dep_agents_config["plugins"]]

    if not os.path.isfile(os.path.join(pkg_dir, "ivpm.yaml")):
        return None
    try:
        from ..proj_info import ProjInfo
        info = ProjInfo.mkFromProj(pkg_dir)
    except Exception:
        return None
    if info is None:
        return None
    cfg = info.handler_configs.get("agents", {}) or {}
    plugins = cfg.get("plugins", None)
    return [str(p) for p in plugins] if plugins is not None else None


def load_at(plugin_root: str,
            owner_name: str,
            kind: str = "dependency",
            explicit: bool = True,
            with_mcp: bool = True,
            overlay: Optional[Mapping] = None) -> Tuple[Optional[DiscoveredPlugin], List[Diagnostic]]:
    """Load one plugin from a known root, resolving its components.

    ``explicit`` says whether someone named this path on purpose.  When they
    did, "this is not an Agent Plugins manifest" is escalated from an
    informational note to a warning: silence would leave the user wondering why
    their configured plugin never appeared.
    """
    if overlay is not None:
        manifest, diags = apply_overlay(plugin_root, overlay)
    else:
        manifest, diags = load_plugin(plugin_root)
    if manifest is None:
        return (None, _escalate(diags) if explicit else diags)

    skill_dirs, sdiags = iter_skill_dirs(manifest)
    diags.extend(sdiags)

    mcp_cfg = None
    if with_mcp:
        mcp_cfg, mdiags = load_mcp(manifest)
        diags.extend(mdiags)

    return (DiscoveredPlugin(
        owner_name=owner_name,
        kind=kind,
        manifest=manifest,
        skill_dirs=tuple(skill_dirs),
        mcp=mcp_cfg,
        diagnostics=tuple(diags),
        components=_components.find(manifest),
    ), diags)


def load_from_reference(path: str,
                        owner_name: str,
                        kind: str = "entry-point",
                        with_mcp: bool = True) -> Tuple[Optional[DiscoveredPlugin], List[Diagnostic]]:
    """Load a plugin named by either spelling: a manifest path or a root dir.

    Used for ``agent.plugins`` entry-point results and for
    ``ivpm show plugins --check``, where the reference comes from outside a
    package tree and no containment check applies.
    """
    plugin_root = normalize_plugin_path(path)
    if plugin_root is None:
        return (None, [Diagnostic(
            "warning", "plugins.not-a-plugin-path",
            "%s: '%s' is neither a plugin manifest nor a directory containing one"
            % (owner_name, path))])
    return load_at(plugin_root, owner_name, kind, explicit=True, with_mcp=with_mcp)


def apply_overlay(plugin_root: str, overlay: Mapping
                  ) -> Tuple[Optional[PluginManifest], List[Diagnostic]]:
    """Load a plugin whose marketplace entry supplies manifest data.

    ``overlay`` is ``{"strict": bool, "fields": {...}}``.  With ``strict``
    the plugin's own manifest is the authority and the entry only fills gaps
    (``description``, ``version``); without it -- or when the plugin has no
    manifest at all -- the entry *is* the manifest, as Claude Code treats it.
    """
    fields = dict(overlay.get("fields") or {})
    strict = overlay.get("strict", True) is not False
    has_own = normalize_plugin_path(plugin_root) is not None

    if strict and has_own:
        manifest, diags = load_plugin(plugin_root)
        if manifest is None:
            return (manifest, diags)
        fill = {}
        for key in ("description", "version"):
            if getattr(manifest, key) is None and isinstance(fields.get(key), str):
                fill[key] = fields[key]
        return (dc.replace(manifest, **fill) if fill else manifest, diags)

    where = "marketplace entry for %s" % plugin_root
    return load_claude_data(plugin_root, fields, where, synthesized=True)


def _rel_key(root_dir: str, path: str) -> str:
    """``path`` relative to ``root_dir`` in the form overlays are keyed by."""
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root_dir))
    return rel.replace(os.sep, "/")


def _escalate(diags: List[Diagnostic]) -> List[Diagnostic]:
    """Raise 'not an Agent Plugins manifest' from info to warning."""
    return [dc.replace(d, severity="warning")
            if d.code == "manifest.not-a-plugin" and d.severity == "info" else d
            for d in diags]

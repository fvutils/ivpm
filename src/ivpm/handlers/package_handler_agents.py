#****************************************************************************
#* package_handler_agents.py
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
import dataclasses as dc
import glob as _glob
import json
import logging
import os
import re
import shutil
from typing import Dict, List, Optional, Tuple

from ..package import Package
from ..project_ops_info import ProjectUpdateInfo
from .package_handler import PackageHandler, ToolchainSupport
from .handler_phases import HandlerPhase

_logger = logging.getLogger("ivpm.handlers.package_handler_agents")

# Frontmatter delimited by lines containing only '---'
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_FIELD_RE = re.compile(r"^(\w[\w-]*):\s*(.+)$", re.MULTILINE)

# Agent-tool mirror targets in addition to the always-present .agents/skills/,
# as (config_key, subdir, plugin_strategy). Each is opt-out (default-on) via
# package.with.agents; an explicit False (e.g. claude: false) skips the target.
#
# plugin_strategy says how an Agent Plugin reaches this tool:
#
#   "install"  -- the tool has a real plugin mechanism, so the plugin is
#                 materialized whole and the tool namespaces its skills
#                 itself. Claude Code loads any directory under a skills
#                 directory that contains .claude-plugin/plugin.json as
#                 "<name>@skills-dir".
#   "unbundle" -- the tool understands individual skills only, so a plugin's
#                 skills are linked one by one.
#
# The two are mutually exclusive per tool: doing both would surface every
# skill twice, once namespaced by the plugin and once bare.
#
# .agents/skills/ is always "unbundle". That is also what Codex wants -- it
# scans .agents/skills from the working directory up to the repository root --
# so Codex needs no mirror of its own.
_TOOL_TARGETS = (
    ("claude", os.path.join(".claude", "skills"), "install"),
    ("cursor", os.path.join(".cursor", "skills"), "unbundle"),
)

# Precedence used when the same skill directory is discovered by more than one
# mechanism (see _dedup_entries). Lower sorts first / wins. Plugin-owned
# entries outrank bare ones: a plugin supplies a real name and provenance.
_KIND_RANK = {
    "plugin-project": 0,
    "plugin-dependency": 1,
    "project": 2,
    "dependency": 3,
}

# Claude Code's manifest location within a plugin. Its schema and the Agent
# Plugins schema share every field name we emit, and Claude Code documents
# that it ignores unrecognized top-level fields, so the Agent Plugins manifest
# is copied across verbatim rather than rewritten.
_CLAUDE_MANIFEST_DIR = ".claude-plugin"
_CLAUDE_MCP_NAME = ".mcp.json"


def _parse_frontmatter(path: str) -> Optional[Dict[str, str]]:
    """Return a dict of frontmatter fields, or None on failure."""
    try:
        with open(path) as fh:
            content = fh.read()
    except OSError as exc:
        _logger.warning("Could not read %s: %s", path, exc)
        return None

    m = _FRONTMATTER_RE.match(content)
    if not m:
        return None

    fields: Dict[str, str] = {}
    for fm in _FIELD_RE.finditer(m.group(1)):
        fields[fm.group(1)] = fm.group(2).strip()
    return fields


def _symlinks_supported(dest_parent: str) -> bool:
    """Return True if the filesystem at dest_parent supports symlinks."""
    probe = os.path.join(dest_parent, ".ivpm_symlink_probe")
    try:
        os.symlink(".", probe)
        os.remove(probe)
        return True
    except (OSError, NotImplementedError):
        return False


def _copy_skill_dir(src_dir: str, dest_dir: str):
    """Fallback copy: SKILL.md plus companion directories."""
    os.makedirs(dest_dir, exist_ok=True)
    src = os.path.join(src_dir, "SKILL.md")
    if os.path.isfile(src):
        shutil.copy2(src, os.path.join(dest_dir, "SKILL.md"))
    for companion in ("scripts", "references", "assets"):
        src = os.path.join(src_dir, companion)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(dest_dir, companion), dirs_exist_ok=True)


@dc.dataclass(frozen=True)
class SkillEntry(object):
    kind: str
    owner_name: str
    root_dir: str
    skill_dir: str
    #: Set when this skill came from an Agent Plugin. Drives naming, and lets
    #: an "install"-strategy tool skip the skill (it gets the whole plugin).
    plugin_name: Optional[str] = None


@dc.dataclass
class PackageHandlerAgents(PackageHandler):
    name               = "agents"
    description        = "Creates .agents/skills/ symlinks (or copies) for deps that provide SKILL.md files"
    leaf_when          = None
    root_when          = None
    # INTEGRATE runs after INSTALL (the phase barrier), so the python venv is
    # already populated when we discover agent.skills entry-points.
    phase              = HandlerPhase.INTEGRATE
    conditions_summary = (
        "leaf: all non-PyPI packages; "
        "root: always (cleans stale entries even when no skills are present)"
    )
    # Every root-phase artifact this handler produces (.agents/, .claude/,
    # .cursor/) is project-scoped. In a shared tool directory there is no
    # project to attach them to, so the handler is skipped outright rather
    # than scattering agent config through the tool tree.
    toolchain_support  = ToolchainSupport.UNSUPPORTED

    @classmethod
    def handler_info(cls):
        from ..show.info_types import HandlerInfo
        return HandlerInfo(
            name=cls.name,
            description=cls.description,
            phase=cls.phase,
            conditions=cls.conditions_summary,
            toolchain_support=cls.toolchain_support.value,
            notes="\n".join([
                "Skills:",
                "  Creates symlinks from .agents/skills/<pkg> to the directory containing",
                "  each dependency's SKILL.md. Symlinks are relative when the target is",
                "  inside the project tree, absolute otherwise. Falls back to directory",
                "  copy on platforms without symlink support.",
                "  Skill paths support glob patterns (e.g. skills/**/SKILL.md).",
                "  Python packages may register skills via the 'agent.skills' entry-point",
                "  group (legacy 'ivpm.skill' is also accepted); each entry-point is a",
                "  callable returning a path or list of paths to directories with SKILL.md.",
                "  A skill discovered by more than one mechanism is linked once.",
                "",
                "Agent Plugins (agent-plugins.org):",
                "  A plugin.json at a package root or under plugins/*/ -- or matched by",
                "  with.agents.plugins, a dep entry, or the 'agent.plugins' entry-point",
                "  group -- is validated against the specification and linked whole into",
                "  .agents/plugins/<name>. A plugin's skills are then projected per tool:",
                "  tools with their own plugin mechanism receive the plugin installed",
                "  (Claude Code: .claude/skills/<name>/ with .claude-plugin/plugin.json),",
                "  tools without one receive each skill as <plugin>-<skill>. The two are",
                "  mutually exclusive per tool. An unrelated plugin.json is ignored",
                "  silently; a malformed Agent Plugins manifest warns.",
                "  MCP servers declared by a plugin are opt-in (mcp: true); review them",
                "  first with 'ivpm show plugins --mcp'.",
                "",
                "Targets:",
                "  .agents/skills/ and .agents/plugins/ are always populated. Mirroring to",
                "  .claude/ and .cursor/ is opt-out via claude: false / cursor: false under",
                "  package.with.agents.",
            ]),
        )

    # All discovered skills, with enough context to derive human-readable link names
    skill_entries: List[SkillEntry] = dc.field(default_factory=list)
    # All discovered Agent Plugins (agent_plugins.discovery.DiscoveredPlugin)
    plugin_entries: List[object] = dc.field(default_factory=list)
    _managed_names: List[str] = dc.field(default_factory=list, init=False, repr=False)
    _managed_plugins: List[str] = dc.field(default_factory=list, init=False, repr=False)
    # state key -> names written under it this run
    _managed: dict = dc.field(default_factory=dict, init=False, repr=False)
    _prev_state: dict = dc.field(default_factory=dict, init=False, repr=False)

    def reset(self):
        self.skill_entries = []
        self.plugin_entries = []
        self._managed_names = []
        self._managed_plugins = []
        self._managed = {}

    def on_root_pre_load(self, update_info: ProjectUpdateInfo):
        self.reset()
        self._prev_state = update_info.handler_state.get("agents", {})

    def on_leaf_post_load(self, pkg: Package, update_info: ProjectUpdateInfo):
        if not hasattr(pkg, "path") or pkg.path is None:
            return
        if getattr(pkg, "src_type", None) == "pypi":
            return

        # Plugins first: a plugin is a strictly more informative answer than a
        # loose skill directory, and its skills are enumerated from its own
        # manifest rather than guessed at.
        found_plugins = self._discover_plugins(
            pkg.name, pkg.path, "plugin-dependency",
            getattr(pkg, "agents_config", None))
        if found_plugins:
            with self._lock:
                self.plugin_entries.extend(found_plugins)

        patterns = self._get_skill_patterns(pkg)

        if patterns is not None:
            found = self._resolve_skill_dirs(pkg.name, pkg.path, patterns)
        else:
            # Priority 3: auto-probe (root SKILL.md + skills/ directory)
            found = self._auto_probe_skill_dirs(pkg.name, pkg.path)

        if found:
            entries = [
                SkillEntry("dependency", pkg.name, pkg.path, skill_dir)
                for skill_dir in found
            ]
            with self._lock:
                self.skill_entries.extend(entries)

    def on_root_post_load(self, update_info: ProjectUpdateInfo):
        # This handler is declared UNSUPPORTED for toolchain mode, so the
        # dispatcher never gets here without a project. project_root() (rather
        # than the _or_none form) makes any future path that does reach here
        # fail loudly instead of writing .agents/ into a shared tool tree.
        project_dir = update_info.project_root()
        agents_cfg = update_info.handler_configs.get("agents", {}) or {}

        expand_skills = bool(agents_cfg.get("expand_skills", True))
        plugin_install = bool(agents_cfg.get("plugin_install", True))
        emit_mcp = bool(agents_cfg.get("mcp", False))

        # .agents/skills/ is always populated and must stay targets[0] (used as
        # the symlink-support probe location below). Each tool target is
        # opt-out: enabled by default, skipped only when set to a false value.
        # Each target carries the strategy by which plugins reach it.
        targets = [(".agents", os.path.join(project_dir, ".agents", "skills"), "unbundle")]
        for cfg_key, subdir, strategy in _TOOL_TARGETS:
            if not bool(agents_cfg.get(cfg_key, True)):
                continue
            targets.append((cfg_key, os.path.join(project_dir, subdir),
                            strategy if plugin_install else "unbundle"))

        # Remove entries created by the previous run before writing new ones
        self._remove_managed(project_dir, self._prev_state)

        self.plugin_entries.extend(self._discover_project_plugins(update_info, project_dir))
        self.skill_entries.extend(self._discover_project_skills(update_info, project_dir))

        # Consume plugins pushed by the Python handler (agent.plugins entry-points)
        from ..agent_plugins import discovery as _discovery
        for ep_name, plugin_dir in getattr(update_info, 'pending_plugin_dirs', []):
            plugin, diags = _discovery.load_from_reference(
                plugin_dir, ep_name, kind="plugin-dependency")
            self._log_diags(diags, ep_name)
            if plugin is not None:
                self.plugin_entries.append(plugin)

        # Consume skills pushed by the Python handler (agent.skills entry-points)
        for ep_name, skill_dir in getattr(update_info, 'pending_skill_dirs', []):
            skill_file = os.path.join(skill_dir, "SKILL.md")
            if not os.path.isfile(skill_file):
                _logger.warning(
                    "agent.skills entrypoint '%s': no SKILL.md in %s", ep_name, skill_dir)
                continue
            if not self._validate_frontmatter(skill_file, ep_name):
                continue
            self.skill_entries.append(SkillEntry("dependency", ep_name, skill_dir, skill_dir))

        self.plugin_entries = self._dedup_plugins(self.plugin_entries)
        self.skill_entries = self._drop_plugin_owned(self.skill_entries, self.plugin_entries)
        if expand_skills:
            self.skill_entries.extend(self._expand_plugin_skills(self.plugin_entries))
        self.skill_entries = self._dedup_entries(self.skill_entries)

        if not self.skill_entries and not self.plugin_entries:
            return

        for _, tgt, _strategy in targets:
            os.makedirs(tgt, exist_ok=True)

        use_symlinks = _symlinks_supported(targets[0][1])
        deps_dir_norm = os.path.normpath(update_info.deps_dir)

        plugin_assigned = self._assign_plugin_names(self.plugin_entries)
        skill_assigned = self._assign_dest_names(self.skill_entries)

        # Names written per state key, so the next run cleans up exactly what
        # this one created -- which differs per target once "install" is in play.
        managed: Dict[str, List[str]] = {}

        # The neutral view: whole plugins, plus every skill individually.
        if plugin_assigned:
            plugins_dir = os.path.join(project_dir, ".agents", "plugins")
            os.makedirs(plugins_dir, exist_ok=True)
            for dest_name, plugin in plugin_assigned:
                self._link_dir(os.path.join(plugins_dir, dest_name), plugin.root_dir,
                               project_dir, deps_dir_norm, use_symlinks)
            managed["agents_plugins"] = [n for n, _ in plugin_assigned]

        n_installed = 0
        for cfg_key, tgt, strategy in targets:
            names: List[str] = []

            if strategy == "install":
                for dest_name, plugin in plugin_assigned:
                    if self._install_plugin(os.path.join(tgt, dest_name), plugin,
                                            project_dir, deps_dir_norm,
                                            use_symlinks, emit_mcp):
                        names.append(dest_name)
                        n_installed += 1

            for dest_name, entry in skill_assigned:
                # A plugin installed whole already carries this skill, namespaced
                # by the tool. Linking it here too would surface it twice.
                if strategy == "install" and entry.plugin_name is not None:
                    continue
                self._link_dir(os.path.join(tgt, dest_name), entry.skill_dir,
                               project_dir, deps_dir_norm, use_symlinks)
                names.append(dest_name)

            key = "agents_skills" if cfg_key == ".agents" else "%s_skills" % cfg_key
            managed[key] = names

        self._managed = managed
        self._managed_names = managed.get("agents_skills", [])
        self._managed_plugins = managed.get("agents_plugins", [])

        from ..utils import note
        parts = ["%d skill(s)" % len(skill_assigned)]
        if plugin_assigned:
            parts.append("%d plugin(s)" % len(plugin_assigned))
        if n_installed:
            parts.append("%d native plugin install(s)" % n_installed)
        note("Populated %s into %d target(s) (%s)" % (
            ", ".join(parts), len(targets), "symlinks" if use_symlinks else "copies"))

    def get_state_entries(self) -> dict:
        """Persist created entry names so the next run can clean them up."""
        # Only what this run actually wrote. A tool disabled *this* run needs no
        # entry: the run that wrote its files recorded them, and the first run
        # after the tool is disabled cleans up using that earlier state.
        # Recording names for a target we never wrote to would risk the next run
        # deleting a same-named entry the user created by hand.
        return {k: v for k, v in self._managed.items() if v}

    # ------------------------------------------------------------------ #
    # Agent Plugins
    # ------------------------------------------------------------------ #

    _UNSET = object()

    def _discover_plugins(self, owner_name: str, root_dir: str, kind: str,
                          dep_agents_config=None, patterns=_UNSET) -> List[object]:
        """Find Agent Plugins in a package (or in the project itself)."""
        from ..agent_plugins import discovery as _discovery
        if patterns is self._UNSET:
            patterns = _discovery.resolve_patterns(root_dir, dep_agents_config)
        plugins, diags = _discovery.discover(owner_name, root_dir, patterns, kind=kind)
        self._log_diags(diags, owner_name)
        for plugin in plugins:
            self._log_diags(plugin.diagnostics, owner_name)
        return plugins

    def _discover_project_plugins(self, update_info: ProjectUpdateInfo,
                                  project_dir: str) -> List[object]:
        project_name = update_info.project_name or os.path.basename(os.path.normpath(project_dir))
        agents_cfg = update_info.handler_configs.get("agents", {}) or {}
        patterns = agents_cfg.get("plugins", None)
        if patterns is not None:
            patterns = [str(p) for p in patterns]
        return self._discover_plugins(project_name, project_dir, "plugin-project",
                                      patterns=patterns)

    @staticmethod
    def _dedup_plugins(plugins: List[object]) -> List[object]:
        """Collapse plugins resolving to the same root.

        Two IVPM packages may vendor the same plugin, and an editable Python
        install resolves an ``agent.plugins`` entry-point back to a tree that is
        also reachable through deps_dir. Compared by realpath for the same
        reason ``_dedup_entries`` is: a cached dependency is a symlink into the
        shared cache, so one plugin has two spellings.
        """
        by_root: Dict[str, object] = {}
        for plugin in plugins:
            key = os.path.realpath(plugin.root_dir)
            prev = by_root.get(key)
            if prev is None:
                by_root[key] = plugin
                continue
            if _KIND_RANK.get(plugin.kind, 99) < _KIND_RANK.get(prev.kind, 99):
                by_root[key] = plugin
            _logger.debug("Plugin %s discovered more than once (%s and %s)",
                          key, prev.owner_name, plugin.owner_name)
        return list(by_root.values())

    @staticmethod
    def _drop_plugin_owned(entries: List[SkillEntry],
                           plugins: List[object]) -> List[SkillEntry]:
        """Remove loose skills that live inside a discovered plugin.

        A package whose root *is* a plugin has a ``skills/`` directory, which
        the ordinary auto-probe finds too. Those directories belong to the
        plugin: it names them and decides how they are projected. Dropping them
        here rather than relying on dedup matters because with
        ``expand_skills: false`` there is no plugin-owned entry to outrank them,
        and they would reappear under a package-derived name.
        """
        if not plugins:
            return entries

        from ..agent_plugins.manifest import within
        roots = [p.root_dir for p in plugins]
        kept = []
        for entry in entries:
            if entry.plugin_name is None and any(within(r, entry.skill_dir) for r in roots):
                _logger.debug("Skill %s belongs to a plugin; not linked separately",
                              entry.skill_dir)
                continue
            kept.append(entry)
        return kept

    @staticmethod
    def _expand_plugin_skills(plugins: List[object]) -> List[SkillEntry]:
        """Turn each plugin's skills into individually linkable entries.

        This is how a plugin reaches tools that understand skills but not
        plugins -- which today is most of them, including Codex by way of
        ``.agents/skills``.
        """
        entries: List[SkillEntry] = []
        for plugin in plugins:
            for skill_dir in plugin.skill_dirs:
                entries.append(SkillEntry(
                    kind=plugin.kind,
                    owner_name=plugin.owner_name,
                    root_dir=plugin.root_dir,
                    skill_dir=skill_dir,
                    plugin_name=plugin.name))
        return entries

    def _assign_plugin_names(self, plugins: List[object]) -> List[Tuple[str, object]]:
        """Name plugin links from the manifest, disambiguating by owner package.

        The manifest name is spec-constrained to a filesystem-safe charset, so
        it is a better link name than anything derived from a directory path.
        """
        ordered = sorted(plugins, key=lambda p: (p.kind, p.name, p.owner_name))
        candidates = [[p.name, "%s-%s" % (p.owner_name, p.name)] for p in ordered]
        names = self._resolve_names(
            candidates, [("plugin %s (from %s)" % (p.name, p.owner_name)) for p in ordered])
        return sorted(zip(names, ordered), key=lambda item: item[0])

    def _install_plugin(self, dest: str, plugin, project_dir: str,
                        deps_dir_norm: str, use_symlinks: bool,
                        emit_mcp: bool) -> bool:
        """Materialize a plugin in the form a plugin-aware tool expects.

        The tool loads a directory as a plugin when it finds
        ``.claude-plugin/plugin.json`` in it, so the installed directory is a
        thin shell: every top-level entry of the real plugin is linked through,
        and only the manifest (and optionally the MCP configuration) is
        generated. Linking rather than copying means edits to a dependency's
        skills are picked up without re-running ``ivpm update``.

        Returns True when the plugin was installed.
        """
        if os.path.exists(dest) or os.path.islink(dest):
            _logger.warning("Cannot install plugin '%s'; %s already exists",
                            plugin.name, dest)
            return False

        os.makedirs(dest, exist_ok=True)

        ships_own_manifest = os.path.isfile(
            os.path.join(plugin.root_dir, _CLAUDE_MANIFEST_DIR, "plugin.json"))

        for entry in sorted(os.listdir(plugin.root_dir)):
            # mcp.json is translated below rather than linked: the tool reads
            # '.mcp.json' and spells the plugin-root placeholder differently.
            if entry == "mcp.json":
                continue
            if entry == _CLAUDE_MANIFEST_DIR and not ships_own_manifest:
                continue
            src = os.path.join(plugin.root_dir, entry)
            self._link_dir(os.path.join(dest, entry), src, project_dir,
                           deps_dir_norm, use_symlinks, quiet=True)

        if not ships_own_manifest:
            # The Agent Plugins manifest is already a valid manifest for the
            # tool: the field names coincide and unrecognized top-level fields
            # (our '$schema', 'extensions') are ignored at load time. Copy it
            # verbatim rather than rewriting it.
            manifest_dir = os.path.join(dest, _CLAUDE_MANIFEST_DIR)
            os.makedirs(manifest_dir, exist_ok=True)
            try:
                shutil.copy2(os.path.join(plugin.root_dir, "plugin.json"),
                             os.path.join(manifest_dir, "plugin.json"))
            except OSError as exc:
                _logger.warning("Could not install manifest for plugin '%s': %s",
                                plugin.name, exc)
                return False

        if emit_mcp and plugin.mcp is not None and plugin.mcp.servers:
            self._write_tool_mcp(dest, plugin, project_dir)

        return True

    def _write_tool_mcp(self, dest: str, plugin, project_dir: str):
        """Translate the plugin's mcp.json into the tool's own MCP file.

        Only the spelling differs: the tool names the plugin root
        ``${CLAUDE_PLUGIN_ROOT}`` where Agent Plugins says ``${PLUGIN_ROOT}``,
        and has no equivalent of ``${PLUGIN_DATA}``, which is therefore
        resolved to the concrete per-plugin directory IVPM allocates.

        That directory is deliberately *not* tracked for cleanup: the
        specification requires plugin data to survive updates.
        """
        data_dir = os.path.join(project_dir, ".agents", "data", plugin.name)
        os.makedirs(data_dir, exist_ok=True)

        def subst(value: str) -> str:
            return (value
                    .replace("${PLUGIN_ROOT}", "${CLAUDE_PLUGIN_ROOT}")
                    .replace("${PLUGIN_DATA}", data_dir))

        servers = {}
        for srv in plugin.mcp.servers:
            if srv.type == "stdio":
                command = srv.command
                if command.startswith("./"):
                    command = "${CLAUDE_PLUGIN_ROOT}/" + command[2:]
                entry = {"type": "stdio", "command": subst(command)}
                if srv.args:
                    entry["args"] = [subst(a) for a in srv.args]
                if srv.env:
                    entry["env"] = {k: subst(v) for k, v in srv.env.items()}
                if srv.cwd:
                    cwd = srv.cwd
                    if cwd.startswith("./"):
                        cwd = "${CLAUDE_PLUGIN_ROOT}/" + cwd[2:]
                    entry["cwd"] = subst(cwd)
            else:
                entry = {"type": srv.type, "url": srv.url}
                if srv.headers:
                    entry["headers"] = dict(srv.headers)
            servers[srv.name] = entry

        try:
            with open(os.path.join(dest, _CLAUDE_MCP_NAME), "w") as fh:
                json.dump({"mcpServers": servers}, fh, indent=2)
                fh.write("\n")
        except OSError as exc:
            _logger.warning("Could not write MCP configuration for plugin '%s': %s",
                            plugin.name, exc)

    @staticmethod
    def _log_diags(diags, owner_name: str):
        """Route plugin diagnostics to the log. Info stays at debug level: it is
        dominated by 'this unrelated plugin.json is not an Agent Plugins
        manifest', which auto-probe is expected to encounter."""
        for d in diags:
            if d.severity == "info":
                _logger.debug("%s: %s", owner_name, d.message)
            else:
                _logger.warning("%s: %s", owner_name, d.message)

    # ------------------------------------------------------------------ #

    def _link_dir(self, dest: str, src: str, project_dir: str,
                  deps_dir_norm: str, use_symlinks: bool,
                  quiet: bool = False):
        """Link (or copy) one directory into place.

        Symlinks are relative when the source is inside the project tree and
        absolute otherwise, so a workspace stays relocatable while a
        shared-cache dependency still resolves.
        """
        if use_symlinks:
            src_norm = os.path.normpath(src)
            project_dir_norm = os.path.normpath(project_dir)
            if src_norm == project_dir_norm or src_norm.startswith(project_dir_norm + os.sep):
                link_target = os.path.relpath(src, os.path.dirname(dest))
            else:
                link_target = os.path.abspath(src)
            self._ensure_symlink(dest, link_target, src, deps_dir_norm, quiet=quiet)
        else:
            if os.path.isdir(src):
                self._ensure_copy(dest, src)
            elif not os.path.exists(dest):
                shutil.copy2(src, dest)

    def _get_skill_patterns(self, pkg) -> Optional[List[str]]:
        """Return skill glob patterns (priority 1 or 2), or None for auto-probe.

        Priority:
          1. pkg.agents_config['skills'] — consumer-specified via dep entry
          2. dep's own ivpm.yaml with.agents.skills
          3. None → caller falls through to auto-probe
        """
        # Priority 1: consumer dep-entry override
        dep_agents = getattr(pkg, "agents_config", None) or {}
        if dep_agents.get("skills") is not None:
            return [str(p) for p in dep_agents["skills"]]

        # Priority 2: dep's own ivpm.yaml
        if not os.path.isfile(os.path.join(pkg.path, "ivpm.yaml")):
            return None
        try:
            from ..proj_info import ProjInfo
            info = ProjInfo.mkFromProj(pkg.path)
        except Exception:
            return None
        if info is None:
            return None
        cfg = info.handler_configs.get("agents", {}) or {}
        skills_list = cfg.get("skills", None)
        return [str(p) for p in skills_list] if skills_list is not None else None

    def _discover_project_skills(self, update_info: ProjectUpdateInfo, project_dir: str) -> List[SkillEntry]:
        project_name = update_info.project_name or os.path.basename(os.path.normpath(project_dir))
        agents_cfg = update_info.handler_configs.get("agents", {}) or {}
        patterns = agents_cfg.get("skills", None)

        if patterns is not None:
            found = self._resolve_skill_dirs(
                project_name,
                project_dir,
                [str(p) for p in patterns])
        else:
            # Auto-probe: root SKILL.md + skills/ directory
            found = self._auto_probe_skill_dirs(project_name, project_dir)

        return [SkillEntry("project", project_name, project_dir, skill_dir) for skill_dir in found]

    def _resolve_skill_dirs(self, owner_name: str, root_dir: str, patterns: List[str]) -> List[str]:
        found = []
        seen = set()

        for pattern in patterns:
            matches = sorted(_glob.glob(pattern, root_dir=root_dir, recursive=True))
            if not matches:
                _logger.warning(
                    "Package %s: skill pattern '%s' matched no files", owner_name, pattern)
                continue
            for match in matches:
                skill_file = os.path.join(root_dir, match)
                skill_dir = os.path.dirname(skill_file)
                if not self._validate_frontmatter(skill_file, owner_name):
                    continue
                if skill_dir in seen:
                    continue
                found.append(skill_dir)
                seen.add(skill_dir)

        return found

    def _dedup_entries(self, entries: List[SkillEntry]) -> List[SkillEntry]:
        """Collapse entries that resolve to the same skill directory.

        The same directory can be discovered by more than one mechanism: a source
        Python dep may ship SKILL.md files *and* register an 'agent.skills'
        entry-point, and an editable install resolves that entry-point back to the
        same tree under deps_dir. Paths are compared by realpath because a cached
        dep's deps_dir path is a symlink into the shared cache, so the on-disk and
        entry-point views of one skill are spelled differently.

        Precedence keeps the entry that names and links best: project entries
        first, then path-discovered dependency entries (which carry package
        provenance for naming and live inside the project tree, so the created
        symlink stays relative), then bare entry-point entries.
        """
        by_target: Dict[str, Tuple[Tuple[int, int], SkillEntry]] = {}

        for entry in entries:
            key = os.path.realpath(entry.skill_dir)
            # Entry-point entries carry no package root: root_dir == skill_dir
            has_root = os.path.normpath(entry.root_dir) != os.path.normpath(entry.skill_dir)
            rank = (_KIND_RANK.get(entry.kind, len(_KIND_RANK)), 0 if has_root else 1)

            prev = by_target.get(key)
            if prev is not None:
                keep = entry if rank < prev[0] else prev[1]
                _logger.debug(
                    "Skill %s discovered more than once (%s and %s); keeping the %s entry",
                    key, prev[1].owner_name, entry.owner_name, keep.owner_name)
                if keep is prev[1]:
                    continue
            by_target[key] = (rank, entry)

        return [entry for _, entry in by_target.values()]

    def _assign_dest_names(self, entries: List[SkillEntry]) -> List[Tuple[str, SkillEntry]]:
        ordered = sorted(entries, key=lambda e: self._entry_sort_key(e))
        candidates = [self._name_candidates(e) for e in ordered]
        labels = ["skill %s (from %s)" % (e.skill_dir, e.owner_name) for e in ordered]
        names = self._resolve_names(candidates, labels)
        return sorted(zip(names, ordered), key=lambda item: item[0])

    @classmethod
    def _resolve_names(cls, candidates: List[List[str]], labels: List[str]) -> List[str]:
        """Pick one name per item, escalating to longer candidates on collision.

        Each item supplies its candidate names shortest-first. Colliding items
        both advance to their next candidate; when an item runs out, an
        arbitrary numeric suffix is the last resort and is worth a warning.
        """
        levels = [0 for _ in candidates]

        while True:
            collisions = cls._find_name_collisions(candidates, levels)
            if not collisions:
                break

            advanced = False
            for idxs in collisions.values():
                for idx in idxs:
                    if levels[idx] + 1 < len(candidates[idx]):
                        levels[idx] += 1
                        advanced = True
            if not advanced:
                break

        names = []
        used = set()
        for idx, label in enumerate(labels):
            base_name = candidates[idx][levels[idx]]
            dest_name = base_name
            suffix = 2
            while dest_name in used:
                # Items pointing at one directory are merged before we get here,
                # so this means two *distinct* things exhausted their candidate
                # names and one is getting an arbitrary suffix.
                _logger.warning(
                    "Name '%s' is already in use; linking %s as '%s-%d'",
                    base_name, label, base_name, suffix)
                dest_name = "%s-%d" % (base_name, suffix)
                suffix += 1
            used.add(dest_name)
            names.append(dest_name)

        return names

    @staticmethod
    def _find_name_collisions(candidates: List[List[str]], levels: List[int]) -> Dict[str, List[int]]:
        names = {}
        for idx, opts in enumerate(candidates):
            name = opts[levels[idx]]
            names.setdefault(name, []).append(idx)
        return {name: idxs for name, idxs in names.items() if len(idxs) > 1}

    @staticmethod
    def _entry_sort_key(entry: SkillEntry):
        return (entry.kind, entry.owner_name, entry.skill_dir)

    def _name_candidates(self, entry: SkillEntry) -> List[str]:
        parts = self._relative_dir_parts(entry.root_dir, entry.skill_dir)

        if entry.plugin_name is not None:
            # A plugin gives the skill a real owner: prefer '<plugin>-<skill>'
            # over anything derived from the directory layout, and fall back to
            # including the IVPM package when two plugins share a name.
            dir_name = parts[-1] if parts else entry.plugin_name
            return ["%s-%s" % (entry.plugin_name, dir_name),
                    "%s-%s-%s" % (entry.owner_name, entry.plugin_name, dir_name)]

        if entry.kind == "dependency":
            return self._dependency_name_candidates(entry.owner_name, entry.root_dir, parts)
        else:
            return self._project_name_candidates(entry.root_dir, parts)

    @staticmethod
    def _relative_dir_parts(root_dir: str, skill_dir: str) -> List[str]:
        rel_dir = os.path.relpath(skill_dir, root_dir)
        if rel_dir == ".":
            return []
        return [part for part in rel_dir.split(os.sep) if part]

    @staticmethod
    def _dependency_name_candidates(pkg_name: str, root_dir: str, rel_parts: List[str]) -> List[str]:
        if not rel_parts:
            return [pkg_name]

        dir_name = rel_parts[-1]
        parent_parts = rel_parts[:-1]
        candidates = ["-".join([pkg_name, dir_name])]

        for depth in range(1, len(parent_parts) + 1):
            prefix = parent_parts[-depth:]
            candidates.append("-".join([pkg_name] + prefix + [dir_name]))

        return candidates

    @staticmethod
    def _project_name_candidates(root_dir: str, rel_parts: List[str]) -> List[str]:
        if rel_parts:
            dir_name = rel_parts[-1]
            parent_parts = rel_parts[:-1]
        else:
            dir_name = os.path.basename(os.path.normpath(root_dir))
            parent = os.path.basename(os.path.dirname(os.path.normpath(root_dir)))
            parent_parts = [parent] if parent else []

        candidates = [dir_name]

        for depth in range(1, len(parent_parts) + 1):
            prefix = parent_parts[-depth:]
            candidates.append("-".join(prefix + [dir_name]))

        return candidates

    def _auto_probe_skill_dirs(self, owner_name: str, root_dir: str) -> List[str]:
        """Auto-probe for skill dirs when no explicit skills config is present.

        Checks:
          1. <root>/SKILL.md  — the package root itself
          2. Any SKILL.md files found recursively under <root>/skills/
        """
        found: List[str] = []
        seen: set = set()

        # Root-level SKILL.md
        skill_file = os.path.join(root_dir, "SKILL.md")
        if os.path.isfile(skill_file) and self._validate_frontmatter(skill_file, owner_name):
            found.append(root_dir)
            seen.add(os.path.normpath(root_dir))

        # skills/ sub-directory
        skills_dir = os.path.join(root_dir, "skills")
        if os.path.isdir(skills_dir):
            for rel_md in sorted(_glob.glob("**/SKILL.md", root_dir=skills_dir, recursive=True)):
                abs_md = os.path.join(skills_dir, rel_md)
                skill_dir = os.path.normpath(os.path.dirname(abs_md))
                if skill_dir in seen:
                    continue
                if self._validate_frontmatter(abs_md, owner_name):
                    found.append(skill_dir)
                    seen.add(skill_dir)

        return found

    def _validate_frontmatter(self, path: str, pkg_name: str) -> bool:
        fields = _parse_frontmatter(path)
        if not fields:
            _logger.warning(
                "Package %s: %s has missing or malformed frontmatter; skipping",
                pkg_name, path)
            return False
        if not fields.get("name") or not fields.get("description"):
            _logger.warning(
                "Package %s: %s frontmatter missing required 'name' or 'description'; skipping",
                pkg_name, path)
            return False
        return True

    def _ensure_symlink(self, dest: str, link_target: str, skill_dir: str,
                        deps_dir_norm: str, quiet: bool = False):
        """Create or replace symlink at dest pointing to link_target (relative or absolute).

        ``quiet`` suppresses the "path exists" warning for links written inside
        a directory this run just created, where an existing entry means the
        plugin ships that name itself rather than a user having placed it.
        """
        if os.path.islink(dest):
            # Resolve stored target to absolute path for comparison
            stored_target = os.readlink(dest)
            stored_abs = os.path.normpath(os.path.join(os.path.dirname(dest), stored_target))
            expected_abs = os.path.normpath(skill_dir)

            if stored_abs == expected_abs:
                return  # Already correct, silently leave it

            # Check if it points into deps_dir
            if stored_abs.startswith(deps_dir_norm + os.sep):
                os.unlink(dest)
                os.symlink(link_target, dest)
            else:
                _logger.warning(
                    "Symlink %s points outside deps_dir to %s; leaving as-is",
                    dest, stored_abs)
        elif os.path.exists(dest):
            if not quiet:
                _logger.warning(
                    "Cannot create symlink %s; path exists and is not a symlink",
                    dest)
        else:
            os.symlink(link_target, dest)

    def _ensure_copy(self, dest: str, skill_dir: str):
        """Create or replace copy at dest, handling existing entries gracefully."""
        if os.path.exists(dest):
            _logger.warning(
                "Skill copy %s already exists; skipping", dest)
        else:
            _copy_skill_dir(skill_dir, dest)

    @staticmethod
    def _remove_managed(project_dir: str, prev_state: dict):
        """Remove symlinks/copies written by the previous run.

        Note ``.agents/data/`` is never touched: it is the per-plugin data
        directory, which the Agent Plugins specification requires to survive
        updates.
        """
        pairs = [("agents_skills", os.path.join(".agents", "skills")),
                 ("agents_plugins", os.path.join(".agents", "plugins"))]
        pairs += [("%s_skills" % cfg_key, subdir)
                  for cfg_key, subdir, _strategy in _TOOL_TARGETS]
        for key, subdir in pairs:
            names = prev_state.get(key, [])
            if not names:
                continue
            skills_dir = os.path.join(project_dir, subdir)
            for name in names:
                entry = os.path.join(skills_dir, name)
                if os.path.islink(entry):
                    os.unlink(entry)
                elif os.path.isdir(entry):
                    shutil.rmtree(entry)

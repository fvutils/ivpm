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
import logging
import os
from typing import Dict, List, Optional

from .._compat import glob_rel
from ..agent_skills import frontmatter as _frontmatter
from ..agent_skills import install as _install
from ..agent_skills import naming as _naming
from ..agent_skills.model import SkillEntry
from ..package import Package
from ..project_ops_info import ProjectUpdateInfo
from .package_handler import PackageHandler, ToolchainSupport
from .handler_phases import HandlerPhase

_logger = logging.getLogger("ivpm.handlers.package_handler_agents")

# Discovery lives here; naming, linking and cleanup live in ivpm.agent_skills,
# which the 'ivpm skills' command shares. Module-level aliases keep the names
# tests patch.
_TOOL_TARGETS = _install.TOOL_TARGETS
_symlinks_supported = _install.symlinks_supported
_parse_frontmatter = _frontmatter.parse

__all__ = ["PackageHandlerAgents", "SkillEntry"]


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

        # .agents/skills/ is always populated and stays targets[0] (the
        # symlink-support probe location below). Each tool target is opt-out:
        # enabled by default, skipped only when set to a false value.
        agents = [key for key, _subdir, _strategy in _TOOL_TARGETS
                  if bool(agents_cfg.get(key, True))]
        targets = _install.build_targets(project_dir, agents, plugin_install)

        # Names 'ivpm skills' owns in this project are off limits: never
        # removed or replaced here (see agent_skills.state).
        from ..agent_skills import state as _state
        cmd_state = _state.load_quiet(project_dir)
        reserved = cmd_state.installed_map() if cmd_state is not None else {}

        # Remove entries created by the previous run before writing new ones
        _install.remove_managed(project_dir, self._prev_state, keep=reserved)

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

        # Consume skills pushed by the Python handler: agent.skills entry
        # points and <venv>/share/agent-skills, filtered by
        # with.agents.entrypoints.
        ep_filter = agents_cfg.get("entrypoints", True)
        for item in getattr(update_info, 'pending_skill_dirs', []):
            owner, skill_dir = item[0], item[1]
            meta = item[2] if len(item) > 2 else {}
            source = meta.get("source", "entrypoint")
            label = "agent.skills entrypoint" if source == "entrypoint" else "share/agent-skills"
            skill_file = os.path.join(skill_dir, "SKILL.md")
            if not os.path.isfile(skill_file):
                _logger.warning("%s '%s': no SKILL.md in %s", label, owner, skill_dir)
                continue
            if not self._validate_frontmatter(skill_file, owner):
                continue
            if not self._entrypoint_selected(ep_filter, owner, skill_dir, source, meta):
                continue
            self.skill_entries.append(SkillEntry(
                "dependency", owner, skill_dir, skill_dir, source=source))

        self.plugin_entries = self._dedup_plugins(self.plugin_entries)
        self.skill_entries = self._drop_plugin_owned(self.skill_entries, self.plugin_entries)
        if expand_skills:
            self.skill_entries.extend(self._expand_plugin_skills(self.plugin_entries))
        self.skill_entries = self._dedup_entries(self.skill_entries)
        if reserved:
            self.skill_entries = self._drop_command_owned(
                self.skill_entries, project_dir, cmd_state)

        if not self.skill_entries and not self.plugin_entries:
            return

        for tgt in targets:
            os.makedirs(tgt.path, exist_ok=True)

        use_symlinks = _symlinks_supported(targets[0].path)
        linker = _install.Linker(project_dir, use_symlinks,
                                 deps_dir=update_info.deps_dir, log=_logger)

        plugin_assigned = _naming.assign_plugin_names(self.plugin_entries, _logger)
        skill_assigned = _naming.assign_dest_names(
            self.skill_entries, _logger,
            reserved={n for names in reserved.values() for n in names})

        managed, n_installed = _install.populate(
            linker, targets, skill_assigned, plugin_assigned, emit_mcp)

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
            if _naming.KIND_RANK.get(plugin.kind, 99) < _naming.KIND_RANK.get(prev.kind, 99):
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

    def _get_skill_patterns(self, pkg) -> Optional[List[str]]:
        """Return skill glob patterns (priority 1 or 2), or None for auto-probe.

        Priority:
          1. pkg.agents_config['skills'] — consumer-specified via dep entry
          2. dep's own ivpm.yaml with.agents.export — what it offers dependents
             (an empty list offers nothing)
          3. dep's own ivpm.yaml with.agents.skills
          4. None → caller falls through to auto-probe
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
        # 'export' is what the package offers its dependents; 'skills' is
        # what it uses as a project and is only the fallback here.
        export = cfg.get("export", None)
        if export is not None:
            return [str(p) for p in export]
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
            matches = sorted(glob_rel(pattern, root_dir, recursive=True))
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

    @staticmethod
    def _entrypoint_selected(ep_filter, owner: str, skill_dir: str,
                             source: str, meta: dict) -> bool:
        """Apply with.agents.entrypoints: true (all), false (none) or a list
        of 'ivpm skills' selectors."""
        if ep_filter is True or ep_filter is None:
            return True
        if ep_filter is False:
            return False
        from ..agent_skills import select as _select
        name = _frontmatter.skill_name(skill_dir) or os.path.basename(skill_dir)
        av = _select.Available(
            name=name, ep_name=owner if source == "entrypoint" else None,
            dist=meta.get("dist"), version=None, path=skill_dir)
        sels = [str(x) for x in ep_filter] if isinstance(ep_filter, list) else [str(ep_filter)]
        return any(_select.matches(sel, av) for sel in sels)

    @staticmethod
    def _drop_command_owned(entries: List[SkillEntry], project_dir: str,
                            cmd_state) -> List[SkillEntry]:
        """Drop skills 'ivpm skills' already installed in this project.

        The same skill under the command's name is already there, so linking it
        again would only surface it twice. Matched by realpath for links and by
        content for copies.
        """
        skills_dir = os.path.join(project_dir, ".agents", "skills")
        owned_real = set()
        owned_hash = set()
        for ent in cmd_state.installed.get("agents_skills", []):
            path = os.path.join(skills_dir, ent.name)
            if os.path.islink(path):
                owned_real.add(os.path.realpath(path))
            elif ent.hash:
                owned_hash.add(ent.hash)
        kept = []
        for entry in entries:
            if os.path.realpath(entry.skill_dir) in owned_real:
                continue
            if owned_hash and _install.content_hash(entry.skill_dir) in owned_hash:
                continue
            kept.append(entry)
        return kept

    @staticmethod
    def _dedup_entries(entries: List[SkillEntry]) -> List[SkillEntry]:
        return _naming.dedup_entries(entries, _logger)

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
            for rel_md in sorted(glob_rel("**/SKILL.md", skills_dir, recursive=True)):
                abs_md = os.path.join(skills_dir, rel_md)
                skill_dir = os.path.normpath(os.path.dirname(abs_md))
                if skill_dir in seen:
                    continue
                if self._validate_frontmatter(abs_md, owner_name):
                    found.append(skill_dir)
                    seen.add(skill_dir)

        return found

    @staticmethod
    def _validate_frontmatter(path: str, pkg_name: str) -> bool:
        return _frontmatter.validate(path, pkg_name, _logger)

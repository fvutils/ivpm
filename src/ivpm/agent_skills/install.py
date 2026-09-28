#****************************************************************************
#* install.py
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
"""Install targets, link/copy, plugin installation and cleanup."""
import dataclasses as dc
import hashlib
import json
import logging
import os
import shutil
from typing import Collection, Dict, List, Optional, Tuple

_logger = logging.getLogger("ivpm.agent_skills.install")

# Agent-tool mirror targets in addition to the always-present .agents/skills/,
# as (config_key, subdir, plugin_strategy). Each is opt-out (default-on); an
# explicit False (e.g. claude: false) skips the target.
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
TOOL_TARGETS = (
    ("claude", os.path.join(".claude", "skills"), "install"),
    ("cursor", os.path.join(".cursor", "skills"), "unbundle"),
)

#: Every agent name accepted by ``ivpm skills --agent``, in target order.
AGENTS = ("agents",) + tuple(key for key, _, _ in TOOL_TARGETS)

# Claude Code's manifest location within a plugin. Its schema and the Agent
# Plugins schema share every field name we emit, and Claude Code documents
# that it ignores unrecognized top-level fields, so the Agent Plugins manifest
# is copied across verbatim rather than rewritten.
CLAUDE_MANIFEST_DIR = ".claude-plugin"
CLAUDE_MCP_NAME = ".mcp.json"

# Never copied into an installed skill: bytecode caches and VCS metadata are
# not part of a skill, and a stale .pyc would defeat the content hash.
_COPY_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".git")


@dc.dataclass(frozen=True)
class Target(object):
    """One directory skills are installed into."""
    #: 'agents', 'claude' or 'cursor'
    agent: str
    path: str
    #: How Agent Plugins reach this target: 'install' or 'unbundle'
    strategy: str

    @property
    def state_key(self) -> str:
        return "%s_skills" % self.agent


def build_targets(root: str, agents: Collection[str],
                  plugin_install: bool = True) -> List[Target]:
    """Targets under ``root`` for the named agents.

    ``.agents/skills`` is always first, whether or not 'agents' is named: it is
    the neutral view every other target mirrors, and the symlink-support
    probe location.
    """
    targets = [Target("agents", os.path.join(root, ".agents", "skills"), "unbundle")]
    for key, subdir, strategy in TOOL_TARGETS:
        if key in agents:
            targets.append(Target(key, os.path.join(root, subdir),
                                  strategy if plugin_install else "unbundle"))
    return targets


def symlinks_supported(dest_parent: str) -> bool:
    """Return True if the filesystem at dest_parent supports symlinks."""
    probe = os.path.join(dest_parent, ".ivpm_symlink_probe")
    try:
        os.symlink(".", probe)
        os.remove(probe)
        return True
    except (OSError, NotImplementedError):
        return False


def copy_skill_dir(src_dir: str, dest_dir: str):
    """Copy a whole skill directory.

    The Agent Skills specification lets a skill carry any files it likes, so
    everything is copied except bytecode caches and VCS metadata. Symlinks
    inside the skill are followed: the copy must stand on its own.
    """
    shutil.copytree(src_dir, dest_dir, ignore=_COPY_IGNORE, dirs_exist_ok=True)


def content_hash(path: str) -> str:
    """A stable hash of a skill directory's content (as it would be copied).

    Used to tell a stale copy from a current one. Excluded names match
    ``copy_skill_dir`` so a fresh copy hashes equal to its source.
    """
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(path, followlinks=True):
        dirnames[:] = sorted(d for d in dirnames if d not in ("__pycache__", ".git"))
        for fn in sorted(filenames):
            if fn.endswith((".pyc", ".pyo")):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, path).replace(os.sep, "/")
            h.update(rel.encode("utf-8") + b"\0")
            try:
                with open(full, "rb") as fh:
                    for chunk in iter(lambda: fh.read(65536), b""):
                        h.update(chunk)
            except OSError:
                h.update(b"<unreadable>")
            h.update(b"\0")
    return h.hexdigest()


def remove_entry(path: str):
    """Remove a link or copy written by us."""
    if os.path.islink(path):
        os.unlink(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.exists(path):
        os.unlink(path)


def remove_managed(root: str, prev_state: dict,
                   keep: Optional[Dict[str, Collection[str]]] = None):
    """Remove symlinks/copies recorded in ``prev_state``.

    ``prev_state`` maps a state key (``agents_skills``, ``claude_skills``, ...,
    ``agents_plugins``) to the names written there. Names listed in ``keep``
    under the same key are left alone -- another manager owns them now.

    Note ``.agents/data/`` is never touched: it is the per-plugin data
    directory, which the Agent Plugins specification requires to survive
    updates.
    """
    keep = keep or {}
    pairs = [("agents_skills", os.path.join(".agents", "skills")),
             ("agents_plugins", os.path.join(".agents", "plugins"))]
    pairs += [("%s_skills" % key, subdir) for key, subdir, _strategy in TOOL_TARGETS]
    for key, subdir in pairs:
        names = prev_state.get(key, [])
        if not names:
            continue
        skills_dir = os.path.join(root, subdir)
        kept = keep.get(key, ())
        for name in names:
            if isinstance(name, dict):
                name = name.get("name")
            if not name or name in kept:
                continue
            remove_entry(os.path.join(skills_dir, name))


class Linker(object):
    """Links (or copies) directories into targets under one root.

    Symlinks are relative when the source is inside ``root`` and absolute
    otherwise, so a workspace stays relocatable while a shared-cache or
    site-packages source still resolves.
    """

    def __init__(self, root: str, use_symlinks: bool,
                 deps_dir: Optional[str] = None,
                 log: logging.Logger = _logger):
        # Absolute, so a root given as "." still yields relative links inside it
        self.root = os.path.abspath(root)
        self.use_symlinks = use_symlinks
        self.deps_dir_norm = os.path.normpath(deps_dir) if deps_dir else None
        self.log = log

    def link_dir(self, dest: str, src: str, quiet: bool = False):
        if self.use_symlinks:
            src_norm = os.path.abspath(src)
            if src_norm == self.root or src_norm.startswith(self.root + os.sep):
                link_target = os.path.relpath(src, os.path.dirname(dest))
            else:
                link_target = os.path.abspath(src)
            self.ensure_symlink(dest, link_target, src, quiet=quiet)
        else:
            if os.path.isdir(src):
                self.ensure_copy(dest, src)
            elif not os.path.exists(dest):
                shutil.copy2(src, dest)

    def ensure_symlink(self, dest: str, link_target: str, skill_dir: str,
                       quiet: bool = False):
        """Create or replace symlink at dest pointing to link_target.

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
            if self.deps_dir_norm and stored_abs.startswith(self.deps_dir_norm + os.sep):
                os.unlink(dest)
                os.symlink(link_target, dest)
            else:
                self.log.warning(
                    "Symlink %s points outside deps_dir to %s; leaving as-is",
                    dest, stored_abs)
        elif os.path.exists(dest):
            if not quiet:
                self.log.warning(
                    "Cannot create symlink %s; path exists and is not a symlink",
                    dest)
        else:
            os.symlink(link_target, dest)

    def ensure_copy(self, dest: str, skill_dir: str):
        """Create a copy at dest, leaving an existing entry alone."""
        if os.path.exists(dest):
            self.log.warning(
                "Skill copy %s already exists; skipping", dest)
        else:
            copy_skill_dir(skill_dir, dest)

    # -------------------------------------------------------------- #
    # Agent Plugins
    # -------------------------------------------------------------- #

    def install_plugin(self, dest: str, plugin, emit_mcp: bool) -> bool:
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
            self.log.warning("Cannot install plugin '%s'; %s already exists",
                             plugin.name, dest)
            return False

        os.makedirs(dest, exist_ok=True)

        ships_own_manifest = os.path.isfile(
            os.path.join(plugin.root_dir, CLAUDE_MANIFEST_DIR, "plugin.json"))

        for entry in sorted(os.listdir(plugin.root_dir)):
            # mcp.json is translated below rather than linked: the tool reads
            # '.mcp.json' and spells the plugin-root placeholder differently.
            if entry == "mcp.json":
                continue
            if entry == CLAUDE_MANIFEST_DIR and not ships_own_manifest:
                continue
            src = os.path.join(plugin.root_dir, entry)
            self.link_dir(os.path.join(dest, entry), src, quiet=True)

        if not ships_own_manifest:
            # The Agent Plugins manifest is already a valid manifest for the
            # tool: the field names coincide and unrecognized top-level fields
            # (our '$schema', 'extensions') are ignored at load time. Copy it
            # verbatim rather than rewriting it.
            manifest_dir = os.path.join(dest, CLAUDE_MANIFEST_DIR)
            os.makedirs(manifest_dir, exist_ok=True)
            try:
                shutil.copy2(os.path.join(plugin.root_dir, "plugin.json"),
                             os.path.join(manifest_dir, "plugin.json"))
            except OSError as exc:
                self.log.warning("Could not install manifest for plugin '%s': %s",
                                 plugin.name, exc)
                return False

        if emit_mcp and plugin.mcp is not None and plugin.mcp.servers:
            self.write_tool_mcp(dest, plugin)

        return True

    def write_tool_mcp(self, dest: str, plugin):
        """Translate the plugin's mcp.json into the tool's own MCP file.

        Only the spelling differs: the tool names the plugin root
        ``${CLAUDE_PLUGIN_ROOT}`` where Agent Plugins says ``${PLUGIN_ROOT}``,
        and has no equivalent of ``${PLUGIN_DATA}``, which is therefore
        resolved to the concrete per-plugin directory IVPM allocates.

        That directory is deliberately *not* tracked for cleanup: the
        specification requires plugin data to survive updates.
        """
        data_dir = os.path.join(self.root, ".agents", "data", plugin.name)
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
            with open(os.path.join(dest, CLAUDE_MCP_NAME), "w") as fh:
                json.dump({"mcpServers": servers}, fh, indent=2)
                fh.write("\n")
        except OSError as exc:
            self.log.warning("Could not write MCP configuration for plugin '%s': %s",
                             plugin.name, exc)


def populate(linker: Linker, targets: List[Target],
             skill_assigned: List[Tuple[str, object]],
             plugin_assigned: List[Tuple[str, object]],
             emit_mcp: bool = False) -> Tuple[Dict[str, List[str]], int]:
    """Write every assigned skill and plugin into every target.

    ``skill_assigned`` holds ``(dest_name, SkillEntry)`` pairs and
    ``plugin_assigned`` ``(dest_name, DiscoveredPlugin)`` pairs.

    Returns ``(managed, n_native_installs)``, where ``managed`` maps each
    state key to the names written under it -- which differs per target once
    the "install" strategy is in play.
    """
    managed: Dict[str, List[str]] = {}

    for tgt in targets:
        os.makedirs(tgt.path, exist_ok=True)

    # The neutral view: whole plugins, plus every skill individually.
    if plugin_assigned:
        plugins_dir = os.path.join(linker.root, ".agents", "plugins")
        os.makedirs(plugins_dir, exist_ok=True)
        for dest_name, plugin in plugin_assigned:
            linker.link_dir(os.path.join(plugins_dir, dest_name), plugin.root_dir)
        managed["agents_plugins"] = [n for n, _ in plugin_assigned]

    n_installed = 0
    for tgt in targets:
        names: List[str] = []

        if tgt.strategy == "install":
            for dest_name, plugin in plugin_assigned:
                if linker.install_plugin(os.path.join(tgt.path, dest_name), plugin, emit_mcp):
                    names.append(dest_name)
                    n_installed += 1

        for dest_name, entry in skill_assigned:
            # A plugin installed whole already carries this skill, namespaced
            # by the tool. Linking it here too would surface it twice.
            if tgt.strategy == "install" and getattr(entry, "plugin_name", None) is not None:
                continue
            linker.link_dir(os.path.join(tgt.path, dest_name), entry.skill_dir)
            names.append(dest_name)

        managed[tgt.state_key] = names

    return managed, n_installed

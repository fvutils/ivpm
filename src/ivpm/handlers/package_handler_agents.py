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
import logging
import os
import re
import shutil
from typing import Dict, List, Optional, Tuple

from ..package import Package
from ..project_ops_info import ProjectUpdateInfo
from .package_handler import PackageHandler

_logger = logging.getLogger("ivpm.handlers.package_handler_agents")

# Frontmatter delimited by lines containing only '---'
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_FIELD_RE = re.compile(r"^(\w[\w-]*):\s*(.+)$", re.MULTILINE)


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


@dc.dataclass
class PackageHandlerAgents(PackageHandler):
    name               = "agents"
    description        = "Creates .agents/skills/ symlinks (or copies) for deps that provide SKILL.md files"
    leaf_when          = None
    root_when          = None
    phase              = 0
    conditions_summary = (
        "leaf: all non-PyPI packages; "
        "root: always (cleans stale entries even when no skills are present)"
    )

    @classmethod
    def handler_info(cls):
        from ..show.info_types import HandlerInfo
        return HandlerInfo(
            name=cls.name,
            description=cls.description,
            phase=cls.phase,
            conditions=cls.conditions_summary,
            notes=(
                "Creates relative symlinks from .agents/skills/<pkg> to the directory "
                "containing each dependency's SKILL.md. "
                "When claude: true is set under package.with.agents, also mirrors to .claude/skills/. "
                "Falls back to directory copy on platforms without symlink support. "
                "Skill paths support glob patterns (e.g. skills/**/SKILL.md)."
            ),
        )

    # All discovered skills, with enough context to derive human-readable link names
    skill_entries: List[SkillEntry] = dc.field(default_factory=list)
    _managed_names: List[str] = dc.field(default_factory=list, init=False, repr=False)
    _prev_state: dict = dc.field(default_factory=dict, init=False, repr=False)

    def reset(self):
        self.skill_entries = []
        self._managed_names = []

    def on_root_pre_load(self, update_info: ProjectUpdateInfo):
        self.reset()
        self._prev_state = update_info.handler_state.get("agents", {})

    def on_leaf_post_load(self, pkg: Package, update_info: ProjectUpdateInfo):
        if not hasattr(pkg, "path") or pkg.path is None:
            return
        if getattr(pkg, "src_type", None) == "pypi":
            return

        patterns = self._get_skill_patterns(pkg)

        if patterns is not None:
            found = self._resolve_skill_dirs(pkg.name, pkg.path, patterns)
        else:
            # Priority 3: auto-probe
            found = []
            skill_file = os.path.join(pkg.path, "SKILL.md")
            if os.path.isfile(skill_file):
                if self._validate_frontmatter(skill_file, pkg.name):
                    found.append(pkg.path)

        if found:
            entries = [
                SkillEntry("dependency", pkg.name, pkg.path, skill_dir)
                for skill_dir in found
            ]
            with self._lock:
                self.skill_entries.extend(entries)

    def on_root_post_load(self, update_info: ProjectUpdateInfo):
        project_dir = update_info.project_dir or os.path.dirname(update_info.deps_dir)
        agents_cfg = update_info.handler_configs.get("agents", {}) or {}
        do_claude = bool(agents_cfg.get("claude", False))

        targets = [os.path.join(project_dir, ".agents", "skills")]
        claude_skills_dir = os.path.join(project_dir, ".claude", "skills")
        if do_claude or os.path.exists(os.path.join(project_dir, ".claude")):
            targets.append(claude_skills_dir)

        # Remove entries created by the previous run before writing new ones
        self._remove_managed(project_dir, self._prev_state)

        self.skill_entries.extend(self._discover_project_skills(update_info, project_dir))

        if not self.skill_entries:
            return

        for tgt in targets:
            os.makedirs(tgt, exist_ok=True)

        use_symlinks = _symlinks_supported(targets[0])
        deps_dir_norm = os.path.normpath(update_info.deps_dir)
        assigned = self._assign_dest_names(self.skill_entries)
        self._managed_names = [dest_name for dest_name, _ in assigned]

        for dest_name, entry in assigned:
            for tgt in targets:
                dest = os.path.join(tgt, dest_name)
                if use_symlinks:
                    rel_target = os.path.relpath(entry.skill_dir, tgt)
                    self._ensure_symlink(dest, rel_target, entry.skill_dir, deps_dir_norm)
                else:
                    self._ensure_copy(dest, entry.skill_dir)

        total = len(assigned)
        from ..utils import note
        note("Populated .agents/skills/ with %d skill(s) (%s)" % (
            total, "symlinks" if use_symlinks else "copies"))

    def get_state_entries(self) -> dict:
        """Persist created entry names so the next run can clean them up."""
        if not self._managed_names:
            return {}
        # Store same names for both targets; _remove_managed checks what exists
        return {"agents_skills": self._managed_names, "claude_skills": self._managed_names}

    # ------------------------------------------------------------------ #

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
            found = []
            skill_file = os.path.join(project_dir, "SKILL.md")
            if os.path.isfile(skill_file) and self._validate_frontmatter(skill_file, project_name):
                found.append(project_dir)

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

    def _assign_dest_names(self, entries: List[SkillEntry]) -> List[Tuple[str, SkillEntry]]:
        ordered = sorted(entries, key=lambda e: self._entry_sort_key(e))
        levels = [0 for _ in ordered]
        candidates = [self._name_candidates(e) for e in ordered]

        while True:
            collisions = self._find_name_collisions(candidates, levels)
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

        assigned = []
        used = set()
        for idx, entry in enumerate(ordered):
            base_name = candidates[idx][levels[idx]]
            dest_name = base_name
            suffix = 2
            while dest_name in used:
                dest_name = "%s-%d" % (base_name, suffix)
                suffix += 1
            used.add(dest_name)
            assigned.append((dest_name, entry))

        assigned.sort(key=lambda item: item[0])
        return assigned

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

    def _ensure_symlink(self, dest: str, rel_target: str, skill_dir: str, deps_dir_norm: str):
        """Create or replace symlink at dest, handling existing entries gracefully."""
        if os.path.islink(dest):
            # Resolve stored target to absolute path
            stored_target = os.readlink(dest)
            stored_abs = os.path.normpath(os.path.join(os.path.dirname(dest), stored_target))
            expected_abs = os.path.normpath(skill_dir)

            if stored_abs == expected_abs:
                return  # Already correct, silently leave it

            # Check if it points into deps_dir
            if stored_abs.startswith(deps_dir_norm + os.sep):
                os.unlink(dest)
                os.symlink(rel_target, dest)
            else:
                _logger.warning(
                    "Symlink %s points outside deps_dir to %s; leaving as-is",
                    dest, stored_abs)
        elif os.path.exists(dest):
            _logger.warning(
                "Cannot create symlink %s; path exists and is not a symlink",
                dest)
        else:
            os.symlink(rel_target, dest)

    def _ensure_copy(self, dest: str, skill_dir: str):
        """Create or replace copy at dest, handling existing entries gracefully."""
        if os.path.exists(dest):
            _logger.warning(
                "Skill copy %s already exists; skipping", dest)
        else:
            _copy_skill_dir(skill_dir, dest)

    @staticmethod
    def _remove_managed(project_dir: str, prev_state: dict):
        """Remove symlinks/copies written by the previous run."""
        for key, subdir in (("agents_skills", ".agents/skills"),
                             ("claude_skills", ".claude/skills")):
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

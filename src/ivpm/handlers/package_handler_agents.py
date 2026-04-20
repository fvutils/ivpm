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
from typing import Dict, List, Optional

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

    # pkg_name -> list of absolute paths to skill *directories*
    skill_dirs: Dict[str, List[str]] = dc.field(default_factory=dict)
    _prev_state: dict = dc.field(default_factory=dict, init=False, repr=False)

    def reset(self):
        self.skill_dirs = {}

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
            found = []
            for pattern in patterns:
                matches = sorted(_glob.glob(pattern, root_dir=pkg.path, recursive=True))
                if not matches:
                    _logger.warning(
                        "Package %s: skill pattern '%s' matched no files", pkg.name, pattern)
                    continue
                for match in matches:
                    skill_file = os.path.join(pkg.path, match)
                    if self._validate_frontmatter(skill_file, pkg.name):
                        found.append(os.path.dirname(skill_file))
        else:
            # Priority 3: auto-probe
            found = []
            skill_file = os.path.join(pkg.path, "SKILL.md")
            if os.path.isfile(skill_file):
                if self._validate_frontmatter(skill_file, pkg.name):
                    found.append(pkg.path)

        if found:
            with self._lock:
                self.skill_dirs[pkg.name] = found

    def on_root_post_load(self, update_info: ProjectUpdateInfo):
        project_dir = update_info.project_dir or os.path.dirname(update_info.deps_dir)
        agents_cfg = update_info.handler_configs.get("agents", {}) or {}
        do_claude = bool(agents_cfg.get("claude", False))

        targets = [os.path.join(project_dir, ".agents", "skills")]
        if do_claude:
            targets.append(os.path.join(project_dir, ".claude", "skills"))

        # Remove entries created by the previous run before writing new ones
        self._remove_managed(project_dir, self._prev_state)

        if not self.skill_dirs:
            return

        for tgt in targets:
            os.makedirs(tgt, exist_ok=True)

        use_symlinks = _symlinks_supported(targets[0])

        for pkg_name, dirs in sorted(self.skill_dirs.items()):
            for idx, skill_dir in enumerate(dirs, start=1):
                dest_name = pkg_name if len(dirs) == 1 else "%s-%d" % (pkg_name, idx)
                for tgt in targets:
                    dest = os.path.join(tgt, dest_name)
                    if use_symlinks:
                        rel_target = os.path.relpath(skill_dir, tgt)
                        os.symlink(rel_target, dest)
                    else:
                        _copy_skill_dir(skill_dir, dest)

        total = sum(len(v) for v in self.skill_dirs.values())
        from ..utils import note
        note("Populated .agents/skills/ with %d skill(s) (%s)" % (
            total, "symlinks" if use_symlinks else "copies"))

    def get_state_entries(self) -> dict:
        """Persist created entry names so the next run can clean them up."""
        if not self.skill_dirs:
            return {}
        names = []
        for pkg_name, dirs in sorted(self.skill_dirs.items()):
            for idx in range(1, len(dirs) + 1):
                names.append(pkg_name if len(dirs) == 1 else "%s-%d" % (pkg_name, idx))
        # Store same names for both targets; _remove_managed checks what exists
        return {"agents_skills": names, "claude_skills": names}

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

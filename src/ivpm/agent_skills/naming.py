#****************************************************************************
#* naming.py
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
"""Deduplication and link-name assignment for discovered skills."""
import logging
import os
from typing import Collection, Dict, List, Tuple

from . import frontmatter
from .model import SkillEntry

_logger = logging.getLogger("ivpm.agent_skills.naming")

# Precedence used when the same skill directory is discovered by more than one
# mechanism (see dedup_entries). Lower sorts first / wins. Plugin-owned
# entries outrank bare ones: a plugin supplies a real name and provenance.
KIND_RANK = {
    "plugin-project": 0,
    "plugin-dependency": 1,
    "project": 2,
    "dependency": 3,
}


def dedup_entries(entries: List[SkillEntry],
                  log: logging.Logger = _logger) -> List[SkillEntry]:
    """Collapse entries that resolve to the same skill directory.

    The same directory can be discovered by more than one mechanism: a source
    Python dep may ship SKILL.md files *and* register an 'agent.skills'
    entry-point, and an editable install resolves that entry-point back to the
    same tree under deps_dir. Paths are compared by realpath because a cached
    dep's deps_dir path is a symlink into the shared cache, so the on-disk and
    entry-point views of one skill are spelled differently.

    Precedence keeps the entry that links best: project entries first, then
    path-discovered dependency entries (which live inside the project tree, so
    the created symlink stays relative), then bare entry-point entries.
    """
    by_target: Dict[str, Tuple[Tuple[int, int], SkillEntry]] = {}

    for entry in entries:
        key = os.path.realpath(entry.skill_dir)
        # Entry-point entries carry no package root: root_dir == skill_dir
        has_root = os.path.normpath(entry.root_dir) != os.path.normpath(entry.skill_dir)
        rank = (KIND_RANK.get(entry.kind, len(KIND_RANK)), 0 if has_root else 1)

        prev = by_target.get(key)
        if prev is not None:
            keep = entry if rank < prev[0] else prev[1]
            log.debug(
                "Skill %s discovered more than once (%s and %s); keeping the %s entry",
                key, prev[1].owner_name, entry.owner_name, keep.owner_name)
            if keep is prev[1]:
                continue
        by_target[key] = (rank, entry)

    return [entry for _, entry in by_target.values()]


def assign_dest_names(entries: List[SkillEntry],
                      log: logging.Logger = _logger,
                      reserved: Collection[str] = ()) -> List[Tuple[str, SkillEntry]]:
    """Give every entry a unique link name.

    The Agent Skills specification requires a skill's directory to be named
    after its frontmatter ``name``, so that is every skill's first choice,
    whatever route found it. Only a collision moves a skill off it -- first to
    ``<owner>-<name>``, then to route-specific names -- and that is worth a
    warning, because the installed directory then breaks the rule.

    ``reserved`` names are owned by someone else and never assigned.
    """
    ordered = sorted(entries, key=entry_sort_key)
    candidates = [name_candidates(e, log) for e in ordered]
    if reserved:
        for idx, cands in enumerate(candidates):
            free = [c for c in cands if c not in reserved]
            candidates[idx] = free or ["%s-%s" % (cands[-1], ordered[idx].owner_name)]
    labels = ["skill %s (from %s)" % (e.skill_dir, e.owner_name) for e in ordered]
    names = resolve_names(candidates, labels, log, reserved)

    by_first: Dict[str, List[int]] = {}
    for idx, entry in enumerate(ordered):
        if entry.plugin_name is None and candidates[idx]:
            by_first.setdefault(candidates[idx][0], []).append(idx)
    for first, idxs in sorted(by_first.items()):
        moved = [i for i in idxs if names[i] != first]
        if len(idxs) > 1 and moved:
            log.warning(
                "%d skills are named '%s'; linking %s. A linked name that differs "
                "from the skill's frontmatter name breaks the Agent Skills "
                "specification", len(idxs), first,
                ", ".join("%s as '%s'" % (labels[i], names[i]) for i in idxs))

    return sorted(zip(names, ordered), key=lambda item: item[0])


def assign_plugin_names(plugins: List[object],
                        log: logging.Logger = _logger) -> List[Tuple[str, object]]:
    """Name plugin links from the manifest, disambiguating by owner package.

    The manifest name is spec-constrained to a filesystem-safe charset, so
    it is a better link name than anything derived from a directory path.
    """
    ordered = sorted(plugins, key=lambda p: (p.kind, p.name, p.owner_name))
    candidates = [[p.name, "%s-%s" % (p.owner_name, p.name)] for p in ordered]
    names = resolve_names(
        candidates, [("plugin %s (from %s)" % (p.name, p.owner_name)) for p in ordered], log)
    return sorted(zip(names, ordered), key=lambda item: item[0])


def resolve_names(candidates: List[List[str]], labels: List[str],
                  log: logging.Logger = _logger,
                  reserved: Collection[str] = ()) -> List[str]:
    """Pick one name per item, escalating to longer candidates on collision.

    Each item supplies its candidate names shortest-first. Colliding items
    both advance to their next candidate; when an item runs out, an
    arbitrary numeric suffix is the last resort and is worth a warning.
    """
    levels = [0 for _ in candidates]

    while True:
        collisions = find_name_collisions(candidates, levels)
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
    used = set(reserved)
    for idx, label in enumerate(labels):
        base_name = candidates[idx][levels[idx]]
        dest_name = base_name
        suffix = 2
        while dest_name in used:
            # Items pointing at one directory are merged before we get here,
            # so this means two *distinct* things exhausted their candidate
            # names and one is getting an arbitrary suffix.
            log.warning(
                "Name '%s' is already in use; linking %s as '%s-%d'",
                base_name, label, base_name, suffix)
            dest_name = "%s-%d" % (base_name, suffix)
            suffix += 1
        used.add(dest_name)
        names.append(dest_name)

    return names


def find_name_collisions(candidates: List[List[str]], levels: List[int]) -> Dict[str, List[int]]:
    names = {}
    for idx, opts in enumerate(candidates):
        name = opts[levels[idx]]
        names.setdefault(name, []).append(idx)
    return {name: idxs for name, idxs in names.items() if len(idxs) > 1}


def entry_sort_key(entry: SkillEntry):
    return (entry.kind, entry.owner_name, entry.skill_dir)


def name_candidates(entry: SkillEntry, log: logging.Logger = _logger) -> List[str]:
    parts = relative_dir_parts(entry.root_dir, entry.skill_dir)

    if entry.plugin_name is not None:
        # A plugin gives the skill a real owner: prefer '<plugin>-<skill>'
        # over anything derived from the directory layout, and fall back to
        # including the IVPM package when two plugins share a name. The
        # plugin, not the skill, is the unit a plugin-aware tool namespaces.
        dir_name = parts[-1] if parts else entry.plugin_name
        return ["%s-%s" % (entry.plugin_name, dir_name),
                "%s-%s-%s" % (entry.owner_name, entry.plugin_name, dir_name)]

    if entry.kind == "dependency":
        route = dependency_name_candidates(entry.owner_name, entry.root_dir, parts)
    else:
        route = project_name_candidates(entry.root_dir, parts)

    name = frontmatter.skill_name(entry.skill_dir)
    if not name or not frontmatter.is_safe_dir_name(name):
        return route

    # A package or project whose root *is* the skill is named after its
    # repository, not its skill, so only a nested skill directory is held to
    # the directory-name rule.
    is_tree_root = entry.source == "path" and \
        os.path.normpath(entry.root_dir) == os.path.normpath(entry.skill_dir)
    dir_name = os.path.basename(os.path.normpath(entry.skill_dir))
    if not is_tree_root and dir_name != name:
        log.warning(
            "Skill %s (from %s): directory '%s' does not match its name '%s'; "
            "linking it as '%s'", entry.skill_dir, entry.owner_name, dir_name,
            name, name)

    candidates = [name, "%s-%s" % (entry.owner_name, name)]
    for c in route:
        if c not in candidates:
            candidates.append(c)
    return candidates


def relative_dir_parts(root_dir: str, skill_dir: str) -> List[str]:
    rel_dir = os.path.relpath(skill_dir, root_dir)
    if rel_dir == ".":
        return []
    return [part for part in rel_dir.split(os.sep) if part]


def dependency_name_candidates(pkg_name: str, root_dir: str, rel_parts: List[str]) -> List[str]:
    if not rel_parts:
        return [pkg_name]

    dir_name = rel_parts[-1]
    parent_parts = rel_parts[:-1]
    candidates = ["-".join([pkg_name, dir_name])]

    for depth in range(1, len(parent_parts) + 1):
        prefix = parent_parts[-depth:]
        candidates.append("-".join([pkg_name] + prefix + [dir_name]))

    return candidates


def project_name_candidates(root_dir: str, rel_parts: List[str]) -> List[str]:
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

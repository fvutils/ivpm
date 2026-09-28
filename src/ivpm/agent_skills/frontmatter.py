#****************************************************************************
#* frontmatter.py
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
"""SKILL.md frontmatter parsing and validation."""
import logging
import os
import re
from typing import Dict, List, Optional

_logger = logging.getLogger("ivpm.agent_skills.frontmatter")

# Frontmatter delimited by lines containing only '---'
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_FIELD_RE = re.compile(r"^(\w[\w-]*):\s*(.+)$", re.MULTILINE)

# Agent Skills specification (agentskills.io/specification): 1-64 characters
# of lowercase alphanumerics and hyphens, not starting or ending with a
# hyphen, no consecutive hyphens.
_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
NAME_MAX = 64
DESCRIPTION_MAX = 1024


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse(path: str, log: logging.Logger = _logger) -> Optional[Dict[str, str]]:
    """Return a dict of frontmatter fields, or None on failure."""
    try:
        with open(path) as fh:
            content = fh.read()
    except OSError as exc:
        log.warning("Could not read %s: %s", path, exc)
        return None

    m = _FRONTMATTER_RE.match(content)
    if not m:
        return None

    fields: Dict[str, str] = {}
    for fm in _FIELD_RE.finditer(m.group(1)):
        fields[fm.group(1)] = _unquote(fm.group(2).strip())
    return fields


def skill_name(skill_dir: str) -> Optional[str]:
    """The frontmatter ``name`` of the skill in ``skill_dir``, or None."""
    fields = parse(os.path.join(skill_dir, "SKILL.md"))
    if not fields:
        return None
    return fields.get("name") or None


def name_problems(name: str) -> List[str]:
    """Ways ``name`` breaks the Agent Skills specification (empty if none)."""
    problems = []
    if len(name) > NAME_MAX:
        problems.append("longer than %d characters" % NAME_MAX)
    if not _NAME_RE.match(name):
        problems.append("not lowercase a-z, 0-9 and single inner hyphens")
    return problems


def is_safe_dir_name(name: str) -> bool:
    """True when ``name`` can be used as one path component."""
    return bool(name) and name not in (".", "..") \
        and not any(c in name for c in ("/", "\\", "\0"))


def validate(path: str, owner: str, log: logging.Logger = _logger) -> bool:
    """Check SKILL.md at ``path`` for the fields a skill cannot work without.

    Missing frontmatter, ``name`` or ``description`` is an error (returns
    False). A name or description that breaks the specification's format
    rules is only a warning: the skill works in every agent regardless, and
    refusing to link it over a naming nit would do more harm than good.
    """
    fields = parse(path, log)
    if not fields:
        log.warning(
            "Package %s: %s has missing or malformed frontmatter; skipping",
            owner, path)
        return False
    if not fields.get("name") or not fields.get("description"):
        log.warning(
            "Package %s: %s frontmatter missing required 'name' or 'description'; skipping",
            owner, path)
        return False

    problems = name_problems(fields["name"])
    if problems:
        log.warning(
            "Package %s: %s: skill name '%s' does not follow the Agent Skills "
            "specification (%s)", owner, path, fields["name"], "; ".join(problems))
    if len(fields["description"]) > DESCRIPTION_MAX:
        log.warning(
            "Package %s: %s: description is longer than %d characters",
            owner, path, DESCRIPTION_MAX)
    return True

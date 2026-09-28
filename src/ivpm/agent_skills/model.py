#****************************************************************************
#* model.py
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
import dataclasses as dc
from typing import Optional


@dc.dataclass(frozen=True)
class SkillEntry(object):
    """One discovered skill directory.

    ``kind`` is the discovery role -- ``project``, ``dependency``,
    ``plugin-project`` or ``plugin-dependency`` -- and drives dedup precedence.
    ``owner_name`` is the package, project, entry-point or distribution that
    supplied it. Entry-point and share/ entries carry no package root, so for
    them ``root_dir == skill_dir``.

    ``source`` says how the directory was found: ``path`` (a package or
    project tree), ``entrypoint`` (an ``agent.skills`` entry point) or
    ``share`` (``<prefix>/share/agent-skills``).
    """
    kind: str
    owner_name: str
    root_dir: str
    skill_dir: str
    #: Set when this skill came from an Agent Plugin. Drives naming, and lets
    #: an "install"-strategy tool skip the skill (it gets the whole plugin).
    plugin_name: Optional[str] = None
    source: str = "path"

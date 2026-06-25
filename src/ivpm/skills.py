#****************************************************************************
#* skills.py
#*
#* Agent-skill discovery hook. Registered via the 'agent.skills' entry-point
#* group so that any environment with ivpm installed exposes the bundled IVPM
#* agent skill to the agents handler (which symlinks/copies it into
#* .agents/skills/, .claude/skills/, and .cursor/skills/).
#****************************************************************************
import os
from typing import List


def get_skill_dirs() -> List[str]:
    """Return directories containing bundled IVPM agent ``SKILL.md`` files.

    Referenced by the ``agent.skills`` entry-point in pyproject.toml. Each
    returned directory must contain a ``SKILL.md`` with valid frontmatter.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    return [os.path.join(here, "share", "skills", "ivpm")]

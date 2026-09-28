#****************************************************************************
#* __init__.py
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
"""Agent-skill discovery, naming and installation.

The engine shared by the ``agents`` handler (``ivpm update``) and the
``ivpm skills`` command. Nothing here depends on ``ProjectUpdateInfo``,
``Package`` or handler classes, so it can run in a plain directory with a
plain virtual environment.

- ``frontmatter`` -- SKILL.md parsing and validation
- ``model``       -- ``SkillEntry``
- ``naming``      -- dedup and link-name assignment
- ``install``     -- targets, link/copy, plugin install, cleanup
- ``query``       -- query an interpreter's entry points and share/agent-skills
- ``select``      -- selector matching for ``ivpm skills``
- ``state``       -- the ``ivpm skills`` state file
"""

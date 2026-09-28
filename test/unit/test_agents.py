import logging
import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from .test_base import TestBase

from ivpm.handlers.package_handler_agents import PackageHandlerAgents, SkillEntry


class TestAgents(TestBase):

    @contextmanager
    def entrypoint_skills(self, *pairs):
        """Inject (ep_name, skill_dir) pairs as if the python handler had queried the venv.

        The real producer runs pip/uv and interrogates the venv's entry-points
        (PackageHandlerPython._push_entrypoint_skills); these tests use
        skip_venv=True, so push the same (name, dir) pairs onto update_info just
        before the agents handler consumes them. Each skill_dir may be a callable
        so tests can name paths that only exist once the update has started.
        """
        orig = PackageHandlerAgents.on_root_post_load

        def wrapper(handler, update_info):
            for pair in pairs:
                name, d = pair[0], pair[1]
                item = (name, os.path.normpath(d() if callable(d) else d))
                update_info.pending_skill_dirs.append(item + tuple(pair[2:]))
            return orig(handler, update_info)

        with patch.object(PackageHandlerAgents, "on_root_post_load", wrapper):
            yield

    def skill_links(self):
        """Sorted names present in .agents/skills/ (empty when the dir is absent)."""
        skills_dir = os.path.join(self.testdir, ".agents", "skills")
        return sorted(os.listdir(skills_dir)) if os.path.isdir(skills_dir) else []

    # ------------------------------------------------------------------ #
    # Basic symlink creation                                               #
    # ------------------------------------------------------------------ #

    def test_agents_dir_created(self):
        """Single dep with root SKILL.md → .agents/skills/<pkg> symlink created."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_basic
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        link = os.path.join(self.testdir, ".agents", "skills", "agent-leaf1")
        self.assertTrue(os.path.exists(link), ".agents/skills/agents_leaf1 should exist")

    def test_symlink_is_relative(self):
        """Symlink target is a relative path (does not start with '/')."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_relative
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        link = os.path.join(self.testdir, ".agents", "skills", "agent-leaf1")
        self.assertTrue(os.path.islink(link), "Should be a symlink")
        target = os.readlink(link)
        self.assertFalse(os.path.isabs(target),
                         "Symlink target should be relative, got: %s" % target)

    def test_skill_md_fallback(self):
        """Dep has SKILL.md at root → picked up by auto-probe."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_fallback
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf2
                      url: file://${DATA_DIR}/agents_leaf2
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        link = os.path.join(self.testdir, ".agents", "skills", "agent-leaf2")
        self.assertTrue(os.path.exists(link))

    def test_symlink_skill_md_accessible(self):
        """SKILL.md is readable through the created symlink."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_readable
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf2
                      url: file://${DATA_DIR}/agents_leaf2
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        link = os.path.join(self.testdir, ".agents", "skills", "agent-leaf2")
        skill_via_link = os.path.join(link, "SKILL.md")
        self.assertTrue(os.path.isfile(skill_via_link),
                        "SKILL.md should be accessible through the symlink")

    # ------------------------------------------------------------------ #
    # Tool mirroring (.claude / .cursor) — opt-out, default-on            #
    # ------------------------------------------------------------------ #

    def test_claude_default_on(self):
        """claude: absent → .claude/skills/ populated by default (opt-out)."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_claude_default
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        claude_link = os.path.join(self.testdir, ".claude", "skills", "agent-leaf1")
        self.assertTrue(os.path.exists(claude_link),
                        ".claude/skills/agents_leaf1 should be created by default")

    def test_claude_true(self):
        """claude: true → both .agents/skills/ and .claude/skills/ populated."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_claude
            with:
                agents:
                    claude: true
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        agents_link = os.path.join(self.testdir, ".agents", "skills", "agent-leaf1")
        claude_link = os.path.join(self.testdir, ".claude", "skills", "agent-leaf1")
        self.assertTrue(os.path.exists(agents_link), ".agents/skills/agents_leaf1 should exist")
        self.assertTrue(os.path.exists(claude_link), ".claude/skills/agents_leaf1 should exist")

    def test_claude_false_optout(self):
        """claude: false → .agents/skills/ populated but .claude/ not created."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_no_claude
            with:
                agents:
                    claude: false
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        agents_link = os.path.join(self.testdir, ".agents", "skills", "agent-leaf1")
        claude_dir = os.path.join(self.testdir, ".claude")
        self.assertTrue(os.path.exists(agents_link), ".agents/skills/agents_leaf1 should exist")
        self.assertFalse(os.path.isdir(claude_dir),
                         ".claude/ should not be created when claude: false")

    def test_cursor_default_on(self):
        """cursor: absent → .cursor/skills/ populated by default (opt-out)."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_cursor_default
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        cursor_link = os.path.join(self.testdir, ".cursor", "skills", "agent-leaf1")
        self.assertTrue(os.path.exists(cursor_link),
                        ".cursor/skills/agents_leaf1 should be created by default")

    def test_cursor_false_optout(self):
        """cursor: false → .cursor/ not created, .agents and .claude present."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_no_cursor
            with:
                agents:
                    cursor: false
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        agents_link = os.path.join(self.testdir, ".agents", "skills", "agent-leaf1")
        claude_link = os.path.join(self.testdir, ".claude", "skills", "agent-leaf1")
        cursor_dir = os.path.join(self.testdir, ".cursor")
        self.assertTrue(os.path.exists(agents_link), ".agents/skills/agents_leaf1 should exist")
        self.assertTrue(os.path.exists(claude_link), ".claude/skills/agents_leaf1 should exist")
        self.assertFalse(os.path.isdir(cursor_dir),
                         ".cursor/ should not be created when cursor: false")

    def test_all_tools_populated(self):
        """Defaults → .agents, .claude and .cursor all mirror the same skill."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_all_tools
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        for tool in (".agents", ".claude", ".cursor"):
            link = os.path.join(self.testdir, tool, "skills", "agent-leaf1")
            self.assertTrue(os.path.exists(link),
                            "%s/skills/agents_leaf1 should exist" % tool)

    def test_explicit_false_wins_over_existing_dir(self):
        """Pre-existing .claude/ + claude: false → .claude/skills/ not populated."""
        # Manually create a .claude/ directory before the update
        os.makedirs(os.path.join(self.testdir, ".claude"), exist_ok=True)

        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_false_wins
            with:
                agents:
                    claude: false
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        claude_skills = os.path.join(self.testdir, ".claude", "skills")
        self.assertFalse(os.path.exists(claude_skills),
                         ".claude/skills/ must not be populated when claude: false, "
                         "even if .claude/ already exists")

    def test_stale_cleanup_after_disable(self):
        """Disabling a tool on a re-run removes its previously created entries."""
        # First run: defaults → .cursor/skills/ created
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_disable
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        cursor_link = os.path.join(self.testdir, ".cursor", "skills", "agent-leaf1")
        self.assertTrue(os.path.exists(cursor_link),
                        ".cursor/skills/agents_leaf1 should exist after first run")

        # Second run: cursor disabled → its prior entry must be cleaned up
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_disable
            with:
                agents:
                    cursor: false
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        self.assertFalse(os.path.exists(cursor_link),
                         "stale .cursor/skills/agents_leaf1 should be removed after disable")

    # ------------------------------------------------------------------ #
    # No skills → no directory                                            #
    # ------------------------------------------------------------------ #

    def test_no_skills_no_dir(self):
        """No deps with skills → .agents/ not created."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_none
            dep-sets:
                - name: default-dev
                  deps:
                    - name: leaf_proj1
                      url: file://${DATA_DIR}/leaf_proj1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        agents_dir = os.path.join(self.testdir, ".agents")
        self.assertFalse(os.path.isdir(agents_dir), ".agents/ should not be created")

    # ------------------------------------------------------------------ #
    # Bad frontmatter                                                      #
    # ------------------------------------------------------------------ #

    def test_bad_frontmatter_warns(self):
        """Malformed frontmatter → warning logged, package skipped."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_bad_fm
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_bad_frontmatter
                      url: file://${DATA_DIR}/agents_bad_frontmatter
                      src: dir
        """)
        with self.assertLogs("ivpm.handlers.package_handler_agents", level=logging.WARNING) as cm:
            self.ivpm_update(skip_venv=True)

        self.assertTrue(any("malformed frontmatter" in msg or "missing" in msg for msg in cm.output))
        agents_dir = os.path.join(self.testdir, ".agents")
        self.assertFalse(os.path.isdir(agents_dir))

    # ------------------------------------------------------------------ #
    # Multiple packages                                                    #
    # ------------------------------------------------------------------ #

    def test_multiple_packages(self):
        """Two deps each with SKILL.md → both appear in .agents/skills/."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_multi_pkg
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
                    - name: agents_leaf2
                      url: file://${DATA_DIR}/agents_leaf2
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        skills_dir = os.path.join(self.testdir, ".agents", "skills")
        self.assertTrue(os.path.exists(os.path.join(skills_dir, "agent-leaf1")))
        self.assertTrue(os.path.exists(os.path.join(skills_dir, "agent-leaf2")))

    def test_root_project_skill_md_fallback(self):
        """Root project SKILL.md → .agents/skills/<project-dir> symlink created."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_root_skill
            dep-sets:
                - name: default-dev
                  deps: []
        """)
        self.mkFile("SKILL.md", """---
name: root-skill
description: Root project skill
---
Body
""")
        self.ivpm_update(skip_venv=True)

        link = os.path.join(self.testdir, ".agents", "skills", "root-skill")
        self.assertTrue(os.path.exists(link), "Root project skill link should exist")

    def test_root_project_conflicting_dir_names(self):
        """Conflicting root-project skill dir names use parent segments."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_root_conflicts
            with:
                agents:
                    skills:
                        - agents/review/SKILL.md
                        - tutorials/review/SKILL.md
            dep-sets:
                - name: default-dev
                  deps: []
        """)
        self.mkFile("agents/review/SKILL.md", """---
name: agents-review
description: Review skill under agents
---
Body
""")
        self.mkFile("tutorials/review/SKILL.md", """---
name: tutorials-review
description: Review skill under tutorials
---
Body
""")
        self.ivpm_update(skip_venv=True)

        skills_dir = os.path.join(self.testdir, ".agents", "skills")
        self.assertTrue(os.path.exists(os.path.join(skills_dir, "agents-review")))
        self.assertTrue(os.path.exists(os.path.join(skills_dir, "tutorials-review")))

    # ------------------------------------------------------------------ #
    # Stale entry cleanup                                                  #
    # ------------------------------------------------------------------ #

    def test_stale_links_removed(self):
        """Second ivpm update after removing a dep → old symlink removed."""
        # First run: two deps
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_stale
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
                    - name: agents_leaf2
                      url: file://${DATA_DIR}/agents_leaf2
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        skills_dir = os.path.join(self.testdir, ".agents", "skills")
        self.assertTrue(os.path.exists(os.path.join(skills_dir, "agent-leaf1")))
        self.assertTrue(os.path.exists(os.path.join(skills_dir, "agent-leaf2")))

        # Second run: only one dep
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_stale
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        self.assertTrue(os.path.exists(os.path.join(skills_dir, "agent-leaf1")))
        self.assertFalse(os.path.exists(os.path.join(skills_dir, "agent-leaf2")),
                         "Stale agents_leaf2 link should have been removed")

    # ------------------------------------------------------------------ #
    # Dep's own ivpm.yaml skill declaration (priority 2)                  #
    # ------------------------------------------------------------------ #

    def test_declared_skill_paths(self):
        """Dep's ivpm.yaml lists non-root path → that directory is linked."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_declared
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_multi_skill
                      url: file://${DATA_DIR}/agents_multi_skill
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        skills_dir = os.path.join(self.testdir, ".agents", "skills")
        # Two skill paths → links named from their directories
        link1 = os.path.join(skills_dir, "agent-multi-root")
        link2 = os.path.join(skills_dir, "agent-multi-sub")
        self.assertTrue(os.path.exists(link1), "First declared skill path should be linked")
        self.assertTrue(os.path.exists(link2), "Second declared skill path should be linked")

    def test_declared_paths_override_probe(self):
        """Dep declares skills: in ivpm.yaml → default probe is not used."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_override_probe
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_multi_skill
                      url: file://${DATA_DIR}/agents_multi_skill
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        skills_dir = os.path.join(self.testdir, ".agents", "skills")
        expected = {"agent-multi-root", "agent-multi-sub"}
        self.assertEqual(expected, set(os.listdir(skills_dir)),
                         "Only explicitly declared skill paths should be linked")

    # ------------------------------------------------------------------ #
    # Consumer dep-entry agents: override (priority 1)                    #
    # ------------------------------------------------------------------ #

    def test_dep_spec_overrides_self(self):
        """Consumer dep-entry agents: takes priority over dep's own ivpm.yaml."""
        # agents_multi_skill declares two paths; we override with just one
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_dep_spec_override
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_multi_skill
                      url: file://${DATA_DIR}/agents_multi_skill
                      src: dir
                      agents:
                          skills:
                              - SKILL.md
        """)
        self.ivpm_update(skip_venv=True)

        skills_dir = os.path.join(self.testdir, ".agents", "skills")
        # Only one path → link named 'agents_multi_skill' (no suffix)
        link = os.path.join(skills_dir, "agent-multi-root")
        self.assertTrue(os.path.exists(link), "Consumer-specified skill should be linked")
        # Suffix links should not exist
        self.assertFalse(os.path.exists(link + "-1"))
        self.assertFalse(os.path.exists(link + "-2"))

    def test_dep_spec_no_ivpm_yaml(self):
        """Non-IVPM dep (no ivpm.yaml) with agents: in consumer dep-entry works."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_no_ivpm_yaml
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_no_ivpm_yaml
                      url: file://${DATA_DIR}/agents_no_ivpm_yaml
                      src: dir
                      agents:
                          skills:
                              - SKILL.md
        """)
        self.ivpm_update(skip_venv=True)

        link = os.path.join(self.testdir, ".agents", "skills", "agent-no-ivpm-yaml")
        self.assertTrue(os.path.exists(link))

    # ------------------------------------------------------------------ #
    # Glob patterns                                                        #
    # ------------------------------------------------------------------ #

    def test_dep_spec_skill_patterns(self):
        """Dep entry with glob pattern skills/**/SKILL.md — all matches linked."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_glob
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_glob_tree
                      url: file://${DATA_DIR}/agents_glob_tree
                      src: dir
                      agents:
                          skills:
                              - skills/**/SKILL.md
        """)
        self.ivpm_update(skip_venv=True)

        skills_dir = os.path.join(self.testdir, ".agents", "skills")
        link1 = os.path.join(skills_dir, "agent-glob-alpha")
        link2 = os.path.join(skills_dir, "agent-glob-beta")
        self.assertTrue(os.path.exists(link1), "First glob match should be linked")
        self.assertTrue(os.path.exists(link2), "Second glob match should be linked")

    def test_dep_spec_pattern_no_match_warns(self):
        """Glob pattern with no matches → warning logged, no link."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_no_match
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
                      agents:
                          skills:
                              - nonexistent/**/SKILL.md
        """)
        with self.assertLogs("ivpm.handlers.package_handler_agents", level=logging.WARNING) as cm:
            self.ivpm_update(skip_venv=True)

        self.assertTrue(any("matched no files" in msg for msg in cm.output))
        agents_dir = os.path.join(self.testdir, ".agents")
        self.assertFalse(os.path.isdir(agents_dir))

    # ------------------------------------------------------------------ #
    # Copy fallback (no symlink support)                                   #
    # ------------------------------------------------------------------ #

    def test_companion_dirs_copied_on_fallback(self):
        """On copy fallback: scripts/ and assets/ are copied alongside SKILL.md."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_copy_fallback
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_with_assets
                      url: file://${DATA_DIR}/agents_with_assets
                      src: dir
        """)

        with patch("ivpm.handlers.package_handler_agents._symlinks_supported",
                   return_value=False):
            self.ivpm_update(skip_venv=True)

        dest = os.path.join(self.testdir, ".agents", "skills", "agent-with-assets")
        self.assertTrue(os.path.isdir(dest))
        self.assertTrue(os.path.isfile(os.path.join(dest, "SKILL.md")))
        self.assertTrue(os.path.isdir(os.path.join(dest, "scripts")))
        self.assertTrue(os.path.isdir(os.path.join(dest, "assets")))

    # ------------------------------------------------------------------ #
    # Symlink idempotency and error handling                               #
    # ------------------------------------------------------------------ #

    def test_existing_correct_symlink_left_alone(self):
        """If symlink exists and points to correct target → silently leave it."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_idempotent
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
        """)
        # First run
        self.ivpm_update(skip_venv=True)

        link = os.path.join(self.testdir, ".agents", "skills", "agent-leaf1")
        self.assertTrue(os.path.islink(link))
        original_target = os.readlink(link)

        # Second run (same configuration)
        self.ivpm_update(skip_venv=True)

        # Link should still exist and point to same target
        self.assertTrue(os.path.islink(link))
        self.assertEqual(os.readlink(link), original_target)

    def test_symlink_to_wrong_deps_target_replaced(self):
        """Symlink points to different target within deps_dir → replace it."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_replace
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
        """)
        self.ivpm_update(skip_venv=True)

        link = os.path.join(self.testdir, ".agents", "skills", "agent-leaf1")
        self.assertTrue(os.path.islink(link))

        # Manually create a wrong symlink to a different location
        wrong_target = os.path.join(self.testdir, "some_other_location")
        os.makedirs(wrong_target, exist_ok=True)
        os.unlink(link)
        os.symlink(os.path.relpath(wrong_target, os.path.dirname(link)), link)

        # Second run should fix it
        self.ivpm_update(skip_venv=True)

        # Link should now point to the correct location
        self.assertTrue(os.path.islink(link))
        resolved = os.path.normpath(os.path.join(os.path.dirname(link), os.readlink(link)))
        deps_leaf = os.path.join(self.testdir, "packages/agents_leaf1")
        self.assertEqual(resolved, deps_leaf)

    def test_symlink_outside_deps_dir_warned_and_left(self):
        """Symlink points outside deps_dir → warn and leave as-is."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_external_link
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
        """)

        # Create a symlink outside deps_dir before first run
        skills_dir = os.path.join(self.testdir, ".agents", "skills")
        os.makedirs(skills_dir, exist_ok=True)
        external_target = os.path.join(self.testdir, "external")
        os.makedirs(external_target, exist_ok=True)
        link = os.path.join(skills_dir, "agent-leaf1")
        os.symlink(os.path.relpath(external_target, skills_dir), link)

        # Run update and check warning is logged
        with self.assertLogs("ivpm.handlers.package_handler_agents", level=logging.WARNING) as cm:
            self.ivpm_update(skip_venv=True)

        self.assertTrue(any("points outside deps_dir" in msg for msg in cm.output))

        # Link should still point to external location
        self.assertTrue(os.path.islink(link))
        resolved = os.path.normpath(os.path.join(os.path.dirname(link), os.readlink(link)))
        self.assertEqual(resolved, external_target)

    def test_non_symlink_file_at_dest_warned_and_skipped(self):
        """Regular file exists at dest → warn and skip."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_file_conflict
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
        """)

        # Create a regular file where the symlink should go
        skills_dir = os.path.join(self.testdir, ".agents", "skills")
        os.makedirs(skills_dir, exist_ok=True)
        file_path = os.path.join(skills_dir, "agent-leaf1")
        with open(file_path, "w") as f:
            f.write("existing file")

        # Run update and check warning is logged
        with self.assertLogs("ivpm.handlers.package_handler_agents", level=logging.WARNING) as cm:
            self.ivpm_update(skip_venv=True)

        self.assertTrue(any("not a symlink" in msg for msg in cm.output))

        # File should still exist
        self.assertTrue(os.path.isfile(file_path))
        with open(file_path) as f:
            self.assertEqual(f.read(), "existing file")

    def test_existing_copy_skipped_with_warning(self):
        """Copy fallback: existing directory → warn and skip."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_copy_exists
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_with_assets
                      url: file://${DATA_DIR}/agents_with_assets
                      src: dir
        """)

        # Create existing directory where copy should go
        dest_dir = os.path.join(self.testdir, ".agents", "skills", "agent-with-assets")
        os.makedirs(dest_dir, exist_ok=True)
        marker_file = os.path.join(dest_dir, "marker.txt")
        with open(marker_file, "w") as f:
            f.write("existing content")

        # Run update with copy fallback
        with patch("ivpm.handlers.package_handler_agents._symlinks_supported",
                   return_value=False):
            with self.assertLogs("ivpm.handlers.package_handler_agents", level=logging.WARNING) as cm:
                self.ivpm_update(skip_venv=True)

        self.assertTrue(any("already exists" in msg for msg in cm.output))

        # Original directory should remain unchanged
        self.assertTrue(os.path.isfile(marker_file))
        with open(marker_file) as f:
            self.assertEqual(f.read(), "existing content")

    # ------------------------------------------------------------------ #
    # agent.skills entry-points, and dedup against path discovery         #
    # ------------------------------------------------------------------ #

    def test_entrypoint_skill_linked(self):
        """A skill supplied only by an agent.skills entry-point is linked."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_ep_only
            dep-sets:
                - name: default-dev
                  deps: []
        """)
        with self.entrypoint_skills(("epskill", os.path.join(self.data_dir, "agents_leaf1"))):
            self.ivpm_update(skip_venv=True)

        # Named after the skill's frontmatter, not the entry point
        self.assertEqual(["agent-leaf1"], self.skill_links())
        skill_md = os.path.join(self.testdir, ".agents", "skills", "agent-leaf1", "SKILL.md")
        self.assertTrue(os.path.isfile(skill_md), "SKILL.md should be reachable through the link")

    def test_entrypoint_and_dep_root_deduped(self):
        """Dep root SKILL.md + entry-point for the same dir → one link, not two."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_ep_dep_root
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
        """)
        pkg_dir = os.path.join(self.testdir, "packages", "agents_leaf1")
        # An editable install resolves the entry-point back through any symlink
        with self.entrypoint_skills(("agents_leaf1", lambda: os.path.realpath(pkg_dir))):
            self.ivpm_update(skip_venv=True)

        self.assertEqual(["agent-leaf1"], self.skill_links())
        # The dependency entry wins, so the link stays relative to the project
        link = os.path.join(self.testdir, ".agents", "skills", "agent-leaf1")
        self.assertTrue(os.path.islink(link))
        self.assertFalse(os.path.isabs(os.readlink(link)))

    def test_entrypoint_and_dep_subdir_deduped(self):
        """Entry-point pointing into a dep's skills/ dir does not duplicate the probe result."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_ep_dep_subdir
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_glob_tree
                      url: file://${DATA_DIR}/agents_glob_tree
                      src: dir
        """)
        alpha = os.path.join(self.testdir, "packages", "agents_glob_tree", "skills", "alpha")
        with self.entrypoint_skills(("alpha", lambda: os.path.realpath(alpha))):
            self.ivpm_update(skip_venv=True)

        # Without dedup this also yields a second 'alpha' link to the same directory
        self.assertEqual(["agent-glob-alpha", "agent-glob-beta"], self.skill_links())

    def test_entrypoint_via_symlinked_dir_deduped(self):
        """Same skill reached through a symlink and its real path → one link."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_ep_symlink
            with:
                agents:
                    skills:
                        - linked/x/SKILL.md
            dep-sets:
                - name: default-dev
                  deps: []
        """)
        self.mkFile("real/x/SKILL.md", """---
name: symlinked-skill
description: Reached by two different paths
---
Body
""")
        os.symlink("real", os.path.join(self.testdir, "linked"))

        with self.entrypoint_skills(("x", os.path.join(self.testdir, "real", "x"))):
            self.ivpm_update(skip_venv=True)

        self.assertEqual(["symlinked-skill"], self.skill_links())

    def test_entrypoint_matching_root_project_skill_deduped(self):
        """Root project SKILL.md + entry-point at the project dir → one link, project-named."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_ep_root_project
            dep-sets:
                - name: default-dev
                  deps: []
        """)
        self.mkFile("SKILL.md", """---
name: root-skill
description: Root project skill also published as an entry-point
---
Body
""")
        with self.entrypoint_skills(("test_agents_ep_root_project", self.testdir)):
            self.ivpm_update(skip_venv=True)

        self.assertEqual(["root-skill"], self.skill_links())

    def test_distinct_skills_still_both_linked(self):
        """Dedup is keyed on the directory, not the owner → distinct dirs both survive."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_ep_distinct
            dep-sets:
                - name: default-dev
                  deps:
                    - name: agents_leaf1
                      url: file://${DATA_DIR}/agents_leaf1
                      src: dir
        """)
        with self.entrypoint_skills(("leaf2ep", os.path.join(self.data_dir, "agents_leaf2"))):
            self.ivpm_update(skip_venv=True)

        self.assertEqual(["agent-leaf1", "agent-leaf2"], self.skill_links())

    def test_overlapping_glob_patterns_dedup(self):
        """Two patterns matching the same SKILL.md produce a single link."""
        self.mkFile("ivpm.yaml", """
        package:
            name: test_agents_overlapping_globs
            with:
                agents:
                    skills:
                        - skills/*/SKILL.md
                        - skills/**/SKILL.md
            dep-sets:
                - name: default-dev
                  deps: []
        """)
        self.mkFile("skills/alpha/SKILL.md", """---
name: alpha
description: Matched by both patterns
---
Body
""")
        self.ivpm_update(skip_venv=True)

        self.assertEqual(["alpha"], self.skill_links())

    def test_dedup_entries_precedence(self):
        """_dedup_entries keeps project > path-discovered dependency > entry-point."""
        handler = PackageHandlerAgents()
        skill_dir = os.path.join(self.testdir, "pkg", "skills", "alpha")
        os.makedirs(skill_dir)

        ep_entry = SkillEntry("dependency", "alpha", skill_dir, skill_dir)
        dep_entry = SkillEntry("dependency", "pkg", os.path.join(self.testdir, "pkg"), skill_dir)
        proj_entry = SkillEntry("project", "proj", self.testdir, skill_dir)

        self.assertEqual([dep_entry], handler._dedup_entries([ep_entry, dep_entry]))
        self.assertEqual([dep_entry], handler._dedup_entries([dep_entry, ep_entry]))
        self.assertEqual([proj_entry], handler._dedup_entries([ep_entry, proj_entry, dep_entry]))

        other = SkillEntry("dependency", "beta", skill_dir, os.path.join(self.testdir, "pkg"))
        self.assertEqual(2, len(handler._dedup_entries([dep_entry, other])))


def _skill_md(name, description=None):
    return "---\nname: %s\ndescription: %s\n---\nBody\n" % (
        name, description or "Skill %s" % name)


class TestAgentsSkillNaming(TestBase):
    """Frontmatter-first naming (U1), name validation (U2), producer export
    (U3), whole-directory copy (U4), share/agent-skills, entrypoint selection
    and coexistence with 'ivpm skills'."""

    entrypoint_skills = TestAgents.entrypoint_skills
    skill_links = TestAgents.skill_links

    def mk_skill(self, rel_dir, name, description=None):
        self.mkFile(os.path.join(rel_dir, "SKILL.md"), _skill_md(name, description))
        return os.path.join(self.testdir, rel_dir)

    def dep_yaml(self, *deps, with_block=""):
        lines = ["package:", "    name: consumer"]
        if with_block:
            lines.append(with_block)
        lines += ["    dep-sets:", "        - name: default-dev", "          deps:"]
        if not deps:
            lines[-1] += " []"
        for dep in deps:
            name, extra = (dep, "") if isinstance(dep, str) else dep
            lines += ["            - name: %s" % name,
                      "              url: file://${TEST_DIR}/deps/%s" % name,
                      "              src: dir"]
            if extra:
                lines.append(extra)
        return "\n".join(lines) + "\n"

    def agents_warnings(self, fn):
        """Run fn; return the agents handler's warning messages."""
        records = []

        class _Collect(logging.Handler):
            def emit(self, record):
                records.append(record)

        logger = logging.getLogger("ivpm.handlers.package_handler_agents")
        handler = _Collect(level=logging.WARNING)
        logger.addHandler(handler)
        try:
            fn()
        finally:
            logger.removeHandler(handler)
        return [r.getMessage() for r in records]

    # U1 ------------------------------------------------------------------

    def test_u1a_one_entrypoint_many_skills(self):
        """One entry point returning three dirs → three frontmatter-named links, no warning."""
        dirs = [self.mk_skill("site/pkg/share/skills/%s" % n, n)
                for n in ("pss", "pss-api", "pss-checkers")]
        self.mkFile("ivpm.yaml", self.dep_yaml())
        with self.entrypoint_skills(*[("pss", d) for d in dirs]):
            warnings = self.agents_warnings(lambda: self.ivpm_update(skip_venv=True))
        self.assertEqual(["pss", "pss-api", "pss-checkers"], self.skill_links())
        self.assertEqual([], warnings)

    def test_u1b_dependency_tree_skill_named_by_frontmatter(self):
        self.mk_skill("deps/dep1/skills/alpha", "alpha")
        self.mkFile("ivpm.yaml", self.dep_yaml("dep1"))
        self.ivpm_update(skip_venv=True)
        self.assertEqual(["alpha"], self.skill_links())

    def test_u1c_same_skill_by_tree_and_entrypoint_linked_once(self):
        self.mk_skill("deps/dep1/skills/alpha", "alpha")
        self.mkFile("ivpm.yaml", self.dep_yaml("dep1"))
        alpha = os.path.join(self.testdir, "packages", "dep1", "skills", "alpha")
        with self.entrypoint_skills(("dep1-ep", lambda: os.path.realpath(alpha))):
            self.ivpm_update(skip_venv=True)
        self.assertEqual(["alpha"], self.skill_links())

    def test_u1d_shared_name_falls_back_to_owner_prefix_with_warning(self):
        self.mk_skill("deps/dep1/skills/review", "review")
        self.mk_skill("deps/dep2/skills/review", "review")
        self.mkFile("ivpm.yaml", self.dep_yaml("dep1", "dep2"))
        warnings = self.agents_warnings(lambda: self.ivpm_update(skip_venv=True))
        self.assertEqual(["dep1-review", "dep2-review"], self.skill_links())
        self.assertTrue(any("2 skills are named 'review'" in w for w in warnings), warnings)

    def test_u1e_old_names_removed_through_state(self):
        """A link written under the pre-U1 '<pkg>-<dir>' name is cleaned up."""
        import json
        self.mk_skill("deps/dep1/skills/alpha", "alpha")
        self.mkFile("ivpm.yaml", self.dep_yaml("dep1"))
        self.ivpm_update(skip_venv=True)

        skills_dir = os.path.join(self.testdir, ".agents", "skills")
        old = os.path.join(skills_dir, "dep1-alpha")
        os.symlink(os.path.join("..", "..", "packages", "dep1", "skills", "alpha"), old)
        state_file = os.path.join(self.testdir, "packages", "ivpm.json")
        with open(state_file) as fh:
            data = json.load(fh)
        data["handlers"]["agents"]["agents_skills"] = ["dep1-alpha"]
        with open(state_file, "w") as fh:
            json.dump(data, fh)

        self.ivpm_update(skip_venv=True)
        self.assertEqual(["alpha"], self.skill_links())

    def test_u1_dir_name_mismatch_warns_and_uses_frontmatter(self):
        self.mk_skill("deps/dep1/skills/old-dir", "new-name")
        self.mkFile("ivpm.yaml", self.dep_yaml("dep1"))
        warnings = self.agents_warnings(lambda: self.ivpm_update(skip_venv=True))
        self.assertEqual(["new-name"], self.skill_links())
        self.assertTrue(any("does not match its name" in w for w in warnings), warnings)

    # U2 ------------------------------------------------------------------

    def test_u2_malformed_name_warns_and_still_links(self):
        for bad in ("Bad-Name", "bad--name", "x" * 65):
            with self.subTest(bad=bad):
                self.setUp()
                self.mk_skill("deps/dep1/skills/%s" % bad, bad)
                self.mkFile("ivpm.yaml", self.dep_yaml("dep1"))
                warnings = self.agents_warnings(lambda: self.ivpm_update(skip_venv=True))
                self.assertEqual([bad], self.skill_links())
                self.assertTrue(any("does not follow the Agent Skills" in w for w in warnings),
                                warnings)

    def test_u2_long_description_warns(self):
        self.mk_skill("deps/dep1/skills/alpha", "alpha", "d" * 1025)
        self.mkFile("ivpm.yaml", self.dep_yaml("dep1"))
        warnings = self.agents_warnings(lambda: self.ivpm_update(skip_venv=True))
        self.assertEqual(["alpha"], self.skill_links())
        self.assertTrue(any("longer than 1024" in w for w in warnings), warnings)

    # U3 ------------------------------------------------------------------

    def mk_exporting_dep(self, export_block):
        self.mk_skill("deps/dep1/skills/shipped", "shipped")
        self.mk_skill("deps/dep1/skills/internal", "internal")
        self.mkFile("deps/dep1/ivpm.yaml",
                    "package:\n    name: dep1\n    with:\n        agents:\n%s\n" % export_block)

    def test_u3a_export_limits_what_dependents_get(self):
        self.mk_exporting_dep("            export:\n                - skills/shipped/SKILL.md")
        self.mkFile("ivpm.yaml", self.dep_yaml("dep1"))
        self.ivpm_update(skip_venv=True)
        self.assertEqual(["shipped"], self.skill_links())

    def test_u3a_export_does_not_affect_the_package_as_a_project(self):
        self.mk_exporting_dep("            export:\n                - skills/shipped/SKILL.md")
        dep_dir = os.path.join(self.testdir, "deps", "dep1")
        from ivpm.project_ops import ProjectOps

        class Args(object):
            anonymous_git = None
        ProjectOps(dep_dir, Args()).update(dep_set=None, skip_venv=True, args=Args())
        self.assertEqual(["internal", "shipped"],
                         sorted(os.listdir(os.path.join(dep_dir, ".agents", "skills"))))

    def test_u3b_consumer_override_beats_export(self):
        self.mk_exporting_dep("            export:\n                - skills/shipped/SKILL.md")
        self.mkFile("ivpm.yaml", self.dep_yaml(
            ("dep1", "              agents:\n                  skills:\n"
                     "                      - skills/internal/SKILL.md")))
        self.ivpm_update(skip_venv=True)
        self.assertEqual(["internal"], self.skill_links())

    def test_u3c_empty_export_offers_nothing(self):
        self.mk_exporting_dep("            export: []")
        self.mkFile("ivpm.yaml", self.dep_yaml("dep1"))
        self.ivpm_update(skip_venv=True)
        self.assertEqual([], self.skill_links())

    # U4 ------------------------------------------------------------------

    def test_u4_copy_mode_copies_whole_directory(self):
        self.mk_skill("deps/dep1/skills/alpha", "alpha")
        self.mkFile("deps/dep1/skills/alpha/templates/ext.py.in", "x\n")
        self.mkFile("deps/dep1/skills/alpha/NOTES.txt", "notes\n")
        self.mkFile("deps/dep1/skills/alpha/__pycache__/m.cpython-312.pyc", "junk")
        self.mkFile("deps/dep1/skills/alpha/tool.pyc", "junk")
        self.mkFile("ivpm.yaml", self.dep_yaml("dep1"))
        with patch("ivpm.handlers.package_handler_agents._symlinks_supported",
                   return_value=False):
            self.ivpm_update(skip_venv=True)
        dest = os.path.join(self.testdir, ".agents", "skills", "alpha")
        self.assertFalse(os.path.islink(dest))
        self.assertTrue(os.path.isfile(os.path.join(dest, "templates", "ext.py.in")))
        self.assertTrue(os.path.isfile(os.path.join(dest, "NOTES.txt")))
        self.assertFalse(os.path.exists(os.path.join(dest, "__pycache__")))
        self.assertFalse(os.path.exists(os.path.join(dest, "tool.pyc")))

    # share/agent-skills and with.agents.entrypoints ------------------------

    def test_share_skill_linked(self):
        d = self.mk_skill("venv/share/agent-skills/polars", "polars")
        self.mkFile("ivpm.yaml", self.dep_yaml())
        with self.entrypoint_skills(("agent-skill-polars", d,
                                     {"source": "share", "dist": "agent-skill-polars"})):
            self.ivpm_update(skip_venv=True)
        self.assertEqual(["polars"], self.skill_links())

    def entrypoint_fixture(self):
        return [("pss", self.mk_skill("site/pss/%s" % n, n), {"dist": "pssparser"})
                for n in ("pss", "pss-api")] + \
               [("other", self.mk_skill("site/other/other", "other"), {"dist": "other-dist"})]

    def test_entrypoints_false_links_none(self):
        self.mkFile("ivpm.yaml", self.dep_yaml(
            with_block="    with:\n        agents:\n            entrypoints: false"))
        with self.entrypoint_skills(*self.entrypoint_fixture()):
            self.ivpm_update(skip_venv=True)
        self.assertEqual([], self.skill_links())

    def test_entrypoints_selectors(self):
        cases = [(["dist:pssparser"], ["pss", "pss-api"]),
                 (["other"], ["other"]),
                 (["pss/pss-api"], ["pss-api"]),
                 (["pss-*"], ["pss-api"])]
        for sels, expected in cases:
            with self.subTest(sels=sels):
                self.setUp()
                self.mkFile("ivpm.yaml", self.dep_yaml(
                    with_block="    with:\n        agents:\n            entrypoints: [%s]"
                               % ", ".join('"%s"' % s for s in sels)))
                with self.entrypoint_skills(*self.entrypoint_fixture()):
                    self.ivpm_update(skip_venv=True)
                self.assertEqual(expected, self.skill_links())

    # Coexistence with 'ivpm skills' ------------------------------------------

    def test_names_owned_by_ivpm_skills_are_left_alone(self):
        import json
        from ivpm.agent_skills import state
        self.mk_skill("deps/dep1/skills/alpha", "alpha")
        self.mk_skill("deps/dep1/skills/beta", "beta")
        other = self.mk_skill("elsewhere/beta", "beta")
        skills_dir = os.path.join(self.testdir, ".agents", "skills")
        os.makedirs(skills_dir)
        # 'ivpm skills' owns 'beta' (a different skill of the same name)
        os.symlink(other, os.path.join(skills_dir, "beta"))
        st = state.SkillsState(agents=["agents"], installed={
            "agents_skills": [state.InstalledEntry("beta")]})
        state.save(self.testdir, st)

        self.mkFile("ivpm.yaml", self.dep_yaml("dep1"))
        self.ivpm_update(skip_venv=True)
        self.ivpm_update(skip_venv=True)

        self.assertEqual(os.path.realpath(other),
                         os.path.realpath(os.path.join(skills_dir, "beta")))
        self.assertIn("alpha", self.skill_links())
        self.assertIn("dep1-beta", self.skill_links())
        with open(os.path.join(self.testdir, "packages", "ivpm.json")) as fh:
            written = json.load(fh)["handlers"]["agents"]["agents_skills"]
        self.assertNotIn("beta", written)

    def test_same_skill_installed_by_ivpm_skills_not_linked_twice(self):
        alpha = self.mk_skill("deps/dep1/skills/alpha", "alpha")
        from ivpm.agent_skills import state
        skills_dir = os.path.join(self.testdir, ".agents", "skills")
        os.makedirs(skills_dir)
        self.mkFile("ivpm.yaml", self.dep_yaml("dep1"))
        pkg_alpha = os.path.join(self.testdir, "packages", "dep1", "skills", "alpha")
        os.symlink(alpha, os.path.join(skills_dir, "alpha"))
        state.save(self.testdir, state.SkillsState(
            agents=["agents"], installed={"agents_skills": [state.InstalledEntry("alpha")]}))
        self.ivpm_update(skip_venv=True)
        # dep1's alpha is the same directory (dir src links packages/dep1 → deps/dep1)
        self.assertEqual(os.path.realpath(alpha), os.path.realpath(pkg_alpha))
        self.assertEqual(["alpha"], self.skill_links())


if __name__ == "__main__":
    unittest.main()
